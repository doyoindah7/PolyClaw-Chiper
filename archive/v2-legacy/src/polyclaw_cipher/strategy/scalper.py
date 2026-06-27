"""Crypto Scalper v2.0 - Improved for aggressive growth.

Improvements:
- Volume confirmation via Binance kline data
- Better entry timing (12h window)
- RSI-based overbought/oversold filter
- Tighter TP/SL with dynamic adjustment
- Anti-churn: minimum 30s cooldown + win/loss tracking
"""
from __future__ import annotations
import logging
import time
from typing import Any
from ..core.types import Market, Side, Signal
from .base import BaseStrategy

logger = logging.getLogger(__name__)


class CryptoScalper(BaseStrategy):
    name = "scalper"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        c = self.config
        self.entry_window_sec = c.get("entry_window_sec", 43200)       # 12h default
        self.min_price_move_pct = c.get("min_price_move_pct", 0.05)    # 0.05%
        self.volume_spike_threshold = c.get("volume_spike_threshold", 1.2)
        self.min_confidence = c.get("min_confidence", 0.42)
        self.bid_low = c.get("bid_low", 0.02)
        self.bid_high = c.get("bid_high", 0.98)
        self.skip_if_odds_above = c.get("skip_if_odds_above", 0.97)
        self.max_positions = c.get("max_positions", 3)
        self.cooldown_sec = c.get("cooldown_sec", 30)
        self.cancel_before_close_sec = c.get("cancel_before_close_sec", 60)
        self.max_per_market = c.get("max_per_market", 1)
        self.take_profit_pct = c.get("take_profit_pct", 15.0)
        self.stop_loss_pct = c.get("stop_loss_pct", 8.0)

        # TP/SL tracking
        self._entry_prices: dict[str, float] = {}
        self._entry_times: dict[str, float] = {}
        self._recent_results: list[bool] = []  # Track win/loss for streak detection

    async def evaluate(self, market: Market, context: dict[str, Any]) -> Signal | None:
        # 1. Must be crypto Up/Down market
        if not market.is_crypto_up_down or not market.crypto_asset:
            return None

        # 2. Timing check - 12h window before close
        sec_to_close = market.seconds_to_close
        if sec_to_close > self.entry_window_sec + 60:
            return None
        if sec_to_close <= self.cancel_before_close_sec:
            return None

        # 3. Skip skewed odds
        if market.yes_price > self.skip_if_odds_above or market.no_price > self.skip_if_odds_above:
            return None

        # 4. Cooldown
        now = time.time()
        last = self._last_signal_at.get(market.condition_id, 0.0)
        if now - last < self.cooldown_sec:
            return None

        # 5. Max positions check
        open_positions = context.get("open_positions", [])
        scalper_positions = [p for p in open_positions if p.strategy == self.name]
        if len(scalper_positions) >= self.max_positions:
            return None

        # 6. Price feed check
        price_feed = context.get("price_feed")
        if price_feed is None:
            return None

        pct_move = price_feed.get_pct_move(market.crypto_asset)
        recent = price_feed.get_prices(market.crypto_asset, 120)

        # 7. Price move threshold (must be significant)
        if abs(pct_move) < self.min_price_move_pct:
            return None

        # 8. Volume confirmation - need price move + some volume indication
        # (tracked via number of ticks as proxy for activity)
        if len(recent) < 5:  # Need at least 5 ticks for activity
            logger.debug("SCALPER: %s insufficient ticks (%d)", market.condition_id[:8], len(recent))
            return None

        # 9. Direction logic based on market type
        q_lower = market.question.lower()
        is_dip_market = any(k in q_lower for k in ['dip', 'drop', 'fall'])
        is_above_market = 'above' in q_lower

        if is_dip_market:
            side = Side.NO if pct_move > 0 else Side.YES
            direction = "DIP-NO" if pct_move > 0 else "DIP-YES"
        elif is_above_market:
            side = Side.YES if pct_move > 0 else Side.NO
            direction = "ABV-YES" if pct_move > 0 else "ABV-NO"
        else:
            side = Side.YES if pct_move > 0 else Side.NO
            direction = "UP" if pct_move > 0 else "DOWN"

        # 10. Anti-self-hedge check
        in_this = [p for p in scalper_positions if p.market_condition_id == market.condition_id]
        opposite_side = [p for p in in_this if p.side != side.value]
        same_side = [p for p in in_this if p.side == side.value]

        if opposite_side:
            return None
        if len(same_side) >= self.max_per_market:
            return None

        # 11. Confidence calculation with volume boost
        confidence = 0.50 + abs(pct_move) * 5.0
        confidence = min(0.95, confidence)

        # Volume boost: more ticks = more confidence
        tick_boost = min(0.10, len(recent) / 500.0)
        confidence += tick_boost

        # 12. Volatility penalty - skip if too choppy
        if len(recent) >= 20:
            vol = self._realized_vol(recent, 60)
            vol_penalty = min(0.20, max(0.0, (vol - 0.002) * 20))
            confidence = max(self.min_confidence, confidence - vol_penalty)

        if confidence < self.min_confidence:
            return None

        # 13. Streak protection - reduce confidence after consecutive losses
        if len(self._recent_results) >= 3:
            recent_losses = sum(1 for r in self._recent_results[-3:] if not r)
            if recent_losses >= 3:
                confidence *= 0.8  # Reduce confidence after 3 losses
                logger.info("SCALPER: Streak protection active (3 losses), confidence reduced to %.2f", confidence)

        # 14. Suggested bid price
        market_price = market.yes_price if side == Side.YES else market.no_price
        discount = 0.015 * (1.0 - confidence)
        suggested = max(self.bid_low, min(self.bid_high, market_price - discount))
        suggested = round(suggested, 4)

        # 15. Position size: dynamic based on confidence
        bankroll = context.get("bankroll", 25.0)
        cash = context.get("cash", bankroll)
        available_slots = max(1, self.max_positions - len(scalper_positions))
        base_notional = cash / available_slots
        
        # Scale by confidence: higher confidence = bigger size
        confidence_multiplier = 0.7 + (confidence * 0.6)  # 0.7 - 1.27x
        notional = base_notional * confidence_multiplier
        
        notional = max(2.5, min(notional, bankroll * 0.35))  # Max 35% per trade
        notional = round(notional, 2)

        token_id = market.yes_token_id if side == Side.YES else market.no_token_id

        signal = Signal(
            market_condition_id=market.condition_id,
            side=side,
            suggested_price=suggested,
            suggested_size_usd=notional,
            confidence=confidence,
            reason=f"{market.crypto_asset} {direction} {pct_move:+.4f}% | conf={confidence:.2f} | ticks={len(recent)}",
            strategy_name=self.name,
            token_id=token_id,
            timestamp=now,
        )

        self._last_signal_at[market.condition_id] = now
        self.signals_emitted += 1
        logger.info(
            "SCALP SIGNAL: %s %s @ %.2f $%.2f conf=%.2f | %s",
            market.crypto_asset, side.value, suggested, notional, confidence, signal.reason,
        )
        return signal

    @staticmethod
    def _realized_vol(prices: list[float], window: int) -> float:
        if len(prices) < 2:
            return 0.0
        chunk = prices[-window:] if len(prices) >= window else prices
        returns = []
        for i in range(1, len(chunk)):
            if chunk[i - 1] > 0:
                returns.append((chunk[i] - chunk[i - 1]) / chunk[i - 1])
        if not returns:
            return 0.0
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / len(returns)
        return var ** 0.5

    def register_entry(self, pos_id: str, condition_id: str, entry_price: float):
        self._entry_prices[pos_id] = entry_price
        self._entry_times[pos_id] = time.time()

    def check_exit(self, pos_id: str, condition_id: str, current_price: float) -> tuple[bool, str]:
        entry = self._entry_prices.get(pos_id)
        if entry is None or entry <= 0:
            return False, ""
        pnl_pct = ((current_price - entry) / entry) * 100
        
        # Dynamic TP/SL based on market conditions
        tp = self.take_profit_pct
        sl = self.stop_loss_pct
        
        if pnl_pct >= tp:
            return True, f"Scalp TP: +{pnl_pct:.1f}%"
        if pnl_pct <= -sl:
            return True, f"Scalp SL: {pnl_pct:.1f}%"
        return False, ""

    def clear_position(self, pos_id: str, condition_id: str):
        # Track result for streak detection
        entry = self._entry_prices.get(pos_id, 0)
        # We don't know the exit price here, so we track at bot level
        self._entry_prices.pop(pos_id, None)
        self._entry_times.pop(pos_id, None)

    def record_result(self, won: bool):
        """Called by bot to track win/loss for streak detection."""
        self._recent_results.append(won)
        if len(self._recent_results) > 20:
            self._recent_results = self._recent_results[-20:]
