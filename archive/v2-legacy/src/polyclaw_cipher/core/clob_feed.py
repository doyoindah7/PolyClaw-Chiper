"""Polymarket CLOB price feed — real-time odds via REST polling.

Polls the CLOB API for best bid/ask per token, tracks price history,
and provides % change for momentum/scalper signals on ANY market.
"""
from __future__ import annotations
import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any
import httpx

logger = logging.getLogger(__name__)


@dataclass
class TokenFeed:
    """Tracks price history for a single token (YES or NO side of a market)."""
    token_id: str
    condition_id: str
    side: str  # YES or NO
    ticks: deque = field(default_factory=lambda: deque(maxlen=2000))
    current_price: float = 0.0
    last_update: float = 0.0

    def pct_change(self, lookback_sec: float = 60.0) -> float:
        """% change over last N seconds. Returns 0 if insufficient data."""
        if len(self.ticks) < 5:
            return 0.0
        now = time.time()
        cutoff = now - lookback_sec
        old_price = None
        for ts, p in reversed(self.ticks):
            if ts <= cutoff:
                old_price = p
                break
        if old_price is None:
            # Not enough history for this lookback window
            return 0.0
        if old_price <= 0:
            return 0.0
        return ((self.current_price - old_price) / old_price) * 100

    def volatility(self, lookback_sec: float = 120.0) -> float:
        """Realized volatility over last N seconds."""
        if len(self.ticks) < 10:
            return 0.0
        now = time.time()
        cutoff = now - lookback_sec
        prices = [p for ts, p in self.ticks if ts >= cutoff]
        if len(prices) < 5:
            return 0.0
        returns = []
        for i in range(1, len(prices)):
            if prices[i-1] > 0:
                returns.append((prices[i] - prices[i-1]) / prices[i-1])
        if not returns:
            return 0.0
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / len(returns)
        return var ** 0.5


class CLOBFeed:
    """Polls Polymarket CLOB API for real-time best bid/ask."""

    def __init__(self, poll_interval_sec: float = 2.0):
        self.poll_interval = poll_interval_sec
        self.feeds: dict[str, TokenFeed] = {}  # token_id -> TokenFeed
        self._client: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._tracked_tokens: set[str] = set()  # token_ids to track

    async def start(self):
        self._stop.clear()
        self._client = httpx.AsyncClient(timeout=10.0, verify=False)
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("CLOBFeed started — polling every %.1fs", self.poll_interval)

    async def stop(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()

    def track(self, token_id: str, condition_id: str, side: str):
        """Register a token to track."""
        if token_id and token_id not in self._tracked_tokens:
            self._tracked_tokens.add(token_id)
            self.feeds[token_id] = TokenFeed(
                token_id=token_id,
                condition_id=condition_id,
                side=side,
            )

    def untrack(self, token_id: str):
        self._tracked_tokens.discard(token_id)
        self.feeds.pop(token_id, None)

    def get_price(self, token_id: str) -> float:
        f = self.feeds.get(token_id)
        return f.current_price if f else 0.0

    def get_pct_change(self, token_id: str, lookback: float = 60.0) -> float:
        f = self.feeds.get(token_id)
        return f.pct_change(lookback) if f else 0.0

    def get_volatility(self, token_id: str, lookback: float = 120.0) -> float:
        f = self.feeds.get(token_id)
        return f.volatility(lookback) if f else 0.0

    async def _poll_loop(self):
        while not self._stop.is_set():
            try:
                if self._tracked_tokens:
                    await self._poll_all()
            except Exception as e:
                logger.debug("CLOB poll error: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass

    async def _poll_all(self):
        """Poll all tracked tokens in batches."""
        tokens = list(self._tracked_tokens)
        if not tokens:
            return

        # CLOB API: GET /book?token_id=X returns orderbook
        # Batch: GET /books?token_ids=X,Y,Z
        # Fallback: poll individually
        batch_size = 10
        for i in range(0, len(tokens), batch_size):
            batch = tokens[i:i + batch_size]
            tasks = [self._fetch_book(tid) for tid in batch]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_book(self, token_id: str):
        """Fetch orderbook for a single token."""
        try:
            resp = await self._client.get(
                "https://clob.polymarket.com/book",
                params={"token_id": token_id},
            )
            if resp.status_code == 404:
                # Token not in CLOB — untrack to stop polling
                self.untrack(token_id)
                return
            if resp.status_code != 200:
                return
            data = resp.json()

            # Best bid = highest buy price, Best ask = lowest sell price
            # CLOB API sorts bids ascending, asks ascending
            bids = data.get("bids", [])
            asks = data.get("asks", [])

            if not bids and not asks:
                return

            best_bid = max((float(b["price"]) for b in bids), default=0.0)
            best_ask = min((float(a["price"]) for a in asks), default=0.0)

            # Use last_trade_price if available (most accurate)
            last_trade = data.get("last_trade_price")
            if last_trade:
                try:
                    mid = float(last_trade)
                except (ValueError, TypeError):
                    mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else best_bid or best_ask
            elif best_bid > 0 and best_ask > 0:
                mid = (best_bid + best_ask) / 2
            elif best_ask > 0:
                mid = best_ask
            elif best_bid > 0:
                mid = best_bid
            else:
                return

            feed = self.feeds.get(token_id)
            if feed:
                feed.current_price = mid
                feed.last_update = time.time()
                feed.ticks.append((time.time(), mid))

        except Exception as e:
            logger.debug("CLOB fetch error for %s: %s", token_id[:8], e)
