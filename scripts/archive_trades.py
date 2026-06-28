#!/usr/bin/env python3
"""Auto-archive trade database before reset — preserves all trading history for analysis.

Usage: python3 /app/scripts/archive_trades.py [--csv] [--no-db]
  --csv     Also export to CSV
  --no-db   Skip DB copy (CSV only)

Archives go to /home/ubuntu/polyclaw-cipher-v3/data/trade_archive/ with timestamp.
"""
import sqlite3, os, json, shutil, sys
from datetime import datetime, timezone

DATA_DIR = os.environ.get('DATA_DIR', '/home/ubuntu/polyclaw-cipher-v3/data')
DB_PATH = f'{DATA_DIR}/cipher_v3.db'
ARCHIVE_DIR = f'{DATA_DIR}/trade_archive'
DO_CSV = '--csv' in sys.argv
SKIP_DB = '--no-db' in sys.argv

os.makedirs(ARCHIVE_DIR, exist_ok=True)

ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')

# 1. Backup SQLite DB
if not SKIP_DB and os.path.exists(DB_PATH):
    archive_path = f'{ARCHIVE_DIR}/cipher_v3_{ts}.db'
    shutil.copy2(DB_PATH, archive_path)
    size_kb = os.path.getsize(archive_path) // 1024
    print(f'[ARCHIVE] DB: {archive_path} ({size_kb}KB)')

    db = sqlite3.connect(archive_path)
    trades = db.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
    signals = db.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
    
    # Per-strategy stats
    stats = {}
    for r in db.execute('SELECT strategy, COUNT(*), ROUND(SUM(pnl_dollar),2), ROUND(100.0*SUM(CASE WHEN pnl_dollar > 0 THEN 1 ELSE 0 END)/COUNT(*),1) FROM trades GROUP BY strategy'):
        stats[r[0]] = {'trades': r[1], 'pnl': r[2], 'wr': r[3]}
        print(f'[ARCHIVE] {r[0]}: {r[1]} trades, PnL=${r[2]}, WR={r[3]}%')

    # Summary JSON
    summary = {
        'archive_ts': ts, 'trades_total': trades, 'signals_total': signals,
        'per_strategy': stats, 'db_path': archive_path,
    }
    with open(f'{ARCHIVE_DIR}/summary_{ts}.json', 'w') as f:
        json.dump(summary, f, indent=2)
    
    db.close()
else:
    if not os.path.exists(DB_PATH):
        print(f'[ARCHIVE] WARN: No DB at {DB_PATH} — skipping')
    db = None

# 2. Export CSV (optional)
if DO_CSV and not SKIP_DB:
    db = sqlite3.connect(archive_path if not SKIP_DB else DB_PATH)
    csv_path = f'{ARCHIVE_DIR}/trades_{ts}.csv'
    rows = db.execute('SELECT * FROM trades').fetchall()
    cols = [str(d[0]) for d in db.execute('PRAGMA table_info(trades)').fetchall()]
    with open(csv_path, 'w') as f:
        f.write(','.join(cols) + '\n')
        for r in rows:
            f.write(','.join(str(x) for x in r) + '\n')
    print(f'[ARCHIVE] CSV: {csv_path} ({len(rows)} rows)')
    db.close()

# 3. List all archives
files = sorted(os.listdir(ARCHIVE_DIR))
print(f'\n[ARCHIVE] {len(files)} total files in archive:')
for f in files:
    print(f'  {f}')
