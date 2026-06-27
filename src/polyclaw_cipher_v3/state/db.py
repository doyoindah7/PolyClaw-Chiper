"""SQLite WAL database — async via aiosqlite."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


SCHEMA = """
-- Wallet state (single row, id=1)
CREATE TABLE IF NOT EXISTS wallet (
    id INTEGER PRIMARY KEY DEFAULT 1,
    bankroll REAL NOT NULL,
    cash REAL NOT NULL,
    initial_bankroll REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Open positions
CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    market_condition_id TEXT NOT NULL,
    market_question TEXT,
    side TEXT NOT NULL,
    token_id TEXT,
    entry_price REAL NOT NULL,
    shares REAL NOT NULL,
    invested REAL NOT NULL,
    strategy TEXT NOT NULL,
    opened_at REAL NOT NULL,
    current_price REAL,
    current_value REAL,
    is_pair INTEGER DEFAULT 0,
    pair_id TEXT,
    pair_sibling_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_positions_strategy ON positions(strategy);
CREATE INDEX IF NOT EXISTS idx_positions_market ON positions(market_condition_id);

-- Closed trades
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    market_condition_id TEXT NOT NULL,
    market_question TEXT,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    shares REAL NOT NULL,
    invested REAL NOT NULL,
    pnl_dollar REAL NOT NULL,
    pnl_percent REAL NOT NULL,
    strategy TEXT NOT NULL,
    reason TEXT,
    opened_at REAL NOT NULL,
    closed_at REAL NOT NULL,
    is_pair INTEGER DEFAULT 0,
    pair_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades(closed_at);

-- Signals log
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    market_condition_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    side TEXT NOT NULL,
    suggested_price REAL,
    suggested_size_usd REAL,
    confidence REAL,
    reason TEXT,
    timestamp REAL NOT NULL,
    executed INTEGER DEFAULT 0,
    rejected_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_strategy ON signals(strategy);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(timestamp);

-- Risk state
CREATE TABLE IF NOT EXISTS risk_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- News events (for LLM agent — deferred)
CREATE TABLE IF NOT EXISTS news_events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    headline TEXT NOT NULL,
    body TEXT,
    url TEXT,
    timestamp REAL NOT NULL,
    llm_analyzed INTEGER DEFAULT 0,
    llm_summary TEXT,
    signals_emitted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_news_ts ON news_events(timestamp);
"""


class Database:
    """Async SQLite with WAL mode."""

    def __init__(self, db_path: str = "data/cipher_v3.db"):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        # WAL mode for concurrent reads
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        # Schema
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        logger.info("Database connected: %s (WAL mode)", self.db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected — call connect() first")
        return self._db

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        cursor = await self.db.execute(sql, params)
        await self.db.commit()
        return cursor

    async def execute_no_commit(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        """v3.4.0: Execute without commit — for use inside batch transactions."""
        return await self.db.execute(sql, params)

    async def execute_batch(self, operations: list[tuple[str, tuple]]) -> None:
        """v3.4.0 FIX (BUG-C6): Execute multiple operations in a single transaction.

        Ensures atomicity for multi-step flows like position close:
        DELETE position + INSERT trade + UPDATE wallet = single commit.
        Prevents partial state corruption on crash mid-flow.
        """
        for sql, params in operations:
            await self.db.execute(sql, params)
        await self.db.commit()

    async def commit(self) -> None:
        """v3.4.0: Explicit commit for manual transaction control."""
        await self.db.commit()

    async def fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with self.db.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self.db.execute(sql, params) as cursor:
            return await cursor.fetchall()
