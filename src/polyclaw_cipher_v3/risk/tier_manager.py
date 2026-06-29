"""Tier-based dynamic sizer with hysteresis, cooldown, and grandfather protection.

v3.5.16: Tier configs moved to YAML — easily adjustable per instance.
  Config overrides via `tier.tiers.N` block in YAML.
"""
import logging, time
from typing import Any

logger = logging.getLogger(__name__)

# ── Default tier configs (fallback if YAML doesn't provide) ──
DEFAULT_TIERS = {
    1: {"label": "Aggressive Growth", "min_position_usd": 3.0, "max_pct_per_trade": 0.20,
        "max_open_positions": 10, "tp_pct": 3.0, "sl_pct": 2.0, "enter_next": 350,
        "description": "$25-$350"},
    2: {"label": "Moderate Growth", "min_position_usd": 12.0, "max_pct_per_trade": 0.17,
        "max_open_positions": 8, "tp_pct": 5.0, "sl_pct": 3.0,
        "enter_next": 1100, "exit_prev": 225, "description": "$350-$1,100"},
    3: {"label": "Capital Preservation", "min_position_usd": 30.0, "max_pct_per_trade": 0.06,
        "max_open_positions": 6, "tp_pct": 6.0, "sl_pct": 4.0,
        "enter_next": 5500, "exit_prev": 900, "description": "$1,100-$5,500"},
    4: {"label": "Stable Income", "min_position_usd": 100.0, "max_pct_per_trade": 0.03,
        "max_open_positions": 5, "tp_pct": 8.0, "sl_pct": 5.0,
        "exit_prev": 4500, "description": "$5,500+"},
}


def _merge_tier_configs(yaml_config: dict[str, Any] | None) -> dict[int, dict]:
    """Deep-merge YAML tier overrides into defaults."""
    if not yaml_config or "tiers" not in yaml_config:
        return dict(DEFAULT_TIERS)
    
    merged = {}
    for t in range(1, 5):
        base = dict(DEFAULT_TIERS[t])
        override = yaml_config.get("tiers", {}).get(t, {})
        base.update(override)
        merged[t] = base
    
    return merged


class TierManager:
    def __init__(self, force_tier: int = 0, cooldown_hours: float = 24.0,
                 yaml_config: dict[str, Any] | None = None):
        self.current_tier: int = 1
        self.force_tier: int = force_tier
        self.last_transition: float = 0.0
        self.cooldown_sec: float = cooldown_hours * 3600
        self.transition_count: int = 0
        # v3.5.16: Configurable tier definitions via YAML
        self._tiers = _merge_tier_configs(yaml_config)

    def get_tier(self, bankroll: float) -> int:
        if self.force_tier > 0:
            return min(self.force_tier, 4)
        now = time.time()
        # v3.5.16: Allow chain-transition on first call (no cooldown at startup)
        is_first_call = self.last_transition == 0.0
        if not is_first_call and now - self.last_transition < self.cooldown_sec:
            return self.current_tier
        tier = self.current_tier
        tiers = self._tiers
        
        # Chain upward until bankroll fits (first call can jump multiple tiers)
        while "enter_next" in tiers[tier] and bankroll >= tiers[tier]["enter_next"]:
            tier = min(tier + 1, 4)
            if not is_first_call:
                break  # only chain on first call
        # Check downward transition
        if tier == self.current_tier:
            if "exit_prev" in tiers[tier] and bankroll <= tiers[tier]["exit_prev"]:
                tier = max(tier - 1, 1)
        
        if tier != self.current_tier:
            old_label = self._tiers[self.current_tier]["label"]
            new_cfg = self._tiers[tier]
            self.current_tier = tier
            self.last_transition = now
            self.transition_count += 1
            logger.info("TIER TRANSITION #%d: %s -> %s (bankroll=$%.2f, min_pos=$%.0f, max_pct=%.0f%%)",
                self.transition_count, old_label, new_cfg["label"], bankroll,
                new_cfg["min_position_usd"], new_cfg["max_pct_per_trade"] * 100)
        return self.current_tier

    def get_config(self, bankroll: float) -> dict[str, Any]:
        return {k: v for k, v in self._tiers[self.get_tier(bankroll)].items()
                if k not in ("enter_next", "exit_prev")}

    def stats(self) -> dict[str, Any]:
        cfg = self._tiers[self.current_tier]
        ago = (time.time() - self.last_transition) / 60 if self.last_transition else -1
        return {
            "current_tier": self.current_tier, "tier_label": cfg["label"],
            "tier_range": cfg.get("description", "?"),
            "min_position_usd": cfg["min_position_usd"],
            "max_pct_per_trade": cfg["max_pct_per_trade"],
            "max_open_positions": cfg["max_open_positions"],
            "force_tier": self.force_tier, "transition_count": self.transition_count,
            "last_transition_ago_min": ago,
        }
