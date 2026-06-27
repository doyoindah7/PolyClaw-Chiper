"""Aggressive compounding position sizer — actually used (unlike v2 dead code)."""
from __future__ import annotations

from typing import Any


class CompoundingSizer:
    """Position sizing for aggressive compounding.

    - Divides available CASH equally among open slots
    - Scales by confidence (configurable multiplier)
    - Caps at max_pct_per_trade of bankroll
    - Hard floor: never negative, never below min_position_usd
    """

    def __init__(self, config: dict[str, Any] | None = None):
        c = config or {}
        self.cash_min_pct = c.get("cash_min_pct", 10)
        self.max_pct_per_trade = c.get("max_pct_per_trade", 0.25)
        self.min_position_usd = c.get("min_position_usd", 1.0)
        # Confidence scaling: low conf = 0.6x, high conf = 1.3x
        self.confidence_min_mult = c.get("confidence_min_mult", 0.6)
        self.confidence_max_mult = c.get("confidence_max_mult", 1.3)

    def size(
        self,
        bankroll: float,
        cash: float,
        open_positions_for_strategy: int,
        max_positions_for_strategy: int,
        confidence: float,
        strategy_max_pct: float,
    ) -> float:
        """Calculate position size in USD.

        Args:
            bankroll: Total equity (cash + invested)
            cash: Available cash
            open_positions_for_strategy: Current open positions for THIS strategy
            max_positions_for_strategy: Max concurrent for THIS strategy
            confidence: Signal confidence 0-1
            strategy_max_pct: Per-strategy max capital % (e.g., 0.25 for latency_arb)
        """
        # Cash reserve (global)
        reserve = bankroll * (self.cash_min_pct / 100.0)
        deployable = max(0.0, cash - reserve)

        free_slots = max(1, max_positions_for_strategy - open_positions_for_strategy)
        base_notional = deployable / free_slots

        # Confidence scaling
        conf_mult = self.confidence_min_mult + confidence * (
            self.confidence_max_mult - self.confidence_min_mult
        )
        notional = base_notional * conf_mult

        # Caps
        notional = min(notional, bankroll * self.max_pct_per_trade)
        notional = min(notional, bankroll * strategy_max_pct)
        notional = min(notional, cash * 0.95)  # Always leave 5% buffer

        # Hard floor
        if notional < self.min_position_usd:
            return 0.0

        return round(notional, 2)
