"""Latency arbitrage — Binance price move → Polymarket odds lag.

v3.5.4 FIX: Use API price (market.yes_price/no_price) as fallback when CLOB not available
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from ..core.types import Market, Side, Signal
from .base import BaseStrategy

logger = logging.getLogger(__name__)

THRESHOLD_PATTERN = re.compile(
    r"(?:above|over|at|reach|cross)\s+\$?([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
THRESHOLD_PATTERN_ALT = re.compile(
    r"(?:BTC|ETH|SOL|Bitcoin|Ethereum|Solana)\s*>\s*\$?([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
UP_DOWN_PATTERN = re.compile(
    r"(up|down|higher|lower)\s*(?:or|than|vs\.?|versus)?\s*(up|down|higher|lower)?",
    re.IGNORECASE,
)


class LatencyArbStrategy(BaseStrategy):
    name = "latency_arb"

    def __init__(self, config: dict[str, Any] | None = None, binance_feed=None, clob_feed=None):
        super().__init__(config)
        c = self.config
        self.min_edge_pct = c.get("min_edge_pct", 0.15)
        self.max_position_pct = c.get("max_position_pct", 0.25)
        self.max_positions = c.get("max_positions", 3)
        self.take_profit_pct = c.get("take_profit_pct", 5.0)
        self.stop_loss_pct = c.get("stop_loss_pct", 3.0)
        self.exit_before_close_sec = c.get("exit_before_close_sec", 30)
        self.cooldown_sec = c.get("cooldown_sec", 10)
        self.updown_momentum_sec = c.get("updown_momentum_sec", 60)
        self.updown_min_move_pct = c.get("updown_min_move_pct", 0.01)
        
        self._binance = binance_feed
        self._clob = clob_feed
        self._entry_prices: dict[str, float] = {}
        self._entry_times: dict[str, float] = {}
        
        self._eval_count = 0
        self._skip_no_crypto = 0
        self._skip_no_threshold = 0
        self._skip_no_binance = 0
        self._skip_no_api_price = 0
        self._skip_cooldown = 0
        self._skip_max_pos = 0
        self._skip_low_edge = 0
        self._skip_no_updown_momentum = 0
        self._skip_nearly_resolved = 0       # v3.5.5: market YES or NO > 0.95
        self._skip_updown_decided = 0        # v3.5.5: UPDOWN market YES or NO > 0.85
        self._updown_signals = 0
        self._threshold_signals = 0
        self._updown_evaluated = 0

    def set_feeds(self, binance_feed, clob_feed) -> None:
        self._binance = binance_feed
        self._clob = clob_feed

    def _is_up_down_market(self, question: str) -> bool:
        return bool(UP_DOWN_PATTERN.search(question))

    def _extract_threshold(self, market: Market) -> tuple[str | None, float | None]:
        if not market.crypto_asset:
            return None, None
        
        q = market.question
        
        m = THRESHOLD_PATTERN.search(q)
        if m:
            try:
                threshold = float(m.group(1).replace(",", ""))
                return market.crypto_asset, threshold
            except ValueError:
                pass
        
        m = THRESHOLD_PATTERN_ALT.search(q)
        if m:
            try:
                threshold = float(m.group(1).replace(",", ""))
                return market.crypto_asset, threshold
            except ValueError:
                pass
        
        return None, None

    def _implied_prob_above(self, current_price: float, threshold: float, asset: str, seconds_to_close: float) -> float:
        if current_price <= 0 or threshold <= 0:
            return 0.5
        vol_daily = 0.04
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

    def _implied_prob_updown(self, asset: str) -> float:
        if not self._binance:
            return 0.5
        pct_move = self._binance.get_pct_move_over_sec(asset, self.updown_momentum_sec)
        if abs(pct_move) < self.updown_min_move_pct:
            return 0.5
        if pct_move > 0:
            implied = 0.5 + min(0.25, abs(pct_move) * 2.5)
        else:
            implied = 0.5 - min(0.25, abs(pct_move) * 2.5)
        return max(0.3, min(0.7, implied))

    def _get_price(self, market: Market) -> tuple[float, float]:
        """v3.5.4: Get YES/NO prices from CLOB OR fallback to API price."""
        # Try CLOB first
        yes_price = self._clob.get_price(market.yes_token_id) if self._clob else 0
        no_price = self._clob.get_price(market.no_token_id) if self._clob else 0
        
        # Fallback to API price from scanner
        if yes_price <= 0:
            yes_price = market.yes_price
        if no_price <= 0:
            no_price = market.no_price
            
        return yes_price, no_price

    async def evaluate(self, market: Market, context: dict[str, Any]) -> Signal | None:
        self._eval_count += 1
        
        if not self._binance:
            return None

        if not market.crypto_asset:
            self._skip_no_crypto += 1
            return None

        asset = market.crypto_asset
        binance_price = self._binance.get_price(asset)
        if binance_price <= 0:
            self._skip_no_binance += 1
            return None

        # v3.5.4: Use fallback logic for price
        yes_price, no_price = self._get_price(market)

        if yes_price <= 0 and no_price <= 0:
            self._skip_no_api_price += 1
            return None

        is_updown = self._is_up_down_market(market.question)
        has_threshold = bool(THRESHOLD_PATTERN.search(market.question) or THRESHOLD_PATTERN_ALT.search(market.question))

        # v3.5.5 FIX (P0-03, P0-04): Filter nearly-resolved markets
        # When YES or NO > 0.95, market is already "decided" — edge is illusionary.
        # Real-world slippage 0.5-2% will eat any profit; worse, bot can lose 100% if
        # it bets against the near-certain side. These markets also trap cash for hours.
        max_price = max(yes_price, no_price)
        if max_price > 0.95:
            self._skip_nearly_resolved += 1
            return None

        # v3.5.5: For UPDOWN specifically, also filter when market already decided (>0.85)
        # BTC Up/Down markets at YES=0.98 mean 98% certain Up — betting NO is gambling, not arb
        if is_updown and max_price > 0.85:
            self._skip_updown_decided += 1
            return None

        now = time.time()
        last = self._last_signal_at.get(market.condition_id, 0.0)
        if now - last < self.cooldown_sec:
            self._skip_cooldown += 1
            return None

        open_positions = context.get("open_positions", [])
        my_positions = [p for p in open_positions if p.strategy == self.name]
        if len(my_positions) >= self.max_positions:
            self._skip_max_pos += 1
            return None

        sec_to_close = market.seconds_to_close
        if sec_to_close < self.exit_before_close_sec:
            return None

        implied_prob = 0.5
        edge_yes = 0.0
        edge_no = 0.0
        signal_type = "none"

        threshold_asset, threshold_price = self._extract_threshold(market)
        
        if threshold_asset and threshold_price:
            signal_type = "threshold"
            self._threshold_signals += 1
            implied_prob = self._implied_prob_above(binance_price, threshold_price, asset, sec_to_close)
            edge_yes = (implied_prob - yes_price) * 100
            edge_no = ((1.0 - implied_prob) - no_price) * 100
            
            logger.info(
                "LATENCY_ARB [THRESHOLD]: %s=$%.0f threshold=$%.0f | implied=%.1f%% YES=%.3f NO=%.3f | edge=%+.2f%% | %s",
                asset, binance_price, threshold_price, implied_prob * 100,
                yes_price, no_price, max(edge_yes, edge_no),
                market.question[:60],
            )
        elif is_updown:
            signal_type = "updown"
            self._updown_evaluated += 1
            
            pct_move = self._binance.get_pct_move_over_sec(asset, self.updown_momentum_sec)
            implied_prob = self._implied_prob_updown(asset)
            
            edge_yes = (implied_prob - yes_price) * 100
            edge_no = ((1.0 - implied_prob) - no_price) * 100
            
            logger.info(
                "LATENCY_ARB [UPDOWN]: %s=$%.0f momentum=%+.4f%% | implied=%.1f%% YES=%.3f NO=%.3f | edge=%+.2f%% | min_move=%.3f%% | %s",
                asset, binance_price, pct_move, implied_prob * 100,
                yes_price, no_price, max(edge_yes, edge_no),
                self.updown_min_move_pct, market.question[:60],
            )

            if abs(pct_move) < self.updown_min_move_pct:
                self._skip_no_updown_momentum += 1
                return None
        else:
            self._skip_no_threshold += 1
            return None

        if edge_yes >= self.min_edge_pct and edge_yes > edge_no:
            side = Side.YES
            entry_price = yes_price
            edge = edge_yes
            token_id = market.yes_token_id
        elif edge_no >= self.min_edge_pct:
            side = Side.NO
            entry_price = no_price
            edge = edge_no
            token_id = market.no_token_id
        else:
            self._skip_low_edge += 1
            return None

        confidence = min(0.95, 0.55 + edge / 10.0)
        if confidence < 0.45:
            return None

        bankroll = context.get("bankroll", 25.0)
        cash = context.get("cash", bankroll)
        sizer = context.get("sizer")
        strategy_cap_pct = context.get("strategy_cap_pct", self.max_position_pct)
        if sizer:
            notional = sizer.size(
                bankroll=bankroll, cash=cash,
                open_positions_for_strategy=len(my_positions),
                max_positions_for_strategy=self.max_positions,
                confidence=confidence, strategy_max_pct=strategy_cap_pct,
                total_open_positions=context.get("total_open_positions", 0),
                max_total_positions=context.get("max_total_positions", 10),
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
            "LATENCY ARB SIGNAL [%s]: %s %s | implied=%.2f%% edge=%+.2f%% | conf=%.2f $%.2f | %s",
            signal_type.upper(), asset, side.value,
            implied_prob * 100, edge, confidence, notional, market.question[:50],
        )

        return Signal(
            market_condition_id=market.condition_id,
            side=side,
            suggested_price=entry_price,
            suggested_size_usd=notional,
            confidence=confidence,
            reason=f"LatencyArb[{signal_type}]: {asset}=${binance_price:.0f} implied={implied_prob*100:.1f}% edge={edge:+.2f}%",
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
        return {
            "evaluated": self._eval_count,
            "skip_no_crypto": self._skip_no_crypto,
            "skip_no_threshold": self._skip_no_threshold,
            "skip_no_binance": self._skip_no_binance,
            "skip_no_api_price": self._skip_no_api_price,
            "skip_cooldown": self._skip_cooldown,
            "skip_max_pos": self._skip_max_pos,
            "skip_low_edge": self._skip_low_edge,
            "skip_no_updown_momentum": self._skip_no_updown_momentum,
            "skip_nearly_resolved": self._skip_nearly_resolved,        # v3.5.5
            "skip_updown_decided": self._skip_updown_decided,           # v3.5.5
            "updown_evaluated": self._updown_evaluated,
            "updown_signals": self._updown_signals,
            "threshold_signals": self._threshold_signals,
            "signals_emitted": self.signals_emitted,
            "config": {
                "min_edge_pct": self.min_edge_pct,
                "updown_momentum_sec": self.updown_momentum_sec,
                "updown_min_move_pct": self.updown_min_move_pct,
            }
        }
