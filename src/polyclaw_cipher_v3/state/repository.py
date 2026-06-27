"""Position/Trade/Signal repositories — async SQLite operations."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from ..core.types import Position, Signal, Trade

logger = logging.getLogger(__name__)


class PositionRepository:
    def __init__(self, db):
        self.db = db

    async def open_position(self, pos: Position) -> None:
        await self.db.execute(
            """INSERT INTO positions
            (id, market_condition_id, market_question, side, token_id,
             entry_price, shares, invested, strategy, opened_at,
             current_price, current_value, is_pair, pair_id, pair_sibling_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pos.id, pos.market_condition_id, pos.market_question, pos.side.value,
                pos.token_id, pos.entry_price, pos.shares, pos.invested, pos.strategy,
                pos.opened_at, pos.current_price, pos.current_value,
                int(pos.is_pair), pos.pair_id, pos.pair_sibling_id,
            ),
        )

    async def close_position(self, pos_id: str) -> Position | None:
        """Remove and return position by id."""
        row = await self.db.fetchone("SELECT * FROM positions WHERE id = ?", (pos_id,))
        if row is None:
            return None
        await self.db.execute("DELETE FROM positions WHERE id = ?", (pos_id,))
        return _row_to_position(row)

    async def get_open_positions(self) -> list[Position]:
        rows = await self.db.fetchall("SELECT * FROM positions ORDER BY opened_at DESC")
        return [_row_to_position(r) for r in rows]

    async def get_positions_by_strategy(self, strategy: str) -> list[Position]:
        rows = await self.db.fetchall(
            "SELECT * FROM positions WHERE strategy = ? ORDER BY opened_at DESC",
            (strategy,),
        )
        return [_row_to_position(r) for r in rows]

    async def update_current_value(self, pos_id: str, current_price: float, current_value: float) -> None:
        await self.db.execute(
            "UPDATE positions SET current_price = ?, current_value = ? WHERE id = ?",
            (current_price, current_value, pos_id),
        )

    async def total_invested(self) -> float:
        row = await self.db.fetchone("SELECT COALESCE(SUM(invested), 0) AS total FROM positions")
        return float(row["total"]) if row else 0.0


