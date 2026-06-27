"""Wallet state — bankroll & cash management via SQLite."""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class Wallet:
    """Persistent wallet state backed by SQLite."""

    def __init__(self, db, initial_bankroll: float = 25.0):
        self.db = db
        self.initial_bankroll = initial_bankroll
        self._bankroll: float = 0.0
        self._cash: float = 0.0

    async def load(self) -> None:
        """Load wallet from DB, init if fresh."""
        row = await self.db.fetchone("SELECT * FROM wallet WHERE id = 1")
        if row is None:
            # Fresh init
            self._bankroll = self.initial_bankroll
            self._cash = self.initial_bankroll
            await self._save()
            logger.info("Wallet initialized: $%.2f", self._bankroll)
        else:
            self._bankroll = row["bankroll"]
            self._cash = row["cash"]
            self.initial_bankroll = row["initial_bankroll"]
            logger.info("Wallet loaded: bankroll=$%.2f, cash=$%.2f", self._bankroll, self._cash)

    async def _save(self) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO wallet (id, bankroll, cash, initial_bankroll, updated_at) VALUES (1, ?, ?, ?, ?)",
            (self._bankroll, self._cash, self.initial_bankroll, time.time()),
        )

    @property
    def bankroll(self) -> float:
        # bankroll = cash + sum(invested of open positions)
        # Computed lazily via repository — for simplicity here return cached
        return self._bankroll

    @property
    def cash(self) -> float:
        return self._cash

    async def debit(self, amount: float) -> None:
        """Reduce cash (when opening position)."""
        self._cash -= amount
        await self._save()

    async def credit(self, amount: float) -> None:
        """Add cash (when closing position — return invested + pnl)."""
        self._cash += amount
        await self._save()

    async def set_bankroll(self, value: float) -> None:
        """Update bankroll after recompute (called by repository)."""
        self._bankroll = value
        await self._save()

    def snapshot(self) -> dict:
        return {
            "bankroll": round(self._bankroll, 4),
            "cash": round(self._cash, 4),
            "initial_bankroll": round(self.initial_bankroll, 4),
            "pnl": round(self._bankroll - self.initial_bankroll, 4),
        }
