"""Atomic arbitrage — buy YES + NO when combined ask < $1 (risk-free).

Edge: When YES ask + NO ask < $1 (after fees), buying both sides
guarantees payout of $1 at resolution. Profit = $1 - combined_cost.

This is REAL arbitrage (unlike v2's fake single-leg "arb").
Uses pair-trade Signal with multi-leg support.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..core.types import Leg, Market, Side, Signal
from .base import BaseStrategy

logger = logging.getLogger(__name__)


class AtomicArbStrategy(BaseStrategy):
    name = "atomic_arb"

    def __init__(self, config: dict[str, Any] | None = None, clob_feed=None):
        super().__init__(config)
        c = self.config
        self.min_profit_bps = c.get("min_profit_bps", 40)  # 0.4% min profit (lowered from 100)
        self.max_position_pct = c.get("max_position_pct", 0.40)
        self.max_concurrent = c.get("max_concurrent", 5)
        self.scan_interval_sec = c.get("scan_interval_sec", 1)
        self.cooldown_sec = 5
        # v3.3.1 fix: Add category filter (was missing — atomic_arb traded sports_spread)
        self.skip_random_outcome = c.get("skip_random_outcome", True)
        self.allowed_categories = c.get("allowed_categories",
            ["crypto", "sports_total", "economics", "politics", "other"])
        self._clob = clob_feed
        self._last_signal_at: dict[str, float] = {}

    def set_clob_feed(self, clob_feed) -> None:
        self._clob = clob_feed

    async def evaluate(self, market: Market, context: dict[str, Any]) -> Signal | None:
        if not self._clob:
            return None

        # v3.3.1 fix: Category filter — skip random-outcome markets (sports_spread)
        # Was missing, caused bot to trade "Spread: Belgium (-2.5)" = random outcome
        if self.skip_random_outcome and market.is_random_outcome:
            return None
        cat = market.classify()
        if self.allowed_categories and cat not in self.allowed_categories:
            return None

        # Skip if market is closed or resolved
        if market.is_closed:
            return None

        # Cooldown
        now = time.time()
        last = self._last_signal_at.get(market.condition_id, 0.0)
        if now - last < self.cooldown_sec:
            return None

        # Max concurrent
        open_positions = context.get("open_positions", [])
        arb_positions = [p for p in open_positions if p.strategy == self.name]
        if len(arb_positions) >= self.max_concurrent:
            return None

        # Already in this market?
        if any(p.market_condition_id == market.condition_id for p in arb_positions):
            return None

        # Get best asks from CLOB WS
        yes_ask = self._clob.get_best_ask(market.yes_token_id)
        no_ask = self._clob.get_best_ask(market.no_token_id)
        # Fallback to market.yes_price / no_price if WS data missing
        if yes_ask <= 0:
            yes_ask = market.yes_price
        if no_ask <= 0:
            no_ask = market.no_price

        # Combined ask — this is what we'd pay to buy both sides
        combined_ask = yes_ask + no_ask
        if combined_ask >= 1.0:
            return None

        # Profit in basis points
        profit_bps = int((1.0 - combined_ask) * 10000)
        if profit_bps < self.min_profit_bps:
            return None

        # Position size
        bankroll = context.get("bankroll", 25.0)
        cash = context.get("cash", bankroll)
        sizer = context.get("sizer")
        strategy_cap_pct = context.get("strategy_cap_pct", self.max_position_pct)
        if sizer:
            notional = sizer.size(
                bankroll=bankroll,
                cash=cash,
                open_positions_for_strategy=len(arb_positions),
                max_positions_for_strategy=self.max_concurrent,
                confidence=0.95,  # High confidence — risk-free
                strategy_max_pct=strategy_cap_pct,
                total_open_positions=context.get("total_open_positions", 0),
                max_total_positions=context.get("max_total_positions", 10),
            )
        else:
            notional = min(cash * 0.90, bankroll * self.max_position_pct)
            notional = max(1.0, notional)

        if notional < 1.0:
            return None

        # Split notional between YES and NO legs
        # For arb, we want equal SHARES on both sides so total payout = $1 per share pair
        # shares_yes = shares_no = notional / combined_ask (approx)
        # notional_yes = shares * yes_ask
        # notional_no = shares * no_ask
        shares = notional / combined_ask
        notional_yes = round(shares * yes_ask, 2)
        notional_no = round(shares * no_ask, 2)

        confidence = min(0.99, 0.85 + profit_bps / 200.0)
        self._last_signal_at[market.condition_id] = now
        self.signals_emitted += 1

        logger.info(
            "ATOMIC ARB SIGNAL: %s | YES ask=%.3f NO ask=%.3f combined=%.3f profit=%dbps | $%.2f | %s",
            market.condition_id[:8], yes_ask, no_ask, combined_ask, profit_bps, notional,
            market.question[:50],
        )

        # Build pair signal with both legs
        return Signal(
            market_condition_id=market.condition_id,
            side=Side.YES,  # Primary side (executor creates primary position from first leg)
            suggested_price=yes_ask,
            suggested_size_usd=notional,
            confidence=confidence,
            reason=f"AtomicArb: YES+NO=${combined_ask:.3f} < $1, profit={profit_bps}bps",
            strategy_name=self.name,
            token_id=market.yes_token_id,
            timestamp=now,
            is_pair=True,
            legs=[
                Leg(token_id=market.yes_token_id, side=Side.YES, price=yes_ask, size_usd=notional_yes),
                Leg(token_id=market.no_token_id, side=Side.NO, price=no_ask, size_usd=notional_no),
            ],
        )
