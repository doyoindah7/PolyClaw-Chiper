"""Wallet — JSON-based state persistence (lightweight, crash-recoverable)."""
from __future__ import annotations
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Wallet:
    """Persistent trading state via JSON file.

    Structure:
    {
        "bankroll": 25.00,
        "cash": 25.00,
        "initial_bankroll": 25.00,
        "open_positions": [...],
        "closed_trades": [...],
        "stats": {...},
        "heartbeat": <epoch>,
        "last_scan": <epoch>,
    }
    """

    def __init__(self, path: str = "data/wallet.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state: dict[str, Any] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self._state = json.loads(self.path.read_text())
                logger.info("Wallet loaded: bankroll=$%.2f, trades=%d",
                            self._state.get("bankroll", 0),
                            len(self._state.get("closed_trades", [])))
            except Exception as e:
                logger.error("Wallet load failed: %s — starting fresh", e)
                self._state = {}
        if not self._state:
            self._init_fresh()

    def _init_fresh(self):
        initial = float(os.environ.get("INITIAL_BANKROLL_USD", "25.00"))
        self._state = {
            "bankroll": initial,
            "cash": initial,
            "initial_bankroll": initial,
            "open_positions": [],
            "closed_trades": [],
            "stats": {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
                "signals_emitted": 0,
                "arbs_found": 0,
            },
            "heartbeat": time.time(),
            "last_scan": 0.0,
        }
        self._save()

    def _save(self):
        self._state["heartbeat"] = time.time()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2, default=str))
        tmp.replace(self.path)

    # --- Properties ---
    @property
    def bankroll(self) -> float:
        return self._state.get("bankroll", 0.0)

    @property
    def cash(self) -> float:
        return self._state.get("cash", 0.0)

    @property
    def open_positions(self) -> list[dict]:
        return self._state.get("open_positions", [])

    @property
    def closed_trades(self) -> list[dict]:
        return self._state.get("closed_trades", [])

    @property
    def stats(self) -> dict:
        return self._state.get("stats", {})

    @property
    def initial_bankroll(self) -> float:
        return self._state.get("initial_bankroll", 25.0)

    # --- Mutations ---
    def open_position(self, pos: dict):
        self._state["open_positions"].append(pos)
        self._state["cash"] -= pos.get("invested", 0)
        self._state["bankroll"] = self._calc_bankroll()
        self._save()

    def close_position(self, pos_id: str, trade: dict):
        positions = self._state["open_positions"]
        idx = next((i for i, p in enumerate(positions) if p.get("id") == pos_id), None)
        if idx is not None:
            pos = positions.pop(idx)
            self._state["cash"] += trade.get("pnl_dollar", 0) + pos.get("invested", 0)
            self._state["closed_trades"].append(trade)
            # Keep last 500 trades
            if len(self._state["closed_trades"]) > 500:
                self._state["closed_trades"] = self._state["closed_trades"][-500:]

            # Update stats
            s = self._state["stats"]
            s["total_trades"] += 1
            pnl = trade.get("pnl_dollar", 0)
            s["total_pnl"] = round(s.get("total_pnl", 0) + pnl, 4)
            if pnl > 0:
                s["wins"] += 1
            elif pnl < 0:
                s["losses"] += 1
            s["best_trade"] = max(s.get("best_trade", 0), pnl)
            s["worst_trade"] = min(s.get("worst_trade", 0), pnl)

            self._state["bankroll"] = self._calc_bankroll()
            self._save()

    def update_stats(self, key: str, value: int = 1):
        s = self._state["stats"]
        s[key] = s.get(key, 0) + value
        self._save()

    def update_heartbeat(self):
        self._state["heartbeat"] = time.time()
        self._save()

    def set_last_scan(self, ts: float):
        self._state["last_scan"] = ts
        self._save()

    def _calc_bankroll(self) -> float:
        cash = self._state.get("cash", 0.0)
        invested = sum(p.get("invested", 0) for p in self._state.get("open_positions", []))
        return round(cash + invested, 4)

    def snapshot(self) -> dict[str, Any]:
        return {
            "bankroll": self.bankroll,
            "cash": self.cash,
            "pnl": round(self.bankroll - self.initial_bankroll, 4),
            "trades": len(self.closed_trades),
            "wins": self.stats.get("wins", 0),
            "losses": self.stats.get("losses", 0),
            "win_rate": (
                self.stats.get("wins", 0) / max(1, self.stats.get("total_trades", 1)) * 100
            ),
            "open_positions": self.open_positions,
            "recent_trades": self.closed_trades[-10:],
            "signals": self.stats.get("signals_emitted", 0),
            "arbs": self.stats.get("arbs_found", 0),
        }
