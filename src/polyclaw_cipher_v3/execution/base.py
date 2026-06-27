"""Executor interface — abstract base for paper/live execution."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..core.types import Position, Signal, Trade


class BaseExecutor(ABC):
    @abstractmethod
    async def execute_entry(self, signal: Signal, market_question: str, bankroll: float) -> Position | None:
        """Execute entry. Returns Position if filled, None if not."""
        ...

    @abstractmethod
    async def resolve_position(self, pos: Position, winning_side: str) -> Trade:
        """Resolve position at market close."""
        ...

    @abstractmethod
    async def close_position(self, pos: Position, exit_price: float, reason: str) -> Trade:
        """Close position at given price (TP/SL/max hold)."""
        ...
