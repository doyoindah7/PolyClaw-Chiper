"""Configuration loader — YAML + env vars with Pydantic validation (v3.4.2)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field, ValidationError


# === Pydantic Settings Validation Models ===

class BotSettings(BaseModel):
    mode: str = "paper"
    loop_interval_sec: int = Field(default=1, ge=1)
    scan_interval_sec: int = Field(default=60, ge=1)
    max_open_positions: int = Field(default=10, ge=1)


class MarketSettings(BaseModel):
    min_volume_24h_usd: float = Field(default=500.0, ge=0.0)
    min_liquidity: float = Field(default=100.0, ge=0.0)
    api_page_size: int = Field(default=500, ge=1)
    max_pages: int = Field(default=3, ge=1)
    track_max_markets: int = Field(default=50, ge=1)


class LatencyArbConfig(BaseModel):
    enabled: bool = True
    min_edge_pct: float = Field(default=0.5, ge=0.0)  # v3.5.2: Lowered from 2.0 to 0.5 for more opportunities
    max_position_pct: float = Field(default=0.25, ge=0.0, le=1.0)
    max_positions: int = Field(default=3, ge=1)
    take_profit_pct: float = Field(default=5.0, ge=0.0)
    stop_loss_pct: float = Field(default=3.0, ge=0.0)
    exit_before_close_sec: int = Field(default=30, ge=0)
    cooldown_sec: int = Field(default=10, ge=0)


class AtomicArbConfig(BaseModel):
    enabled: bool = True
    min_profit_bps: int = Field(default=40, ge=0)
    max_position_pct: float = Field(default=0.40, ge=0.0, le=1.0)
    max_concurrent: int = Field(default=5, ge=1)
    scan_interval_sec: int = Field(default=1, ge=1)
    skip_random_outcome: bool = True
    allowed_categories: List[str] = Field(default_factory=lambda: ["crypto", "sports_total", "economics", "politics", "other"])


class ResolutionSnipeConfig(BaseModel):
    enabled: bool = True
    min_odds: float = Field(default=0.88, ge=0.0, le=1.0)
    max_odds: float = Field(default=0.97, ge=0.0, le=1.0)
    max_hours_to_close: int = Field(default=72, ge=0)
    llm_enabled: bool = False
    llm_min_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    max_position_pct: float = Field(default=0.15, ge=0.0, le=1.0)
    max_concurrent: int = Field(default=5, ge=1)
    cooldown_sec: int = Field(default=60, ge=0)
    stop_loss_pct: float = Field(default=10.0, ge=0.0)
    take_profit_pct: float = Field(default=15.0, ge=0.0)
    skip_random_outcome: bool = True
    allowed_categories: List[str] = Field(default_factory=lambda: ["crypto", "economics", "politics", "other"])


class MomentumConfig(BaseModel):
    enabled: bool = True
    lookback_short_sec: int = Field(default=30, ge=1)
    lookback_long_sec: int = Field(default=120, ge=1)
    min_momentum_short_pct: float = Field(default=1.0, ge=0.0)
    min_momentum_long_pct: float = Field(default=0.5, ge=0.0)
    take_profit_pct: float = Field(default=8.0, ge=0.0)
    stop_loss_pct: float = Field(default=4.0, ge=0.0)
    max_hold_sec: int = Field(default=300, ge=0)
    max_position_pct: float = Field(default=0.15, ge=0.0, le=1.0)
    max_positions: int = Field(default=3, ge=1)
    cooldown_sec: int = Field(default=30, ge=0)
    min_entry_price: float = Field(default=0.30, ge=0.0, le=1.0)
    max_entry_price: float = Field(default=0.95, ge=0.0, le=1.0)
    skip_random_outcome: bool = True
    allowed_categories: List[str] = Field(default_factory=lambda: ["crypto", "sports_total", "economics", "other"])


class NewsLlmConfig(BaseModel):
    enabled: bool = False
    llm_model: str = "glm-4.5"
    max_llm_latency_sec: int = Field(default=30, ge=1)
    min_confidence: float = Field(default=0.70, ge=0.0, le=1.0)
    max_position_pct: float = Field(default=0.10, ge=0.0, le=1.0)
    max_positions: int = Field(default=2, ge=1)
    take_profit_pct: float = Field(default=15.0, ge=0.0)
    stop_loss_pct: float = Field(default=8.0, ge=0.0)
    max_hold_sec: int = Field(default=600, ge=0)


class StrategiesConfig(BaseModel):
    latency_arb: LatencyArbConfig = Field(default_factory=LatencyArbConfig)
    atomic_arb: AtomicArbConfig = Field(default_factory=AtomicArbConfig)
    resolution_snipe: ResolutionSnipeConfig = Field(default_factory=ResolutionSnipeConfig)
    momentum: MomentumConfig = Field(default_factory=MomentumConfig)
    news_llm: NewsLlmConfig = Field(default_factory=NewsLlmConfig)


class SizerConfig(BaseModel):
    initial_trade_pct: float = Field(default=0.05, ge=0.0, le=1.0)
    compound_factor: float = Field(default=1.5, ge=0.0)
    max_pct_per_trade: float = Field(default=0.65, ge=0.0, le=1.0)
    cash_buffer_pct: float = Field(default=0.05, ge=0.0, le=1.0)


class PerStrategyRisk(BaseModel):
    max_consecutive_losses: int = Field(default=5, ge=1)
    max_trades_per_hour: int = Field(default=30, ge=1)
    max_capital_pct: float = Field(default=0.25, ge=0.0, le=1.0)


class RiskConfig(BaseModel):
    initial_bankroll_usd: float = Field(default=25.0, ge=0.0)
    max_daily_drawdown_pct: float = Field(default=50.0, ge=0.0, le=100.0)
    max_consecutive_losses_global: int = Field(default=8, ge=1)
    max_trades_per_hour_global: int = Field(default=60, ge=1)
    session_rotation_min: int = Field(default=240, ge=1)
    max_net_exposure_per_asset_pct: float = Field(default=50.0, ge=0.0, le=100.0)
    sizer: SizerConfig = Field(default_factory=SizerConfig)
    per_strategy: Dict[str, PerStrategyRisk] = Field(default_factory=dict)


class WebConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8082, ge=1)
    username: str = "admin"
    password: str = "secure_polyclaw_password_123"


class MonitoringConfig(BaseModel):
    log_level: str = "INFO"
    log_format: str = "json"
    web: WebConfig = Field(default_factory=WebConfig)



class TierConfig(BaseModel):
    """v3.5.12 — Tier-based dynamic position sizing config."""
    mode: str = "auto"
    force_tier: int = Field(default=0, ge=0, le=4)
    cooldown_hours: float = Field(default=24.0, ge=0.0)

class BotConfig(BaseModel):
    bot: BotSettings = Field(default_factory=BotSettings)
    tier: TierConfig = Field(default_factory=TierConfig)
    market: MarketSettings = Field(default_factory=MarketSettings)
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    database_url: str = "sqlite+aiosqlite:///data/cipher_v3.db"
    unified_dashboard: Dict[str, Any] = Field(default_factory=dict)


# === Configuration Loading Logic ===

def _find_config_dir() -> Path:
    for p in [os.environ.get("CONFIG_DIR"), "config", "/app/config"]:
        if p and Path(p).exists():
            return Path(p)
    return Path("config")


def _deep_merge(base: dict, overlay: dict) -> dict:
    result = dict(base)
    for k, v in overlay.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config() -> dict[str, Any]:
    """Load config: default.yaml + {mode}.yaml + env overrides, validated by Pydantic."""
    config_dir = _find_config_dir()
    config: dict[str, Any] = {}

    # 1. Load default.yaml
    default_path = config_dir / "default.yaml"
    if default_path.exists():
        with open(default_path) as f:
            config = yaml.safe_load(f) or {}

    # 2. Load mode-specific overlay
    mode = os.environ.get("BOT_MODE", config.get("bot", {}).get("mode", "paper"))
    mode_path = config_dir / f"{mode}.yaml"
    if mode_path.exists():
        with open(mode_path) as f:
            overlay = yaml.safe_load(f) or {}
        config = _deep_merge(config, overlay)

    # 3. Env overrides
    if mode_env := os.environ.get("BOT_MODE"):
        config.setdefault("bot", {})["mode"] = mode_env
    if bankroll := os.environ.get("INITIAL_BANKROLL_USD"):
        config.setdefault("risk", {})["initial_bankroll_usd"] = float(bankroll)
    if http_host := os.environ.get("HTTP_HOST"):
        config.setdefault("monitoring", {}).setdefault("web", {})["host"] = http_host
    if http_port := os.environ.get("HTTP_PORT"):
        config.setdefault("monitoring", {}).setdefault("web", {})["port"] = int(http_port)
    if v2_url := os.environ.get("V2_API_URL"):
        config.setdefault("unified_dashboard", {})["v2_api_url"] = v2_url

    # 4. Strict Pydantic Validation
    try:
        validated = BotConfig(**config)
        return validated.model_dump()
    except ValidationError as e:
        import sys
        print(f"CRITICAL CONFIGURATION ERROR: Invalid config structure or parameters:\n{e}", file=sys.stderr)
        raise SystemExit(1)

