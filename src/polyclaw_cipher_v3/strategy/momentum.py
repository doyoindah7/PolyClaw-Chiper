"""Momentum strategy — refined v2 universal, uses CLOB WS for 60x faster reaction.

v3.2.0 FIXES:
- Added market category filter: skip random-outcome markets (sports_match, entertainment)
- Raised min_entry_price 0.05 -> 0.30 (skip low-probability entries that often lose)
- This fixes the sports market bug that caused -99.6% loss on "Will Spain win?" NO @ 0.2556
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
        self.min_entry_price = c.get("min_entry_price", 0.30)  # FIX: raised from 0.05
        self.max_entry_price = c.get("max_entry_price", 0.95)
        self.max_notional_pct = c.get("max_notional_pct", 0.15)
        self.min_confidence = c.get("min_confidence", 0.40)
        self.max_volatility = c.get("max_volatility", 0.08)
        # v3.5.12: Max % bankroll in single market
        self.max_per_market_pct = c.get("max_per_market_pct", 0.30)
        # v3.5.16: Volume spike detector config
        self.vol_spike_enabled = c.get("vol_spike_enabled", False)  # Nova-only
        self.vol_spike_threshold = c.get("vol_spike_threshold", 3.0)  # 3x normal = spike
        self.vol_spike_boost = c.get("vol_spike_boost", 0.15)  # lower momentum requirement by 15%
        self.vol_spike_confidence_boost = c.get("vol_spike_confidence_boost", 0.10)  # +10% confidence
        # FIX v3.4.0 (ARCH-1): Category filter — skip random-outcome markets
        self.skip_random_outcome = c.get("skip_random_outcome", True)
        # Updated fallback default from stale "sports_derivative"
        # to match v3.3's category split (sports_total = O/U, sports_spread = excluded)
        self.allowed_categories = c.get("allowed_categories", ["crypto", "sports_total", "economics", "other"])
        self._clob = clob_feed
        self._entry_prices: dict[str, float] = {}
        self._entry_times: dict[str, float] = {}
        self._entry_invested: dict[str, float] = {}  # v3.5.11: fee-aware time exit
        # v3.5.16 debug counters
        self._dbg_no_clob = 0
        self._dbg_random_outcome = 0
        self._dbg_cat_filtered = 0
        self._dbg_vol_filtered = 0
        self._dbg_price_filtered = 0
        self._dbg_cooldown = 0
        self._dbg_max_pos = 0
        self._dbg_one_per_mkt = 0
        self._dbg_no_change = 0
        self._dbg_low_conf = 0
        self._dbg_no_notional = 0
        self._dbg_evaluated = 0
        self._dbg_signal = 0
        self._dbg_vol_spike = 0  # v3.5.16: volume spike boosted signals
        # v3.5.17: Auto-tune v2 per-category params
        self._auto_tune = None  # set via set_auto_tune()

    def set_auto_tune(self, auto_tune) -> None:
        """Set auto-tune v2 engine for per-category parameter lookup."""
        self._auto_tune = auto_tune

    def set_clob_feed(self, clob_feed) -> None:
        self._clob = clob_feed

    async def evaluate(self, market: Market, context: dict[str, Any]) -> Signal | None:
        if not self._clob:
            self._dbg_no_clob += 1
            return None

        # FIX: Category filter — skip random-outcome markets
        # Sports match winner, entertainment = no momentum edge
        if self.skip_random_outcome and market.is_random_outcome:
            self._dbg_random_outcome += 1
            return None
        cat = market.classify()
        # v3.5.17: Auto-tune v2 per-category params
        at_params = None
        if self._auto_tune is not None:
            at_params = self._auto_tune.get_params(cat)
            if not at_params.enabled:
                self._dbg_cat_filtered += 1
                return None
        # Category filter (still apply config-level filter if auto-tune disabled)
        if self._auto_tune is None:
            if self.allowed_categories and cat not in self.allowed_categories:
                self._dbg_cat_filtered += 1
                return None

        # Filters
        if market.volume_24h < 100:  # v3.5.6: 500->100
            self._dbg_vol_filtered += 1
            return None
        # v3.5.16 FIX: Use CLOB mid price for entry range check (market.yes_price is stale from Gamma)
        clob_mid = self._clob.get_price(market.yes_token_id) if self._clob else 0.0
        check_price = clob_mid if clob_mid > 0 else market.yes_price
        # v3.5.17: Use auto-tune per-category entry range if available
        if at_params is not None:
            min_entry, max_entry = at_params.entry_range
        else:
            min_entry, max_entry = self.min_entry_price, self.max_entry_price
        if check_price < min_entry or check_price > max_entry:
            self._dbg_price_filtered += 1
            return None

        # Cooldown
        now = time.time()
        last = self._last_signal_at.get(market.condition_id, 0.0)
        if now - last < self.cooldown_sec:
            self._dbg_cooldown += 1
            return None

        # Max positions
        open_positions = context.get("open_positions", [])
        my_positions = [p for p in open_positions if p.strategy == self.name]
        # v3.5.17: Use auto-tune per-category max_positions if available
        effective_max_pos = at_params.max_positions if at_params is not None else self.max_positions
        if len(my_positions) >= effective_max_pos:
            self._dbg_max_pos += 1
            return None

        # v3.5.12: Per-market concentration limit — prevent >30% bankroll in one market
        # Check total invested across ALL open positions (not just this strategy)
        total_invested_in_market = sum(
            p.invested for p in open_positions
            if getattr(p, 'market_condition_id', None) == market.condition_id
        )
        # Estimate: if we open this trade, what % of bankroll goes to this market?
        # Use conservative estimate: assume notional ~= bankroll * strategy_cap_pct / max(1, free_slots)
        bankroll = context.get("bankroll", 25.0)
        estimated_notional = bankroll * 0.10  # rough: ~10% per trade on average
        if bankroll > 0 and (total_invested_in_market + estimated_notional) > bankroll * self.max_per_market_pct:
            self._dbg_one_per_mkt += 1
            return None

        # One per market
        if any(p.market_condition_id == market.condition_id for p in my_positions):
            self._dbg_one_per_mkt += 1
            return None

        self._dbg_evaluated += 1
        # CLOB data
        yes_change_short = self._clob.get_pct_change(market.yes_token_id, self.lookback_short_sec)
        no_change_short = self._clob.get_pct_change(market.no_token_id, self.lookback_short_sec)
        yes_change_long = self._clob.get_pct_change(market.yes_token_id, self.lookback_long_sec)
        no_change_long = self._clob.get_pct_change(market.no_token_id, self.lookback_long_sec)

        yes_price_clob = self._clob.get_price(market.yes_token_id)
        no_price_clob = self._clob.get_price(market.no_token_id)
        if yes_price_clob <= 0 and no_price_clob <= 0:
            return None

        # v3.5.16: Volume spike detection
        vol_spike = 0.0
        if self.vol_spike_enabled:
            vol_spike = self._clob.get_volume_spike(market.yes_token_id, 60.0, 300.0)
        has_vol_spike = vol_spike >= self.vol_spike_threshold

        # Multi-timeframe analysis
        max_change_short = max(abs(yes_change_short), abs(no_change_short))
        max_change_long = max(abs(yes_change_long), abs(no_change_long))

        # v3.5.16: Lower momentum threshold if volume spike detected
        # v3.5.17: Use auto-tune per-category thresholds if available
        if at_params is not None:
            base_short = at_params.momentum_short_pct
            base_long = at_params.momentum_long_pct
        else:
            base_short = self.min_momentum_short_pct
            base_long = self.min_momentum_long_pct
        effective_min_short = base_short
        effective_min_long = base_long
        if has_vol_spike:
            effective_min_short = base_short * (1.0 - self.vol_spike_boost)
            effective_min_long = base_long * (1.0 - self.vol_spike_boost)

        if max_change_short < effective_min_short:
            self._dbg_no_change += 1
            return None
        if max_change_long < effective_min_long:
            self._dbg_no_change += 1
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

        # v3.5.16: Volume spike confidence boost
        if has_vol_spike:
            confidence = min(0.98, confidence + self.vol_spike_confidence_boost)
            self._dbg_vol_spike += 1
            logger.info(
                "MOMENTUM + VOL SPIKE: %s | spike=%.1fx change=%+.2f%% conf=%.2f | %s",
                market.condition_id[:8], vol_spike, change, confidence, market.question[:50],
            )

        if confidence < self.min_confidence:
            self._dbg_low_conf += 1
            return None

        # Volatility check
        vol = self._clob.get_volatility(token_id, 120.0)
        if vol > self.max_volatility:
            return None
        elif vol > 0.04:
            confidence *= 0.9

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
                total_open_positions=context.get("total_open_positions", 0),
                max_total_positions=context.get("max_total_positions", 10),
            )
        else:
            available_slots = max(1, self.max_positions - len(my_positions))
            notional = (cash / available_slots) * (0.6 + confidence * 0.8)
            notional = min(notional, bankroll * self.max_notional_pct)
            notional = max(2.5, min(notional, cash * 0.90))

        if notional < 1.0:
            self._dbg_no_notional += 1
            return None

        self._dbg_signal += 1
        direction = "UP" if change > 0 else "DOWN"
        self._last_signal_at[market.condition_id] = now
        self.signals_emitted += 1

        logger.info(
            "MOMENTUM SIGNAL: %s %s | short=%+.2f%% long=%+.2f%% | cat=%s | conf=%.2f | $%.2f | %s",
            side.value, direction, max_change_short, max_change_long,
            cat, confidence, notional, market.question[:50],
        )

        return Signal(
            market_condition_id=market.condition_id,
            side=side,
            suggested_price=entry_price,
            suggested_size_usd=notional,
            confidence=confidence,
            reason=f"Momentum: {direction} short={change:+.2f}% long={yes_change_long:+.2f}% vol={vol:.3f} cat={cat}",
            strategy_name=self.name,
            token_id=token_id,
            timestamp=now,
        )

    def get_debug_stats(self) -> dict:
        return {
            "evaluated": self._dbg_evaluated,
            "signals": self._dbg_signal,
            "no_clob": self._dbg_no_clob,
            "random_outcome": self._dbg_random_outcome,
            "cat_filtered": self._dbg_cat_filtered,
            "vol_filtered": self._dbg_vol_filtered,
            "price_filtered": self._dbg_price_filtered,
            "cooldown": self._dbg_cooldown,
            "max_pos": self._dbg_max_pos,
            "one_per_mkt": self._dbg_one_per_mkt,
            "no_change": self._dbg_no_change,
            "low_conf": self._dbg_low_conf,
            "no_notional": self._dbg_no_notional,
            "vol_spike_boosted": self._dbg_vol_spike,
        }

    def register_entry(self, pos_id: str, condition_id: str, entry_price: float, invested: float = 0.0) -> None:
        self._entry_prices[pos_id] = entry_price
        self._entry_times[pos_id] = time.time()
        # v3.5.11: Track invested for fee-aware time exit threshold
        self._entry_invested[pos_id] = invested

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
            # v3.5.11 FIX: Fee-aware time exit — only exit if profit >= $0.10 (covers gas fee)
            # Live trading cost: ~$0.006 gas + ~$0.025 slippage = $0.031 per round trip
            # Profit < $0.10 will be eaten by fees → LOSS in live trading
            # If profit < $0.10 AND not loss, extend hold time (let TP/SL decide later)
            invested = self._entry_invested.get(pos_id, 0.0)
            pnl_dollar = (current_price - entry) / entry * invested if invested > 0 else 0.0
            if pnl_dollar < 0:
                # Loss — exit to cut losses (SL-like behavior)
                return True, f"Momentum time exit: {pnl_pct:.1f}%"
            if pnl_dollar >= 0.10:
                # Profit covers fee — exit OK
                return True, f"Momentum time exit: {pnl_pct:.1f}%"
            # Profit < $0.10 — extend hold, let TP/SL trigger naturally
            # This avoids exit-with-loss-after-fees scenario
            return False, ""
        return False, ""

    def clear_position(self, pos_id: str, condition_id: str) -> None:
        self._entry_prices.pop(pos_id, None)
        self._entry_times.pop(pos_id, None)
        self._entry_invested.pop(pos_id, None)  # v3.5.11
