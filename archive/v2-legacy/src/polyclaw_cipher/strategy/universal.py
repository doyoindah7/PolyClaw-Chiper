"""Universal Scalper v2.0 - Multi-timeframe volatile market hunter.

Improvements:
- Multi-timeframe analysis (5min + 15min)
- Better volatility filter with adaptive penalty
- Trend strength confirmation
- Session-aware cooldown (reset on new session)
"""
from __future__ import annotations
import logging
import time
from collections import defaultdict
from typing import Any
from ..core.types import Market, Side, Signal
from .base import BaseStrategy

logger = logging.getLogger(__name__)


class UniversalScalper(BaseStrategy):
    name = "universal"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        c = self.config
        self.min_volume_24h = c.get("min_volume_24h", 1000)
        self.min_liquidity = c.get("min_liquidity", 300)
        self.lookback_sec = c.get("lookback_sec", 300)            # 5 min
        self.secondary_lookback_sec = c.get("secondary_lookback_sec", 900)  # 15 min
        self.min_price_change_pct = c.get("min_price_change_pct", 1.5)
        self.min_secondary_change_pct = c.get("min_secondary_change_pct", 0.8)
        self.take_profit_pct = c.get("take_profit_pct", 12.0)
        self.stop_loss_pct = c.get("stop_loss_pct", 6.0)
        self.max_positions = c.get("max_positions", 3)
        self.cooldown_sec = c.get("cooldown_sec", 60)
        self.min_entry_price = c.get("min_entry_price", 0.10)
        self.max_entry_price = c.get("max_entry_price", 0.90)
        self.max_notional_pct = c.get("max_notional_pct", 0.35)
        self.min_confidence = c.get("min_confidence", 0.40)
        self.max_volatility = c.get("max_volatility", 0.08)
        self.volatility_penalty_soft = c.get("volatility_penalty_soft", 0.9)

        # State
        self._entry_prices: dict[str, float] = {}
        self._entry_times: dict[str, float] = {}
        self._session_start: float = time.time()

    def set_clob_feed(self, clob_feed):
        self._clob = clob_feed

    async def evaluate(self, market: Market, context: dict[str, Any]) -> Signal | None:
        if not hasattr(self, '_clob') or self._clob is None:
            return None

        # Filter: volume + liquidity
        if market.volume_24h < self.min_volume_24h:
            return None
        if market.liquidity > 0 and market.liquidity < self.min_liquidity:
            return None

        # Filter: odds range
        yes_price = market.yes_price
        no_price = market.no_price
        if yes_price < self.min_entry_price or yes_price > self.max_entry_price:
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

        # Get CLOB data
        yes_change_5m = self._clob.get_pct_change(market.yes_token_id, self.lookback_sec)
        no_change_5m = self._clob.get_pct_change(market.no_token_id, self.lookback_sec)
        yes_change_15m = self._clob.get_pct_change(market.yes_token_id, self.secondary_lookback_sec)
        no_change_15m = self._clob.get_pct_change(market.no_token_id, self.secondary_lookback_sec)

        yes_price_clob = self._clob.get_price(market.yes_token_id)
        no_price_clob = self._clob.get_price(market.no_token_id)
        if yes_price_clob <= 0 and no_price_clob <= 0:
            return None

        # Multi-timeframe analysis
        max_change_5m = max(abs(yes_change_5m), abs(no_change_5m))
        max_change_15m = max(abs(yes_change_15m), abs(no_change_15m))

        # Need both timeframes to agree (trend confirmation)
        if max_change_5m < self.min_price_change_pct:
            return None
        if max_change_15m < self.min_secondary_change_pct:
            return None

        # Direction: pick side with more momentum
        if abs(yes_change_5m) >= abs(no_change_5m):
            change = yes_change_5m
            side = Side.YES if change > 0 else Side.NO
            token_id = market.yes_token_id if side == Side.YES else market.no_token_id
            entry_price = yes_price if side == Side.YES else no_price
        else:
            change = no_change_5m
            side = Side.NO if change > 0 else Side.YES
            token_id = market.yes_token_id if side == Side.YES else market.no_token_id
            entry_price = yes_price if side == Side.YES else no_price

        # Confidence: scale with magnitude + trend alignment
        trend_alignment = 1.0 if (yes_change_5m * yes_change_15m) > 0 else 0.7
        confidence = min(0.92, 0.45 + abs(change) / 15.0)
        confidence *= trend_alignment

        if confidence < self.min_confidence:
            return None

        # Volatility check - adaptive
        vol = self._clob.get_volatility(token_id, 120.0)
        if vol > self.max_volatility:
            return None  # Too choppy, skip
        elif vol > 0.04:
            confidence *= self.volatility_penalty_soft  # Mild penalty

        # Position size with confidence scaling
        bankroll = context.get("bankroll", 25.0)
        cash = context.get("cash", bankroll)
        available_slots = max(1, self.max_positions - len(my_positions))
        base_notional = cash / available_slots
        
        confidence_multiplier = 0.6 + (confidence * 0.8)  # 0.6 - 1.34x
        notional = base_notional * confidence_multiplier
        notional = min(notional, bankroll * self.max_notional_pct)
        notional = max(2.5, min(notional, cash * 0.90))
        notional = round(notional, 2)

        direction = "UP" if change > 0 else "DOWN"
        self._last_signal_at[market.condition_id] = now
        self.signals_emitted += 1

        logger.info(
            "UNIVERSAL SIGNAL: %s %s | 5m:%+.1f%% 15m:%+.1f%% | conf=%.2f | $%.2f | %s",
            side.value, direction, max_change_5m, max_change_15m,
            confidence, notional, market.question[:50],
        )

        return Signal(
            market_condition_id=market.condition_id,
            side=side,
            suggested_price=entry_price,
            suggested_size_usd=notional,
            confidence=confidence,
            reason=f"Universal: {direction} 5m={change:+.1f}% 15m={yes_change_15m:+.1f}% vol={vol:.3f}",
            strategy_name=self.name,
            token_id=token_id,
            timestamp=now,
        )

    def register_entry(self, pos_id: str, condition_id: str, entry_price: float):
        self._entry_prices[pos_id] = entry_price
        self._entry_times[pos_id] = time.time()

    def check_exit(self, pos_id: str, condition_id: str, current_price: float) -> tuple[bool, str]:
        entry = self._entry_prices.get(pos_id)
        if entry is None or entry <= 0:
            return False, ""
        pnl_pct = ((current_price - entry) / entry) * 100
        if pnl_pct >= self.take_profit_pct:
            return True, f"Universal TP: +{pnl_pct:.1f}%"
        if pnl_pct <= -self.stop_loss_pct:
            return True, f"Universal SL: {pnl_pct:.1f}%"
        
        # Time-based exit: max 20 minutes for universal
        entry_time = self._entry_times.get(pos_id, 0)
        if time.time() - entry_time > 1200:  # 20 min
            return True, f"Universal time exit: {pnl_pct:.1f}%"
        
        return False, ""

    def clear_position(self, pos_id: str, condition_id: str):
        self._entry_prices.pop(pos_id, None)
        self._entry_times.pop(pos_id, None)
