"""Async paper executor — fill-probability simulation, NO blocking calls.

v3.2.0 FIXES:
- Pair signal support: creates BOTH legs for atomic_arb
- take_pair_sibling() method for bot.py to get the second position
- If any leg fails fill, entire pair is rejected (atomic)
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from typing import Any

from ..core.types import Position, Signal, Side, Trade
from .base import BaseExecutor

logger = logging.getLogger(__name__)


class PaperExecutor(BaseExecutor):
    """Async paper executor with pair-trade support."""

    def __init__(self, config: dict[str, Any] | None = None):
        c = config or {}
        self.slippage_bps = c.get("slippage_bps", 25)
        self.fill_prob_base = c.get("fill_probability_base", 0.85)
        self.fill_prob_at_bid_low = c.get("fill_probability_at_bid_low", 0.95)
        self.fill_prob_at_bid_high = c.get("fill_probability_at_bid_high", 0.65)
        self.queue_factor = c.get("queue_position_factor", 0.6)
        self.latency_sec = c.get("simulated_latency_sec", 0.2)
        # Pair sibling: for atomic arb, executor creates 2 positions
        self._pair_sibling: Position | None = None

    async def execute_entry(
        self, signal: Signal, market_question: str, bankroll: float
    ) -> Position | None:
        """Simulate order fill. NON-blocking. For pair signals, creates both legs."""
        # Async latency simulation (no time.sleep!)
        await asyncio.sleep(self.latency_sec)

        # Fill probability per leg
        # For pair signals, ALL legs must fill (atomic)
        filled_legs = []
        for leg in signal.legs:
            if await self._simulate_fill(leg.price):
                slip = self.slippage_bps / 10000.0
                fill_price = round(min(0.99, max(0.01, leg.price * (1 + slip))), 4)
                filled_legs.append((leg, fill_price))
            else:
                logger.debug(
                    "Paper fill REJECTED: %s @ %.4f | %s",
                    leg.side.value, leg.price, market_question[:40],
                )
                # For pair signals: if ANY leg fails, reject entire pair
                self._pair_sibling = None
                return None

        if not filled_legs:
            return None

        # Use first leg as primary position
        primary_leg, primary_price = filled_legs[0]
        primary_shares = signal.suggested_size_usd / primary_price if primary_price > 0 else 0
        primary_invested = primary_shares * primary_price
        pos_id = uuid.uuid4().hex[:8]
        pair_id = signal.id if signal.is_pair else ""

        # For pair signals: calculate shares based on COMBINED ask
        # shares = notional / combined_ask (same shares on both sides)
        if signal.is_pair and len(filled_legs) >= 2:
            combined_ask = primary_price + filled_legs[1][1]
            if combined_ask > 0:
                pair_shares = signal.suggested_size_usd / combined_ask
            else:
                pair_shares = primary_shares
            primary_shares = pair_shares
            primary_invested = pair_shares * primary_price

        pos = Position(
            id=pos_id,
            market_condition_id=signal.market_condition_id,
            market_question=market_question,
            side=primary_leg.side,
            token_id=primary_leg.token_id,
            entry_price=primary_price,
            shares=primary_shares,
            invested=primary_invested,
            strategy=signal.strategy_name,
            opened_at=time.time(),
            current_price=primary_price,
            current_value=primary_invested,
            is_pair=signal.is_pair,
            pair_id=pair_id,
        )

        # For pair signals: create sibling position (second leg)
        self._pair_sibling = None
        if signal.is_pair and len(filled_legs) >= 2:
            second_leg, second_price = filled_legs[1]
            second_invested = primary_shares * second_price  # Same shares as primary
            sibling_id = uuid.uuid4().hex[:8]

            self._pair_sibling = Position(
                id=sibling_id,
                market_condition_id=signal.market_condition_id,
                market_question=market_question,
                side=second_leg.side,
                token_id=second_leg.token_id,
                entry_price=second_price,
                shares=primary_shares,  # Same shares
                invested=second_invested,
                strategy=signal.strategy_name,
                opened_at=time.time(),
                current_price=second_price,
                current_value=second_invested,
                is_pair=True,
                pair_id=pair_id,
                pair_sibling_id=pos_id,
            )
            # Link primary to sibling
            pos.pair_sibling_id = sibling_id

        logger.info(
            "PAPER FILL: %s %s @ %.4f | %d shares | $%.2f%s | %s",
            signal.strategy_name.upper(), primary_leg.side.value, primary_price,
            int(primary_shares), primary_invested,
            " +PAIR" if self._pair_sibling else "",
            market_question[:50],
        )
        return pos

    def take_pair_sibling(self) -> Position | None:
        """Get and clear the pending pair sibling position."""
        sibling = self._pair_sibling
        self._pair_sibling = None
        return sibling

    async def _simulate_fill(self, price: float) -> bool:
        """Simulate fill probability based on bid level."""
        norm = max(0.0, min(1.0, (price - 0.05) / 0.90))
        fill_prob = self.fill_prob_at_bid_low - norm * (
            self.fill_prob_at_bid_low - self.fill_prob_at_bid_high
        )
        fill_prob = max(0.10, min(0.99, fill_prob * self.queue_factor + self.fill_prob_base * 0.3))
        return random.random() <= fill_prob

    async def resolve_position(self, pos: Position, winning_side: str) -> Trade:
        """Resolve position at market close."""
        won = pos.side.value == winning_side
        exit_price = 1.0 if won else 0.0
        exit_value = pos.shares * exit_price
        pnl = exit_value - pos.invested
        pnl_pct = (pnl / pos.invested) * 100 if pos.invested > 0 else 0.0

        trade = Trade(
            id=uuid.uuid4().hex[:8],
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
            is_pair=pos.is_pair,
            pair_id=pos.pair_id,
        )

        logger.info(
            "RESOLVE: %s %s | entry=%.4f exit=%.4f | PnL=$%.4f (%.1f%%) | %s",
            pos.strategy.upper(), pos.side.value, pos.entry_price, exit_price,
            pnl, pnl_pct, pos.market_question[:50],
        )
        return trade

    async def close_position(self, pos: Position, exit_price: float, reason: str) -> Trade:
        """Close position at given price (TP/SL/max hold)."""
        exit_price = max(0.01, min(0.99, exit_price))
        exit_value = pos.shares * exit_price
        pnl = exit_value - pos.invested
        pnl_pct = (pnl / pos.invested) * 100 if pos.invested > 0 else 0.0

        trade = Trade(
            id=uuid.uuid4().hex[:8],
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
            is_pair=pos.is_pair,
            pair_id=pos.pair_id,
        )

        logger.info(
            "CLOSE: %s %s | entry=%.4f exit=%.4f | PnL=$%.4f (%.1f%%) | %s | %s",
            pos.strategy.upper(), pos.side.value, pos.entry_price, exit_price,
            pnl, pnl_pct, reason, pos.market_question[:40],
        )
        return trade
