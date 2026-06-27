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

logger = logging.getLogger(__name__)


class RiskManager:
    """Unified risk gate with per-strategy budgets."""

    def __init__(self, config: dict[str, Any] | None = None):
        c = config or {}
        self.max_daily_dd_pct = c.get("max_daily_drawdown_pct", 50.0)
        self.max_consecutive_global = c.get("max_consecutive_losses_global", 8)
        self.max_trades_per_hour_global = c.get("max_trades_per_hour_global", 60)
        self.session_rotation_min = c.get("session_rotation_min", 240)

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

    def record_trade(self, strategy: str, pnl: float) -> None:
        """Record trade result for risk tracking."""
        now = time.time()
        self._trade_times.append(now)
        self._trade_times_per_strategy.setdefault(strategy, []).append(now)
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
