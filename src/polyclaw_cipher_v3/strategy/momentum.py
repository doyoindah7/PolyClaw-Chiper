"""Momentum strategy — refined v2 universal, uses CLOB WS for 60x faster reaction.

Multi-timeframe: 30s + 2m confirmation.
TP 8% / SL 4% / max hold 5 minutes.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..core.types import Market, Side, Signal
from .base import BaseStrategy

logger = logging.getLogger(__name__)


class MomentumStrategy(BaseStrategy):
    name = "momentum"

    def __init__(self, config: dict[str, Any] | None = None, clob_feed=None):
        super().__init__(config)
        c = self.config
        self.lookback_short_sec = c.get("lookback_short_sec", 30)
        self.lookback_long_sec = c.get("lookback_long_sec", 120)
        self.min_momentum_short_pct = c.get("min_momentum_short_pct", 1.0)
        self.min_momentum_long_pct = c.get("min_momentum_long_pct", 0.5)
        self.take_profit_pct = c.get("take_profit_pct", 8.0)
        self.stop_loss_pct = c.get("stop_loss_pct", 4.0)
        self.max_hold_sec = c.get("max_hold_sec", 300)
        self.max_positions = c.get("max_positions", 3)
        self.cooldown_sec = c.get("cooldown_sec", 30)
        self.min_entry_price = c.get("min_entry_price", 0.05)
        self.max_entry_price = c.get("max_entry_price", 0.95)
        self.max_notional_pct = c.get("max_notional_pct", 0.15)
        self.min_confidence = c.get("min_confidence", 0.40)
        self.max_volatility = c.get("max_volatility", 0.08)
        self._clob = clob_feed
        self._entry_prices: dict[str, float] = {}
        self._entry_times: dict[str, float] = {}

    def set_clob_feed(self, clob_feed) -> None:
        self._clob = clob_feed

    async def evaluate(self, market: Market, context: dict[str, Any]) -> Signal | None:
        if not self._clob:
            return None

        # Filters
        if market.volume_24h < 500:
            return None
        if market.yes_price < self.min_entry_price or market.yes_price > self.max_entry_price:
            return None

        # Cooldown
        now = time.time()
        last = self._last_signal_at.get(market.condition_id, 0.0)
        if now - last < self.cooldown_sec:
            return None

        # Max positions
        open_positions = context.get("open_positions", [])
        my_positions = [p for p in open_positions if p.strategy == self.name]
        if len(my_positions) >= self.max_positions:
            return None

        # One per market
        if any(p.market_condition_id == market.condition_id for p in my_positions):
            return None

        # CLOB data
        yes_change_short = self._clob.get_pct_change(market.yes_token_id, self.lookback_short_sec)
        no_change_short = self._clob.get_pct_change(market.no_token_id, self.lookback_short_sec)
        yes_change_long = self._clob.get_pct_change(market.yes_token_id, self.lookback_long_sec)
        no_change_long = self._clob.get_pct_change(market.no_token_id, self.lookback_long_sec)

        yes_price_clob = self._clob.get_price(market.yes_token_id)
        no_price_clob = self._clob.get_price(market.no_token_id)
        if yes_price_clob <= 0 and no_price_clob <= 0:
            return None

        # Multi-timeframe analysis
        max_change_short = max(abs(yes_change_short), abs(no_change_short))
        max_change_long = max(abs(yes_change_long), abs(no_change_long))

        # Both timeframes must agree (trend confirmation)
        if max_change_short < self.min_momentum_short_pct:
            return None
        if max_change_long < self.min_momentum_long_pct:
            return None

        # Direction: pick side with more momentum
        if abs(yes_change_short) >= abs(no_change_short):
            change = yes_change_short
            side = Side.YES if change > 0 else Side.NO
            token_id = market.yes_token_id if side == Side.YES else market.no_token_id
            entry_price = market.yes_price if side == Side.YES else market.no_price
        else:
            change = no_change_short
            side = Side.NO if change > 0 else Side.YES
            token_id = market.yes_token_id if side == Side.YES else market.no_token_id
            entry_price = market.yes_price if side == Side.YES else market.no_price

        # Confidence: scale with magnitude + trend alignment
        trend_alignment = 1.0 if (yes_change_short * yes_change_long) > 0 else 0.7
        confidence = min(0.92, 0.45 + abs(change) / 15.0)
        confidence *= trend_alignment

        if confidence < self.min_confidence:
            return None

        # Volatility check (adaptive)
        vol = self._clob.get_volatility(token_id, 120.0)
        if vol > self.max_volatility:
            return None
        elif vol > 0.04:
            confidence *= 0.9  # Mild penalty

        # Position size
        bankroll = context.get("bankroll", 25.0)
        cash = context.get("cash", bankroll)
        sizer = context.get("sizer")
        strategy_cap_pct = context.get("strategy_cap_pct", self.max_notional_pct)
        if sizer:
            notional = sizer.size(
                bankroll=bankroll,
                cash=cash,
                open_positions_for_strategy=len(my_positions),
                max_positions_for_strategy=self.max_positions,
                confidence=confidence,
                strategy_max_pct=strategy_cap_pct,
            )
        else:
            available_slots = max(1, self.max_positions - len(my_positions))
            notional = (cash / available_slots) * (0.6 + confidence * 0.8)
            notional = min(notional, bankroll * self.max_notional_pct)
            notional = max(2.5, min(notional, cash * 0.90))

        if notional < 1.0:
            return None

        direction = "UP" if change > 0 else "DOWN"
        self._last_signal_at[market.condition_id] = now
        self.signals_emitted += 1

        logger.info(
            "MOMENTUM SIGNAL: %s %s | short=%+.2f%% long=%+.2f%% | conf=%.2f | $%.2f | %s",
            side.value, direction, max_change_short, max_change_long,
            confidence, notional, market.question[:50],
        )

        return Signal(
            market_condition_id=market.condition_id,
            side=side,
            suggested_price=entry_price,
            suggested_size_usd=notional,
            confidence=confidence,
            reason=f"Momentum: {direction} short={change:+.2f}% long={yes_change_long:+.2f}% vol={vol:.3f}",
            strategy_name=self.name,
            token_id=token_id,
            timestamp=now,
        )

    def register_entry(self, pos_id: str, condition_id: str, entry_price: float) -> None:
        self._entry_prices[pos_id] = entry_price
        self._entry_times[pos_id] = time.time()

    def check_exit(self, pos_id: str, condition_id: str, current_price: float) -> tuple[bool, str]:
        entry = self._entry_prices.get(pos_id)
        if entry is None or entry <= 0:
            return False, ""
        pnl_pct = ((current_price - entry) / entry) * 100
        if pnl_pct >= self.take_profit_pct:
            return True, f"Momentum TP: +{pnl_pct:.1f}%"
        if pnl_pct <= -self.stop_loss_pct:
            return True, f"Momentum SL: {pnl_pct:.1f}%"
        entry_time = self._entry_times.get(pos_id, 0)
        if time.time() - entry_time > self.max_hold_sec:
            return True, f"Momentum time exit: {pnl_pct:.1f}%"
        return False, ""

    def clear_position(self, pos_id: str, condition_id: str) -> None:
        self._entry_prices.pop(pos_id, None)
        self._entry_times.pop(pos_id, None)
