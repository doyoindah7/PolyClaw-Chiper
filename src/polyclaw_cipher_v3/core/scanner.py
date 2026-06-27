"""Market scanner — Polymarket Gamma API keyset endpoint.

Slower than v2 (60s poll) because WebSocket CLOB handles real-time prices.
Real resolution detection via `closed` + `resolvedBy` fields.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..core.types import Market, classify_market
from .resolution import parse_resolution

logger = logging.getLogger(__name__)

CRYPTO_PATTERNS = [
    re.compile(
        r"(?P<asset>BTC|Bitcoin|ETH|Ethereum|SOL|Solana|BNB|XRP|Ripple|DOGE|Dogecoin)\s+"
        r"(?:Up|Down|Up\s+or\s+Down|Up/Down).*?"
        r"(?:on\s+)?(?P<date>[A-Z][a-z]+\s+\d{1,2}|\d{1,2}[a-z]{2}\s+[A-Z][a-z]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<asset>BTC|Bitcoin|ETH|Ethereum|SOL|Solana|BNB|XRP|Ripple|DOGE|Dogecoin)\s+"
        r"(?:Up\s+or\s+Down|price).*?"
        r"(?P<window>15[\s-]?Minute|Hourly|1[\s-]?Hour|Daily|4[\s-]?Hour)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:price\s+of\s+)?(?P<asset>BTC|Bitcoin|ETH|Ethereum|SOL|Solana|BNB|XRP|Ripple|DOGE|Dogecoin)\s+"
        r"(?:be\s+)?above\s+\$[\d,]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<asset>BTC|Bitcoin|ETH|Ethereum|SOL|Solana|BNB|XRP|Ripple|DOGE|Dogecoin)\s+"
        r"(?:dip|drop|fall)\s+to\s+\$[\d,]+",
        re.IGNORECASE,
    ),
]

ASSET_NORMALIZE = {
    "BTC": "BTC", "BITCOIN": "BTC",
    "ETH": "ETH", "ETHEREUM": "ETH",
    "SOL": "SOL", "SOLANA": "SOL",
    "BNB": "BNB",
    "XRP": "XRP", "RIPPLE": "XRP",
    "DOGE": "DOGE", "DOGECOIN": "DOGE",
}

WINDOW_NORMALIZE = {
    "15MINUTE": 15, "15-MINUTE": 15, "15 MINUTE": 15,
    "HOURLY": 60, "1HOUR": 60, "1-HOUR": 60, "1 HOUR": 60,
    "DAILY": 1440, "4HOUR": 240, "4-HOUR": 240,
}


class MarketScanner:
    def __init__(
        self,
        gamma_api: str = "https://gamma-api.polymarket.com",
        min_volume: float = 500,
        page_size: int = 500,
        max_pages: int = 3,
    ):
        self.api = gamma_api.rstrip("/")
        self.min_volume = min_volume
        self.page_size = page_size
        self.max_pages = max_pages
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, Market] = {}
        self._cache_ts: float = 0.0

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            import os
            verify = os.environ.get("VERIFY_SSL", "true").lower() == "true"
            self._client = httpx.AsyncClient(timeout=15.0, verify=verify)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def scan(self) -> list[Market]:
        """Scan all active markets via keyset endpoint."""
        client = await self._ensure_client()
        all_items: list[dict] = []
        next_cursor: str | None = None

        for page in range(self.max_pages):
            params = {
                "active": "true", "closed": "false",
                "limit": str(self.page_size),
                "order": "volume24hr", "ascending": "false",
            }
            if next_cursor:
                params["next_cursor"] = next_cursor

            try:
                resp = await client.get(f"{self.api}/markets/keyset", params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error("Gamma API error: %s", e)
                break

            if not isinstance(data, dict):
                break

            items = data.get("markets") or []
            if not items:
                break

            all_items.extend(items)
            next_cursor = data.get("next_cursor")
            if not next_cursor:
                break
            await asyncio.sleep(0.3)

        markets: list[Market] = []
        skipped = 0
        for item in all_items:
            m = self._parse(item)
            if m and m.volume_24h >= self.min_volume:
                markets.append(m)
                self._cache[m.condition_id] = m
            else:
                skipped += 1

        self._cache_ts = datetime.now(UTC).timestamp()
        logger.info("Scanned %d items → %d markets (%d skipped)", len(all_items), len(markets), skipped)
        return markets

    async def fetch_market(self, condition_id: str) -> Market | None:
        """Fetch single market by condition_id (for resolution check)."""
        client = await self._ensure_client()
        try:
            resp = await client.get(f"{self.api}/markets/{condition_id}")
            resp.raise_for_status()
            item = resp.json()
            return self._parse(item) if item else None
        except Exception as e:
            logger.debug("Fetch market %s failed: %s", condition_id[:8], e)
            return None

    def _parse(self, item: Any) -> Market | None:
        if not isinstance(item, dict):
            return None

        cid = item.get("conditionId") or item.get("condition_id")
        if not cid:
            return None

        question = item.get("question") or ""
        slug = item.get("slug") or ""

        # End date
        ed_str = item.get("endDate") or item.get("end_date") or ""
        if not ed_str:
            start = item.get("startDate") or ""
            if start:
                try:
                    ed = self._parse_date(start) + timedelta(hours=24)
                except Exception:
                    ed = datetime.now(UTC) + timedelta(hours=24)
            else:
                ed = datetime.now(UTC) + timedelta(hours=24)
        else:
            try:
                ed = self._parse_date(ed_str)
            except Exception:
                return None

        # Token IDs
        clob = item.get("clobTokenIds") or item.get("clob_token_ids")
        if isinstance(clob, str):
            try:
                clob = json.loads(clob)
            except Exception:
                clob = []
        if clob and isinstance(clob, list) and len(clob) >= 2:
            yes_tok, no_tok = str(clob[0]), str(clob[1])
        else:
            return None

        # Prices
        prices = item.get("outcomePrices") or item.get("outcome_prices") or []
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except Exception:
                prices = []
        yes_price = float(prices[0]) if len(prices) > 0 else 0.5
        no_price = float(prices[1]) if len(prices) > 1 else 1.0 - yes_price

        vol = float(item.get("volume24hr") or 0.0)
        asset, window = self._extract_crypto(question, slug)

        # Real resolution fields
        is_closed, resolved_by = parse_resolution(item)

        return Market(
            condition_id=str(cid),
            question=question,
            slug=slug,
            end_date=ed,
            yes_token_id=yes_tok,
            no_token_id=no_tok,
            yes_price=yes_price,
            no_price=no_price,
            yes_bid=float(item.get("bestBid") or 0.0),
            yes_ask=float(item.get("bestAsk") or 0.0),
            no_bid=float(item.get("bestBidNo") or 0.0),
            no_ask=float(item.get("bestAskNo") or 0.0),
            spread=float(item.get("spread") or 0.0),
            volume_24h=vol,
            liquidity=float(item.get("liquidity") or 0.0),
            is_active=bool(item.get("active", True)),
            is_closed=is_closed,
            resolved_by=resolved_by,
            crypto_asset=asset,
            market_category=classify_market(question),
            window_minutes=window,
        )

    def _extract_crypto(self, question: str, slug: str) -> tuple[str | None, int | None]:
        text = f"{question} {slug}"
        for pat in CRYPTO_PATTERNS:
            m = pat.search(text)
            if m:
                asset = ASSET_NORMALIZE.get(m.group("asset").upper())
                if not asset:
                    continue
                try:
                    w = WINDOW_NORMALIZE.get(
                        m.group("window").upper().replace("-", " ").replace("_", " ").strip()
                    )
                    if w:
                        return asset, w
                except (IndexError, AttributeError):
                    pass
                try:
                    if m.group("date"):
                        return asset, 1440
                except (IndexError, AttributeError):
                    pass
                return asset, 1440
        return None, None

    @staticmethod
    def _parse_date(s: str) -> datetime:
        s = s.rstrip("Z")
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(s, fmt).replace(tzinfo=UTC)
                except ValueError:
                    continue
            raise
