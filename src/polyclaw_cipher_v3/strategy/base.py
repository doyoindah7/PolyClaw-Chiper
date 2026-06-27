"""Base strategy interface — event-driven."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..core.types import Market, Signal


class BaseStrategy(ABC):
    """All strategies implement evaluate(). Event-driven via event bus."""

    name: str = "base"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._last_signal_at: dict[str, float] = {}
        self.signals_emitted: int = 0
        self.trades_won: int = 0
        self.trades_lost: int = 0
        self.total_pnl: float = 0.0

    @abstractmethod
    async def evaluate(self, market: Market, context: dict[str, Any]) -> Signal | None:
        """Return Signal if entry conditions met, None otherwise."""
        ...

    def stats(self) -> dict[str, Any]:
        total = self.trades_won + self.trades_lost
        return {
            "name": self.name,
            "signals_emitted": self.signals_emitted,
            "trades": total,
            "wins": self.trades_won,
            "losses": self.trades_lost,
            "win_rate": round((self.trades_won / total * 100) if total > 0 else 0.0, 2),
            "pnl": round(self.total_pnl, 4),
            "enabled": self.config.get("enabled", True),
        }

    # Optional hooks for TP/SL management
    def register_entry(self, pos_id: str, condition_id: str, entry_price: float) -> None:
        """Called when a position is opened (for TP/SL tracking)."""
        pass

    def check_exit(self, pos_id: str, condition_id: str, current_price: float) -> tuple[bool, str]:
        """Check if position should exit. Returns (should_exit, reason)."""
        return False, ""

    def clear_position(self, pos_id: str, condition_id: str) -> None:
        """Called when a position is closed."""
        pass

    def record_result(self, won: bool) -> None:
        """Called by bot to track win/loss for streak detection."""
        pass
