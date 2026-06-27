"""Base strategy interface."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any
from ..core.types import Market, Signal


class BaseStrategy(ABC):
    """All strategies implement evaluate()."""

    name: str = "base"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._last_signal_at: dict[str, float] = {}
        self.signals_emitted: int = 0
        self.trades_won: int = 0
        self.trades_lost: int = 0
        self.total_pnl: float = 0.0

    @abstractmethod
    async def evaluate(
        self, market: Market, context: dict[str, Any]
    ) -> Signal | None:
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
            "win_rate": (self.trades_won / total * 100) if total > 0 else 0.0,
            "pnl": round(self.total_pnl, 4),
        }
