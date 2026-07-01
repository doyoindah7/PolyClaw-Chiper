# -*- coding: utf-8 -*-
"""HTTP server — FastAPI + v3-only dashboard (full width, detailed).

Bind to 0.0.0.0:8082 (public access).
Dashboard: http://3.107.53.103:8082/

After v2 stopped, dashboard is v3-only with:
- Larger KPI cards (6 across, full width)
- Detailed open positions with unrealized P&L
- Per-strategy performance breakdown
- Recent trades with full details
- Risk + system status
- Config summary
"""
from __future__ import annotations
import aiohttp

import asyncio
import logging
import os
import time
from typing import Any

import secrets
import uvicorn
from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Gauge

logger = logging.getLogger(__name__)

security = HTTPBasic()

# v3.4.2: Core Prometheus metrics gauges
METRICS = {
    "bankroll": Gauge("polyclaw_bankroll_usd", "Current wallet bankroll in USD"),
    "cash": Gauge("polyclaw_cash_usd", "Current available cash in USD"),
    "pnl": Gauge("polyclaw_pnl_usd", "Net realized PnL in USD"),
    "open_positions": Gauge("polyclaw_open_positions_count", "Current open positions count"),
    "total_trades": Gauge("polyclaw_total_trades_count", "Total closed trades count"),
    "win_rate": Gauge("polyclaw_win_rate_pct", "Win rate percentage of trades"),
    "btc_price": Gauge("polyclaw_btc_price_usd", "Real-time BTC price from Binance stream"),
    "uptime": Gauge("polyclaw_uptime_seconds", "Bot process uptime in seconds"),
}

def _parse_ts(val) -> float:
    """Parse various timestamp formats to Unix epoch float."""
    if not val:
        return 0.0
    try:
        if isinstance(val, (int, float)):
            return float(val) if val > 10000000000 else float(val) * 1000
        from datetime import datetime
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


