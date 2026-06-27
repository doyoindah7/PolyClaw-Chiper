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

import asyncio
import logging
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


class HTTPServer:
    """FastAPI HTTP server with v3-only dashboard."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8082,
        get_stats=None,
        config: dict[str, Any] | None = None,
    ):
        self.host = host
        self.port = port
        self.get_stats = get_stats
        self.config = config or {}
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
            return DASHBOARD_HTML

        @self.app.get("/api/stats")
        async def stats():
            if self.get_stats:
                return JSONResponse(self.get_stats())
            return JSONResponse({"error": "stats callback not set"}, status_code=500)

        @self.app.get("/api/health")
        async def health():
            # Unprotected: safe to expose for docker / cluster healthchecks
            return {
                "status": "ok",
                "version": "3.5.0",
                "uptime_sec": int(time.time() - (self._start_time or time.time())),
            }

        @self.app.get("/api/config")
        async def config_endpoint():
            return JSONResponse(self.config)

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
<title>PolyClaw-Cipher v3.5.0 🔍</title>
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
  <div class="hdr">
    <div>
      <h1>🔍 PolyClaw-Cipher v3.5.0</h1>
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

  <!-- Recent Trades -->
  <div class="card" style="margin-bottom:14px">
    <div class="card-title">
      <span>📝 Recent Trades (closed)</span>
      <span class="badge" id="trade-count">0</span>
    </div>
    <div class="scroll-list" id="trades-container">
      <div class="empty">No closed trades yet</div>
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

function renderKPIs(d) {
  const bankroll = d.bankroll || 0;
  const cash = d.cash || 0;
  const invested = d.deployed !== undefined ? d.deployed : (bankroll - cash);
  const pnl = d.pnl || 0;
  const pnlPct = (pnl / INITIAL_BANKROLL * 100);
  const positions = d.open_positions || [];
  const trades = d.trades || 0;
  const winRate = d.win_rate || 0;

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
  const cont = document.getElementById('positions-container');
  const positions = d.open_positions || [];
  document.getElementById('pos-count').textContent = positions.length;
  if (positions.length === 0) {
    cont.innerHTML = '<div class="empty">No open positions</div>';
    return;
  }
  let html = '<table class="tbl"><thead><tr>' +
    '<th>Market</th><th>Side</th><th>Strat</th><th>Entry</th><th>Cur</th><th>Invested</th><th>Cur Val</th><th>Unreal P&L</th><th>Age</th>' +
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
      '<td><span class="tag ' + strat + '">' + strat + '</span>' + pairBadge + '</td>' +
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
      '<div class="strat-stat"><div class="s-lbl">WR</div><div class="s-val">' + fmt(wr, 0) + '%</div></div>' +
      '</div></div>';
  }
  cont.innerHTML = html;
}

function renderTrades(d) {
  const cont = document.getElementById('trades-container');
  const trades = d.recent_trades || [];
  document.getElementById('trade-count').textContent = trades.length;
  if (trades.length === 0) {
    cont.innerHTML = '<div class="empty">No closed trades yet — positions will close on TP/SL/resolution</div>';
    return;
  }
  let html = '<table class="tbl"><thead><tr>' +
    '<th>Market</th><th>Strat</th><th>Side</th><th>Entry</th><th>Exit</th><th>PnL $</th><th>PnL %</th><th>Reason</th><th>When</th>' +
    '</tr></thead><tbody>';
  for (const t of trades.slice().reverse()) {
    const pnlCls = t.pnl_dollar >= 0 ? 'pnl-pos' : 'pnl-neg';
    const strat = t.strategy || '';
    html += '<tr>' +
      '<td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + (t.market_question||'').replace(/"/g,'&quot;') + '">' + (t.market_question||'').substring(0,35) + '</td>' +
      '<td><span class="tag ' + strat + '">' + strat + '</span></td>' +
      '<td class="side-' + (t.side||'') + '">' + (t.side||'') + '</td>' +
      '<td>$' + fmt(t.entry_price, 4) + '</td>' +
      '<td>$' + fmt(t.exit_price, 4) + '</td>' +
      '<td class="' + pnlCls + '">' + (t.pnl_dollar>=0?'+':'') + '$' + fmt(t.pnl_dollar, 2) + '</td>' +
      '<td class="' + pnlCls + '">' + (t.pnl_percent>=0?'+':'') + fmt(t.pnl_percent, 1) + '%</td>' +
      '<td style="color:var(--muted);font-size:0.62rem;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + (t.reason||'').replace(/"/g,'&quot;') + '">' + (t.reason||'') + '</td>' +
      '<td style="color:var(--muted)">' + timeAgo(t.closed_at) + '</td>' +
      '</tr>';
  }
  html += '</tbody></table>';
  cont.innerHTML = html;
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

async function refresh() {
  try {
    const d = await fetchWithRetry('/api/stats');
    if (d) {
      lastData = d;
      renderKPIs(d);
      renderPositions(d);
      renderStrategies(d);
      renderTrades(d);
      renderRisk(d);
      renderSystem(d);
      refreshSuccessCount++;
      document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
      updateConnectionStatus(true);
    } else {
      refreshFailCount++;
      updateConnectionStatus(false);
    }
  } catch(e) {
    refreshFailCount++;
    updateConnectionStatus(false);
  }
}

refresh();
setInterval(refresh, REFRESH_MS);
setInterval(() => {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString();
}, 1000);
</script>
</body>
</html>"""
