#!/usr/bin/env python3
"""PolyClaw-Cipher Trade Analyzer — learn from past runs, tune next run.

Runs after archive_trades.py, before fresh session reset.
Reads trade archive DB + current DB, analyzes performance,
generates config recommendations for next cycle.

Usage:
    python3 scripts/analyze_trades.py                    # analyze latest archive
    python3 scripts/analyze_trades.py --db /path/to.db   # analyze specific DB
    python3 scripts/analyze_trades.py --apply            # generate config overlay
    python3 scripts/analyze_trades.py --report           # print report only

Output:
    data/analysis/analysis_YYYYMMDD_HHMMSS.json  — full analysis
    data/analysis/recommendations.yaml           — config overlay (if --apply)
    stdout                                        — human-readable summary
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

# === Config ===
BASE_DIR = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = BASE_DIR / "data" / "trade_archive"
ANALYSIS_DIR = BASE_DIR / "data" / "analysis"
DEFAULT_DB = BASE_DIR / "data" / "cipher_v3.db"

# Current config defaults (for comparison)
CURRENT_CONFIG = {
    "min_entry_price": 0.10,
    "max_entry_price": 0.95,
    "min_momentum_short_pct": 0.5,
    "min_momentum_long_pct": 0.25,
    "take_profit_pct": 8.0,
    "stop_loss_pct": 4.0,
    "max_hold_sec": 300,
    "cooldown_sec": 30,
    "max_positions": 6,
}


def find_latest_archive():
    """Find latest archived DB."""
    if not ARCHIVE_DIR.exists():
        return None
    dbs = sorted(ARCHIVE_DIR.glob("cipher_v3_*.db"), reverse=True)
    return dbs[0] if dbs else None


def load_trades(db_path):
    """Load all trades from DB."""
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    trades = []
    try:
        rows = db.execute("SELECT * FROM trades ORDER BY closed_at").fetchall()
        for r in rows:
            trades.append({
                "id": r["id"],
                "market_question": r["market_question"] or "",
                "side": r["side"],
                "entry_price": r["entry_price"],
                "exit_price": r["exit_price"],
                "shares": r["shares"],
                "invested": r["invested"],
                "pnl_dollar": r["pnl_dollar"],
                "pnl_percent": r["pnl_percent"],
                "strategy": r["strategy"],
                "reason": r["reason"] or "",
                "opened_at": r["opened_at"],
                "closed_at": r["closed_at"],
                "is_pair": r["is_pair"],
            })
    except Exception as e:
        print(f"Error loading trades: {e}", file=sys.stderr)
    finally:
        db.close()
    return trades


def load_signals(db_path):
    """Load all signals from DB."""
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    signals = []
    try:
        rows = db.execute("SELECT * FROM signals ORDER BY timestamp").fetchall()
        for r in rows:
            signals.append({
                "id": r["id"],
                "strategy": r["strategy"],
                "side": r["side"],
                "suggested_price": r["suggested_price"],
                "suggested_size_usd": r["suggested_size_usd"],
                "confidence": r["confidence"],
                "executed": bool(r["executed"]),
                "rejected_reason": r["rejected_reason"] or "",
                "timestamp": r["timestamp"],
            })
    except Exception:
        pass
    finally:
        db.close()
    return signals


def classify_market(question):
    """Classify market by question keywords."""
    q = question.lower()
    if any(w in q for w in ["btc", "ethereum", "solana", "bitcoin", "eth ", "crypto"]):
        return "crypto"
    if any(w in q for w in ["vs ", " v ", " v. ", "—"]):
        return "sports_match"
    if any(w in q for w in ["over", "under", "total"]):
        return "sports_total"
    if any(w in q for w in ["spread", "handicap"]):
        return "sports_spread"
    if any(w in q for w in ["election", "president", "vote", "senate", "congress"]):
        return "politics"
    if any(w in q for w in ["fed", "rate", "gdp", "inflation", "cpi", "economic"]):
        return "economics"
    if any(w in q for w in ["movie", "game", "music", "award", "celebrity"]):
        return "entertainment"
    return "other"


def analyze_trades(trades, signals):
    """Full analysis of trade data."""
    if not trades:
        return {"error": "No trades to analyze"}

    report = {}
    total = len(trades)
    wins = [t for t in trades if t["pnl_dollar"] > 0]
    losses = [t for t in trades if t["pnl_dollar"] <= 0]
    total_pnl = sum(t["pnl_dollar"] for t in trades)

    # === 1. Overview ===
    report["overview"] = {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / total * 100, 1) if total > 0 else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl_per_trade": round(total_pnl / total, 2) if total > 0 else 0,
        "avg_win": round(sum(t["pnl_dollar"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(t["pnl_dollar"] for t in losses) / len(losses), 2) if losses else 0,
        "profit_factor": round(
            abs(sum(t["pnl_dollar"] for t in wins)) / max(abs(sum(t["pnl_dollar"] for t in losses)), 0.01), 2
        ) if losses else 999,
    }

    # === 2. Entry Price Analysis ===
    price_buckets = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        bucket = round(t["entry_price"] * 10) / 10  # 0.1 buckets
        b = f"{bucket:.1f}-{bucket+0.1:.1f}"
        price_buckets[b]["trades"] += 1
        if t["pnl_dollar"] > 0:
            price_buckets[b]["wins"] += 1
        price_buckets[b]["pnl"] += t["pnl_dollar"]

    best_bucket = max(price_buckets.items(), key=lambda x: x[1]["pnl"]) if price_buckets else None
    report["entry_price_analysis"] = {
        "buckets": {k: {
            "trades": v["trades"],
            "wr": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] > 0 else 0,
            "pnl": round(v["pnl"], 2),
            "pct_of_profit": round(v["pnl"] / total_pnl * 100, 1) if total_pnl != 0 else 0,
        } for k, v in sorted(price_buckets.items())},
        "best_bucket": best_bucket[0] if best_bucket else None,
        "recommendation": None,
    }
    if best_bucket:
        lo = float(best_bucket[0].split("-")[0])
        hi = float(best_bucket[0].split("-")[1])
        report["entry_price_analysis"]["recommendation"] = {
            "min_entry_price": lo,
            "max_entry_price": hi,
            "reason": f"Sweet spot {best_bucket[0]} — WR {price_buckets[best_bucket[0]]['wins']}/{price_buckets[best_bucket[0]]['trades']}, PnL ${price_buckets[best_bucket[0]]['pnl']:.2f}",
        }

    # === 3. Hold Time Analysis ===
    hold_buckets = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        hold_sec = t["closed_at"] - t["opened_at"]
        if hold_sec < 60:
            b = "0-1min"
        elif hold_sec < 180:
            b = "1-3min"
        elif hold_sec < 300:
            b = "3-5min"
        elif hold_sec < 600:
            b = "5-10min"
        elif hold_sec < 1800:
            b = "10-30min"
        else:
            b = "30min+"
        hold_buckets[b]["trades"] += 1
        if t["pnl_dollar"] > 0:
            hold_buckets[b]["wins"] += 1
        hold_buckets[b]["pnl"] += t["pnl_dollar"]

    best_hold = max(hold_buckets.items(), key=lambda x: x[1]["pnl"]) if hold_buckets else None
    report["hold_time_analysis"] = {
        "buckets": {k: {
            "trades": v["trades"],
            "wr": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] > 0 else 0,
            "pnl": round(v["pnl"], 2),
        } for k, v in hold_buckets.items()},
        "best_bucket": best_hold[0] if best_hold else None,
        "recommendation": None,
    }
    if best_hold:
        hold_map = {"0-1min": 60, "1-3min": 180, "3-5min": 300, "5-10min": 600, "10-30min": 1800, "30min+": 3600}
        report["hold_time_analysis"]["recommendation"] = {
            "max_hold_sec": hold_map.get(best_hold[0], 300),
            "reason": f"Best PnL at {best_hold[0]} — ${best_hold[1]['pnl']:.2f}",
        }

    # === 4. Category Performance ===
    cat_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        cat = classify_market(t["market_question"])
        cat_stats[cat]["trades"] += 1
        if t["pnl_dollar"] > 0:
            cat_stats[cat]["wins"] += 1
        cat_stats[cat]["pnl"] += t["pnl_dollar"]

    report["category_performance"] = {
        cat: {
            "trades": v["trades"],
            "wr": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] > 0 else 0,
            "pnl": round(v["pnl"], 2),
            "avg_pnl": round(v["pnl"] / v["trades"], 2) if v["trades"] > 0 else 0,
        }
        for cat, v in sorted(cat_stats.items(), key=lambda x: -x[1]["pnl"])
    }

    # === 5. Strategy Performance ===
    strat_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        strat = t["strategy"]
        strat_stats[strat]["trades"] += 1
        if t["pnl_dollar"] > 0:
            strat_stats[strat]["wins"] += 1
        strat_stats[strat]["pnl"] += t["pnl_dollar"]

    report["strategy_performance"] = {
        s: {
            "trades": v["trades"],
            "wr": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] > 0 else 0,
            "pnl": round(v["pnl"], 2),
        }
        for s, v in sorted(strat_stats.items(), key=lambda x: -x[1]["pnl"])
    }

    # === 6. Exit Reason Analysis ===
    reason_stats = defaultdict(lambda: {"trades": 0, "pnl": 0.0})
    for t in trades:
        reason = t["reason"][:30] if t["reason"] else "unknown"
        reason_stats[reason]["trades"] += 1
        reason_stats[reason]["pnl"] += t["pnl_dollar"]

    report["exit_reasons"] = {
        r: {
            "trades": v["trades"],
            "pnl": round(v["pnl"], 2),
            "avg_pnl": round(v["pnl"] / v["trades"], 2) if v["trades"] > 0 else 0,
        }
        for r, v in sorted(reason_stats.items(), key=lambda x: -x[1]["pnl"])
    }

    # === 7. Slippage Estimation ===
    slippage_data = []
    for t in trades:
        if t["entry_price"] > 0 and t["exit_price"] > 0:
            # Exit slippage = how much exit differs from entry (negative = favorable)
            slip = (t["exit_price"] - t["entry_price"]) / t["entry_price"] * 100
            slippage_data.append(slip)

    report["slippage_estimation"] = {
        "avg_exit_vs_entry_pct": round(sum(slippage_data) / len(slippage_data), 2) if slippage_data else 0,
        "min": round(min(slippage_data), 2) if slippage_data else 0,
        "max": round(max(slippage_data), 2) if slippage_data else 0,
        "sample_size": len(slippage_data),
    }

    # === 8. Signal Analysis ===
    if signals:
        total_signals = len(signals)
        executed = sum(1 for s in signals if s["executed"])
        rejected = total_signals - executed
        reject_reasons = Counter(s["rejected_reason"] for s in signals if not s["executed"] and s["rejected_reason"])

        report["signal_analysis"] = {
            "total_signals": total_signals,
            "executed": executed,
            "rejected": rejected,
            "execution_rate": round(executed / total_signals * 100, 1) if total_signals > 0 else 0,
            "top_reject_reasons": dict(reject_reasons.most_common(5)),
        }

    # === 9. Config Recommendations ===
    recs = {}

    # Entry price recommendation
    ep_rec = report["entry_price_analysis"].get("recommendation")
    if ep_rec:
        cur_min = CURRENT_CONFIG["min_entry_price"]
        cur_max = CURRENT_CONFIG["max_entry_price"]
        if abs(ep_rec["min_entry_price"] - cur_min) > 0.05:
            recs["min_entry_price"] = ep_rec["min_entry_price"]
        if abs(ep_rec["max_entry_price"] - cur_max) > 0.05:
            recs["max_entry_price"] = ep_rec["max_entry_price"]

    # Hold time recommendation
    ht_rec = report["hold_time_analysis"].get("recommendation")
    if ht_rec:
        cur_hold = CURRENT_CONFIG["max_hold_sec"]
        if abs(ht_rec["max_hold_sec"] - cur_hold) > 60:
            recs["max_hold_sec"] = ht_rec["max_hold_sec"]

    # TP/SL recommendation based on avg win/loss
    ov = report["overview"]
    if ov["avg_win"] > 0 and ov["avg_loss"] < 0:
        avg_win_pct = ov["avg_win"] / (sum(t["invested"] for t in wins) / len(wins)) * 100 if wins else 0
        avg_loss_pct = abs(ov["avg_loss"] / (sum(t["invested"] for t in losses) / len(losses))) * 100 if losses else 0
        if avg_win_pct > 0:
            recommended_tp = round(avg_win_pct * 0.9, 1)  # 90% of avg win
            if abs(recommended_tp - CURRENT_CONFIG["take_profit_pct"]) > 1:
                recs["take_profit_pct"] = recommended_tp
        if avg_loss_pct > 0:
            recommended_sl = round(avg_loss_pct * 1.2, 1)  # 120% of avg loss
            if abs(recommended_sl - CURRENT_CONFIG["stop_loss_pct"]) > 0.5:
                recs["stop_loss_pct"] = recommended_sl

    # Category filter recommendation
    cat_perf = report["category_performance"]
    bad_cats = [cat for cat, v in cat_perf.items() if v["trades"] >= 5 and v["wr"] < 40]
    if bad_cats:
        recs["exclude_categories"] = bad_cats

    report["recommendations"] = recs
    return report


def print_summary(report):
    """Print human-readable summary."""
    ov = report.get("overview", {})
    print("=" * 60)
    print("📊 PolyClaw-Cipher Trade Analysis")
    print("=" * 60)
    print(f"\nTrades: {ov.get('total_trades', 0)} | WR: {ov.get('win_rate', 0)}% | PnL: ${ov.get('total_pnl', 0):.2f}")
    print(f"Avg Win: ${ov.get('avg_win', 0):.2f} | Avg Loss: ${ov.get('avg_loss', 0):.2f}")
    print(f"Profit Factor: {ov.get('profit_factor', 0)}")

    print("\n--- Entry Price Buckets ---")
    for b, v in report.get("entry_price_analysis", {}).get("buckets", {}).items():
        print(f"  {b}: {v['trades']}T {v['wr']}%WR ${v['pnl']:.2f} ({v['pct_of_profit']}% of PnL)")

    print("\n--- Hold Time ---")
    for b, v in report.get("hold_time_analysis", {}).get("buckets", {}).items():
        print(f"  {b}: {v['trades']}T {v['wr']}%WR ${v['pnl']:.2f}")

    print("\n--- Category Performance ---")
    for cat, v in report.get("category_performance", {}).items():
        print(f"  {cat}: {v['trades']}T {v['wr']}%WR ${v['pnl']:.2f} (avg ${v['avg_pnl']:.2f})")

    print("\n--- Strategy Performance ---")
    for s, v in report.get("strategy_performance", {}).items():
        print(f"  {s}: {v['trades']}T {v['wr']}%WR ${v['pnl']:.2f}")

    print("\n--- Exit Reasons ---")
    for r, v in report.get("exit_reasons", {}).items():
        print(f"  {r}: {v['trades']}T ${v['pnl']:.2f} (avg ${v['avg_pnl']:.2f})")

    if "signal_analysis" in report:
        sa = report["signal_analysis"]
        print(f"\n--- Signals ---")
        print(f"  Total: {sa['total_signals']} | Executed: {sa['executed']} ({sa['execution_rate']}%)")
        if sa.get("top_reject_reasons"):
            print("  Top reject reasons:")
            for r, c in sa["top_reject_reasons"].items():
                print(f"    {r}: {c}")

    print("\n--- 🎯 Config Recommendations ---")
    recs = report.get("recommendations", {})
    if not recs:
        print("  No changes recommended — current config is optimal")
    else:
        for k, v in recs.items():
            cur = CURRENT_CONFIG.get(k, "N/A")
            print(f"  {k}: {cur} → {v}")
    print("\n" + "=" * 60)


def save_analysis(report, output_dir):
    """Save analysis to JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"analysis_{ts}.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n💾 Analysis saved: {path}")
    return path


