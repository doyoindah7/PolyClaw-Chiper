#!/usr/bin/env python3
"""Archive live trading session into master_history.db with full metadata.

Usage: python scripts/master_archive.py --instance Cipher --source Ireland

Copies trades from live cipher_v3.db → master_history.db
with instance tagging, then cleans up.
"""
import sqlite3, os, sys, json, argparse
from datetime import datetime, timezone

DATA_DIR = os.environ.get('DATA_DIR', 'data')
LIVE_DB = f'{DATA_DIR}/cipher_v3.db'
MASTER_DB = f'{DATA_DIR}/master_history.db'

MARKET_KEYWORDS = {
    "crypto": ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "xrp", "doge"],
    "sports_match": [" vs ", " vs. ", " versus "],
    "sports_total": ["o/u", "over/under", "total points", "total runs", "total goals"],
    "sports_spread": ["spread", "handicap", "-1.5", "+1.5", "-2.5", "+2.5"],
    "politics": ["president", "election", "congress", "senate", "vote", "trump", "biden"],
    "economics": ["gdp", "inflation", "cpi", "fed", "interest rate", "unemployment", "tariff"],
}

def classify(question: str) -> str:
    q = (question or "").lower()
    for cat, kws in MARKET_KEYWORDS.items():
        if any(kw in q for kw in kws):
            return cat
    return "other"


def archive_live(instance: str, source: str):
    if not os.path.exists(LIVE_DB):
        print(f"ERROR: Live DB not found: {LIVE_DB}")
        sys.exit(1)
    
    os.makedirs(DATA_DIR, exist_ok=True)
    
    src = sqlite3.connect(LIVE_DB)
    master = sqlite3.connect(MASTER_DB)
    
    # Ensure master schema
    master.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT, market_condition_id TEXT, market_question TEXT, side TEXT,
            entry_price REAL, exit_price REAL, shares REAL, invested REAL,
            pnl_dollar REAL, pnl_percent REAL, strategy TEXT, reason TEXT,
            opened_at REAL, closed_at REAL, is_pair INTEGER, pair_id TEXT,
            instance TEXT, run_id TEXT, source_vps TEXT, market_category TEXT
        )
    """)
    master.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id TEXT, market_condition_id TEXT, strategy TEXT, side TEXT,
            suggested_price REAL, suggested_size_usd REAL, confidence REAL,
            reason TEXT, timestamp REAL, executed INTEGER, rejected_reason TEXT,
            instance TEXT, run_id TEXT, source_vps TEXT
        )
    """)
    
    run_id = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    
    # Copy trades
    try:
        rows = src.execute('SELECT * FROM trades').fetchall()
        cols = [c[1] for c in src.execute('PRAGMA table_info(trades)').fetchall()]
        
        count = 0
        for row in rows:
            d = {cols[i]: row[i] for i in range(len(cols))}
            category = classify(d.get('market_question', ''))
            master.execute("""
                INSERT OR IGNORE INTO trades
                (id, market_condition_id, market_question, side, entry_price, exit_price,
                 shares, invested, pnl_dollar, pnl_percent, strategy, reason,
                 opened_at, closed_at, is_pair, pair_id,
                 instance, run_id, source_vps, market_category)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                d.get('id'), d.get('market_condition_id'), d.get('market_question'),
                d.get('side'), d.get('entry_price'), d.get('exit_price'),
                d.get('shares'), d.get('invested'), d.get('pnl_dollar'),
                d.get('pnl_percent'), d.get('strategy'), d.get('reason'),
                d.get('opened_at'), d.get('closed_at'), d.get('is_pair'),
                d.get('pair_id'), instance, run_id, source, category
            ))
            count += 1
        
        print(f"Trades: {count} rows → {instance}/{source}")
    except Exception as e:
        print(f"WARN: trades copy failed: {e}")
    
    # Copy signals
    try:
        srows = src.execute('SELECT * FROM signals').fetchall()
        scols = [c[1] for c in src.execute('PRAGMA table_info(signals)').fetchall()]
        
        scount = 0
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
                instance, run_id, source
            ))
            scount += 1
        
        print(f"Signals: {scount} rows")
    except Exception as e:
        print(f"WARN: signals copy failed: {e}")
    
    master.commit()
    
    # Summary
    total = master.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
    per_inst = master.execute(
        'SELECT instance, source_vps, COUNT(*), ROUND(AVG(pnl_dollar),2), ROUND(100.0*SUM(CASE WHEN pnl_dollar>0 THEN 1 ELSE 0 END)/COUNT(*),1) FROM trades GROUP BY instance, source_vps'
    ).fetchall()
    
    print(f"\nMaster DB total: {total} trades")
    for inst, src_vps, cnt, avg_pnl, wr in per_inst:
        print(f"  {inst} ({src_vps}): {cnt} trades, avg PnL=${avg_pnl}, WR={wr}%")
    
    master.close()
    src.close()
    print(f"\nDone. Run ID: {run_id}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--instance', required=True, choices=['Cipher', 'Fifteen', 'Scalper'])
    parser.add_argument('--source', required=True, choices=['EC2', 'Ireland'])
    args = parser.parse_args()
    archive_live(args.instance, args.source)
