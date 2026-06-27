"""Resolution sniping — buy near-certain markets at 0.90-0.97 discount.

v3.2.0 FIXES:
- Added market category filter: skip sports_match and entertainment (random outcome)
- Only snipe markets with deterministic resolution (crypto threshold, economics)
- Sports markets can have upset = -93% loss in one event
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..core.types import Market, Side, Signal
from .base import BaseStrategy

logger = logging.getLogger(__name__)


class ResolutionSnipeStrategy(BaseStrategy):
    name = "resolution_snipe"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        c = self.config
        self.min_odds = c.get("min_odds", 0.90)
        self.max_odds = c.get("max_odds", 0.97)
        self.max_hours_to_close = c.get("max_hours_to_close", 24)
        self.llm_enabled = c.get("llm_enabled", False)
        self.llm_min_confidence = c.get("llm_min_confidence", 0.85)
        self.max_position_pct = c.get("max_position_pct", 0.15)
        self.max_concurrent = c.get("max_concurrent", 5)
        self.cooldown_sec = c.get("cooldown_sec", 60)
        self.stop_loss_pct = c.get("stop_loss_pct", 10.0)
        self.take_profit_pct = c.get("take_profit_pct", 15.0)
        # FIX: Category filter — only snipe predictable markets
        self.skip_random_outcome = c.get("skip_random_outcome", True)
        self.allowed_categories = c.get("allowed_categories", ["crypto", "economics", "other"])
        self._llm_client = None
        self._entry_prices: dict[str, float] = {}

    def set_llm_client(self, llm_client) -> None:
        self._llm_client = llm_client
        self.llm_enabled = True
        logger.info("ResolutionSnipe: LLM client injected, LLM mode enabled")

    async def evaluate(self, market: Market, context: dict[str, Any]) -> Signal | None:
        # Skip closed markets
        if market.is_closed:
            return None

        # FIX: Category filter — skip random-outcome markets
        # Sports match winner = unpredictable, can upset and lose -93%
        if self.skip_random_outcome and market.is_random_outcome:
            return None
        cat = market.classify()
        if self.allowed_categories and cat not in self.allowed_categories:
            return None

        # Hours to close
        sec_to_close = market.seconds_to_close
        if sec_to_close <= 0:
            return None
        hours_to_close = sec_to_close / 3600.0
        if hours_to_close > self.max_hours_to_close:
            return None

        # Cooldown
        now = time.time()
        last = self._last_signal_at.get(market.condition_id, 0.0)
        if now - last < self.cooldown_sec:
            return None

        # Max concurrent
        open_positions = context.get("open_positions", [])
        my_positions = [p for p in open_positions if p.strategy == self.name]
        if len(my_positions) >= self.max_concurrent:
            return None

        # Already in this market?
        if any(p.market_condition_id == market.condition_id for p in my_positions):
            return None

        # Find side with high odds (near-certain)
        if market.yes_price >= self.min_odds and market.yes_price <= self.max_odds:
            side = Side.YES
            entry_price = market.yes_price
            token_id = market.yes_token_id
            near_certain_side = "YES"
        elif market.no_price >= self.min_odds and market.no_price <= self.max_odds:
            side = Side.NO
            entry_price = market.no_price
            token_id = market.no_token_id
            near_certain_side = "NO"
        else:
            return None

        # LLM-assisted confidence check (if enabled)
        if self.llm_enabled and self._llm_client:
            try:
                llm_result = await self._llm_client.assess_near_certainty(market, {
                    "near_certain_side": near_certain_side,
                    "hours_to_close": hours_to_close,
                })
                if llm_result.confidence < self.llm_min_confidence:
                    logger.debug(
                        "ResolutionSnipe LLM rejected %s: conf=%.2f < %.2f",
                        market.condition_id[:8], llm_result.confidence, self.llm_min_confidence,
                    )
                    return None
                confidence = llm_result.confidence
                reasoning = f"LLM: {llm_result.reasoning}"
            except Exception as e:
                logger.warning("LLM assess_near_certainty failed: %s, fallback to threshold", e)
                confidence = 0.80
                reasoning = f"Threshold fallback (LLM error)"
        else:
            # Threshold-only mode
            odds_range = self.max_odds - self.min_odds
            position_in_range = (entry_price - self.min_odds) / odds_range if odds_range > 0 else 0.5
            confidence = 0.75 + position_in_range * 0.15
            reasoning = f"Threshold: {near_certain_side}={entry_price:.3f}, {hours_to_close:.1f}h to close, cat={cat}"

        # Position size
        bankroll = context.get("bankroll", 25.0)
        cash = context.get("cash", bankroll)
        sizer = context.get("sizer")
        strategy_cap_pct = context.get("strategy_cap_pct", self.max_position_pct)
        if sizer:
            notional = sizer.size(
                bankroll=bankroll,
                cash=cash,
                open_positions_for_strategy=len(my_positions),
                max_positions_for_strategy=self.max_concurrent,
                confidence=confidence,
                strategy_max_pct=strategy_cap_pct,
            )
        else:
            notional = min(cash * 0.90, bankroll * self.max_position_pct)
            notional = max(1.0, notional)

        if notional < 1.0:
            return None

        # Expected profit if held to resolution
        expected_profit_pct = ((1.0 - entry_price) / entry_price) * 100

        self._last_signal_at[market.condition_id] = now
        self.signals_emitted += 1

        logger.info(
            "RESOLUTION SNIPE SIGNAL: %s %s @ %.3f | expected +%.1f%% | %dh | cat=%s | conf=%.2f $%.2f | %s",
            near_certain_side, side.value, entry_price, expected_profit_pct,
            int(hours_to_close), cat, confidence, notional, market.question[:50],
        )

        return Signal(
            market_condition_id=market.condition_id,
            side=side,
            suggested_price=entry_price,
            suggested_size_usd=notional,
            confidence=confidence,
            reason=f"ResolutionSnipe: {reasoning}, expected +{expected_profit_pct:.1f}%, cat={cat}",
            strategy_name=self.name,
            token_id=token_id,
            timestamp=now,
        )

    def register_entry(self, pos_id: str, condition_id: str, entry_price: float) -> None:
        self._entry_prices[pos_id] = entry_price

    def check_exit(self, pos_id: str, condition_id: str, current_price: float) -> tuple[bool, str]:
        entry = self._entry_prices.get(pos_id)
        if entry is None or entry <= 0:
            return False, ""
        pnl_pct = ((current_price - entry) / entry) * 100
        if pnl_pct <= -self.stop_loss_pct:
            return True, f"ResolutionSnipe SL: {pnl_pct:.1f}% (odds reversed)"
        if pnl_pct >= self.take_profit_pct:
            return True, f"ResolutionSnipe TP: +{pnl_pct:.1f}% (early profit take)"
        return False, ""

    def clear_position(self, pos_id: str, condition_id: str) -> None:
        self._entry_prices.pop(pos_id, None)