class HTTPServer:
    """FastAPI HTTP server with v3-only dashboard."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8082,
        get_stats=None,
        config: dict[str, Any] | None = None,
        get_db_stats=None,  # v3.5.7: daemon lightweight watchdog
        wal_checkpoint=None,  # v3.5.7: daemon lightweight watchdog
        get_trades_paginated=None,  # v3.5.11: dashboard trade history
    ):
        self.host = host
        self.port = port
        self.get_stats = get_stats
        self.config = config or {}
        self.get_db_stats = get_db_stats  # async callable: (hours: int) -> dict
        self.wal_checkpoint = wal_checkpoint  # async callable: () -> None
        self.get_trades_paginated = get_trades_paginated  # async: (page, limit) -> (trades, total)
        self._server = None
        self._task = None
        self._start_time: float = 0.0
        self.app = FastAPI(title="PolyClaw-Cipher v3", docs_url=None, redoc_url=None)
        self._setup_routes()

    def _setup_routes(self) -> None:
        web_conf = self.config.get("monitoring", {}).get("web", {})
        username = web_conf.get("username", "admin")
        password = web_conf.get("password", "secure_polyclaw_password_123")

        async def get_current_user(request: Request, credentials: HTTPBasicCredentials = Depends(security)):
            # Local checks from the daemon on 127.0.0.1 bypass auth to prevent healthcheck loops
            if not username or not password:
                return "local"  # Auth disabled
            if request.client and request.client.host in ("127.0.0.1", "localhost", "::1"):
                return "local"

            correct_username = secrets.compare_digest(credentials.username, username)
            correct_password = secrets.compare_digest(credentials.password, password)
            if not (correct_username and correct_password):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect username or password",
                    headers={"WWW-Authenticate": "Basic"},
                )
            return credentials.username

        @self.app.get("/", response_class=HTMLResponse)
        async def dashboard():
            from .. import __version__ as ver
            html = DASHBOARD_HTML.replace("v3.5.9", f"v{ver}").replace("v3.5.15", f"v{ver}")
            return HTMLResponse(html)

        @self.app.get("/api/stats")
        async def stats():
            if self.get_stats:
                return JSONResponse(self.get_stats())
            return JSONResponse({"error": "stats callback not set"}, status_code=500)

        @self.app.get("/api/poly_positions")
        async def poly_positions():
            """Fetch real positions from Polymarket Data API (source of truth)."""
            funder = os.environ.get("LIVE_FUNDER", "0xf9f38a1dc12fc665222734cf73b1a8f5daf24e9a")
            try:
                url = f"https://data-api.polymarket.com/positions?user={funder}"
                async with aiohttp.ClientSession() as sess:
                    async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            return JSONResponse({"error": f"Poly API {resp.status}"}, status_code=502)
                        data = await resp.json()
                active = []
                for p in data:
                    size = float(p.get("size", 0))
                    if size < 0.01 or p.get("redeemable", True):
                        continue
                    cur_val = float(p.get("currentValue", 0) or 0)
                    avg_px = float(p.get("avgPrice", 0) or 0)
                    cur_px = float(p.get("curPrice", 0) or 0)
                    if cur_px <= 0:
                        cur_px = avg_px
                    side = p.get("outcome", "?")
                    title = p.get("title", "?")
                    active.append({
                        "side": side,
                        "shares": size,
                        "entry_price": avg_px,
                        "current_price": cur_px,
                        "current_value": cur_val,
                        "invested": size * avg_px,
                        "market_question": title,
                        "token_id": p.get("asset", ""),
                        "opened_at": _parse_ts(p.get("createdAt") or p.get("updatedAt")),  # for dashboard age
                    })
                return JSONResponse({"positions": active, "count": len(active)})
            except Exception as e:
                logger.warning(f"Poly positions API error: {e}")
                return JSONResponse({"positions": [], "count": 0, "error": str(e)[:100]})

        @self.app.get("/api/health")
        async def health():
            # Unprotected: safe to expose for docker / cluster healthchecks
            # v3.5.9: Use __version__ from package instead of hardcoded string
            from .. import __version__
            label = os.environ.get("TG_INSTANCE_LABEL", os.environ.get("BOT_MODE", "bot"))
            return {
                "status": "ok",
                "version": __version__,
                "uptime_sec": int(time.time() - (self._start_time or time.time())),
                "instance_label": label,
                "mode": os.environ.get("BOT_MODE", "paper"),
            }

        @self.app.get("/api/config")
        async def config_endpoint():
            return JSONResponse(self.config)

        @self.app.get("/api/trades")
        async def trades_paginated(page: int = 1, limit: int = 20):
            """v3.5.11: Paginated trade history for dashboard. Public (same as /api/stats).

            Returns: {trades: [...], page, limit, total, total_pages}
            """
            if not self.get_trades_paginated:
                return JSONResponse({"error": "trades callback not configured"}, status_code=503)
            try:
                trades, total = await self.get_trades_paginated(page, limit)
                total_pages = (total + limit - 1) // limit if limit > 0 else 0
                return JSONResponse({
                    "trades": [t for t in trades],
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "total_pages": total_pages,
                })
            except Exception as e:
                logger.error("trades endpoint error: %s", e)
                return JSONResponse({"error": str(e)}, status_code=500)

        @self.app.get("/metrics")
        async def metrics():
            if self.get_stats:
                try:
                    stats = self.get_stats()
                    METRICS["bankroll"].set(stats.get("bankroll", 0.0))
                    METRICS["cash"].set(stats.get("cash", 0.0))
                    METRICS["pnl"].set(stats.get("pnl", 0.0))
                    METRICS["open_positions"].set(len(stats.get("open_positions", [])))
                    METRICS["total_trades"].set(stats.get("trades", 0))  # v3.5.0: key is "trades" not "total_trades"
                    METRICS["win_rate"].set(stats.get("win_rate", 0.0))
                    METRICS["btc_price"].set(stats.get("btc_price", 0.0))
                    METRICS["uptime"].set(stats.get("uptime_sec", 0))
                except Exception as e:
                    logger.error("Failed to update Prometheus metrics: %s", e)
            return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

        # v3.5.7: Admin endpoints for daemon lightweight watchdog
        # Localhost-only via middleware below. Used for:
        #   - /api/admin/db_stats: pre-computed signal/trade aggregates for daemon monitoring
        #   - /api/admin/wal_checkpoint: trigger manual WAL checkpoint (safer than docker exec sqlite3)

        @self.app.get("/api/admin/db_stats")
        async def admin_db_stats(request: Request, hours: int = 1):
            """Pre-computed DB aggregates for daemon watchdog. Localhost only."""
            # Localhost check (defense in depth)
            client_host = request.client.host if request.client else ""
            if client_host not in ("127.0.0.1", "::1", "localhost"):
                return JSONResponse({"error": "Admin endpoints localhost only"}, status_code=403)
            if not self.get_db_stats:
                return JSONResponse({"error": "db_stats callback not configured"}, status_code=503)
            try:
                hours = max(1, min(168, hours))  # clamp 1h-7d
                stats = await self.get_db_stats(hours)
                return JSONResponse(stats)
            except Exception as e:
                logger.error("admin_db_stats error: %s", e)
                return JSONResponse({"error": str(e)}, status_code=500)

        @self.app.post("/api/admin/wal_checkpoint")
        async def admin_wal_checkpoint(request: Request):
            """Trigger manual WAL checkpoint. Localhost only."""
            client_host = request.client.host if request.client else ""
            if client_host not in ("127.0.0.1", "::1", "localhost"):
                return JSONResponse({"error": "Admin endpoints localhost only"}, status_code=403)
            if not self.wal_checkpoint:
                return JSONResponse({"error": "wal_checkpoint callback not configured"}, status_code=503)
            try:
                await self.wal_checkpoint()
                logger.info("WAL checkpoint triggered via admin API")
                return JSONResponse({"status": "ok", "checkpoint": "completed"})
            except Exception as e:
                logger.error("admin_wal_checkpoint error: %s", e)
                return JSONResponse({"error": str(e)}, status_code=500)

    async def start(self) -> None:
        import uvicorn
        self._start_time = time.time()
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve(), name="http_server")
        logger.info("HTTP server on http://%s:%d", self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


# === v3-only Dashboard HTML ===
# Full-width layout, larger KPIs, detailed positions/trades/strategies

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PolyClaw-Cipher v3.5.15</title>
<style>
:root {
  --bg: #0a0e14; --card: #131820; --card2: #0f141c; --border: #1e2836;
  --text: #c8d6e5; --muted: #6b7d91; --dim: #4a5a6e;
  --green: #00e676; --red: #ff5252; --blue: #448aff; --purple: #bb86fc;
  --orange: #ff9100; --gold: #ffd740; --cyan: #18ffff;
  --radius: 10px;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  background: var(--bg); color: var(--text);
  font-family: 'SF Mono','Segoe UI',system-ui,sans-serif;
  min-height: 100vh; line-height: 1.4;
}
.wrap { max-width: 1400px; margin: 0 auto; padding: 14px 20px; }

.hdr {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 0; border-bottom: 1px solid var(--border); margin-bottom: 14px;
}
.hdr h1 { font-size: 1.4rem; font-weight: 800; }
.hdr .sub { font-size: 0.7rem; color: var(--muted); margin-top: 3px; }
.hdr .live-dot {
  width: 9px; height: 9px; border-radius: 50%; display: inline-block;
  margin-right: 6px; animation: pulse 2s infinite;
}
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
@keyframes flashGreen { 0%{background:transparent} 50%{background:rgba(0,230,118,0.25)} 100%{background:transparent} }
@keyframes flashRed { 0%{background:transparent} 50%{background:rgba(255,82,82,0.25)} 100%{background:transparent} }
.flash-up { animation: flashGreen 0.6s ease; }
.flash-down { animation: flashRed 0.6s ease; }
.update-counter { font-size: 0.55rem; color: var(--dim); margin-left: 6px; }
.hdr .live { color: var(--green); font-size: 0.78rem; font-weight: 600; }
.hdr .clock { color: var(--muted); font-size: 0.78rem; }

/* KPI Row — 6 cards full width */
.kpi-row { display: grid; grid-template-columns: repeat(6,1fr); gap: 12px; margin-bottom: 14px; }
.kpi {
  background: var(--card); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 14px 16px; text-align: center;
  transition: border-color 0.2s;
}
.kpi:hover { border-color: var(--muted); }
.kpi .lbl { font-size: 0.6rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
.kpi .val { font-size: 1.5rem; font-weight: 800; margin-top: 5px; }
.kpi .delta { font-size: 0.65rem; margin-top: 3px; }
.kpi .delta.pos { color: var(--green); }
.kpi .delta.neg { color: var(--red); }
.kpi .delta.neu { color: var(--muted); }
.val.green { color: var(--green); }
.val.red { color: var(--red); }
.val.gold { color: var(--gold); }
.val.blue { color: var(--blue); }
.val.cyan { color: var(--cyan); }

/* Capital allocation bar */
.alloc-bar-wrap { margin-bottom: 14px; }
.alloc-label {
  display: flex; justify-content: space-between;
  font-size: 0.62rem; color: var(--muted); margin-bottom: 5px;
  text-transform: uppercase; letter-spacing: 0.5px;
}
.alloc-bar {
  height: 8px; background: rgba(255,255,255,0.05); border-radius: 4px;
  overflow: hidden; display: flex;
}
.alloc-fill-cash { background: var(--blue); height: 100%; transition: width 0.3s; }
.alloc-fill-pos { background: var(--green); height: 100%; transition: width 0.3s; }

/* Two-column layout */
.cols { display: grid; grid-template-columns: 1.4fr 1fr; gap: 14px; margin-bottom: 14px; }

.card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 14px; overflow: hidden;
}
.card-title {
  font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.5px; color: var(--muted); margin-bottom: 10px;
  display: flex; align-items: center; justify-content: space-between;
}
.card-title .badge {
  font-size: 0.55rem; padding: 2px 8px; border-radius: 4px;
  background: var(--card2); color: var(--text);
}

/* Table */
.tbl { width: 100%; border-collapse: collapse; font-size: 0.7rem; }
.tbl th {
  text-align: left; padding: 7px 8px; color: var(--muted);
  font-weight: 600; text-transform: uppercase; font-size: 0.55rem;
  letter-spacing: 0.5px; border-bottom: 1px solid var(--border);
}
.tbl td { padding: 8px; border-bottom: 1px solid rgba(30,40,54,0.4); }
.tbl tr:hover td { background: rgba(255,255,255,0.02); }
.tbl .pnl-pos { color: var(--green); font-weight: 600; }
.tbl .pnl-neg { color: var(--red); font-weight: 600; }
.tbl .side-YES { color: var(--green); font-weight: 600; }
.tbl .side-NO { color: var(--red); font-weight: 600; }

.tag {
  display: inline-block; padding: 2px 7px; border-radius: 3px;
  font-size: 0.55rem; font-weight: 600; text-transform: uppercase;
}
.tag.latency_arb { background: rgba(68,138,255,0.15); color: var(--blue); }
.tag.atomic_arb { background: rgba(0,230,118,0.15); color: var(--green); }
.tag.resolution_snipe { background: rgba(255,145,0,0.15); color: var(--orange); }
.tag.momentum { background: rgba(187,134,252,0.15); color: var(--purple); }
.tag.news_llm { background: rgba(24,255,255,0.15); color: var(--cyan); }
.tag.convergence_scalper { background: rgba(255,82,82,0.15); color: var(--red); }

/* Strategy cards */
.strat-grid { display: grid; gap: 8px; }
.strat {
  background: var(--card2); border: 1px solid var(--border);
  border-radius: 8px; padding: 11px 13px;
}
.strat-head { display: flex; align-items: center; justify-content: space-between; }
.strat-name { font-weight: 700; font-size: 0.82rem; }
.strat-stats { display: grid; grid-template-columns: repeat(5,1fr); gap: 6px; margin-top: 9px; }
.strat-stat .s-lbl { font-size: 0.52rem; color: var(--muted); text-transform: uppercase; }
.strat-stat .s-val { font-size: 0.8rem; font-weight: 600; }

/* Empty state */
.empty { color: var(--dim); text-align: center; padding: 20px; font-size: 0.75rem; font-style: italic; }

/* Scrollable lists */
.scroll-list { max-height: 280px; overflow-y: auto; }
.scroll-list::-webkit-scrollbar { width: 5px; }
.scroll-list::-webkit-scrollbar-track { background: transparent; }
.scroll-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

/* Status grid */
.status-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.status-item {
  background: var(--card2); border: 1px solid var(--border);
  border-radius: 6px; padding: 10px 12px;
}
.status-item .s-lbl { font-size: 0.55rem; color: var(--muted); text-transform: uppercase; }
.status-item .s-val { font-size: 0.85rem; font-weight: 700; margin-top: 3px; }

@media (max-width: 900px) {
  .kpi-row { grid-template-columns: repeat(3,1fr); }
  .cols { grid-template-columns: 1fr; }
  .strat-stats { grid-template-columns: repeat(3,1fr); }
}
</style>
</head>
<body>
<div class="wrap">
  <div id="alerts-container"></div>
  <div class="hdr">
    <div>
            <h1>🔍 <span id="instance-label">PolyClaw-Cipher</span> v3.5.15</h1>
      <div class="sub">Paper Trading · auto-refresh 5s · <span id="refresh-status" style="color:var(--green)">connecting...</span> · updated <span id="last-update">--</span></div>
    </div>
    <div style="text-align:right">
      <div class="live"><span class="live-dot" id="live-dot" style="background:var(--green)"></span><span id="live-text">LIVE</span></div>
      <div class="clock" id="clock">--:--:--</div>
    </div>
  </div>

  <!-- KPI Row -->
  <div class="kpi-row">
    <div class="kpi"><div class="lbl">Bankroll</div><div class="val gold" id="kpi-bankroll">$0.00</div><div class="delta neu" id="kpi-bankroll-delta">vs $25.00 initial</div></div>
    <div class="kpi"><div class="lbl">P&L Total</div><div class="val" id="kpi-pnl">$0.00</div><div class="delta neu" id="kpi-pnl-pct">--</div></div>
    <div class="kpi"><div class="lbl">Cash</div><div class="val blue" id="kpi-cash">$0.00</div><div class="delta neu" id="kpi-cash-pct">--</div></div>
    <div class="kpi"><div class="lbl">Deployed</div><div class="val green" id="kpi-invested">$0.00</div><div class="delta neu" id="kpi-invested-pct">--</div></div>
    <div class="kpi"><div class="lbl">Open Positions</div><div class="val cyan" id="kpi-positions">0</div><div class="delta neu" id="kpi-positions-info">--</div></div>
    <div class="kpi"><div class="lbl">Win Rate</div><div class="val" id="kpi-winrate">0%</div><div class="delta neu" id="kpi-trades">0 trades</div></div>
  </div>

  <!-- Capital Allocation Bar -->
  <div class="alloc-bar-wrap">
    <div class="alloc-label">
      <span>Capital Allocation</span>
      <span id="alloc-label">--</span>
    </div>
    <div class="alloc-bar">
      <div class="alloc-fill-cash" id="bar-cash" style="width:0%"></div>
      <div class="alloc-fill-pos" id="bar-pos" style="width:0%"></div>
    </div>
  </div>

  <!-- Positions + Strategies -->
  <div class="cols">
    <div class="card">
      <div class="card-title">
        <span>📊 Open Positions</span>
        <span class="badge" id="pos-count">0</span>
      </div>
      <div class="scroll-list" id="positions-container">
        <div class="empty">No open positions</div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">
        <span>🎯 Strategies</span>
      </div>
      <div class="strat-grid" id="strategies-container">
        <div class="empty">Loading...</div>
      </div>
    </div>
  </div>

  <!-- Trade History (table format, filterable, expandable rows) -->
  <div class="card" style="margin-bottom:14px" id="history-card">
    <div class="card-title" style="cursor:pointer" onclick="toggleHistoryPanel()">
      <span>📚 Trade History</span>
      <span id="history-total-wrap" style="margin-left:8px;font-size:0.85rem;font-weight:700">
        <span id="history-total" style="color:var(--blue);font-size:1.05rem;font-weight:800">…</span>
        <span style="color:var(--muted);font-size:0.65rem;font-weight:500;margin-left:3px">total trades</span>
      </span>
      <span style="font-size:0.65rem;color:var(--muted);margin-left:8px" id="history-page-info">[click to expand]</span>
    </div>
    <div id="history-panel" style="display:none">
      <!-- Filter buttons -->
      <div style="display:flex;gap:6px;padding:8px 12px;border-bottom:1px solid var(--border);font-size:0.7rem;flex-wrap:wrap">
        <button id="filter-all" onclick="setHistoryFilter('all')" style="background:var(--accent);color:white;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:0.65rem;font-weight:600">All</button>
        <button id="filter-profit" onclick="setHistoryFilter('profit')" style="background:var(--card2);color:var(--text);border:1px solid var(--border);padding:4px 12px;border-radius:4px;cursor:pointer;font-size:0.65rem">🏆 Most Profit</button>
        <button id="filter-loss" onclick="setHistoryFilter('loss')" style="background:var(--card2);color:var(--text);border:1px solid var(--border);padding:4px 12px;border-radius:4px;cursor:pointer;font-size:0.65rem">📉 Most Loss</button>
        <span style="flex:1"></span>
        <span id="history-filter-info" style="color:var(--muted);align-self:center">Showing all trades</span>
      </div>
      <div class="scroll-list" id="history-container" style="max-height:400px">
        <div class="empty">Loading trades...</div>
      </div>
      <div id="history-pagination" style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-top:1px solid var(--border);font-size:0.7rem">
        <span style="color:var(--muted);font-size:0.6rem">Auto-refresh page 1 every 5s</span>
        <div>
          <button id="history-prev" onclick="historyPrevPage()" style="background:var(--card2);color:var(--text);border:1px solid var(--border);padding:4px 10px;border-radius:4px;cursor:pointer;font-size:0.65rem">← Prev</button>
          <span style="margin:0 6px;color:var(--muted)">|</span>
          <button id="history-next" onclick="historyNextPage()" style="background:var(--card2);color:var(--text);border:1px solid var(--border);padding:4px 10px;border-radius:4px;cursor:pointer;font-size:0.65rem">Next →</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Risk + System Status -->
  <div class="cols">
    <div class="card">
      <div class="card-title"><span>🛡️ Risk Status</span></div>
      <div class="status-grid">
        <div class="status-item"><div class="s-lbl">Daily DD Limit</div><div class="s-val" id="risk-dd">--</div></div>
        <div class="status-item"><div class="s-lbl">Consec. Losses</div><div class="s-val" id="risk-consec">--</div></div>
        <div class="status-item"><div class="s-lbl">Trades/Hour</div><div class="s-val" id="risk-rate">--</div></div>
        <div class="status-item"><div class="s-lbl">Daily P&L</div><div class="s-val" id="risk-daily-pnl">--</div></div>
        <div class="status-item"><div class="s-lbl">Session Age</div><div class="s-val" id="risk-session">--</div></div>
        <div class="status-item"><div class="s-lbl">Disabled Strategies</div><div class="s-val" id="risk-disabled">--</div></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title"><span>⚙️ System Status</span></div>
      <div class="status-grid">
        <div class="status-item"><div class="s-lbl">Bot Status</div><div class="s-val" id="sys-bot-status">--</div></div>
        <div class="status-item"><div class="s-lbl">Markets Tracked</div><div class="s-val" id="sys-markets">0</div></div>
        <div class="status-item"><div class="s-lbl">Crypto Up/Down</div><div class="s-val" id="sys-crypto">0</div></div>
        <div class="status-item"><div class="s-lbl">CLOB WS</div><div class="s-val" id="sys-clob">--</div></div>
        <div class="status-item"><div class="s-lbl">Binance WS</div><div class="s-val" id="sys-binance">--</div></div>
        <div class="status-item"><div class="s-lbl">BTC Price</div><div class="s-val" id="sys-btc">--</div></div>
        <div class="status-item" style="grid-column:span 2"><div class="s-lbl">Markets</div><div class="cat-grid" id="market-dist"></div></div>
        <div class="status-item"><div class="s-lbl">Uptime</div><div class="s-val" id="sys-uptime">--</div></div>
        <div class="status-item"><div class="s-lbl">Last Signal</div><div class="s-val" id="sys-last-signal">--</div></div>
        <div class="status-item"><div class="s-lbl">Last Trade</div><div class="s-val" id="sys-last-trade">--</div></div>
      </div>
    </div>
  </div>
</div>

<script>
const REFRESH_MS = 5000;
const INITIAL_BANKROLL = 25.00;
let lastData = null;
let refreshSuccessCount = 0;
let refreshFailCount = 0;

function fmt(n, dec=2) {
  if (n === null || n === undefined || isNaN(n)) return '--';
  return Number(n).toFixed(dec);
}
function fmtUsd(n) { return (n === null || n === undefined || isNaN(n)) ? '--' : '$' + fmt(n, 2); }
function timeAgo(ts) {
  if (!ts) return '--';
  const s = Math.floor(Date.now()/1000 - ts);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s/60) + 'm';
  return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
}
function pnlColor(v) { return v > 0 ? 'green' : v < 0 ? 'red' : ''; }
function pnlSign(v) { return v >= 0 ? '+' : ''; }
function fmtUptime(sec) {
  if (!sec || sec < 0) return '--';
  const h = Math.floor(sec/3600);
  const m = Math.floor((sec%3600)/60);
  const s = sec%60;
  if (h > 0) return h + 'h ' + m + 'm';
  if (m > 0) return m + 'm ' + s + 's';
  return s + 's';
}

async function fetchWithRetry(url, retries = 2) {
  for (let i = 0; i <= retries; i++) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(8000) });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return await r.json();
    } catch(e) {
      if (i === retries) return null;
      await new Promise(res => setTimeout(res, 500 * (i + 1)));
    }
  }
  return null;
}

function updateConnectionStatus(ok) {
  const dot = document.getElementById('live-dot');
  const text = document.getElementById('live-text');
  const status = document.getElementById('refresh-status');
  if (ok) {
    dot.style.background = 'var(--green)';
    text.textContent = 'LIVE';
    status.textContent = 'live';
    status.style.color = 'var(--green)';
  } else {
    dot.style.background = 'var(--red)';
    text.textContent = 'OFFLINE';
    status.textContent = 'reconnecting...';
    status.style.color = 'var(--red)';
  }
}

// v3.6.1: Flash animation on value change
let _prevValues = {};
function _flashOnChange(id, newVal, prevVal) {
  const el = document.getElementById(id);
  if (!el || prevVal === undefined || prevVal === newVal) return;
  el.classList.remove('flash-up', 'flash-down');
  void el.offsetWidth; // force reflow
  el.classList.add(newVal > prevVal ? 'flash-up' : 'flash-down');
}

function renderKPIs(d) {
  const bankroll = d.bankroll || 0;
  const cash = d.cash || 0;
  const invested = d.deployed !== undefined ? d.deployed : (bankroll - cash);
  const pnl = d.pnl || 0;
  const pnlPct = (pnl / INITIAL_BANKROLL * 100);
  const positions = d.open_positions || [];
  const trades = d.trades || 0;
  const winRate = d.win_rate || 0;

  _flashOnChange('kpi-bankroll', bankroll.toFixed(4), (_prevValues.bankroll||''));
  _flashOnChange('kpi-cash', cash.toFixed(4), (_prevValues.cash||''));
  _flashOnChange('kpi-invested', invested.toFixed(4), (_prevValues.invested||''));
  _flashOnChange('kpi-positions', positions.length.toString(), (_prevValues.positions||''));
  _prevValues = { bankroll: bankroll.toFixed(4), cash: cash.toFixed(4), invested: invested.toFixed(4), positions: positions.length.toString() };

  document.getElementById('kpi-bankroll').textContent = fmtUsd(bankroll);
  document.getElementById('kpi-bankroll-delta').textContent = pnlSign(pnl) + fmtUsd(pnl) + ' vs $' + INITIAL_BANKROLL.toFixed(2);

  const pnlEl = document.getElementById('kpi-pnl');
  pnlEl.textContent = pnlSign(pnl) + fmtUsd(pnl);
  pnlEl.className = 'val ' + pnlColor(pnl);
  document.getElementById('kpi-pnl-pct').textContent = pnlSign(pnlPct) + fmt(pnlPct, 2) + '%';

  document.getElementById('kpi-cash').textContent = fmtUsd(cash);
  const cashPct = bankroll > 0 ? (cash / bankroll * 100) : 0;
  document.getElementById('kpi-cash-pct').textContent = fmt(cashPct, 1) + '% idle';

  document.getElementById('kpi-invested').textContent = fmtUsd(invested);
  const invPct = bankroll > 0 ? (invested / bankroll * 100) : 0;
  document.getElementById('kpi-invested-pct').textContent = fmt(invPct, 1) + '% deployed';

  document.getElementById('kpi-positions').textContent = positions.length;
  document.getElementById('kpi-positions-info').textContent = positions.length > 0 ? 'active' : 'idle';

  const wrEl = document.getElementById('kpi-winrate');
  wrEl.textContent = fmt(winRate, 1) + '%';
  wrEl.className = 'val ' + (winRate >= 50 ? 'green' : winRate > 0 ? 'gold' : '');
  document.getElementById('kpi-trades').textContent = trades + ' closed trades';

  // Alloc bar
  document.getElementById('bar-cash').style.width = cashPct + '%';
  document.getElementById('bar-pos').style.width = invPct + '%';
  document.getElementById('alloc-label').textContent =
    fmt(cashPct, 0) + '% cash / ' + fmt(invPct, 0) + '% deployed';
}

function renderPositions(d) {
  // Fetch positions from Poly API async, then render
  fetch('/api/poly_positions').then(r => r.json()).then(pd => {
    renderPositionsFromPoly(pd.positions || []);
  }).catch(() => {
    // Fallback to bot DB positions
    renderPositionsFromPoly((d.open_positions || []).map(p => ({
      side: p.side, shares: p.shares, entry_price: p.entry_price,
      current_price: p.current_price, current_value: p.current_value || p.invested,
      invested: p.invested, market_question: p.market_question
    })));
  });
}

function renderPositionsFromPoly(positions) {
  const cont = document.getElementById('positions-container');
  document.getElementById('pos-count').textContent = positions.length;
  if (positions.length === 0) {
    cont.innerHTML = '<div class="empty">No open positions</div>';
    return;
  }
  let html = '<table class="tbl"><thead><tr>' +
    '<th>Market</th><th>Side</th><th>Shares</th><th>Entry</th><th>Cur</th><th>Invested</th><th>Cur Val</th><th>Unreal P&L</th><th>Age</th>' +
    '</tr></thead><tbody>';
  for (const p of positions) {
    const curVal = p.current_value || p.invested;
    const unrealPnl = curVal - p.invested;
    const unrealPnlPct = p.invested > 0 ? (unrealPnl / p.invested * 100) : 0;
    const pnlCls = unrealPnl >= 0 ? 'pnl-pos' : 'pnl-neg';
    const strat = p.strategy || '';
    const pairBadge = p.is_pair ? ' <span class="tag" style="background:rgba(255,215,64,0.15);color:var(--gold)">PAIR</span>' : '';
    html += '<tr>' +
      '<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + (p.market_question||'').replace(/"/g,'&quot;') + '">' + (p.market_question||'').substring(0,40) + '</td>' +
      '<td class="side-' + (p.side||'') + '">' + (p.side||'') + '</td>' +
      '<td>' + fmt(p.shares || 0, 1) + '</td>' +
      '<td>$' + fmt(p.entry_price, 4) + '</td>' +
      '<td>$' + fmt(p.current_price, 4) + '</td>' +
      '<td>$' + fmt(p.invested, 2) + '</td>' +
      '<td>$' + fmt(curVal, 2) + '</td>' +
      '<td class="' + pnlCls + '">' + pnlSign(unrealPnl) + '$' + fmt(unrealPnl, 2) + ' (' + pnlSign(unrealPnlPct) + fmt(unrealPnlPct, 1) + '%)</td>' +
      '<td style="color:var(--muted)">' + timeAgo(p.opened_at) + '</td>' +
      '</tr>';
  }
  html += '</tbody></table>';
  cont.innerHTML = html;
}

function renderStrategies(d) {
  const cont = document.getElementById('strategies-container');
  const strats = d.strategies || [];
  if (strats.length === 0) {
    cont.innerHTML = '<div class="empty">No strategies</div>';
    return;
  }
  let html = '';
  for (const s of strats) {
    const wr = s.win_rate || 0;
    const pnlCls = s.pnl >= 0 ? 'green' : 'red';
    const enabledBadge = s.enabled === false ? ' ⏸️' : '';
    const statusTag = !s.enabled ? 'disabled' : (s.trades > 0 ? wr.toFixed(0) + '% WR' : 'idle');
    html += '<div class="strat"><div class="strat-head">' +
      '<div class="strat-name">' + s.name + enabledBadge + '</div>' +
      '<div class="tag ' + s.name + '">' + statusTag + '</div>' +
      '</div><div class="strat-stats">' +
      '<div class="strat-stat"><div class="s-lbl">Signals</div><div class="s-val">' + (s.signals_emitted||0) + '</div></div>' +
      '<div class="strat-stat"><div class="s-lbl">Trades</div><div class="s-val">' + (s.trades||0) + '</div></div>' +
      '<div class="strat-stat"><div class="s-lbl">W/L</div><div class="s-val">' + (s.wins||0) + '/' + (s.losses||0) + '</div></div>' +
      '<div class="strat-stat"><div class="s-lbl">PnL</div><div class="s-val ' + pnlCls + '">' + (s.pnl>=0?'+':'') + '$' + fmt(s.pnl||0, 4) + '</div></div>' +
      '<div class="strat-stat"><div class="s-lbl">Exec%</div><div class="s-val" style="color:' + (s.signals_emitted>0 && (s.trades||0)/s.signals_emitted>0.5 ? 'var(--green)' : 'var(--gold)') + '">' + fmt(s.signals_emitted>0 ? ((s.trades||0)/s.signals_emitted*100) : 0, 0) + '%</div></div>' +
      '</div></div>';
  }
  cont.innerHTML = html;
}

function renderSignals(d) {
  // Removed: Recent Signals section deleted from dashboard (was always 0).
  // Kept as no-op for backward compat.
}

function renderTrades(d) {
  // Removed in v3.5.11: Recent Trades section replaced by Trade History (always visible).
  // Trade History auto-loads via loadHistoryPage() and auto-refreshes page 1 every 5s.
  // This function kept as no-op for backward compat (in case called elsewhere).
}

function renderRisk(d) {
  if (!d.risk) return;
  const r = d.risk;
  const cfg = r.config || {};
  document.getElementById('risk-dd').textContent = (cfg.max_daily_drawdown_pct || '--') + '%';
  document.getElementById('risk-consec').textContent = (r.consecutive_losses_global || 0) + '/' + (cfg.max_consecutive_losses_global || '--');
  document.getElementById('risk-rate').textContent = (r.trades_this_hour || 0) + '/' + (cfg.max_trades_per_hour_global || '--');
  const dailyPnl = r.daily_pnl || 0;
  const dailyEl = document.getElementById('risk-daily-pnl');
  dailyEl.textContent = pnlSign(dailyPnl) + '$' + fmt(dailyPnl, 2);
  dailyEl.style.color = dailyPnl >= 0 ? 'var(--green)' : 'var(--red)';
  document.getElementById('risk-session').textContent = fmt(r.session_age_min || 0, 0) + ' min';
  const disabled = r.disabled_strategies || [];
  document.getElementById('risk-disabled').textContent = disabled.length > 0 ? disabled.join(', ') : 'none';
  document.getElementById('risk-disabled').style.color = disabled.length > 0 ? 'var(--red)' : 'var(--green)';
}

function renderSystem(d) {
  // v3.5.0: Bot status with color coding
  const statusEl = document.getElementById('sys-bot-status');
  const status = d.bot_status || 'UNKNOWN';
  statusEl.textContent = status;
  const statusColors = {
    'ACTIVE': 'var(--green)',
    'IDLE': 'var(--gold)',
    'STAGNANT': 'var(--red)',
    'CASH_STUCK': 'var(--orange)',
    'STARTING': 'var(--blue)',
  };
  statusEl.style.color = statusColors[status] || 'var(--muted)';

  document.getElementById('sys-markets').textContent = d.markets || 0;
  document.getElementById('sys-crypto').textContent = d.crypto_markets || 0;
  const ws = d.ws_status || {};
  const clobEl = document.getElementById('sys-clob');
  clobEl.textContent = ws.clob_connected ? (ws.clob_tokens || 0) + ' tokens' : 'OFFLINE';
  clobEl.style.color = ws.clob_connected ? 'var(--green)' : 'var(--red)';
  const binanceEl = document.getElementById('sys-binance');
  binanceEl.textContent = ws.binance_connected ? 'CONNECTED' : 'OFFLINE';
  binanceEl.style.color = ws.binance_connected ? 'var(--green)' : 'var(--red)';
  if (d.btc_price) {
    const move = d.btc_move || 0;
    document.getElementById('sys-btc').textContent = '$' + fmt(d.btc_price, 0) + ' (' + pnlSign(move) + fmt(move, 3) + '%)';
  }
  document.getElementById('sys-uptime').textContent = fmtUptime(d.uptime_sec || 0);

  // v3.5.0: Last signal/trade timestamps
  const sigEl = document.getElementById('sys-last-signal');
  if (d.last_signal_at) {
    sigEl.textContent = timeAgo(d.last_signal_at);
    const age = Date.now()/1000 - d.last_signal_at;
    sigEl.style.color = age < 300 ? 'var(--green)' : age < 600 ? 'var(--gold)' : 'var(--red)';
  } else {
    sigEl.textContent = 'never';
    sigEl.style.color = 'var(--muted)';
  }
  const tradeEl = document.getElementById('sys-last-trade');
  if (d.last_trade_at) {
    tradeEl.textContent = timeAgo(d.last_trade_at);
  } else {
    tradeEl.textContent = 'never';
    tradeEl.style.color = 'var(--muted)';
  }
}

function renderAlerts(d) {
  const alerts = [];
  const botStatus = d.bot_status || 'UNKNOWN';
  const ws = d.ws_status || {};
  
  // Bot status alert
  if (botStatus === 'STAGNANT') {
    alerts.push({ level: 'error', msg: '⚠️ BOT STAGNANT — No activity for 15+ minutes. Daemon restarting...' });
  } else if (botStatus === 'CASH_STUCK') {
    alerts.push({ level: 'error', msg: '💰 CASH STUCK — Positions blocking trade execution. Restart recommended.' });
  } else if (botStatus === 'IDLE') {
    alerts.push({ level: 'warn', msg: '📊 IDLE — No positions, no trades. Waiting for opportunities...' });
  }
  
  // WS alerts
  if (!ws.clob_connected) {
    alerts.push({ level: 'error', msg: '🔌 CLOB WebSocket DISCONNECTED' });
  }
  if (ws.clob_reconnects > 3) {
    alerts.push({ level: 'warn', msg: '📡 CLOB reconnecting often: ' + ws.clob_reconnects + 'x' });
  }
  if (!ws.binance_connected) {
    alerts.push({ level: 'error', msg: '📊 Binance WebSocket DISCONNECTED' });
  }
  
  // Strategy disabled
  const disabled = d.risk?.disabled_strategies || [];
  if (disabled.length > 0) {
    alerts.push({ level: 'warn', msg: '🛡️ Strategies disabled: ' + disabled.join(', ') });
  }
  
  // High deployment
  const invPct = d.deployed && d.bankroll ? (d.deployed / d.bankroll * 100) : 0;
  if (invPct > 85) {
    alerts.push({ level: 'warn', msg: '📊 High deployment: ' + Math.round(invPct) + '% — consider cash buffer' });
  }
  
  // Render
  const alertDiv = document.getElementById('alerts-container');
  if (alerts.length === 0) {
    alertDiv.style.display = 'none';
    alertDiv.innerHTML = '';
  } else {
    alertDiv.style.display = 'block';
    alertDiv.innerHTML = alerts.map(a => 
      '<div class="alert-banner alert-' + a.level + '">' + a.msg + '</div>'
    ).join('');
  }
}

function togglePanel(id) {
  const el = document.getElementById(id);
  el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

// Trade History: table format, filterable, expandable rows, paginated
let historyPage = 1;
let historyTotalPages = 1;
let historyFilter = 'all'; // 'all' | 'profit' | 'loss'
let historyPanelOpen = false;
let historyAllTrades = []; // cache for filtering
let historyTotalCount = null; // cached total (fetched in background)

// Fetch total trade count in background (cheap: limit=1) — updates badge even when panel collapsed
async function fetchHistoryTotal() {
  try {
    const resp = await fetch('/api/trades?page=1&limit=1');
    if (!resp.ok) return;
    const data = await resp.json();
    historyTotalCount = data.total || 0;
    // Only update badge if panel is closed (when open, loadHistoryPage handles it)
    if (!historyPanelOpen) {
      document.getElementById('history-total').textContent = historyTotalCount;
      document.getElementById('history-page-info').textContent = '[click to expand]';
    }
  } catch (e) {
    // Silent fail — don't spam console
  }
}

async function toggleHistoryPanel() {
  const panel = document.getElementById('history-panel');
  const willShow = panel.style.display === 'none';
  panel.style.display = willShow ? 'block' : 'none';
  historyPanelOpen = willShow;
  document.getElementById('history-page-info').textContent = willShow ? 'Loading...' : '[click to expand]';
  if (willShow) {
    await loadHistoryPage(1);
  }
}

function setHistoryFilter(filter) {
  historyFilter = filter;
  // Update button styles
  const btns = {'all': 'filter-all', 'profit': 'filter-profit', 'loss': 'filter-loss'};
  for (const [k, id] of Object.entries(btns)) {
    const btn = document.getElementById(id);
    if (k === filter) {
      btn.style.background = 'var(--accent)';
      btn.style.color = 'white';
      btn.style.fontWeight = '600';
      btn.style.border = 'none';
    } else {
      btn.style.background = 'var(--card2)';
      btn.style.color = 'var(--text)';
      btn.style.fontWeight = 'normal';
      btn.style.border = '1px solid var(--border)';
    }
  }
  // Re-render with filter
  renderHistoryTable();
}

async function loadHistoryPage(page, silent) {
  if (!historyPanelOpen) return;
  const container = document.getElementById('history-container');
  if (!silent) container.innerHTML = '<div class="empty">Loading page ' + page + '...</div>';
  try {
    const resp = await fetch('/api/trades?page=' + page + '&limit=20');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    historyPage = data.page || 1;
    historyTotalPages = data.total_pages || 1;
    historyAllTrades = data.trades || [];
    document.getElementById('history-total').textContent = data.total || 0;
    document.getElementById('history-page-info').textContent =
      'Page ' + historyPage + ' of ' + historyTotalPages;
    document.getElementById('history-prev').disabled = (historyPage <= 1);
    document.getElementById('history-next').disabled = (historyPage >= historyTotalPages);
    renderHistoryTable();
  } catch (e) {
    container.innerHTML = '<div class="empty">Error loading trades: ' + e.message + '</div>';
  }
}

function renderHistoryTable() {
  const container = document.getElementById('history-container');
  let trades = historyAllTrades;
  let filterLabel = 'Showing all trades';
  if (historyFilter === 'profit') {
    trades = trades.filter(t => t.pnl_dollar > 0).sort((a,b) => b.pnl_dollar - a.pnl_dollar);
    filterLabel = '🏆 Top profit on this page';
  } else if (historyFilter === 'loss') {
    trades = trades.filter(t => t.pnl_dollar < 0).sort((a,b) => a.pnl_dollar - b.pnl_dollar);
    filterLabel = '📉 Top losses on this page';
  }
  document.getElementById('history-filter-info').textContent = filterLabel + ' (' + trades.length + ')';

  if (trades.length === 0) {
    container.innerHTML = '<div class="empty">No trades found</div>';
    return;
  }

  let html = '<table class="tbl"><thead><tr>' +
    '<th>Market</th><th>Side</th><th>Shares</th><th>Invested</th><th>Entry</th><th>Exit</th>' +
    '<th>PnL $</th><th>PnL %</th><th>Reason</th><th>When</th><th>Age</th>' +
    '</tr></thead><tbody>';
  for (const t of trades) {
    const ageSec = t.closed_at - t.opened_at;
    const ageStr = ageSec < 60 ? Math.round(ageSec) + 's' : ageSec < 3600 ? Math.round(ageSec/60) + 'm' : (ageSec/3600).toFixed(1) + 'h';
    const pnlCls = t.pnl_dollar >= 0 ? 'pnl-pos' : 'pnl-neg';
    const safeQ = (t.market_question||'').replace(/"/g,'&quot;');
    const safeR = (t.reason||'').replace(/"/g,'&quot;');
    html += '<tr style="cursor:pointer" onclick="toggleTradeDetail(\\'' + t.id + '\\')">' +
      '<td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + safeQ + '">' + (t.market_question||'').substring(0,35) + '</td>' +
      '<td class="side-' + (t.side||'') + '">' + (t.side||'') + '</td>' +
      '<td>' + fmt(t.shares||0, 1) + '</td>' +
      '<td>$' + fmt(t.invested||0, 2) + '</td>' +
      '<td>$' + fmt(t.entry_price, 4) + '</td>' +
      '<td>$' + fmt(t.exit_price, 4) + '</td>' +
      '<td class="' + pnlCls + '" style="font-weight:600">' + (t.pnl_dollar>=0?'+':'') + '$' + fmt(t.pnl_dollar, 4) + '</td>' +
      '<td class="' + pnlCls + '">' + (t.pnl_percent>=0?'+':'') + fmt(t.pnl_percent, 1) + '%</td>' +
      '<td style="color:var(--muted);font-size:0.6rem;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + safeR + '">' + (t.reason||'') + '</td>' +
      '<td style="color:var(--muted);font-size:0.65rem">' + timeAgo(t.closed_at) + '</td>' +
      '<td style="color:var(--muted);font-size:0.65rem">' + ageStr + '</td>' +
      '</tr>';
    // Expandable detail row (hidden by default)
    const date = new Date(t.closed_at * 1000).toISOString().replace('T',' ').substring(0,19);
    const pairBadge = t.is_pair ? ' <span style="color:var(--purple)">PAIR</span>' : '';
    html += '<tr id="detail-' + t.id + '" style="display:none">' +
      '<td colspan="11" style="background:var(--card2);padding:10px 12px;font-size:0.7rem;color:var(--muted)">' +
      '<div style="margin-bottom:4px"><b style="color:var(--text)">Full market:</b> ' + (t.market_question||'') + '</div>' +
      '<div style="margin-bottom:4px"><b style="color:var(--text)">Trade ID:</b> ' + t.id + pairBadge + ' · <b style="color:var(--text)">Strategy:</b> ' + t.strategy + '</div>' +
      '<div style="margin-bottom:4px"><b style="color:var(--text)">Entry:</b> $' + fmt(t.entry_price,4) + ' → <b style="color:var(--text)">Exit:</b> $' + fmt(t.exit_price,4) + '</div>' +
      '<div style="margin-bottom:4px"><b style="color:var(--text)">Invested:</b> $' + fmt(t.invested,2) + ' · <b style="color:var(--text)">Shares:</b> ' + fmt(t.shares,2) + '</div>' +
      '<div style="margin-bottom:4px"><b style="color:var(--text)">PnL:</b> <span class="' + pnlCls + '">' + (t.pnl_dollar>=0?'+':'') + '$' + fmt(t.pnl_dollar,4) + ' (' + (t.pnl_percent>=0?'+':'') + fmt(t.pnl_percent,2) + '%)</span></div>' +
      '<div style="margin-bottom:4px"><b style="color:var(--text)">Reason:</b> ' + (t.reason||'') + '</div>' +
      '<div><b style="color:var(--text)">Closed:</b> ' + date + ' UTC (' + timeAgo(t.closed_at) + ' ago)</div>' +
      '</td></tr>';
  }
  html += '</tbody></table>';
  container.innerHTML = html;
}

function toggleTradeDetail(id) {
  const row = document.getElementById('detail-' + id);
  if (row) row.style.display = row.style.display === 'none' ? '' : 'none';
}

async function historyPrevPage() {
  if (historyPage > 1) await loadHistoryPage(historyPage - 1);
}
async function historyNextPage() {
  if (historyPage < historyTotalPages) await loadHistoryPage(historyPage + 1);
}

function timeAgo(ts) {
  if (!ts) return '--';
  const s = Math.floor(Date.now()/1000 - ts);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s/60) + 'm';
  if (s < 86400) return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
  return Math.floor(s/86400) + 'd';
}

function updateBotStatus(d) {
  const status = d.bot_status || 'UNKNOWN';
  const badge = document.getElementById('bot-status-badge');
  const text = document.getElementById('bot-status-text');
  const colors = {
    'ACTIVE': 'var(--green)',
    'IDLE': 'var(--orange)',
    'STAGNANT': 'var(--red)',
    'CASH_STUCK': 'var(--red)',
    'STARTING': 'var(--gold)',
    'UNKNOWN': 'var(--muted)'
  };
  badge.style.background = colors[status] || 'var(--muted)';
  text.textContent = status;
  text.style.color = colors[status] || 'var(--muted)';
  
  // Update version dynamically
  const versionEl = document.getElementById('bot-version');
  if (versionEl && d.version) versionEl.textContent = d.version;
}

function renderMarketDist(d) {
  const cats = d.market_categories || {};
  const cont = document.getElementById('market-dist');
  const colorMap = {
    'crypto': 'crypto', 'sports_match': 'sports', 'sports_total': 'sports_total',
    'sports_spread': 'sports', 'economics': 'economics', 'politics': 'politics',
    'other': 'other', 'entertainment': 'other'
  };
  let html = '';
  for (const [cat, count] of Object.entries(cats)) {
    const cls = colorMap[cat] || 'other';
    html += '<div class="cat-chip cat-' + cls + '">' + cat.replace('_',' ') + ': ' + count + '</div>';
  }
  cont.innerHTML = html || '<span style="color:var(--muted);font-size:0.6rem">loading...</span>';
}

function renderSystem(d) {
  document.getElementById('sys-markets').textContent = d.markets || 0;
  document.getElementById('sys-crypto').textContent = d.crypto_markets || 0;
  const ws = d.ws_status || {};
  const clobEl = document.getElementById('sys-clob');
  clobEl.textContent = ws.clob_connected ? (ws.clob_tokens || 0) + ' tokens' : 'OFFLINE';
  clobEl.style.color = ws.clob_connected ? 'var(--green)' : 'var(--red)';
  const binanceEl = document.getElementById('sys-binance');
  binanceEl.textContent = ws.binance_connected ? 'CONNECTED' : 'OFFLINE';
  binanceEl.style.color = ws.binance_connected ? 'var(--green)' : 'var(--red)';
  if (d.btc_price) {
    const move = d.btc_move || 0;
    document.getElementById('sys-btc').textContent = '$' + fmt(d.btc_price, 0) + ' (' + pnlSign(move) + fmt(move*100, 2) + '%)';
  }
  // Last signal
  const lastSig = d.last_signal_at;
  document.getElementById('sys-last-signal').textContent = timeAgo(lastSig);
  document.getElementById('sys-last-signal').style.color = !lastSig ? 'var(--muted)' : (Date.now()/1000 - lastSig > 600 ? 'var(--orange)' : 'var(--green)');
  // Last trade
  const lastTrade = d.last_trade_at;
  document.getElementById('sys-last-trade').textContent = timeAgo(lastTrade);
  document.getElementById('sys-last-trade').style.color = !lastTrade ? 'var(--muted)' : (Date.now()/1000 - lastTrade > 3600 ? 'var(--orange)' : 'var(--green)');
  document.getElementById('sys-uptime').textContent = fmtUptime(d.uptime_sec || 0);
  // Market distribution
  renderMarketDist(d);
}

async function loadInstanceLabel() {
  try {
    const r = await fetch('/api/health');
    const d = await r.json();
    const label = (d.instance_label || d.mode || 'bot');
    const cap = label.charAt(0).toUpperCase() + label.slice(1);
    document.getElementById('instance-label').textContent = cap;
    document.title = '🔍 ' + cap + ' - PolyClaw v' + d.version;
  } catch(e) {}
}
loadInstanceLabel();

async function refresh() {
  try {
    const d = await fetchWithRetry('/api/stats');
    if (d) {
      lastData = d;
      renderKPIs(d);
      renderPositions(d);
      renderStrategies(d);
      renderRisk(d);
      renderSystem(d);
      refreshSuccessCount++;
      document.getElementById('last-update').innerHTML = new Date().toLocaleTimeString() + ' <span id="update-secs-ago" class="update-counter">(0s ago)</span>';
      _lastUpdateTs = Date.now();
      updateConnectionStatus(true);
      // Auto-refresh Trade History page 1 (silent — no loading spinner)
      // Only refresh if panel is open AND user is on page 1 with 'all' filter
      if (historyPanelOpen && historyPage === 1 && historyFilter === 'all') {
        loadHistoryPage(1, true);
      } else if (!historyPanelOpen) {
        // Panel collapsed: just refresh the total count badge (cheap)
        fetchHistoryTotal();
      }
    } else {
      refreshFailCount++;
      updateConnectionStatus(false);
    }
  } catch(e) {
    refreshFailCount++;
    updateConnectionStatus(false);
  }
}

// Initial load: fetch stats + trade history total count
refresh();
fetchHistoryTotal();
setInterval(refresh, REFRESH_MS);
let _lastUpdateTs = Date.now();
setInterval(() => {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString();
  const ago = Math.round((Date.now() - (_lastUpdateTs || Date.now())) / 1000);
  const el = document.getElementById('update-secs-ago');
  if (el) el.textContent = '(' + ago + 's ago)';
}, 1000);
</script>
</body>
</html>"""
