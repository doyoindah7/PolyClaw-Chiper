"""Binance WebSocket price feed — BTC/ETH/SOL real-time prices."""
from __future__ import annotations
import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
import websockets

logger = logging.getLogger(__name__)


@dataclass
class AssetFeed:
    symbol: str
    ticks: deque = field(default_factory=lambda: deque(maxlen=5000))
    current_price: float = 0.0
    window_start: float = 0.0

    def pct_move(self) -> float:
        """Calculate % move over recent tick window (last 60 ticks ≈ 1 min)."""
        if len(self.ticks) < 2:
            return 0.0
        recent = list(self.ticks)
        if len(recent) >= 60:
            baseline = recent[-60]
        else:
            baseline = recent[0]
        if baseline <= 0:
            return 0.0
        return ((self.current_price - baseline) / baseline) * 100

    def reset_window(self):
        """No-op now — pct_move uses tick history automatically."""
        pass


class PriceFeed:
    def __init__(self, ws_url: str = "wss://stream.binance.com:9443"):
        self.ws_url = ws_url
        self.feeds: dict[str, AssetFeed] = {
            "BTC": AssetFeed("BTC"),
            "ETH": AssetFeed("ETH"),
            "SOL": AssetFeed("SOL"),
        }
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("PriceFeed started")

    async def stop(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def get_pct_move(self, symbol: str) -> float:
        f = self.feeds.get(symbol.upper())
        return f.pct_move() if f else 0.0

    def get_prices(self, symbol: str, count: int = 100) -> list[float]:
        f = self.feeds.get(symbol.upper())
        if not f:
            return []
        return [t for t in list(f.ticks)[-count:]]

    def get_price(self, symbol: str) -> float:
        f = self.feeds.get(symbol.upper())
        return f.current_price if f else 0.0

    def reset_window(self, symbol: str):
        f = self.feeds.get(symbol.upper())
        if f:
            f.reset_window()

    async def _run(self):
        streams = "btcusdt@trade/btcusdt@kline_15m/ethusdt@trade/ethusdt@kline_15m/solusdt@trade/solusdt@kline_15m"
        url = f"{self.ws_url}/stream?streams={streams}"
        delay = 1.0

        while not self._stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info("Binance WS connected: 6 streams")
                    delay = 1.0
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        self._handle(raw)
            except Exception as e:
                logger.warning("Binance WS error: %s. Reconnect in %.1fs", e, delay)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                    break
                except TimeoutError:
                    delay = min(60, delay * 2)

    def _handle(self, raw: str | bytes):
        try:
            msg = json.loads(raw)
            data = msg.get("data", msg)
            stream = msg.get("stream", "")

            if "@trade" in stream:
                symbol = "BTC" if "btcusdt" in stream else "ETH" if "ethusdt" in stream else "SOL"
                price = float(data.get("p", 0))
                if price > 0:
                    feed = self.feeds[symbol]
                    feed.current_price = price
                    feed.ticks.append(price)

            elif "@kline" in stream:
                symbol = "BTC" if "btcusdt" in stream else "ETH" if "ethusdt" in stream else "SOL"
                kline = data.get("k", {})
                price = float(kline.get("c", 0))
                if price > 0:
                    self.feeds[symbol].current_price = price

        except Exception as e:
            logger.debug("Price feed parse error: %s", e)
