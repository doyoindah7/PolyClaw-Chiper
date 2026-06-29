#!/usr/bin/env python3
"""Merge all trade archives into master_history.db with instance tagging.

v3.5.16: Unified master database for cross-run auto-tune.
Reads all cipher_v3_*.db from trade_archive/, tags by instance/run/source,
and merges into a single master_history.db.

Usage: python scripts/merge_archives.py [--source EC2|Ireland] [--instance Cipher|Fifteen|Scalper]
"""
import sqlite3, os, sys, glob, re
from datetime import datetime, timezone

ARCHIVE_DIR = os.environ.get('DATA_DIR', 'data') + '/trade_archive'
MASTER_DB = os.environ.get('DATA_DIR', 'data') + '/master_history.db'

# Market category classifier
def classify_market(question: str) -> str:
    q = (question or "").lower()
    pairs = [
        ("crypto", ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "xrp", "doge"]),
        ("sports_match", [" vs ", " vs. ", " versus ", " - "]),
        ("sports_total", ["o/u", "over/under", "over under", "total points", "total runs",
                         "total goals", "total touchdowns", "total score"]),
        ("sports_spread", ["spread", "handicap", "-1.5", "+1.5", "-2.5", "+2.5"]),
        ("politics", ["president", "election", "congress", "senate", "vote", "poll",
                     "democrat", "republican", "trump", "biden", "approval", "cabinet"]),
        ("economics", ["gdp", "inflation", "cpi", "fed", "interest rate", "unemployment",
                      "treasury", "stock", "s&p", "nasdaq", "dow", "tariff"]),
    ]
    for cat, keywords in pairs:
        if any(kw in q for kw in keywords):
            return cat
    return "other"


def create_master_db():
    os.makedirs(os.path.dirname(MASTER_DB), exist_ok=True)
    db = sqlite3.connect(MASTER_DB)
    
    # Trades table with metadata columns
    db.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT,
            market_condition_id TEXT,
            market_question TEXT,
            side TEXT,
            entry_price REAL,
            exit_price REAL,
            shares REAL,
            invested REAL,
            pnl_dollar REAL,
            pnl_percent REAL,
            strategy TEXT,
            reason TEXT,
            opened_at REAL,
            closed_at REAL,
            is_pair INTEGER,
            pair_id TEXT,
            -- v3.5.16 metadata
            instance TEXT,
            run_id TEXT,
            source_vps TEXT,
            market_category TEXT
        )
    """)
    
    # Archive registry
    db.execute("""
        CREATE TABLE IF NOT EXISTS archive_registry (
            run_id TEXT PRIMARY KEY,
            instance TEXT,
            source_vps TEXT,
            archived_at TEXT,
            trade_count INTEGER,
            signal_count INTEGER,
            total_pnl REAL,
            win_rate REAL
        )
    """)
    
    # Auto-tune config history
    db.execute("""
        CREATE TABLE IF NOT EXISTS auto_tune_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            applied_at TEXT,
            instance TEXT,
            trades_analyzed INTEGER,
            win_rate REAL,
            total_pnl REAL,
            tp_pct REAL,
            sl_pct REAL,
            min_entry REAL,
            max_entry REAL,
            hold_sec INTEGER,
            changes TEXT
        )
    """)
    
    # Signals table
    db.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id TEXT,
            market_condition_id TEXT,
            strategy TEXT,
            side TEXT,
            suggested_price REAL,
            suggested_size_usd REAL,
            confidence REAL,
            reason TEXT,
            timestamp REAL,
            executed INTEGER,
            rejected_reason TEXT,
            instance TEXT,
            run_id TEXT,
            source_vps TEXT
        )
    """)
    
    db.commit()
    return db


