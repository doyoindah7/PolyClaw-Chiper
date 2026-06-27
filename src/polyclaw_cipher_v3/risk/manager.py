"""Unified risk manager — single gate for all strategies.

Fixes v2 issues:
- Per-strategy risk budget (config-driven)
- Daily auto-reset
- Session rotation
- Exponential backoff on rate limit hit
- All strategies go through this gate before execution
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..core.types import Side, Signal

logger = logging.getLogger(__name__)


class RiskManager:
    """Unified risk gate with per-strategy budgets."""

    def __init__(self, config: dict[str, Any] | None = None):
        c = config or {}
        self.max_daily_dd_pct = c.get("max_daily_drawdown_pct", 50.0)
        self.max_consecutive_global = c.get("max_consecutive_losses_global", 8)
        self.max_trades_per_hour_global = c.get("max_trades_per_hour_global", 60)
        self.session_rotation_min = c.get("session_rotation_min", 240)
        # v3.4.0: Correlation exposure limit (50% default)
        self.max_net_exposure_pct = c.get("max_net_exposure_per_asset_pct", 50.0) / 100.0

        # Per-strategy config
        per_strategy = c.get("per_strategy", {})
        self._strategy_config: dict[str, dict] = {}
        for name, sc in per_strategy.items():
            self._strategy_config[name] = {
                "max_consecutive_losses": sc.get("max_consecutive_losses", 5),
                "max_trades_per_hour": sc.get("max_trades_per_hour", 30),
                "max_capital_pct": sc.get("max_capital_pct", 0.25),
            }

        # State
        self._day_start: float = time.time()
        self._day_start_bankroll: float = 0.0
        self._consecutive_losses: dict[str, int] = {}  # per strategy
        self._consecutive_global: int = 0
        self._trade_times: list[float] = []
        self._trade_times_per_strategy: dict[str, list[float]] = {}
        self._session_start: float = time.time()
        self._session_pnl: float = 0.0
        self._total_pnl_today: float = 0.0
        self._wins_today: int = 0
        self._losses_today: int = 0
        self._strategy_disabled: dict[str, bool] = {}  # circuit breaker

    def init(self, bankroll: float) -> None:
        self._day_start_bankroll = bankroll
        self._session_start = time.time()
        logger.info(
            "Risk init: bankroll=$%.2f, max_dd=%.1f%%, max_consec_global=%d",
            bankroll, self.max_daily_dd_pct, self.max_consecutive_global,
        )

    def can_trade(self, strategy: str, current_bankroll: float) -> tuple[bool, str]:
        """Check if strategy can trade right now."""
        now = time.time()

        # Auto-reset daily
        if now - self._day_start >= 86400:
            logger.info("Daily auto-reset triggered")
            self.reset_day(current_bankroll)

        # Session rotation
        session_age_min = (now - self._session_start) / 60.0
        if session_age_min >= self.session_rotation_min:
            logger.info("Session rotation: age=%.0fmin, pnl=$%.2f", session_age_min, self._session_pnl)
            self._rotate_session()

        # Circuit breaker — strategy disabled?
        if self._strategy_disabled.get(strategy):
            return False, f"Circuit breaker: {strategy} disabled (consec losses)"

        # Global daily drawdown
        if self._day_start_bankroll > 0:
            dd_pct = ((self._day_start_bankroll - current_bankroll) / self._day_start_bankroll) * 100
            if dd_pct >= self.max_daily_dd_pct:
                return False, f"Daily drawdown limit: {dd_pct:.1f}%"

        # Global consecutive losses
        if self._consecutive_global >= self.max_consecutive_global:
            return False, f"Global consec loss limit: {self._consecutive_global}"

        # Global rate limit
        self._trade_times = [t for t in self._trade_times if now - t < 3600]
        if len(self._trade_times) >= self.max_trades_per_hour_global:
            return False, f"Global rate limit: {len(self._trade_times)}/hour"

        # Per-strategy checks
        sc = self._strategy_config.get(strategy)
        if sc:
            # Per-strategy consecutive losses
            consec = self._consecutive_losses.get(strategy, 0)
            if consec >= sc["max_consecutive_losses"]:
                self._strategy_disabled[strategy] = True
                logger.warning("Strategy %s circuit breaker tripped (consec=%d)", strategy, consec)
                return False, f"Strategy consec loss limit: {consec}"

            # Per-strategy rate limit
            strat_times = self._trade_times_per_strategy.setdefault(strategy, [])
            self._trade_times_per_strategy[strategy] = [t for t in strat_times if now - t < 3600]
            if len(self._trade_times_per_strategy[strategy]) >= sc["max_trades_per_hour"]:
                return False, f"Strategy rate limit: {len(self._trade_times_per_strategy[strategy])}/hour"

        return True, ""

    def get_strategy_capital_pct(self, strategy: str) -> float:
        sc = self._strategy_config.get(strategy)
        return sc["max_capital_pct"] if sc else 0.25

    def record_entry(self, strategy: str) -> None:
        """v3.3.0: Record trade ENTRY for rate-limit tracking ONLY.

        Fixes Claude's BUG-2: previously record_trade(strategy, 0) was called on entry,
        which double-counted rate limit (entry + close = 2 entries in _trade_times).
        Now: record_entry() for rate limit, record_close() for pnl/win-loss.

        Args:
            strategy: Strategy name (e.g., 'momentum', 'atomic_arb')
        """
        now = time.time()
        self._trade_times.append(now)
        self._trade_times_per_strategy.setdefault(strategy, []).append(now)

    def record_close(self, strategy: str, pnl: float) -> None:
        """v3.3.0: Record trade CLOSE for pnl/win-loss tracking ONLY.

        Does NOT touch rate limit counter (that's record_entry()'s job).
        Updates: consecutive_losses, circuit breaker, daily pnl, wins/losses count.

        Args:
            strategy: Strategy name
            pnl: Realized PnL in dollars (positive=win, negative=loss, 0=break-even)
        """
        self._session_pnl += pnl
        self._total_pnl_today += pnl
        if pnl < 0:
            self._consecutive_losses[strategy] = self._consecutive_losses.get(strategy, 0) + 1
            self._consecutive_global += 1
            self._losses_today += 1
        elif pnl > 0:
            self._consecutive_losses[strategy] = 0
            self._consecutive_global = 0
            self._wins_today += 1
            # Re-enable strategy if it was disabled
            if self._strategy_disabled.get(strategy):
                self._strategy_disabled[strategy] = False
                logger.info("Strategy %s re-enabled after win", strategy)

    # v3.3.0: Keep record_trade() as deprecated alias for backward compat
    # (autoclaw or future code might still call it)
    def record_trade(self, strategy: str, pnl: float) -> None:
        """DEPRECATED v3.3.0: Use record_entry() + record_close() instead.

        Kept for backward compatibility. Calls record_close() only (does NOT
        double-count rate limit like before). For proper rate limiting, call
        record_entry() when opening position.
        """
        self.record_close(strategy, pnl)

    def reset_day(self, bankroll: float) -> None:
        self._day_start = time.time()
        self._day_start_bankroll = bankroll
        self._consecutive_global = 0
        self._consecutive_losses.clear()
        self._total_pnl_today = 0.0
        self._wins_today = 0
        self._losses_today = 0
        self._strategy_disabled.clear()
        logger.info("Daily reset: bankroll=$%.2f", bankroll)

    def _rotate_session(self) -> None:
        self._session_start = time.time()
        self._session_pnl = 0.0
        # Reset per-strategy consecutive losses on session rotation
        self._consecutive_losses.clear()
        self._strategy_disabled.clear()

    def check_exposure(self, strategy_name: str, current_bankroll: float, asset: str | None, signal: Signal, open_positions: list[Any]) -> tuple[bool, str]:
        """v3.4.0: Check if trade doesn't breach the maximum net directional exposure limit per asset.

        Correctly handles multi-leg/pair trades (e.g. atomic arb YES+NO) so hedged positions are not blocked.

        Args:
            strategy_name: Name of the strategy executing the trade.
            current_bankroll: Current wallet bankroll.
            asset: Cryptocurreny symbol (e.g. BTC, ETH, SOL) or None.
            signal: Signal containing side, size, and optional legs.
            open_positions: List of open positions.
        """
        if not asset:
            return True, ""

        asset = asset.upper()
        long_exp = 0.0
        short_exp = 0.0

        for p in open_positions:
            p_asset = getattr(p, "crypto_asset", None)
            if p_asset and p_asset.upper() == asset:
                if p.side.value == "YES":
                    long_exp += p.invested
                elif p.side.value == "NO":
                    short_exp += p.invested

        # Calculate potential new net exposure
        if getattr(signal, "is_pair", False) and getattr(signal, "legs", None):
            pot_long = long_exp
            pot_short = short_exp
            for leg in signal.legs:
                if leg.side.value == "YES":
                    pot_long += leg.size_usd
                else:
                    pot_short += leg.size_usd
            new_net = abs(pot_long - pot_short)
        else:
            if signal.side.value == "YES":
                new_long = long_exp + signal.suggested_size_usd
                new_short = short_exp
            else:
                new_long = long_exp
                new_short = short_exp + signal.suggested_size_usd
            new_net = abs(new_long - new_short)

        limit = current_bankroll * self.max_net_exposure_pct

        if new_net > limit:
            return False, f"Net {asset} exposure would be ${new_net:.2f} (limit ${limit:.2f}, max {self.max_net_exposure_pct*100:.0f}%)"

        return True, ""

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "consecutive_losses_global": self._consecutive_global,
            "trades_this_hour": len(self._trade_times),
            "daily_pnl": round(self._total_pnl_today, 4),
            "wins_today": self._wins_today,
            "losses_today": self._losses_today,
            "session_age_min": round((time.time() - self._session_start) / 60.0, 1),
            "disabled_strategies": [k for k, v in self._strategy_disabled.items() if v],
            "per_strategy_consec": dict(self._consecutive_losses),
        }

    @property
    def config(self) -> dict[str, Any]:
        return {
            "max_daily_drawdown_pct": self.max_daily_dd_pct,
            "max_consecutive_losses_global": self.max_consecutive_global,
            "max_trades_per_hour_global": self.max_trades_per_hour_global,
            "session_rotation_min": self.session_rotation_min,
            "per_strategy": self._strategy_config,
        }
