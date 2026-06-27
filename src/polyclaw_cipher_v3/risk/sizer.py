"""Aggressive compounding position sizer — v3.3.0 with dynamic cash buffer.

v3.3.0 changes (based on Claude + Lisa + Grok consensus):
- Dynamic cash buffer: auto-increase reserve if deployed > 70%
- Per-strategy cap (strategy_max_pct) is now PRIMARY source of truth
- Global max_pct_per_trade is now safety CEILING only (not effective cap)
- Fixes 3-layer config conflict: strategies.*.max_position_pct (dead) +
  risk.per_strategy.*.max_capital_pct (primary) + risk.sizer.max_pct_per_trade (ceiling)
"""
from __future__ import annotations

from typing import Any


class CompoundingSizer:
    """Position sizing for aggressive compounding with dynamic cash buffer.

    - Per-strategy cap (strategy_max_pct) = PRIMARY source of truth
    - Global max_pct_per_trade = safety ceiling only (catch typos)
    - Dynamic cash buffer: if deployed > threshold, force higher reserve
    """

    def __init__(self, config: dict[str, Any] | None = None):
        c = config or {}
        self.cash_min_pct = c.get("cash_min_pct", 15)  # v3.3.0: 10→15
        self.max_pct_per_trade = c.get("max_pct_per_trade", 0.65)  # v3.3.0: ceiling only
        self.min_position_usd = c.get("min_position_usd", 2.0)
        # Confidence scaling: low conf = 0.6x, high conf = 1.3x
        self.confidence_min_mult = c.get("confidence_min_mult", 0.6)
        self.confidence_max_mult = c.get("confidence_max_mult", 1.3)
        # v3.3.0: Dynamic cash buffer
        self.dynamic_cash_buffer = c.get("dynamic_cash_buffer", True)
        self.high_deploy_threshold = c.get("high_deploy_threshold", 0.70)
        self.high_deploy_reserve = c.get("high_deploy_reserve", 0.25)

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
            strategy_max_pct: Per-strategy max capital % (PRIMARY source of truth)
        """
        # v3.3.0: Dynamic cash buffer — auto-increase reserve if over-deployed
        # v3.3.1 fix: Deadlock prevention — if cash < reserve (over-deployed),
        # don't block entirely. Allow emergency trading with reduced size.
        effective_cash_min_pct = self.cash_min_pct
        if self.dynamic_cash_buffer and bankroll > 0:
            deployed_pct = (bankroll - cash) / bankroll
            if deployed_pct > self.high_deploy_threshold:
                effective_cash_min_pct = self.high_deploy_reserve * 100  # Force 25%

        # Cash reserve (global, potentially dynamic)
        reserve = bankroll * (effective_cash_min_pct / 100.0)
        deployable = max(0.0, cash - reserve)

        # v3.3.1 fix: Emergency mode — if deployable = 0 but cash > min_position_usd,
        # allow reduced trading (50% of available cash) to prevent deadlock.
        # Bot stays active, can generate TP/SL exits to free cash naturally.
        if deployable < self.min_position_usd and cash > self.min_position_usd:
            deployable = cash * 0.5  # Emergency: use 50% of available cash
            # Note: confidence scaling will further reduce this

        free_slots = max(1, max_positions_for_strategy - open_positions_for_strategy)
        base_notional = deployable / free_slots

        # Confidence scaling
        conf_mult = self.confidence_min_mult + confidence * (
            self.confidence_max_mult - self.confidence_min_mult
        )
        notional = base_notional * conf_mult

        # v3.3.0: Caps — per-strategy is PRIMARY, global is ceiling only
        # Order matters: per-strategy cap applied FIRST (primary source of truth),
        # then global ceiling as safety net (catch typos like 5.0 = 500%)
        notional = min(notional, bankroll * strategy_max_pct)  # PRIMARY cap
        notional = min(notional, bankroll * self.max_pct_per_trade)  # Safety ceiling
        notional = min(notional, cash * 0.95)  # Always leave 5% buffer

        # Hard floor
        if notional < self.min_position_usd:
            return 0.0

        return round(notional, 2)
