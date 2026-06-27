"""Binance WebSocket price feed — BTC/ETH/SOL real-time.

Refined from v2:
- Emits to event bus (decoupled from strategies)
- Tracks latency (receive time vs tick timestamp)
- Auto-reconnect with exponential backoff
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field

import websockets

from ..core.types import BinanceTick

logger = logging.getLogger(__name__)


@dataclass
class AssetFeed:
    symbol: str
    ticks: deque = field(default_factory=lambda: deque(maxlen=5000))
    current_price: float = 0.0
    last_update: float = 0.0

    def pct_move(self, lookback_ticks: int = 60) -> float:
        """% move over last N ticks (~1 minute at 1 tick/sec)."""
        if len(self.ticks) < 2:
            return 0.0
        recent = list(self.ticks)
        # ticks are stored as (timestamp, price) tuples
        if len(recent) >= lookback_ticks:
            baseline = recent[-lookback_ticks][1]
        else:
            baseline = recent[0][1]
        if baseline <= 0:
            return 0.0
        return ((self.current_price - baseline) / baseline) * 100

    def pct_move_over_sec(self, seconds: float) -> float:
        """% move over last N seconds."""
        if len(self.ticks) < 2:
            return 0.0
        now = time.time()
        cutoff = now - seconds
        old_price = None
        # ticks stored as (ts, price)
        for ts, p in reversed(self.ticks):
            if ts <= cutoff:
                old_price = p
                break
        if old_price is None or old_price <= 0:
            return 0.0
        return ((self.current_price - old_price) / old_price) * 100


class BinanceFeed:
    """Binance WS subscriber — emits BinanceTick to event bus."""

    def __init__(self, event_bus, ws_url: str = "wss://stream.binance.com:9443"):
        self.event_bus = event_bus
        self.ws_url = ws_url
        self.feeds: dict[str, AssetFeed] = {
            "BTC": AssetFeed("BTC"),
            "ETH": AssetFeed("ETH"),
            "SOL": AssetFeed("SOL"),
        }
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.connected = False
        self.last_message_ts: float = 0.0
        self.reconnect_count: int = 0

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="binance_ws")
        logger.info("BinanceFeed starting")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def get_price(self, symbol: str) -> float:
        f = self.feeds.get(symbol.upper())
        return f.current_price if f else 0.0

    def get_pct_move(self, symbol: str, lookback_ticks: int = 60) -> float:
        f = self.feeds.get(symbol.upper())
        return f.pct_move(lookback_ticks) if f else 0.0

    def get_pct_move_over_sec(self, symbol: str, seconds: float) -> float:
        f = self.feeds.get(symbol.upper())
        return f.pct_move_over_sec(seconds) if f else 0.0

    def get_prices(self, symbol: str, count: int = 100) -> list[float]:
        f = self.feeds.get(symbol.upper())
        if not f:
            return []
        # ticks are stored as (timestamp, price) tuples
        return [p for _, p in list(f.ticks)[-count:]]

    async def _run(self) -> None:
        streams = "btcusdt@trade/ethusdt@trade/solusdt@trade"
        url = f"{self.ws_url}/stream?streams={streams}"
        delay = 1.0

        while not self._stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    self.connected = True
                    self.last_message_ts = time.time()
                    logger.info("Binance WS connected: 3 trade streams")
                    delay = 1.0  # Reset backoff on success

                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        self.last_message_ts = time.time()
                        await self._handle(raw)
                        # v3.4.0 FIX (ARCH-2): Removed per-tick ws_status publish.
                        # Was firing 5-20x/sec with zero subscribers — pure waste.
                        # Status already published on disconnect in except block below.
            except Exception as e:
                self.connected = False
                self.reconnect_count += 1
                logger.warning("Binance WS error: %s. Reconnect in %.1fs (attempt %d)",
                               e, delay, self.reconnect_count)
                await self.event_bus.publish("ws_status", {
                    "source": "binance",
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
            msg = json.loads(raw)
            data = msg.get("data", msg)
            stream = msg.get("stream", "")

            if "@trade" in stream:
                if "btcusdt" in stream:
                    symbol = "BTC"
                elif "ethusdt" in stream:
                    symbol = "ETH"
                elif "solusdt" in stream:
                    symbol = "SOL"
                else:
                    return

                price = float(data.get("p", 0))
                volume = float(data.get("q", 0))  # quantity
                ts = data.get("T", int(time.time() * 1000)) / 1000.0

                if price > 0:
                    feed = self.feeds[symbol]
                    feed.current_price = price
                    feed.last_update = time.time()
                    feed.ticks.append((time.time(), price))

                    # Emit tick to event bus
                    tick = BinanceTick(
                        symbol=symbol,
                        price=price,
                        volume=volume,
                        timestamp=ts,
                    )
                    await self.event_bus.publish("binance_tick", tick)

        except Exception as e:
            logger.debug("Binance parse error: %s", e)