def generate_config_overlay(report, output_dir):
    """Generate YAML config overlay from recommendations."""
    recs = report.get("recommendations", {})
    if not recs:
        print("\nNo recommendations — skipping config overlay")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "recommendations.yaml"

    lines = ["# PolyClaw-Cipher auto-generated config overlay", f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    lines.append("# === Momentum strategy recommendations ===")
    lines.append("strategies:")
    lines.append("  momentum:")
    for k, v in recs.items():
        if k in ("min_entry_price", "max_entry_price", "take_profit_pct", "stop_loss_pct",
                 "max_hold_sec", "cooldown_sec", "max_positions"):
            lines.append(f"    {k}: {v}")

    if "exclude_categories" in recs:
        lines.append(f"    # Excluded categories (WR < 40%): {recs['exclude_categories']}")

    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(f"\n⚙️ Config overlay saved: {path}")
    print("   Apply with: docker compose -f docker-compose.yml -f recommendations.yaml up --build -d")
    return path


def main():
    parser = argparse.ArgumentParser(description="PolyClaw-Cipher Trade Analyzer")
    parser.add_argument("--db", type=str, help="Path to DB file (default: latest archive or live DB)")
    parser.add_argument("--apply", action="store_true", help="Generate config overlay YAML")
    parser.add_argument("--report", action="store_true", help="Print report only (no save)")
    args = parser.parse_args()

    # Find DB to analyze
    if args.db:
        db_path = Path(args.db)
    else:
        db_path = find_latest_archive() or DEFAULT_DB

    if not Path(db_path).exists():
        print(f"❌ DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"📖 Analyzing: {db_path}")

    # Load data
    trades = load_trades(db_path)
    signals = load_signals(db_path)
    print(f"   Loaded: {len(trades)} trades, {len(signals)} signals")

    if not trades:
        print("❌ No trades found in DB", file=sys.stderr)
        sys.exit(1)

    # Analyze
    report = analyze_trades(trades, signals)

    # Output
    print_summary(report)

    if not args.report:
        save_analysis(report, ANALYSIS_DIR)

    if args.apply:
        generate_config_overlay(report, ANALYSIS_DIR)


if __name__ == "__main__":
    main()
