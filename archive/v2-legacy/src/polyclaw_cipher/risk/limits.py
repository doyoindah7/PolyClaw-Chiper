"""Risk Limits v2.0 - Auto-reset daily + session rotation."""
from __future__ import annotations
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class DrawdownLimiter:
    """Drawdown limiter with daily auto-reset and session management."""

    def __init__(self, config: dict[str, Any] | None = None):
        c = config or {}
        self.max_daily_dd_pct = c.get("max_daily_drawdown_pct", 40.0)
        self.max_consecutive = c.get("max_consecutive_losses", 5)
        self.max_trades_per_hour = c.get("max_trades_per_hour", 30)
        self.session_rotation_min = c.get("session_rotation_min", 240)  # 4 hours

        self._day_start: float = time.time()
        self._day_start_bankroll: float = 0.0
        self._consecutive_losses: int = 0
        self._trade_times: list[float] = []
        self._session_start: float = time.time()
        self._session_pnl: float = 0.0
        self._total_pnl_today: float = 0.0
        self._wins_today: int = 0
        self._losses_today: int = 0

    def init(self, bankroll: float):
        self._day_start_bankroll = bankroll
        self._session_start = time.time()
        logger.info("Risk init: bankroll=$%.2f, max_dd=%.1f%%, max_consec=%d",
                     bankroll, self.max_daily_dd_pct, self.max_consecutive)

    def can_trade(self, current_bankroll: float) -> tuple[bool, str]:
        now = time.time()

        # Auto-reset daily
        if now - self._day_start >= 86400:
            logger.info("Daily auto-reset triggered")
            self.reset_day(current_bankroll)

        # Session rotation check
        session_age_min = (now - self._session_start) / 60.0
        if session_age_min >= self.session_rotation_min:
            logger.info("Session rotation: age=%.0fmin, pnl=$%.2f", session_age_min, self._session_pnl)
            self._rotate_session()

        # Check daily drawdown
        if self._day_start_bankroll > 0:
            dd_pct = ((self._day_start_bankroll - current_bankroll) / self._day_start_bankroll) * 100
            if dd_pct >= self.max_daily_dd_pct:
                return False, f"Daily drawdown limit: {dd_pct:.1f}%"

        # Consecutive losses
        if self._consecutive_losses >= self.max_consecutive:
            return False, f"Consecutive loss limit: {self._consecutive_losses}"

        # Trades per hour
        self._trade_times = [t for t in self._trade_times if now - t < 3600]
        if len(self._trade_times) >= self.max_trades_per_hour:
            return False, f"Rate limit: {len(self._trade_times)} trades/hour"

        return True, ""

    def record_trade(self, pnl: float):
        now = time.time()
        self._trade_times.append(now)
        self._session_pnl += pnl
        self._total_pnl_today += pnl
        if pnl < 0:
            self._consecutive_losses += 1
            self._losses_today += 1
        elif pnl > 0:
            self._consecutive_losses = 0
            self._wins_today += 1

    def reset_day(self, bankroll: float):
        """Reset daily stats."""
        self._day_start = time.time()
        self._day_start_bankroll = bankroll
        self._consecutive_losses = 0
        self._total_pnl_today = 0.0
        self._wins_today = 0
        self._losses_today = 0
        logger.info("Daily reset: bankroll=$%.2f", bankroll)

    def _rotate_session(self):
        """Rotate session - reset session stats but keep daily."""
        self._session_start = time.time()
        self._session_pnl = 0.0
        self._consecutive_losses = 0  # Fresh start

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def daily_pnl(self) -> float:
        return self._total_pnl_today

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "consecutive_losses": self._consecutive_losses,
            "trades_this_hour": len(self._trade_times),
            "daily_pnl": round(self._total_pnl_today, 4),
            "wins_today": self._wins_today,
            "losses_today": self._losses_today,
            "session_age_min": round((time.time() - self._session_start) / 60.0, 1),
        }
