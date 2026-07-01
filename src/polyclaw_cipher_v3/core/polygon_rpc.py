"""Polygon RPC client — multi-endpoint with failover.

v3.5.16: Uses primary (llamarpc), fallback (ankr), emergency (public).
Auto-failover on timeout/error, tracks latency per endpoint.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_RPCS = [
    ("primary", "https://polygon.drpc.org"),
    ("fallback", "https://1rpc.io/matic"),
    ("emergency", "https://rpc.ankr.com/polygon"),
]


class PolygonRPC:
    """Multi-endpoint Polygon RPC with auto-failover and latency tracking."""

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        rpc_cfg = cfg.get("execution", {}).get("rpc", {})

        self.endpoints: list[tuple[str, str]] = [
            ("primary", rpc_cfg.get("primary", DEFAULT_RPCS[0][1])),
            ("fallback", rpc_cfg.get("fallback", DEFAULT_RPCS[1][1])),
            ("emergency", rpc_cfg.get("emergency", DEFAULT_RPCS[2][1])),
        ]
        self.chain_id = rpc_cfg.get("chain_id", 137)
        self.max_retries = rpc_cfg.get("max_retries", 3)
        self.timeout = rpc_cfg.get("timeout_sec", 10)

        self._client: httpx.AsyncClient | None = None
        self._active_idx: int = 0
        self._latency: dict[str, list[float]] = {label: [] for label, _ in self.endpoints}
        self._fail_count: dict[str, int] = {label: 0 for label, _ in self.endpoints}
        self._last_failover: float = 0.0

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=self.timeout)
        # Warm up: find fastest endpoint
        await self._select_best_endpoint()

    async def _select_best_endpoint(self) -> None:
        """Probe all endpoints, select fastest."""
        best_idx = 0
        best_latency = float("inf")

        for i, (label, url) in enumerate(self.endpoints):
            try:
                t0 = time.monotonic()
                resp = await self._client.post(
                    url,
                    json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
                )
                if resp.status_code == 200:
                    lat = time.monotonic() - t0
                    self._latency[label].append(lat)
                    if lat < best_latency:
                        best_latency = lat
                        best_idx = i
                    logger.info("RPC %s (%s): %.0fms", label, url[:35], lat * 1000)
            except Exception as e:
                logger.warning("RPC %s probe failed: %s", label, e)

        self._active_idx = best_idx
        label, url = self.endpoints[best_idx]
        logger.info("RPC: selected %s (%s) at %.0fms", label, url[:35], best_latency * 1000)

    async def call(self, method: str, params: list[Any] | None = None) -> dict[str, Any]:
        """Call RPC method with auto-failover."""
        if not self._client:
            await self.start()

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or [],
            "id": int(time.time() * 1000),
        }

        for attempt in range(self.max_retries):
            label, url = self.endpoints[self._active_idx]

            try:
                t0 = time.monotonic()
                resp = await self._client.post(url, json=payload)
                lat = time.monotonic() - t0

                if resp.status_code == 200:
                    data = resp.json()
                    if "error" in data:
                        raise Exception(f"RPC error: {data['error']}")

                    self._latency[label].append(lat)
                    self._fail_count[label] = 0
                    return data["result"]

                # Rate limited or server error
                if resp.status_code in (429, 502, 503):
                    logger.warning("RPC %s: HTTP %d (attempt %d)", label, resp.status_code, attempt + 1)
                else:
                    logger.error("RPC %s: HTTP %d — %s", label, resp.status_code, resp.text[:100])

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                logger.warning("RPC %s timeout/connect: %s (attempt %d)", label, e, attempt + 1)
            except Exception as e:
                logger.error("RPC %s error: %s (attempt %d)", label, e, attempt + 1)

            # Mark failure and failover
            self._fail_count[label] = self._fail_count.get(label, 0) + 1
            self._failover()

        raise Exception(f"All RPC endpoints failed after {self.max_retries} attempts")

    def _failover(self) -> None:
        """Switch to next endpoint."""
        now = time.time()
        if now - self._last_failover < 5.0:
            return  # Don't failover too fast

        self._active_idx = (self._active_idx + 1) % len(self.endpoints)
        self._last_failover = now
        label, _ = self.endpoints[self._active_idx]
        logger.warning("RPC failover → %s", label)

    def stats(self) -> dict[str, Any]:
        """Return latency and health stats."""
        s = {}
        for label, latencies in self._latency.items():
            if latencies:
                avg = sum(latencies) / len(latencies)
                s[label] = {
                    "avg_ms": round(avg * 1000, 1),
                    "p50_ms": round(sorted(latencies)[len(latencies) // 2] * 1000, 1),
                    "samples": len(latencies),
                    "failures": self._fail_count.get(label, 0),
                }
        return {
            "active": self.endpoints[self._active_idx][0],
            "active_url": self.endpoints[self._active_idx][1][:45],
            "endpoints": s,
        }

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
