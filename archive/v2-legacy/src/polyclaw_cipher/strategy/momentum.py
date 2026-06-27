"""Momentum Hunter — catch volatile markets with rapid price changes.

Scans all active markets for ones where the odds have shifted significantly
in a short period. Enters on momentum continuation, exits on TP/SL or max hold.

Unlike scalper (which uses Binance price feed), momentum uses Polymarket
odds movement itself as the signal.
"""
from __future__ import annotations
import logging
import time
from collections import defaultdict
from typing import Any
from ..core.types import Market, Side, Signal
from .base import BaseStrategy

logger = logging.getLogger(__name__)


class MomentumHunter(BaseStrategy):
    name = "momentum"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        c = self.config
        self.min_price_change_pct = c.get("min_price_change_pct", 2.0)
        self.take_profit_pct = c.get("take_profit_pct", 8.0)
        self.stop_loss_pct = c.get("stop_loss_pct", 4.0)
        self.max_hold_min = c.get("max_hold_min", 30)
        self.max_positions = c.get("max_positions", 3)
        self.cooldown_sec = c.get("cooldown_sec", 10)
        self.min_entry_price = c.get("min_entry_price", 0.03)  # Skip penny markets
        self.max_entry_price = c.get("max_entry_price", 0.95)
        self.max_notional_pct = c.get("max_notional_pct", 0.25)  # Max 25% bankroll per trade

        # Track price history per market
        self._price_history: dict[str, list[tuple[float, float]]] = defaultdict(list)  # (ts, yes_price)
        # Track entry per POSITION (not per market) using position_id
        self._entry_prices: dict[str, float] = {}  # pos_id -> entry price (for the side we bought)
        self._entry_times: dict[str, float] = {}  # pos_id -> entry time
        # Track which condition_ids we have entries for (for quick lookup)
        self._condition_entries: dict[str, list[str]] = defaultdict(list)  # condition_id -> [pos_ids]

    def record_price(self, market: Market):
        """Called every scan cycle to build price history."""
        now = time.time()
        self._price_history[market.condition_id].append((now, market.yes_price))
        # Keep last 2 hours
        cutoff = now - 7200
        hist = self._price_history[market.condition_id]
        while hist and hist[0][0] < cutoff:
            hist.pop(0)

    async def evaluate(self, market: Market, context: dict[str, Any]) -> Signal | None:
        self.record_price(market)

        hist = self._price_history.get(market.condition_id, [])
        if len(hist) < 5:
            logger.debug("MOMENTUM: %s only %d price points, need 5", market.condition_id[:8], len(hist))
            return None

        # Cooldown
        now = time.time()
        last = self._last_signal_at.get(market.condition_id, 0.0)
        if now - last < self.cooldown_sec:
            return None

        # Max positions
        open_positions = context.get("open_positions", [])
        mom_positions = [p for p in open_positions if p.strategy == self.name]
        if len(mom_positions) >= self.max_positions:
            return None

        # Already in this market?
        if any(p.market_condition_id == market.condition_id for p in mom_positions):
            return None

        # Price filters — skip penny markets and near-certain markets
        yes_price = market.yes_price
        no_price = market.no_price
        if yes_price < self.min_entry_price or yes_price > self.max_entry_price:
            return None
        if no_price < self.min_entry_price or no_price > self.max_entry_price:
            return None

        # Calculate price change — compare now vs 60 seconds ago
        # Polymarket odds move slowly, need longer window
        now = time.time()
        cutoff = now - 60.0  # 60 seconds ago
        old_prices = [(t, p) for t, p in hist if t <= cutoff]
        if not old_prices:
            # Not enough history yet — use oldest available
            old_prices = [hist[0]]
        old_price = old_prices[-1][1]
        current = hist[-1][1]

        if old_price <= 0:
            return None

        pct_change = ((current - old_price) / old_price) * 100

        if abs(pct_change) < self.min_price_change_pct:
            return None

        # Direction: momentum continuation
        if pct_change > 0:
            side = Side.YES
            direction = "UP"
            entry_price = yes_price
        else:
            side = Side.NO
            direction = "DOWN"
            entry_price = no_price

        # Confidence based on magnitude — more aggressive
        confidence = min(0.95, 0.55 + abs(pct_change) / 10.0)

        # Position size: use CASH not bankroll, cap at max_notional_pct
        bankroll = context.get("bankroll", 25.0)
        cash = context.get("cash", bankroll)
        available_slots = max(1, self.max_positions - len(mom_positions))
        notional = cash / available_slots
        notional = min(notional, bankroll * self.max_notional_pct)
        notional = max(1.0, notional)

        # Don't trade if notional > available cash
        if notional > cash:
            notional = max(3.0, cash * 0.90)  # Leave 10% buffer, min $3

        token_id = market.yes_token_id if side == Side.YES else market.no_token_id

        self._last_signal_at[market.condition_id] = now
        self.signals_emitted += 1

        logger.info(
            "MOMENTUM SIGNAL: %s %s | odds %.3f→%.3f (%+.1f%%) | conf=%.2f | $%.2f | %s",
            market.condition_id[:8], direction, old_price, current, pct_change,
            confidence, notional, market.question[:60],
        )

        return Signal(
            market_condition_id=market.condition_id,
            side=side,
            suggested_price=entry_price,
            suggested_size_usd=notional,
            confidence=confidence,
            reason=f"Momentum: {direction} {pct_change:+.1f}% in ~1h",
            strategy_name=self.name,
            token_id=token_id,
            timestamp=now,
        )

    def register_entry(self, pos_id: str, condition_id: str, entry_price: float):
        """Called by bot when a momentum position is opened."""
        self._entry_prices[pos_id] = entry_price
        self._entry_times[pos_id] = time.time()
        self._condition_entries[condition_id].append(pos_id)

    def check_exit(self, pos_id: str, condition_id: str, current_price: float) -> tuple[bool, str]:
        """Check if a momentum position should exit.

        Args:
            pos_id: Position ID (not condition_id)
            condition_id: Market condition ID
            current_price: Current price of the SIDE we hold (yes or no)
        """
        entry = self._entry_prices.get(pos_id)
        entry_time = self._entry_times.get(pos_id)
        if entry is None or entry_time is None:
            return False, ""

        pnl_pct = ((current_price - entry) / entry) * 100 if entry > 0 else 0.0
        hold_min = (time.time() - entry_time) / 60.0

        if pnl_pct >= self.take_profit_pct:
            return True, f"Take profit: +{pnl_pct:.1f}%"
        if pnl_pct <= -self.stop_loss_pct:
            return True, f"Stop loss: {pnl_pct:.1f}%"
        if hold_min >= self.max_hold_min:
            return True, f"Max hold: {hold_min:.0f}min"

        return False, ""

    def clear_position(self, pos_id: str, condition_id: str):
        self._entry_prices.pop(pos_id, None)
        self._entry_times.pop(pos_id, None)
        if condition_id in self._condition_entries:
            try:
                self._condition_entries[condition_id].remove(pos_id)
            except ValueError:
                pass
