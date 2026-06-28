"""Tier-based dynamic sizer with hysteresis, cooldown, and grandfather protection."""
import logging, time
from typing import Any

logger = logging.getLogger(__name__)

TIER_CONFIGS = {
    1: {"label": "Aggressive Growth", "min_position_usd": 3.0, "max_pct_per_trade": 0.20, "max_open_positions": 10,
        "tp_pct": 3.0, "sl_pct": 2.0, "description": "$25-$350"},
    2: {"label": "Moderate Growth", "min_position_usd": 12.0, "max_pct_per_trade": 0.17, "max_open_positions": 8,
        "tp_pct": 5.0, "sl_pct": 3.0, "description": "$350-$1,100"},
    3: {"label": "Capital Preservation", "min_position_usd": 30.0, "max_pct_per_trade": 0.06, "max_open_positions": 6,
        "tp_pct": 6.0, "sl_pct": 4.0, "description": "$1,100-$5,500"},
    4: {"label": "Stable Income", "min_position_usd": 100.0, "max_pct_per_trade": 0.03, "max_open_positions": 5,
        "tp_pct": 8.0, "sl_pct": 5.0, "description": "$5,500+"},
}

TIER_BOUNDARIES = {
    1: {"enter_tier2": 350},
    2: {"exit_tier1": 225, "enter_tier3": 1100},
    3: {"exit_tier2": 900, "enter_tier4": 5500},
    4: {"exit_tier3": 4500},
}


class TierManager:
    def __init__(self, force_tier: int = 0, cooldown_hours: float = 24.0):
        self.current_tier: int = 1
        self.force_tier: int = force_tier
        self.last_transition: float = 0.0
        self.cooldown_sec: float = cooldown_hours * 3600
        self.transition_count: int = 0

    def get_tier(self, bankroll: float) -> int:
        if self.force_tier > 0:
            return min(self.force_tier, 4)
        now = time.time()
        if now - self.last_transition < self.cooldown_sec:
            return self.current_tier
        tier = self.current_tier
        if tier == 1 and bankroll >= TIER_BOUNDARIES[1]["enter_tier2"]:
            tier = 2
        elif tier == 2:
            if bankroll <= TIER_BOUNDARIES[2]["exit_tier1"]:
                tier = 1
            elif bankroll >= TIER_BOUNDARIES[2]["enter_tier3"]:
                tier = 3
        elif tier == 3:
            if bankroll <= TIER_BOUNDARIES[3]["exit_tier2"]:
                tier = 2
            elif bankroll >= TIER_BOUNDARIES[3]["enter_tier4"]:
                tier = 4
        elif tier == 4:
            if bankroll <= TIER_BOUNDARIES[4]["exit_tier3"]:
                tier = 3
        if tier != self.current_tier:
            old_label = TIER_CONFIGS[self.current_tier]["label"]
            new_cfg = TIER_CONFIGS[tier]
            self.current_tier = tier
            self.last_transition = now
            self.transition_count += 1
            logger.info("TIER TRANSITION #%d: %s -> %s (bankroll=$%.2f, min_pos=$%.0f, max_pct=%.0f%%)",
                self.transition_count, old_label, new_cfg["label"], bankroll,
                new_cfg["min_position_usd"], new_cfg["max_pct_per_trade"]*100)
        return self.current_tier

    def get_config(self, bankroll: float) -> dict[str, Any]:
        return TIER_CONFIGS[self.get_tier(bankroll)]

    def stats(self) -> dict[str, Any]:
        cfg = TIER_CONFIGS[self.current_tier]
        ago = (time.time() - self.last_transition) / 60 if self.last_transition else -1
        return {
            "current_tier": self.current_tier, "tier_label": cfg["label"],
            "tier_range": cfg["description"], "min_position_usd": cfg["min_position_usd"],
            "max_pct_per_trade": cfg["max_pct_per_trade"],
            "max_open_positions": cfg["max_open_positions"],
            "force_tier": self.force_tier, "transition_count": self.transition_count,
            "last_transition_ago_min": ago,
        }
