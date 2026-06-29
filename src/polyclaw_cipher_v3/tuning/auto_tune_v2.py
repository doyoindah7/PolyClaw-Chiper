"""Auto-Tune v2 — Dynamic market-aware config engine.

Architecture:
- Startup: read master_history.db → build per-category param table → apply
- Periodic (1h): re-evaluate if ≥10 new trades → clamp changes ≤20% → apply
- Safety: rollback if recent WR < 40%, max change 20% per param per update

Per-category params computed from 3,567+ trades:
- entry_range: [min, max] price per category
- momentum_threshold: short_pct, long_pct per category
- hold_time: max_hold_sec per category
- tp_sl: take_profit_pct, stop_loss_pct per category
- side_preference: YES/NO bias per category
- category_blacklist: disable categories with WR < threshold (min 50 trades)
- max_positions: per category concurrency cap

Config toggles allow per-feature disable without killing whole system.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("polyclaw-cipher-v3.tuning")

DAY = 86400.0


@dataclass
class CategoryParams:
    """Parameters for a single market category."""
    enabled: bool = True
    trades: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    entry_range: tuple[float, float] = (0.10, 0.95)
    momentum_short_pct: float = 0.5
    momentum_long_pct: float = 0.25
    hold_time_sec: int = 300
    tp_pct: float = 8.0
    sl_pct: float = 4.0
    side_preference: str = "none"  # "YES", "NO", or "none"
    max_positions: int = 6

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_range"] = list(self.entry_range)
        return d


# Default fallback params (used when category not in table)
DEFAULT_PARAMS = CategoryParams()


class AutoTuneV2:
    """Dynamic market-aware auto-tune engine.

    Usage:
        at = AutoTuneV2(config, db_path, instance_label)
        at.run_startup()           # at bot start
        at.run_periodic()          # every 1 hour
        params = at.get_params(category)  # per-market lookup
    """

    def __init__(self, config: dict, db_path: str | Path, instance_label: str = "bot"):
        self.config = config
        self.db_path = Path(db_path)
        self.instance_label = instance_label

        at_cfg = config.get("auto_tune", {})
        self.enabled = at_cfg.get("enabled", True)
        self.mode = at_cfg.get("mode", "hybrid")
        self.update_interval = at_cfg.get("update_interval_hours", 1) * 3600
        self.min_new_trades = at_cfg.get("min_new_trades", 10)
        self.max_change_pct = at_cfg.get("max_change_pct", 20) / 100.0
        self.rollback_wr = at_cfg.get("rollback_wr", 40) / 100.0
        self.features = at_cfg.get("features", {})

        # State
        self.category_params: dict[str, CategoryParams] = {}
        self.last_update_time: float = 0.0
        self.last_trade_count: int = 0
        self._applied_changes: list[str] = []

    # ── Public API ───────────────────────────────────────────────────────

    def run_startup(self) -> None:
        """Build base model from full master DB history."""
        if not self.enabled:
            logger.info("Auto-tune v2: disabled via config")
            return

        if not self.db_path.exists():
            logger.info("Auto-tune v2: master DB not found — first run, skipping")
            return

        trades = self._load_trades()
        if len(trades) < 20:
            logger.info("Auto-tune v2: only %d trades — need 20+ for tuning", len(trades))
            return

        self.category_params = self._analyze_per_category(trades)
        self.last_trade_count = len(trades)
        self.last_update_time = time.time()

        # Log summary
        enabled_cats = [k for k, v in self.category_params.items() if v.enabled]
        disabled_cats = [k for k, v in self.category_params.items() if not v.enabled]
        logger.info("Auto-tune v2: base model from %d trades — %d categories enabled, %d blacklisted",
                     len(trades), len(enabled_cats), len(disabled_cats))
        for cat, params in sorted(self.category_params.items(), key=lambda x: -x[1].trades):
            status = "✅" if params.enabled else "❌"
            logger.info("  %s %-18s %4d trades  WR=%5.1f%%  entry=[%.2f-%.2f]  hold=%ds  TP=%.1f%% SL=%.1f%%  side=%s",
                        status, cat, params.trades, params.win_rate * 100,
                        params.entry_range[0], params.entry_range[1],
                        params.hold_time_sec, params.tp_pct, params.sl_pct,
                        params.side_preference)

    def run_periodic(self) -> None:
        """Re-evaluate with recent data. Only if ≥ min_new_trades new trades."""
        if not self.enabled or self.mode != "hybrid":
            return

        if time.time() - self.last_update_time < self.update_interval:
            return

        if not self.db_path.exists():
            return

        trades = self._load_trades()
        new_count = len(trades) - self.last_trade_count

        if new_count < self.min_new_trades:
            logger.debug("Auto-tune v2: only %d new trades (need %d) — skipping update",
                         new_count, self.min_new_trades)
            return

        # Check recent WR (last 24h)
        now = time.time()
        recent = [t for t in trades if (t.get("closed_at", 0) or 0) > now - DAY]
        if len(recent) >= 10:
            recent_wins = sum(1 for t in recent if (t.get("pnl_dollar", 0) or 0) > 0)
            recent_wr = recent_wins / len(recent)
            if recent_wr < self.rollback_wr:
                logger.warning("Auto-tune v2: recent WR=%.1f%% < %.0f%% threshold — skipping update (rollback)",
                               recent_wr * 100, self.rollback_wr * 100)
                self.last_update_time = time.time()
                return

        # Compute new params
        new_params = self._analyze_per_category(trades)

        # Clamp changes to max_change_pct
        new_params = self._clamp_changes(self.category_params, new_params)

        old_count = len(self.category_params)
        self.category_params = new_params
        self.last_trade_count = len(trades)
        self.last_update_time = time.time()

        logger.info("Auto-tune v2: periodic update — %d trades analyzed (%d new), %d categories",
                     len(trades), new_count, len(new_params))

    def get_params(self, category: str) -> CategoryParams:
        """Get parameters for a specific market category."""
        return self.category_params.get(category, DEFAULT_PARAMS)

    def get_summary(self) -> dict:
        """Return summary for health endpoint."""
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "categories": len(self.category_params),
            "enabled_categories": [k for k, v in self.category_params.items() if v.enabled],
            "blacklisted": [k for k, v in self.category_params.items() if not v.enabled],
            "last_update": self.last_update_time,
            "trades_analyzed": self.last_trade_count,
        }

    # ── Data Loading ─────────────────────────────────────────────────────

    def _load_trades(self) -> list[dict]:
        """Load trades from master_history.db with decay weights."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM trades ORDER BY closed_at").fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("Auto-tune v2: failed to load trades: %s", e)
            return []

    # ── Per-Category Analysis ────────────────────────────────────────────

    def _analyze_per_category(self, trades: list[dict]) -> dict[str, CategoryParams]:
        """Analyze trades per category and build param table."""
        now = time.time()
        result: dict[str, CategoryParams] = {}

        # Group trades by category
        by_cat: dict[str, list[dict]] = defaultdict(list)
        for t in trades:
            cat = t.get("market_category", "other") or "other"
            by_cat[cat].append(t)

        for cat, cat_trades in by_cat.items():
            if len(cat_trades) < 5:
                # Too few trades — use defaults, keep enabled
                result[cat] = CategoryParams(
                    enabled=True,
                    trades=len(cat_trades),
                    win_rate=self._wr(cat_trades),
                    avg_pnl=self._avg_pnl(cat_trades),
                )
                continue

            params = CategoryParams()
            params.trades = len(cat_trades)
            params.win_rate = self._wr(cat_trades)
            params.avg_pnl = self._avg_pnl(cat_trades)

            # Category blacklist: disable if WR < 40% with 50+ trades
            if self.features.get("category_blacklist", True):
                if len(cat_trades) >= 50 and params.win_rate < 0.40:
                    params.enabled = False
                    logger.info("Auto-tune v2: blacklisting '%s' (WR=%.1f%%, %d trades)",
                                cat, params.win_rate * 100, len(cat_trades))

            # Entry range: use profitable trade price distribution
            if self.features.get("entry_range", True):
                params.entry_range = self._compute_entry_range(cat_trades)

            # Momentum threshold: based on signal strength vs outcome
            if self.features.get("momentum_threshold", True):
                params.momentum_short_pct, params.momentum_long_pct = self._compute_momentum_threshold(cat_trades)

            # Hold time: based on winning trade hold distribution
            if self.features.get("hold_time", True):
                params.hold_time_sec = self._compute_hold_time(cat_trades)

            # TP/SL: median win/loss percentages
            if self.features.get("tp_sl", True):
                params.tp_pct, params.sl_pct = self._compute_tp_sl(cat_trades)

            # Side preference: YES vs NO win rate
            if self.features.get("side_bias", True):
                params.side_preference = self._compute_side_bias(cat_trades)

            # Max positions: scale by category volume
            if self.features.get("max_positions", True):
                params.max_positions = self._compute_max_positions(cat_trades)

            result[cat] = params

        return result

    # ── Parameter Computation ────────────────────────────────────────────

    def _wr(self, trades: list[dict]) -> float:
        wins = sum(1 for t in trades if (t.get("pnl_dollar", 0) or 0) > 0)
        return wins / len(trades) if trades else 0.0

    def _avg_pnl(self, trades: list[dict]) -> float:
        pnls = [t.get("pnl_dollar", 0) or 0 for t in trades]
        return sum(pnls) / len(pnls) if pnls else 0.0

    def _compute_entry_range(self, trades: list[dict]) -> tuple[float, float]:
        """Compute entry price range from profitable trades.

        Use 10th and 90th percentile of winning trade entry prices
        — wider than top-3 buckets, captures most profitable range.
        """
        winners = [t for t in trades if (t.get("pnl_dollar", 0) or 0) > 0]
        if len(winners) < 10:
            # Fallback: use all trades
            winners = trades
        prices = sorted([t.get("entry_price", 0.5) or 0.5 for t in winners])
        n = len(prices)
        lo = prices[int(n * 0.10)]  # 10th percentile
        hi = prices[int(n * 0.90)]  # 90th percentile
        # Sanity: widen if too narrow
        if hi - lo < 0.20:
            lo = max(0.05, lo - 0.10)
            hi = min(0.99, hi + 0.10)
        return (round(lo, 2), round(hi, 2))

    def _compute_momentum_threshold(self, trades: list[dict]) -> tuple[float, float]:
        """Compute momentum thresholds based on trade outcomes.

        If winners have higher momentum signals, lower threshold to catch more.
        If losers have high momentum, raise threshold to filter.
        """
        # Default thresholds
        short_pct = 0.5
        long_pct = 0.25

        # Check if we have momentum data in trades
        # (master DB may not have momentum_pct fields)
        # For now, use WR-based heuristic:
        wr = self._wr(trades)
        if wr > 0.70:
            # High WR category — can afford lower threshold (more signals)
            short_pct = 0.3
            long_pct = 0.15
        elif wr < 0.50:
            # Low WR category — raise threshold (fewer, safer signals)
            short_pct = 0.7
            long_pct = 0.35

        return (short_pct, long_pct)

    def _compute_hold_time(self, trades: list[dict]) -> int:
        """Compute optimal hold time from winning trades."""
        winners = [t for t in trades if (t.get("pnl_dollar", 0) or 0) > 0]
        if not winners:
            return 300  # default 5 min

        holds = []
        for t in winners:
            opened = t.get("opened_at", 0) or 0
            closed = t.get("closed_at", 0) or 0
            if closed > opened:
                holds.append(closed - opened)

        if not holds:
            return 300

        holds.sort()
        median_hold = holds[len(holds) // 2]

        # Cap at reasonable bounds
        return max(30, min(600, int(median_hold)))

    def _compute_tp_sl(self, trades: list[dict]) -> tuple[float, float]:
        """Compute TP/SL from median win/loss percentages."""
        win_pcts = []
        loss_pcts = []
        for t in trades:
            invested = t.get("invested", 0) or 0
            pnl = t.get("pnl_dollar", 0) or 0
            if invested > 0:
                pct = abs(pnl / invested * 100)
                if pnl > 0:
                    win_pcts.append(pct)
                elif pnl < 0:
                    loss_pcts.append(pct)

        win_pcts.sort()
        loss_pcts.sort()

        median_win = win_pcts[len(win_pcts) // 2] if win_pcts else 8.0
        median_loss = loss_pcts[len(loss_pcts) // 2] if loss_pcts else 4.0

        # TP = 90% of median win (take profit slightly before median)
        # SL = 120% of median loss (give a bit more room)
        tp = max(3.0, min(15.0, round(median_win * 0.9, 1)))
        sl = max(2.0, min(10.0, round(median_loss * 1.2, 1)))

        return (tp, sl)

    def _compute_side_bias(self, trades: list[dict]) -> str:
        """Compute YES/NO preference from trade outcomes."""
        yes_wins = sum(1 for t in trades if t.get("side") == "YES" and (t.get("pnl_dollar", 0) or 0) > 0)
        yes_total = sum(1 for t in trades if t.get("side") == "YES")
        no_wins = sum(1 for t in trades if t.get("side") == "NO" and (t.get("pnl_dollar", 0) or 0) > 0)
        no_total = sum(1 for t in trades if t.get("side") == "NO")

        yes_wr = yes_wins / yes_total if yes_total > 0 else 0
        no_wr = no_wins / no_total if no_total > 0 else 0

        # Only bias if difference is significant (>5%) and enough data
        if yes_total < 10 and no_total < 10:
            return "none"
        if yes_wr > no_wr + 0.05:
            return "YES"
        if no_wr > yes_wr + 0.05:
            return "NO"
        return "none"

    def _compute_max_positions(self, trades: list[dict]) -> int:
        """Compute max concurrent positions based on category volume."""
        # More trades in category → allow more concurrent positions
        n = len(trades)
        if n > 500:
            return 8
        elif n > 100:
            return 6
        elif n > 20:
            return 4
        else:
            return 3

    # ── Safety: Clamp Changes ────────────────────────────────────────────

    def _clamp_changes(self, old: dict[str, CategoryParams],
                       new: dict[str, CategoryParams]) -> dict[str, CategoryParams]:
        """Clamp parameter changes to max_change_pct per update."""
        clamped: dict[str, CategoryParams] = {}

        for cat, new_params in new.items():
            old_params = old.get(cat)

            if old_params is None:
                # New category — allow as-is
                clamped[cat] = new_params
                continue

            clamped_params = CategoryParams(
                enabled=new_params.enabled,  # blacklist changes always allowed
                trades=new_params.trades,
                win_rate=new_params.win_rate,
                avg_pnl=new_params.avg_pnl,
            )

            # Clamp entry range
            old_lo, old_hi = old_params.entry_range
            new_lo, new_hi = new_params.entry_range
            lo = self._clamp_value(old_lo, new_lo)
            hi = self._clamp_value(old_hi, new_hi)
            clamped_params.entry_range = (lo, hi)

            # Clamp other params
            clamped_params.momentum_short_pct = self._clamp_value(
                old_params.momentum_short_pct, new_params.momentum_short_pct)
            clamped_params.momentum_long_pct = self._clamp_value(
                old_params.momentum_long_pct, new_params.momentum_long_pct)
            clamped_params.hold_time_sec = int(self._clamp_value(
                old_params.hold_time_sec, new_params.hold_time_sec))
            clamped_params.tp_pct = self._clamp_value(old_params.tp_pct, new_params.tp_pct)
            clamped_params.sl_pct = self._clamp_value(old_params.sl_pct, new_params.sl_pct)
            clamped_params.side_preference = new_params.side_preference  # categorical, no clamp
            clamped_params.max_positions = new_params.max_positions

            clamped[cat] = clamped_params

        return clamped

    def _clamp_value(self, old: float, new: float) -> float:
        """Clamp new value within max_change_pct of old."""
        if old == 0:
            return new
        max_delta = abs(old) * self.max_change_pct
        return max(old - max_delta, min(old + max_delta, new))
