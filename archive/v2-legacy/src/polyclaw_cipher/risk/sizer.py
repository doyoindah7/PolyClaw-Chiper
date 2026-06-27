"""Compounding position sizer — 100% reinvest, capital velocity maximized."""
from __future__ import annotations
from typing import Any


class CompoundingSizer:
    """Position sizing for aggressive compounding.

    Unlike Kelly (which under-deploys), this sizer:
    - Divides available CASH (not bankroll) equally among open slots
    - Always deploys 100% (cash_min_pct=0)
    - Reinvests all profits immediately
    - NEVER goes negative — hard floor at $0
    """

    def __init__(self, config: dict[str, Any] | None = None):
        c = config or {}
        self.cash_min_pct = c.get("cash_min_pct", 0)
        self.max_pct_per_trade = c.get("max_pct_per_trade", 0.40)
        self.reinvest_pct = c.get("reinvest_pct", 100)
        self.min_position_usd = c.get("min_position_usd", 3.0)

    def size(
        self,
        bankroll: float,
        open_positions: int,
        max_positions: int,
        confidence: float,
        cash: float | None = None,
    ) -> float:
        """Calculate position size in USD.

        Args:
            bankroll: Total equity (cash + invested)
            open_positions: Current open position count
            max_positions: Max concurrent positions
            confidence: Signal confidence 0-1
            cash: Available cash. If None, falls back to bankroll.
        """
        # Use cash if provided, otherwise bankroll
        available_cash = cash if cash is not None else bankroll
        available_cash = max(0.0, available_cash)  # HARD FLOOR: never negative

        # Apply cash minimum reserve
        reserve = bankroll * (self.cash_min_pct / 100.0)
        deployable = max(0.0, available_cash - reserve)

        free_slots = max(1, max_positions - open_positions)
        notional = deployable / free_slots

        # Cap at max_pct of bankroll
        notional = min(notional, bankroll * self.max_pct_per_trade)

        # Hard floor: don't return tiny positions
        if notional < self.min_position_usd:
            return 0.0

        return notional
