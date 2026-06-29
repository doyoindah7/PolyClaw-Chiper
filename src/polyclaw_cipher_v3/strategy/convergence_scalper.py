"""Late-Window Convergence Scalping ("Bone Reaper") — v3.5.16 Nova.

Theory (from Gemini insight):
- Near market close (<30min), winning side is 97%+ certain.
- Retail panic-sells losing tokens → winning side briefly dips to ~95%.
- Buy the discounted winning tokens, hold till resolution.
- Profit: 2-5% per trade, fast turnover, near-zero risk.

Mechanics:
1. Scan markets with end_date < 30 min away AND winning side > 97%
2. Wait for CLOB best_ask on winning side to dip below 95¢
3. Buy with max position sizing, hold till market resolves
4. Max hold = time_until_resolution (auto-exit when market closes)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from ..core.types import Market, Side, Signal
from .base import BaseStrategy

logger = logging.getLogger(__name__)

UTC = timezone.utc


class ConvergenceScalper(BaseStrategy):
    name = "convergence_scalper"

    def __init__(self, config: dict | None = None, clob_feed=None):
        super().__init__(config)
        c = self.config
        self.max_minutes_to_close = c.get("max_minutes_to_close", 30)
        self.min_winning_price = c.get("min_winning_price", 0.97)
        self.max_entry_price = c.get("max_entry_price", 0.955)  # Buy when dips below this
        self.take_profit_pct = c.get("take_profit_pct", 5.0)  # 5% TP
        self.stop_loss_pct = c.get("stop_loss_pct", 3.0)  # 3% SL (tight — shouldn't happen)
        self.max_position_pct = c.get("max_position_pct", 0.25)
        self.max_concurrent = c.get("max_concurrent", 3)
        self.cooldown_sec = c.get("cooldown_sec", 10)
        self.min_volume_24h = c.get("min_volume_24h", 500)
        self.allowed_categories = c.get("allowed_categories",
            ["crypto", "sports_match", "sports_total", "economics", "politics", "entertainment", "other"])
        # v3.5.16: min profit threshold
        self.min_profit_bps = c.get("min_profit_bps", 50)  # 0.5% minimum
        self._clob = clob_feed
        self._last_signal_at: dict[str, float] = {}

    def set_clob_feed(self, clob_feed) -> None:
        self._clob = clob_feed

    async def evaluate(self, market: Market, context: dict) -> Signal | None:
        if not self._clob:
            return None

        # Only trade active markets
        if market.is_closed:
            return None

        # Must have an end_date
        if not market.end_date:
            return None

        # Time check: within N minutes of close
        now = time.time()
        end_ts = market.end_date.timestamp() if isinstance(market.end_date, datetime) else float(market.end_date)
        minutes_left = (end_ts - now) / 60.0
        if minutes_left <= 0 or minutes_left > self.max_minutes_to_close:
            return None

        # Volume check
        if market.volume_24h < self.min_volume_24h:
            return None

        # Category filter
        if self.allowed_categories and market.classify() not in self.allowed_categories:
            return None

        # Cooldown
        last = self._last_signal_at.get(market.condition_id, 0.0)
        if now - last < self.cooldown_sec:
            return None

        # Max concurrent
        open_positions = context.get("open_positions", [])
        my_positions = [p for p in open_positions if p.strategy == self.name]
        if len(my_positions) >= self.max_concurrent:
            return None
        if any(p.market_condition_id == market.condition_id for p in my_positions):
            return None

        # Determine winning side: which token is > 97%?
        yes_bid = self._clob.get_best_bid(market.yes_token_id)
        no_bid = self._clob.get_best_bid(market.no_token_id)
        if yes_bid <= 0:
            yes_bid = market.yes_price
        if no_bid <= 0:
            no_bid = market.no_price

        winning_side = None
        winning_token = None
        if yes_bid >= self.min_winning_price:
            winning_side = Side.YES
            winning_token = market.yes_token_id
        elif no_bid >= self.min_winning_price:
            winning_side = Side.NO
            winning_token = market.no_token_id
        else:
            return None  # No side is dominant enough

        # Check if winning side's ask dipped below entry threshold
        winning_ask = self._clob.get_best_ask(winning_token)
        if winning_ask <= 0:
            # Fallback to market price
            winning_ask = market.yes_price if winning_side == Side.YES else market.no_price

        if winning_ask > self.max_entry_price:
            return None  # Not dipped enough yet

        # Profit check: if we buy at winning_ask, profit = $1 - winning_ask
        profit_bps = int((1.0 - winning_ask) * 10000)
        if profit_bps < self.min_profit_bps:
            return None

        # Position sizing
        bankroll = context.get("bankroll", 25.0)
        cash = context.get("cash", bankroll)
        sizer = context.get("sizer")
        if sizer:
            notional = sizer.size(
                bankroll=bankroll,
                cash=cash,
                open_positions_for_strategy=len(my_positions),
                max_positions_for_strategy=self.max_concurrent,
                confidence=0.90,
                strategy_max_pct=self.max_position_pct,
                total_open_positions=context.get("total_open_positions", 0),
                max_total_positions=context.get("max_total_positions", 10),
            )
        else:
            notional = min(cash * 0.85, bankroll * self.max_position_pct)
            notional = max(1.0, notional)

        if notional < 1.0:
            return None

        confidence = min(0.98, 0.85 + profit_bps / 150.0)
        self._last_signal_at[market.condition_id] = now
        self.signals_emitted += 1

        side_label = "YES" if winning_side == Side.YES else "NO"
        logger.info(
            "CONVERGENCE SCALP: %s | %s @ %.3f (%.0fmin left) profit=%dbps size=$%.2f | %s",
            market.condition_id[:8], side_label, winning_ask, minutes_left,
            profit_bps, notional, market.question[:50],
        )

        return Signal(
            market_condition_id=market.condition_id,
            side=winning_side,
            suggested_price=winning_ask,
            suggested_size_usd=notional,
            confidence=confidence,
            reason=f"Convergence: {side_label} dip to ${winning_ask:.3f}, {minutes_left:.0f}min left, {profit_bps}bps",
            strategy_name=self.name,
            token_id=winning_token,
            timestamp=now,
        )