class TradeRepository:
    def __init__(self, db):
        self.db = db

    async def add_trade(self, trade: Trade) -> None:
        await self.db.execute(
            """INSERT INTO trades
            (id, market_condition_id, market_question, side, entry_price, exit_price,
             shares, invested, pnl_dollar, pnl_percent, strategy, reason,
             opened_at, closed_at, is_pair, pair_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade.id, trade.market_condition_id, trade.market_question, trade.side.value,
                trade.entry_price, trade.exit_price, trade.shares, trade.invested,
                trade.pnl_dollar, trade.pnl_percent, trade.strategy, trade.reason,
                trade.opened_at, trade.closed_at, int(trade.is_pair), trade.pair_id,
            ),
        )

    async def get_recent_trades(self, limit: int = 20) -> list[Trade]:
        rows = await self.db.fetchall(
            "SELECT * FROM trades ORDER BY closed_at DESC LIMIT ?", (limit,),
        )
        return [_row_to_trade(r) for r in rows]

    async def get_trades_by_strategy(self, strategy: str, limit: int = 50) -> list[Trade]:
        rows = await self.db.fetchall(
            "SELECT * FROM trades WHERE strategy = ? ORDER BY closed_at DESC LIMIT ?",
            (strategy, limit),
        )
        return [_row_to_trade(r) for r in rows]

    async def count_trades_since(self, since_ts: float) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS cnt FROM trades WHERE closed_at >= ?", (since_ts,),
        )
        return int(row["cnt"]) if row else 0

    async def count_since(self, since_ts: float) -> int:
        """v3.5.7: Count trades closed since timestamp. Alias for count_trades_since."""
        return await self.count_trades_since(since_ts)

    async def sum_pnl_since(self, since_ts: float) -> float:
        """v3.5.7: Sum PnL for trades closed since timestamp."""
        row = await self.db.fetchone(
            "SELECT COALESCE(SUM(pnl_dollar), 0) AS total FROM trades WHERE closed_at >= ?",
            (since_ts,),
        )
        return float(row["total"]) if row else 0.0

    async def stats(self) -> dict[str, Any]:
        row = await self.db.fetchone("""
            SELECT
                COUNT(*) AS total_trades,
                COALESCE(SUM(CASE WHEN pnl_dollar > 0 THEN 1 ELSE 0 END), 0) AS wins,
                COALESCE(SUM(CASE WHEN pnl_dollar < 0 THEN 1 ELSE 0 END), 0) AS losses,
                COALESCE(SUM(pnl_dollar), 0) AS total_pnl,
                COALESCE(MAX(pnl_dollar), 0) AS best_trade,
                COALESCE(MIN(pnl_dollar), 0) AS worst_trade
            FROM trades
        """)
        if not row:
            return {"total_trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
        total = int(row["total_trades"])
        wins = int(row["wins"])
        return {
            "total_trades": total,
            "wins": wins,
            "losses": int(row["losses"]),
            "total_pnl": round(float(row["total_pnl"]), 4),
            "best_trade": round(float(row["best_trade"]), 4),
            "worst_trade": round(float(row["worst_trade"]), 4),
            "win_rate": round((wins / total * 100) if total > 0 else 0.0, 2),
        }

    async def per_strategy_stats(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall("""
            SELECT
                strategy,
                COUNT(*) AS trades,
                COALESCE(SUM(CASE WHEN pnl_dollar > 0 THEN 1 ELSE 0 END), 0) AS wins,
                COALESCE(SUM(CASE WHEN pnl_dollar < 0 THEN 1 ELSE 0 END), 0) AS losses,
                COALESCE(SUM(pnl_dollar), 0) AS pnl
            FROM trades
            GROUP BY strategy
            ORDER BY strategy
        """)
        result = []
        for r in rows:
            total = int(r["trades"])
            wins = int(r["wins"])
            result.append({
                "name": r["strategy"],
                "trades": total,
                "wins": wins,
                "losses": int(r["losses"]),
                "win_rate": round((wins / total * 100) if total > 0 else 0.0, 2),
                "pnl": round(float(r["pnl"]), 4),
            })
        return result

    async def recent_signals_count(self, since_ts: float) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS cnt FROM signals WHERE timestamp >= ?", (since_ts,),
        )
        return int(row["cnt"]) if row else 0

    async def total_signals_count(self) -> int:
        """v3.4.4: Total signals count from DB (survives restart)."""
        row = await self.db.fetchone("SELECT COUNT(*) AS cnt FROM signals")
        return int(row["cnt"]) if row else 0

    async def signals_count_per_strategy(self) -> dict[str, int]:
        """v3.4.4: Signals count per strategy from DB (survives restart)."""
        rows = await self.db.fetchall(
            "SELECT strategy, COUNT(*) AS cnt FROM signals GROUP BY strategy"
        )
        return {r["strategy"]: int(r["cnt"]) for r in rows} if rows else {}


class SignalRepository:
    def __init__(self, db):
        self.db = db

    async def log_signal(self, signal: Signal, executed: bool, rejected_reason: str = "") -> None:
        await self.db.execute(
            """INSERT INTO signals
            (id, market_condition_id, strategy, side, suggested_price, suggested_size_usd,
             confidence, reason, timestamp, executed, rejected_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal.id, signal.market_condition_id, signal.strategy_name, signal.side.value,
                signal.suggested_price, signal.suggested_size_usd, signal.confidence,
                signal.reason, signal.timestamp, int(executed), rejected_reason,
            ),
        )

    async def get_recent_signals(self, limit: int = 50) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,),
        )
        return [dict(r) for r in rows]

    async def count_since(self, cutoff_ts: float, executed: bool | None = None) -> int:
        """v3.5.7: Count signals since cutoff_ts. If `executed` is None, count all.

        Used by daemon's SignalStarvationChecker via /api/admin/db_stats.
        """
        if executed is None:
            row = await self.db.fetchone(
                "SELECT COUNT(*) AS cnt FROM signals WHERE timestamp >= ?", (cutoff_ts,),
            )
        else:
            row = await self.db.fetchone(
                "SELECT COUNT(*) AS cnt FROM signals WHERE timestamp >= ? AND executed = ?",
                (cutoff_ts, int(executed)),
            )
        return int(row["cnt"]) if row else 0

    async def count_by_strategy_since(self, cutoff_ts: float) -> dict[str, int]:
        """v3.5.7: Count signals per strategy since cutoff_ts.

        Returns dict like {"momentum": 12, "atomic_arb": 5, ...}.
        Used by daemon to detect per-strategy signal starvation.
        """
        rows = await self.db.fetchall(
            "SELECT strategy, COUNT(*) AS cnt FROM signals WHERE timestamp >= ? GROUP BY strategy",
            (cutoff_ts,),
        )
        return {r["strategy"]: int(r["cnt"]) for r in rows} if rows else {}


def _row_to_position(row) -> Position:
    from ..core.types import Side
    return Position(
        id=row["id"],
        market_condition_id=row["market_condition_id"],
        market_question=row["market_question"] or "",
        side=Side(row["side"]),
        token_id=row["token_id"] or "",
        entry_price=row["entry_price"],
        shares=row["shares"],
        invested=row["invested"],
        strategy=row["strategy"],
        opened_at=row["opened_at"],
        current_price=row["current_price"] or 0.0,
        current_value=row["current_value"] or 0.0,
        is_pair=bool(row["is_pair"]),
        pair_id=row["pair_id"] or "",
        pair_sibling_id=row["pair_sibling_id"] or "",
    )


def _row_to_trade(row) -> Trade:
    from ..core.types import Side
    return Trade(
        id=row["id"],
        market_condition_id=row["market_condition_id"],
        market_question=row["market_question"] or "",
        side=Side(row["side"]),
        entry_price=row["entry_price"],
        exit_price=row["exit_price"],
        shares=row["shares"],
        invested=row["invested"],
        pnl_dollar=row["pnl_dollar"],
        pnl_percent=row["pnl_percent"],
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        strategy=row["strategy"],
        reason=row["reason"] or "",
        is_pair=bool(row["is_pair"]),
        pair_id=row["pair_id"] or "",
    )
