"""Paper executor — fill-probability simulation.

Simulates realistic order fills with:
- Slippage model (basis points)
- Fill probability based on bid level (lower bid = higher fill prob)
- Queue position factor
- Simulated latency
"""
from __future__ import annotations
import logging
import random
import time
import uuid
from typing import Any
from ..core.types import Position, Signal, Trade

logger = logging.getLogger(__name__)


class PaperExecutor:
    def __init__(self, config: dict[str, Any] | None = None):
        c = config or {}
        self.slippage_bps = c.get("slippage_bps", 30)
        self.fill_prob_base = c.get("fill_probability_base", 0.80)
        self.fill_prob_at_bid_low = c.get("fill_probability_at_bid_low", 0.95)
        self.fill_prob_at_bid_high = c.get("fill_probability_at_bid_high", 0.60)
        self.queue_factor = c.get("queue_position_factor", 0.5)
        self.latency_sec = c.get("simulated_latency_sec", 0.3)

    def execute_entry(
        self, signal: Signal, market_question: str, bankroll: float
    ) -> Position | None:
        """Simulate order fill. Returns Position if filled, None if not."""
        # Simulate latency
        time.sleep(self.latency_sec)

        # Fill probability: higher bid = lower fill prob (maker)
        # Map price from [bid_low=0.05, bid_high=0.95] to fill prob
        price = signal.suggested_price
        # Normalize: 0.05→0.0, 0.95→1.0
        norm = max(0.0, min(1.0, (price - 0.05) / 0.90))
        # Higher price = lower fill probability (we're less competitive)
        fill_prob = self.fill_prob_at_bid_low - norm * (
            self.fill_prob_at_bid_low - self.fill_prob_at_bid_high
        )
        fill_prob = max(0.10, min(0.99, fill_prob * self.queue_factor + self.fill_prob_base * 0.3))

        if random.random() > fill_prob:
            logger.debug(
                "Paper fill REJECTED: %s @ %.2f (prob=%.2f) | %s",
                signal.side.value, price, fill_prob, market_question[:40],
            )
            return None

        # Slippage
        slip = self.slippage_bps / 10000.0
        if signal.side.value == "YES":
            fill_price = price * (1 + slip)
        else:
            fill_price = price * (1 + slip)

        fill_price = round(min(0.99, max(0.01, fill_price)), 4)

        shares = signal.suggested_size_usd / fill_price
        invested = shares * fill_price

        pos = Position(
            id=str(uuid.uuid4())[:8],
            market_condition_id=signal.market_condition_id,
            market_question=market_question,
            side=signal.side,
            token_id=signal.token_id,
            entry_price=fill_price,
            shares=shares,
            invested=invested,
            opened_at=time.time(),
            strategy=signal.strategy_name,
            current_price=fill_price,
            current_value=invested,
        )

        logger.info(
            "PAPER FILL: %s %s @ %.4f | %d shares | $%.2f | %s",
            signal.strategy_name.upper(), signal.side.value, fill_price,
            int(shares), invested, market_question[:50],
        )
        return pos

    def resolve_position(self, pos: Position, winning_side: str) -> Trade:
        """Resolve position at market close."""
        won = pos.side.value == winning_side
        exit_price = 1.0 if won else 0.0
        exit_value = pos.shares * exit_price
        pnl = exit_value - pos.invested
        pnl_pct = (pnl / pos.invested) * 100 if pos.invested > 0 else 0.0

        trade = Trade(
            id=str(uuid.uuid4())[:8],
            market_condition_id=pos.market_condition_id,
            market_question=pos.market_question,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            shares=pos.shares,
            invested=pos.invested,
            pnl_dollar=round(pnl, 4),
            pnl_percent=round(pnl_pct, 2),
            opened_at=pos.opened_at,
            closed_at=time.time(),
            strategy=pos.strategy,
            reason=f"Resolved: {'WON' if won else 'LOST'} ({winning_side})",
        )

        logger.info(
            "RESOLVE: %s %s | entry=%.4f exit=%.4f | PnL=$%.4f (%.1f%%) | %s",
            pos.strategy.upper(), pos.side.value, pos.entry_price, exit_price,
            pnl, pnl_pct, pos.market_question[:50],
        )
        return trade

    def close_position(self, pos: Position, exit_price: float, reason: str) -> Trade:
        """Close position at given price (for TP/SL/max hold)."""
        exit_value = pos.shares * exit_price
        pnl = exit_value - pos.invested
        pnl_pct = (pnl / pos.invested) * 100 if pos.invested > 0 else 0.0

        trade = Trade(
            id=str(uuid.uuid4())[:8],
            market_condition_id=pos.market_condition_id,
            market_question=pos.market_question,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            shares=pos.shares,
            invested=pos.invested,
            pnl_dollar=round(pnl, 4),
            pnl_percent=round(pnl_pct, 2),
            opened_at=pos.opened_at,
            closed_at=time.time(),
            strategy=pos.strategy,
            reason=reason,
        )

        logger.info(
            "CLOSE: %s %s | entry=%.4f exit=%.4f | PnL=$%.4f (%.1f%%) | %s | %s",
            pos.strategy.upper(), pos.side.value, pos.entry_price, exit_price,
            pnl, pnl_pct, reason, pos.market_question[:40],
        )
        return trade
