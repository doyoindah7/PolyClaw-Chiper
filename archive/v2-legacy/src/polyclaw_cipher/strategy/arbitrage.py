"""Arbitrage 101 — buy YES+NO when combined < $1.

Risk-free profit: if YES + NO < $1, buying both sides guarantees
payout of $1 at resolution. Profit = $1 - combined_cost.

Scans ALL markets, not just crypto.
"""
from __future__ import annotations
import logging
import time
from typing import Any
from ..core.types import Market, Side, Signal
from .base import BaseStrategy

logger = logging.getLogger(__name__)


class Arbitrage101(BaseStrategy):
    name = "arbitrage"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        c = self.config
        self.min_profit_bps = c.get("min_profit_bps", 30)  # 0.3%
        self.max_concurrent = c.get("max_concurrent", 3)
        self.max_position_pct = c.get("max_position_pct", 0.30)
        self.opportunities_found: int = 0

    async def evaluate(self, market: Market, context: dict[str, Any]) -> Signal | None:
        # Any binary market qualifies
        combined = market.yes_price + market.no_price

        # Profit in basis points
        if combined >= 1.0:
            return None

        profit_bps = int((1.0 - combined) * 10000)
        if profit_bps < self.min_profit_bps:
            return None

        # Max concurrent
        open_positions = context.get("open_positions", [])
        arb_positions = [p for p in open_positions if p.strategy == self.name]
        if len(arb_positions) >= self.max_concurrent:
            return None

        # Cooldown
        now = time.time()
        last = self._last_signal_at.get(market.condition_id, 0.0)
        if now - last < 5.0:
            return None

        # Position size
        bankroll = context.get("bankroll", 25.0)
        notional = min(bankroll * self.max_position_pct, bankroll * 0.30)
        notional = max(0.50, notional)

        confidence = min(0.99, profit_bps / 100.0 + 0.50)

        self._last_signal_at[market.condition_id] = now
        self.opportunities_found += 1
        self.signals_emitted += 1

        logger.info(
            "ARB SIGNAL: %s | YES=%.3f NO=%.3f combined=%.3f profit=%dbps | %s",
            market.condition_id[:8], market.yes_price, market.no_price,
            combined, profit_bps, market.question[:60],
        )

        return Signal(
            market_condition_id=market.condition_id,
            side=Side.YES,  # Buy YES side (executor handles both)
            suggested_price=market.yes_price,
            suggested_size_usd=notional,
            confidence=confidence,
            reason=f"Arb: YES+NO=${combined:.3f} < $1, profit={profit_bps}bps",
            strategy_name=self.name,
            token_id=market.yes_token_id,
            timestamp=now,
        )
