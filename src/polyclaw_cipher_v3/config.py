"""Configuration loader — YAML + env vars with deep merge."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


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
    """Load config: default.yaml + {mode}.yaml + env overrides."""
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

    return config