def merge_archive(db_path: str, instance: str, run_id: str, source_vps: str, master: sqlite3.Connection):
    """Merge one archive DB into master."""
    if not os.path.exists(db_path):
        print(f"  SKIP: {db_path} not found")
        return 0, 0
    
    src = sqlite3.connect(db_path)
    
    # Merge trades
    try:
        rows = src.execute('SELECT * FROM trades').fetchall()
    except:
        print(f"  SKIP: no trades table in {db_path}")
        src.close()
        return 0, 0
    
    cols = [c[1] for c in src.execute('PRAGMA table_info(trades)').fetchall()]
    
    trade_count = 0
    for row in rows:
        d = {cols[i]: row[i] for i in range(len(cols))}
        market_q = d.get('market_question', '')
        category = classify_market(market_q)
        
        master.execute("""
            INSERT OR IGNORE INTO trades 
            (id, market_condition_id, market_question, side, entry_price, exit_price,
             shares, invested, pnl_dollar, pnl_percent, strategy, reason,
             opened_at, closed_at, is_pair, pair_id,
             instance, run_id, source_vps, market_category)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            d.get('id'), d.get('market_condition_id'), market_q, d.get('side'),
            d.get('entry_price'), d.get('exit_price'), d.get('shares'),
            d.get('invested'), d.get('pnl_dollar'), d.get('pnl_percent'),
            d.get('strategy'), d.get('reason'), d.get('opened_at'),
            d.get('closed_at'), d.get('is_pair'), d.get('pair_id'),
            instance, run_id, source_vps, category
        ))
        trade_count += 1
    
    # Merge signals
    signal_count = 0
    try:
        srows = src.execute('SELECT * FROM signals').fetchall()
        scols = [c[1] for c in src.execute('PRAGMA table_info(signals)').fetchall()]
        for row in srows:
            d = {scols[i]: row[i] for i in range(len(scols))}
            master.execute("""
                INSERT OR IGNORE INTO signals
                (id, market_condition_id, strategy, side, suggested_price,
                 suggested_size_usd, confidence, reason, timestamp,
                 executed, rejected_reason, instance, run_id, source_vps)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                d.get('id'), d.get('market_condition_id'), d.get('strategy'),
                d.get('side'), d.get('suggested_price'), d.get('suggested_size_usd'),
                d.get('confidence'), d.get('reason'), d.get('timestamp'),
                d.get('executed'), d.get('rejected_reason'),
                instance, run_id, source_vps
            ))
            signal_count += 1
    except:
        pass
    
    # Register
    wins = sum(1 for r in rows if r[cols.index('pnl_dollar')] > 0)
    total_pnl = sum(r[cols.index('pnl_dollar')] for r in rows)
    wr = wins / len(rows) * 100 if rows else 0
    
    master.execute("""
        INSERT OR REPLACE INTO archive_registry
        (run_id, instance, source_vps, archived_at, trade_count, signal_count, total_pnl, win_rate)
        VALUES (?,?,?,?,?,?,?,?)
    """, (run_id, instance, source_vps, datetime.now(timezone.utc).isoformat(),
          trade_count, signal_count, round(total_pnl, 2), round(wr, 1)))
    
    src.close()
    return trade_count, signal_count


def main():
    master = create_master_db()
    
    if not os.path.exists(ARCHIVE_DIR):
        print(f"Archive dir not found: {ARCHIVE_DIR}")
        master.close()
        return
    
    archives = sorted(glob.glob(f"{ARCHIVE_DIR}/cipher_v3_*.db"))
    if not archives:
        print("No archives found")
        master.close()
        return
    
    total_trades = 0
    total_signals = 0
    
    for arch_path in archives:
        basename = os.path.basename(arch_path)
        run_id = basename.replace('cipher_v3_', '').replace('.db', '')
        
        # Auto-detect instance from filename context or ask
        # For now, expect instance to be set via env or interactive
        instance = os.environ.get('MERGE_INSTANCE', 'Cipher')
        source_vps = os.environ.get('MERGE_VPS', 'Ireland')
        
        print(f"Merging: {basename} → {instance}/{source_vps}")
        t, s = merge_archive(arch_path, instance, run_id, source_vps, master)
        total_trades += t
        total_signals += s
        print(f"  +{t} trades, +{s} signals")
    
    master.commit()
    
    # Summary
    total = master.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
    instances = master.execute(
        'SELECT instance, COUNT(*), ROUND(AVG(pnl_dollar),2), ROUND(100.0*SUM(CASE WHEN pnl_dollar>0 THEN 1 ELSE 0 END)/COUNT(*),1) FROM trades GROUP BY instance'
    ).fetchall()
    
    print(f"\n{'='*50}")
    print(f"MASTER DB: {MASTER_DB}")
    print(f"Total trades merged: {total}")
    print(f"\nPer instance:")
    for inst, cnt, avg_pnl, wr in instances:
        print(f"  {inst}: {cnt} trades, avg PnL=${avg_pnl}, WR={wr}%")
    
    print(f"\nPer source:")
    sources = master.execute(
        'SELECT source_vps, COUNT(*) FROM trades GROUP BY source_vps'
    ).fetchall()
    for src, cnt in sources:
        print(f"  {src}: {cnt} trades")
    
    print(f"\nPer market category:")
    cats = master.execute(
        'SELECT market_category, COUNT(*), ROUND(AVG(pnl_dollar),2) FROM trades GROUP BY market_category ORDER BY COUNT(*) DESC'
    ).fetchall()
    for cat, cnt, avg_pnl in cats:
        print(f"  {cat}: {cnt} trades, avg PnL=${avg_pnl}")
    
    master.close()


if __name__ == '__main__':
    main()
