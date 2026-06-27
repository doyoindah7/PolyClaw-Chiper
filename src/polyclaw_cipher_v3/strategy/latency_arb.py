"""Latency arbitrage — Binance price move → Polymarket odds lag.

Edge: PM crypto Up/Down odds adjust 200-500ms AFTER Binance price move.
Bot detects Binance move, buys PM YES/NO before odds adjust.

v3.5.2 FIX (MASALAH-6):
- Enhanced threshold patterns to catch more market formats
- Added comprehensive debug logging for market evaluation tracking
- Lowered min_edge_pct to 0.5% for more opportunities
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from ..core.types import Market, Side, Signal
from .base import BaseStrategy

logger = logging.getLogger(__name__)

# Parse threshold from question: "Will Bitcoin be above $100,000 on June 27?"
# v3.5.2: Enhanced patterns to catch more crypto Up/Down market formats
THRESHOLD_PATTERN = re.compile(
    r"(?:above|over|at|reach|cross)\s+\$?([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
# Fallback: match "BTC > $X" or "ETH > $X" format
THRESHOLD_PATTERN_ALT = re.compile(
    r"(?:BTC|ETH|SOL|Bitcoin|Ethereum|Solana)\s*>\s*\$?([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)


class LatencyArbStrategy(BaseStrategy):
    name = "latency_arb"

    def __init__(self, config: dict[str, Any] | None = None, binance_feed=None, clob_feed=None):
        super().__init__(config)
        c = self.config
        # v3.5.2: Lowered from 2.0 to 0.5 for more signal opportunities
        self.min_edge_pct = c.get("min_edge_pct", 0.5)
        self.max_position_pct = c.get("max_position_pct", 0.25)
        self.max_positions = c.get("max_positions", 3)
        self.take_profit_pct = c.get("take_profit_pct", 5.0)
        self.stop_loss_pct = c.get("stop_loss_pct", 3.0)
        self.exit_before_close_sec = c.get("exit_before_close_sec", 30)
        self.cooldown_sec = c.get("cooldown_sec", 10)
        self._binance = binance_feed
        self._clob = clob_feed
        self._entry_prices: dict[str, float] = {}
        self._entry_times: dict[str, float] = {}
        # v3.5.2: Debug counters
        self._eval_count = 0
        self._skip_no_crypto = 0
        self._skip_no_threshold = 0
        self._skip_no_binance = 0
        self._skip_no_pm_price = 0
        self._skip_cooldown = 0
        self._skip_max_pos = 0
        self._skip_low_edge = 0

    def set_feeds(self, binance_feed, clob_feed) -> None:
        self._binance = binance_feed
        self._clob = clob_feed

    def _extract_threshold(self, market: Market) -> tuple[str | None, float | None]:
        """Extract (asset, threshold_price) from market question.
        v3.5.2: Enhanced with alternative patterns."""
        if not market.crypto_asset:
            return None, None
        
        # Try primary pattern: "above/over/at $X"
        m = THRESHOLD_PATTERN.search(market.question)
        if m:
            try:
                threshold = float(m.group(1).replace(",", ""))
                return market.crypto_asset, threshold
            except ValueError:
                pass
        
        # Try alternate pattern: "BTC > $X" format
        m = THRESHOLD_PATTERN_ALT.search(market.question)
        if m:
            try:
                threshold = float(m.group(1).replace(",", ""))
                return market.crypto_asset, threshold
            except ValueError:
                pass
        
        return None, None

    def _implied_prob_above(self, current_price: float, threshold: float, asset: str, seconds_to_close: float) -> float:
        """implied probability that asset will be ABOVE threshold at market close."""
        if current_price <= 0 or threshold <= 0:
            return 0.5

        vol_daily = 0.04  # default fallback (4% daily)
        if self._binance and hasattr(self._binance, "get_volatility_daily"):
            vol_daily = self._binance.get_volatility_daily(asset)

        days_to_close = max(1.0 / 1440.0, seconds_to_close / 86400.0)

        from math import sqrt, log, erf
        sigma = vol_daily * sqrt(days_to_close)

        try:
            d = log(current_price / threshold) / sigma
            prob = 0.5 * (1.0 + erf(d / sqrt(2.0)))
        except (ValueError, ZeroDivisionError):
            prob = 0.5

        return max(0.01, min(0.99, prob))

    async def evaluate(self, market: Market, context: dict[str, Any]) -> Signal | None:
        self._eval_count += 1
        
        if not self._binance or not self._clob:
            return None

        # Only crypto markets with threshold structure
        asset, threshold = self._extract_threshold(market)
        
        if not market.crypto_asset:
            self._skip_no_crypto += 1
            return None
        
        if not asset or not threshold:
            self._skip_no_threshold += 1
            # v3.5.2: Log crypto markets without threshold pattern (INFO level)
            logger.info(
                "LATENCY_ARB SKIP: crypto_asset=%s but NO threshold pattern in: %s",
                market.crypto_asset, market.question[:80],
            )
            return None

        # Get Binance price
        binance_price = self._binance.get_price(asset)
        if binance_price <= 0:
            self._skip_no_binance += 1
            logger.info(
                "LATENCY_ARB SKIP: %s Binance price=0 (asset not tracked?)",
                asset,
            )
            return None

        # Get PM current price (from CLOB WS)
        yes_price_pm = self._clob.get_price(market.yes_token_id)
        no_price_pm = self._clob.get_price(market.no_token_id)
        if yes_price_pm <= 0 and no_price_pm <= 0:
            self._skip_no_pm_price += 1
            return None
        if yes_price_pm <= 0:
            yes_price_pm = market.yes_price
        if no_price_pm <= 0:
            no_price_pm = market.no_price

        # Cooldown
        now = time.time()
        last = self._last_signal_at.get(market.condition_id, 0.0)
        if now - last < self.cooldown_sec:
            self._skip_cooldown += 1
            return None

        # Max positions
        open_positions = context.get("open_positions", [])
        my_positions = [p for p in open_positions if p.strategy == self.name]
        if len(my_positions) >= self.max_positions:
            self._skip_max_pos += 1
            return None

        # Exit before close
        sec_to_close = market.seconds_to_close
        if sec_to_close < self.exit_before_close_sec:
            return None

        # Compute implied probability from Binance
        implied_prob = self._implied_prob_above(binance_price, threshold, asset, sec_to_close)

        # Edge calculation
        edge_yes = (implied_prob - yes_price_pm) * 100  # in percentage points
        edge_no = ((1.0 - implied_prob) - no_price_pm) * 100

        # v3.5.2: Log ALL evaluated crypto markets at INFO level
        logger.info(
            "LATENCY_ARB EVAL: %s=$%.0f threshold=$%.0f | implied=%.1f%% YES=%.3f NO=%.3f | "
            "edge_yes=%+.2f%% edge_no=%+.2f%% | min_edge=%.1f%% | %s",
            asset, binance_price, threshold, implied_prob * 100,
            yes_price_pm, no_price_pm, edge_yes, edge_no,
            self.min_edge_pct, market.question[:60],
        )

        if edge_yes >= self.min_edge_pct and edge_yes > edge_no:
            side = Side.YES
            entry_price = yes_price_pm
            edge = edge_yes
            token_id = market.yes_token_id
        elif edge_no >= self.min_edge_pct:
            side = Side.NO
            entry_price = no_price_pm
            edge = edge_no
            token_id = market.no_token_id
        else:
            self._skip_low_edge += 1
            return None

        # Confidence based on edge magnitude
        confidence = min(0.95, 0.55 + edge / 10.0)
        if confidence < 0.45:
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
                open_positions_for_strategy=len(my_positions),
                max_positions_for_strategy=self.max_positions,
                confidence=confidence,
                strategy_max_pct=strategy_cap_pct,
            )
        else:
            available_slots = max(1, self.max_positions - len(my_positions))
            notional = min(cash / available_slots, bankroll * self.max_position_pct)
            notional = max(2.5, min(notional, cash * 0.90))

        if notional < 1.0:
            return None

        self._last_signal_at[market.condition_id] = now
        self.signals_emitted += 1

        logger.info(
            "LATENCY ARB SIGNAL: %s %s | %s=$%s threshold=$%s | implied=%.2f%% edge=%+.2f%% | conf=%.2f $%.2f | %s",
            asset, side.value, asset, f"{binance_price:.0f}", f"{threshold:.0f}",
            implied_prob * 100, edge, confidence, notional, market.question[:50],
        )

        return Signal(
            market_condition_id=market.condition_id,
            side=side,
            suggested_price=entry_price,
            suggested_size_usd=notional,
            confidence=confidence,
            reason=f"LatencyArb: {asset}=${binance_price:.0f} threshold=${threshold:.0f} implied={implied_prob*100:.1f}% edge={edge:+.2f}%",
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
            return True, f"LatencyArb TP: +{pnl_pct:.1f}%"
        if pnl_pct <= -self.stop_loss_pct:
            return True, f"LatencyArb SL: {pnl_pct:.1f}%"
        return False, ""

    def clear_position(self, pos_id: str, condition_id: str) -> None:
        self._entry_prices.pop(pos_id, None)
        self._entry_times.pop(pos_id, None)

    def get_debug_stats(self) -> dict:
        """v3.5.2: Return debug stats for dashboard/API."""
        return {
            "evaluated": self._eval_count,
            "skip_no_crypto": self._skip_no_crypto,
            "skip_no_threshold": self._skip_no_threshold,
            "skip_no_binance": self._skip_no_binance,
            "skip_no_pm_price": self._skip_no_pm_price,
            "skip_cooldown": self._skip_cooldown,
            "skip_max_pos": self._skip_max_pos,
            "skip_low_edge": self._skip_low_edge,
            "signals_emitted": self.signals_emitted,
        }
