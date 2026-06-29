"""Polymarket CLOB WebSocket subscriber — real-time orderbook.

Replaces v2's REST polling (3s lag) with WS (~50ms lag).
Maintains local orderbook per token via snapshot + delta updates.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import websockets
from sortedcontainers import SortedDict

from ..core.types import TickUpdate

logger = logging.getLogger(__name__)


@dataclass
class LocalOrderbook:
    """Local orderbook for a single token (YES or NO side)."""
    token_id: str
    condition_id: str = ""
    side: str = ""  # YES or NO
    bids: SortedDict = field(default_factory=SortedDict)  # price → size
    asks: SortedDict = field(default_factory=SortedDict)
    last_price: float = 0.0
    last_update: float = 0.0
    ticks: deque = field(default_factory=lambda: deque(maxlen=2000))  # (ts, price)
    tick_times: list = field(default_factory=list)  # v3.5.16: tick timestamps for volume spike

    def apply_snapshot(self, snapshot: dict) -> None:
        """Apply full orderbook snapshot."""
        self.bids.clear()
        self.asks.clear()
        for b in snapshot.get("bids", []):
            try:
                price = float(b.get("price", 0))
                size = float(b.get("size", 0))
                if price > 0 and size > 0:
                    self.bids[price] = size
            except (ValueError, TypeError):
                continue
        for a in snapshot.get("asks", []):
            try:
                price = float(a.get("price", 0))
                size = float(a.get("size", 0))
                if price > 0 and size > 0:
                    self.asks[price] = size
            except (ValueError, TypeError):
                continue
        self.last_update = time.time()

    def apply_delta(self, delta: dict) -> None:
        """Apply orderbook delta (price_level update)."""
        side = delta.get("side", "")  # "BUY" = bid, "SELL" = ask
        try:
            price = float(delta.get("price", 0))
            size = float(delta.get("size", 0))
        except (ValueError, TypeError):
            return
        if price <= 0:
            return
        book = self.bids if side == "BUY" else self.asks
        if size <= 0:
            book.pop(price, None)
        else:
            book[price] = size

    def best_bid(self) -> float:
        return self.bids.peekitem(-1)[0] if self.bids else 0.0

    def best_ask(self) -> float:
        return self.asks.peekitem(0)[0] if self.asks else 0.0

    def mid(self) -> float:
        bb, ba = self.best_bid(), self.best_ask()
        if bb > 0 and ba > 0:
            return (bb + ba) / 2
        return ba or bb or self.last_price

    def spread_bps(self) -> float:
        bb, ba = self.best_bid(), self.best_ask()
        if bb <= 0 or ba <= 0:
            return 0.0
        return ((ba - bb) / ba) * 10000

    def record_tick(self, price: float) -> None:
        if price > 0:
            self.last_price = price
            now = time.time()
            self.ticks.append((now, price))
            self.last_update = now
            # v3.5.16: Track tick rate for volume spike detection
            self.tick_times.append(now)
            # Prune old ticks (keep last 5 min)
            cutoff = now - 300
            self.tick_times = [t for t in self.tick_times if t > cutoff]

    def tick_rate(self, window_sec: float = 60.0) -> float:
        """Ticks per second over last N seconds."""
        if not self.tick_times:
            return 0.0
        now = time.time()
        cutoff = now - window_sec
        recent = [t for t in self.tick_times if t > cutoff]
        return len(recent) / window_sec if window_sec > 0 else 0.0

    def volume_spike_score(self, short_sec: float = 60.0, long_sec: float = 300.0) -> float:
        """Volume spike score: short_rate / max(long_rate, 0.01).
        >3.0 = significant spike (>3x normal activity).
        """
        short_rate = self.tick_rate(short_sec)
        long_rate = self.tick_rate(long_sec)
        if long_rate <= 0:
            return 0.0
        return short_rate / long_rate

    def pct_change(self, lookback_sec: float) -> float:
        """% change over last N seconds."""
        if len(self.ticks) < 2:
            return 0.0
        now = time.time()
        cutoff = now - lookback_sec
        old_price = None
        for ts, p in reversed(self.ticks):
            if ts <= cutoff:
                old_price = p
                break
        if old_price is None or old_price <= 0:
            return 0.0
        return ((self.last_price - old_price) / old_price) * 100

    def volatility(self, lookback_sec: float = 120.0) -> float:
        """Realized volatility over last N seconds."""
        if len(self.ticks) < 5:
            return 0.0
        now = time.time()
        cutoff = now - lookback_sec
        prices = [p for ts, p in self.ticks if ts >= cutoff]
        if len(prices) < 5:
            return 0.0
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
        if not returns:
            return 0.0
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / len(returns)
        return var ** 0.5


class CLOBFeed:
    """Polymarket CLOB WebSocket subscriber."""

    def __init__(
        self,
        event_bus=None,
        ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        max_tokens_per_conn: int = 100,
    ):
        self.event_bus = event_bus
        self.ws_url = ws_url
        self.max_tokens_per_conn = max_tokens_per_conn
        self.books: dict[str, LocalOrderbook] = {}  # token_id → book
        self._tracked_tokens: dict[str, tuple[str, str]] = {}  # token_id → (condition_id, side)
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self.connected = False
        self.reconnect_count: int = 0
        self.last_message_ts: float = 0.0
        # v3.3.0: Track SET of token IDs (not just count) for sync_connections()
        self._last_synced_token_ids: set[str] = set()

    async def start(self) -> None:
        self._stop.clear()
        logger.info("CLOBFeed starting (will spawn connections on track())")

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    def track(self, token_id: str, condition_id: str, side: str) -> None:
        """Register token to track. Does NOT spawn connection — call sync_connections() after all tracks done."""
        if not token_id or token_id in self._tracked_tokens:
            return
        self._tracked_tokens[token_id] = (condition_id, side)
        self.books[token_id] = LocalOrderbook(
            token_id=token_id,
            condition_id=condition_id,
            side=side,
        )

    def untrack(self, token_id: str) -> None:
        self._tracked_tokens.pop(token_id, None)
        self.books.pop(token_id, None)

    async def sync_connections(self) -> None:
        """Spawn/restart WS connections based on current tracked tokens.

        v3.3.0 fixes (Claude's BUG-3 + consensus):
        - Compare SET of token IDs, not just count (catches token rotation)
        - Track _last_synced_token_ids (set) instead of just count
        - Only reconnect if token set actually changed
        - Reduces disruption from "cancel+respawn every 60s" to "only when needed"

        Call this AFTER all track()/untrack() calls are done (e.g., after market scan).
        """
        token_list = list(self._tracked_tokens.keys())
        if not token_list:
            # No tokens to track — cancel all existing connections
            if self._tasks:
                for task in self._tasks:
                    task.cancel()
                for task in self._tasks:
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                self._tasks.clear()
                self._last_synced_token_ids = set()
            return

        current_token_set = set(token_list)
        prev_token_set = self._last_synced_token_ids if self._last_synced_token_ids else set()

        # v3.3.0: Compare SET of token IDs, not just count
        # Only reconnect if token set actually changed (rotation in top-50 by volume)
        if current_token_set == prev_token_set and self._tasks:
            return  # No change, keep existing connections

        # Token set changed — need to reconnect
        # Calculate how many connections we need (max_tokens_per_conn per connection)
        n_conns = (len(token_list) + self.max_tokens_per_conn - 1) // self.max_tokens_per_conn
        n_conns = max(1, n_conns)

        # Cancel existing connections (they have stale token subscriptions)
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

        # Spawn fresh connections with proper batching
        for idx in range(n_conns):
            start = idx * self.max_tokens_per_conn
            end = start + self.max_tokens_per_conn
            batch = token_list[start:end]
            if not batch:
                break
            task = asyncio.create_task(
                self._run_connection(batch, idx),
                name=f"clob_ws_{idx}",
            )
            self._tasks.append(task)

        # v3.3.0: Store set for next comparison
        added = current_token_set - prev_token_set
        removed = prev_token_set - current_token_set
        self._last_synced_token_ids = current_token_set
        logger.info(
            "CLOB WS sync: %d tokens → %d connection(s) | +%d added, -%d removed",
            len(token_list), n_conns,
            len(added), len(removed),
        )

    async def _run_connection(self, tokens: list[str], conn_id: int) -> None:
        """Run one WS connection for a batch of tokens."""
        delay = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2**24,  # 16MB (snapshots can be large)
                ) as ws:
                    # Subscribe to market channel for each token
                    sub_msg = {
                        "type": "Market",
                        "assets_ids": tokens,
                    }
                    await ws.send(json.dumps(sub_msg))
                    # Also subscribe to price_change for tick updates
                    sub_msg2 = {
                        "type": "PriceChange",
                        "assets_ids": tokens,
                    }
                    await ws.send(json.dumps(sub_msg2))

                    self.connected = True
                    self.last_message_ts = time.time()
                    logger.info(
                        "CLOB WS[%d] connected: %d tokens subscribed",
                        conn_id, len(tokens),
                    )
                    delay = 1.0

                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        self.last_message_ts = time.time()
                        await self._handle(raw)

            except Exception as e:
                self.connected = False
                self.reconnect_count += 1
                logger.warning(
                    "CLOB WS[%d] error: %s. Reconnect in %.1fs (attempt %d)",
                    conn_id, e, delay, self.reconnect_count,
                )
                if self.event_bus:
                    await self.event_bus.publish("ws_status", {
                        "source": f"clob_{conn_id}",
                        "connected": False,
                        "error": str(e),
                        "reconnect_attempt": self.reconnect_count,
                    })
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                    break
                except asyncio.TimeoutError:
                    delay = min(60, delay * 2)

    async def _handle(self, raw: str | bytes) -> None:
        try:
            data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            # CLOB WS may send arrays or single objects
            if isinstance(data, list):
                for item in data:
                    await self._handle_item(item)
            elif isinstance(data, dict):
                await self._handle_item(data)
        except Exception as e:
            logger.debug("CLOB parse error: %s", e)

    async def _handle_item(self, item: dict) -> None:
        """Handle single WS message."""
        msg_type = item.get("event_type") or item.get("type", "")
        token_id = item.get("asset_id") or item.get("market", "") or item.get("token_id", "")
        if not token_id:
            return

        book = self.books.get(token_id)
        if book is None:
            return

        if msg_type == "book":
            # Full orderbook snapshot
            book.apply_snapshot(item)
            await self._emit_tick(token_id, book)
        elif msg_type == "price_change":
            # Delta update
            for delta in item.get("changes", []):
                if delta.get("asset_id") == token_id:
                    book.apply_delta(delta)
            await self._emit_tick(token_id, book)
        elif msg_type == "tick_size_change":
            # Ignore — rare, just tick size update
            pass
        elif msg_type == "last_trade_price":
            try:
                price = float(item.get("price", 0))
                if price > 0:
                    book.record_tick(price)
                    await self._emit_tick(token_id, book)
            except (ValueError, TypeError):
                pass

    async def _emit_tick(self, token_id: str, book: LocalOrderbook) -> None:
        """Publish TickUpdate to event bus."""
        mid = book.mid()
        if mid <= 0:
            return
        book.record_tick(mid)
        tick = TickUpdate(
            token_id=token_id,
            price=mid,
            best_bid=book.best_bid(),
            best_ask=book.best_ask(),
            timestamp=time.time(),
        )
        if self.event_bus:
            await self.event_bus.publish("clob_tick", tick)

    # --- Public API (read-only) ---
    def get_price(self, token_id: str) -> float:
        book = self.books.get(token_id)
        return book.mid() if book else 0.0

    def get_best_bid(self, token_id: str) -> float:
        book = self.books.get(token_id)
        return book.best_bid() if book else 0.0

    def get_best_ask(self, token_id: str) -> float:
        book = self.books.get(token_id)
        return book.best_ask() if book else 0.0

    def get_volume_spike(self, token_id: str, short_sec: float = 60.0, long_sec: float = 300.0) -> float:
        """v3.5.16: Volume spike score for token. >3.0 = significant."""
        book = self.books.get(token_id)
        return book.volume_spike_score(short_sec, long_sec) if book else 0.0

    def get_tick_rate(self, token_id: str, window_sec: float = 60.0) -> float:
        """v3.5.16: Ticks per second over last N seconds."""
        book = self.books.get(token_id)
        return book.tick_rate(window_sec) if book else 0.0

    def get_pct_change(self, token_id: str, lookback_sec: float) -> float:
        book = self.books.get(token_id)
        return book.pct_change(lookback_sec) if book else 0.0

    def get_volatility(self, token_id: str, lookback_sec: float = 120.0) -> float:
        book = self.books.get(token_id)
        return book.volatility(lookback_sec) if book else 0.0

    def stats(self) -> dict[str, Any]:
        return {
            "tracked_tokens": len(self._tracked_tokens),
            "connected": self.connected,
            "reconnect_count": self.reconnect_count,
            "last_message_age_sec": (time.time() - self.last_message_ts) if self.last_message_ts else -1,
        }
