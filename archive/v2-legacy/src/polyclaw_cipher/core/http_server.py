"""HTTP server — JSON API + full HTML trading dashboard."""
from __future__ import annotations
import asyncio
import json
import logging
import time
from asyncio import StreamReader, StreamWriter

logger = logging.getLogger(__name__)

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PolyClaw-Cipher 🔍 Dashboard</title>
<style>
:root {
  --bg: #0a0e14; --card: #131820; --card2: #0f141c; --border: #1e2836;
  --text: #c8d6e5; --muted: #6b7d91; --dim: #4a5a6e;
  --green: #00e676; --green2: #2e7d32; --red: #ff5252; --red2: #c62828;
  --blue: #448aff; --purple: #bb86fc; --orange: #ff9100; --gold: #ffd740;
  --radius: 10px;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  background: var(--bg); color: var(--text);
  font-family: 'SF Mono','Segoe UI',system-ui,sans-serif;
  min-height: 100vh; line-height: 1.4;
}
.wrap { max-width: 1200px; margin: 0 auto; padding: 14px 20px; }

/* Header */
.hdr {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 0; border-bottom: 1px solid var(--border); margin-bottom: 14px;
}
.hdr h1 { font-size: 1.25rem; font-weight: 800; }
.hdr .sub { font-size: 0.65rem; color: var(--muted); margin-top: 2px; }
.hdr .live-dot {
  width: 8px; height: 8px; border-radius: 50%; display: inline-block;
  margin-right: 5px; animation: pulse 2s infinite;
}
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
.hdr .live { color: var(--green); font-size: 0.72rem; font-weight: 600; }
.hdr .clock { color: var(--muted); font-size: 0.72rem; }

/* KPI Row */
.kpi-row { display: grid; grid-template-columns: repeat(6,1fr); gap: 10px; margin-bottom: 14px; }
.kpi {
  background: var(--card); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 12px 14px; text-align: center;
}
.kpi .lbl { font-size: 0.58rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
.kpi .val { font-size: 1.35rem; font-weight: 800; margin-top: 4px; }
.kpi .delta { font-size: 0.62rem; margin-top: 2px; }
.kpi .delta.pos { color: var(--green); }
.kpi .delta.neg { color: var(--red); }
.kpi .delta.neu { color: var(--muted); }
.val.green { color: var(--green); }
.val.red { color: var(--red); }
.val.gold { color: var(--gold); }
.val.blue { color: var(--blue); }

/* Two column layout */
.cols { display: grid; grid-template-columns: 1.3fr 1fr; gap: 14px; margin-bottom: 14px; }
.cols.equal { grid-template-columns: 1fr 1fr; }

/* Card */
.card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 14px; overflow: hidden;
}
.card-title {
  font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.5px; color: var(--muted); margin-bottom: 10px;
  display: flex; align-items: center; justify-content: space-between;
}
.card-title .badge {
  font-size: 0.55rem; padding: 2px 8px; border-radius: 4px;
  background: var(--card2); color: var(--text);
}

/* Positions table */
.tbl { width: 100%; border-collapse: collapse; font-size: 0.68rem; }
.tbl th {
  text-align: left; padding: 6px 8px; color: var(--muted);
  font-weight: 600; text-transform: uppercase; font-size: 0.55rem;
  letter-spacing: 0.5px; border-bottom: 1px solid var(--border);
}
.tbl td { padding: 7px 8px; border-bottom: 1px solid rgba(30,40,54,0.4); }
.tbl tr:hover td { background: rgba(255,255,255,0.02); }
.tbl .pnl-pos { color: var(--green); font-weight: 600; }
.tbl .pnl-neg { color: var(--red); font-weight: 600; }
.tbl .side-YES { color: var(--green); }
.tbl .side-NO { color: var(--red); }
.tag {
  display: inline-block; padding: 1px 6px; border-radius: 3px;
  font-size: 0.52rem; font-weight: 600; text-transform: uppercase;
}
.tag.scalper { background: rgba(68,138,255,0.15); color: var(--blue); }
.tag.arbitrage { background: rgba(0,230,118,0.15); color: var(--green); }
.tag.momentum { background: rgba(187,134,252,0.15); color: var(--purple); }

/* Strategy cards */
.strat-grid { display: grid; gap: 8px; }
.strat {
  background: var(--card2); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 12px;
}
.strat-head { display: flex; align-items: center; justify-content: space-between; }
.strat-name { font-weight: 700; font-size: 0.78rem; }
.strat-name .icon { margin-right: 4px; }
.strat-stats { display: grid; grid-template-columns: repeat(4,1fr); gap: 6px; margin-top: 8px; }
.strat-stat .s-lbl { font-size: 0.5rem; color: var(--muted); text-transform: uppercase; }
.strat-stat .s-val { font-size: 0.78rem; font-weight: 600; }

/* Equity bar */
.equity-bar {
  height: 6px; background: rgba(255,255,255,0.05); border-radius: 3px;
  margin-top: 6px; overflow: hidden; display: flex;
}
.eq-fill-cash { background: var(--blue); height: 100%; }
.eq-fill-pos { background: var(--green); height: 100%; }

/* Crypto ticker */
.ticker { display: flex; gap: 12px; margin-bottom: 14px; }
.ticker-item {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 14px; flex: 1; text-align: center;
}
.ticker-item .t-asset { font-size: 0.6rem; color: var(--muted); text-transform: uppercase; }
.ticker-item .t-price { font-size: 1rem; font-weight: 700; }
.ticker-item .t-move { font-size: 0.58rem; }

/* Empty state */
.empty { color: var(--dim); text-align: center; padding: 20px; font-size: 0.72rem; font-style: italic; }

/* Scrollable trade list */
.trade-list { max-height: 220px; overflow-y: auto; }
.trade-list::-webkit-scrollbar { width: 4px; }
.trade-list::-webkit-scrollbar-track { background: transparent; }
.trade-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

@media (max-width: 900px) {
  .kpi-row { grid-template-columns: repeat(3,1fr); }
  .cols { grid-template-columns: 1fr; }
  .cols.equal { grid-template-columns: 1fr; }
  .ticker { flex-wrap: wrap; }
}
</style>
</head>
<body>
<div class="wrap">
  <!-- Header -->
  <div class="hdr">
    <div>
      <h1>🔍 PolyClaw-Cipher</h1>
      <div class="sub">Polymarket Paper Trading · VPS 3.107.53.103 · <span id="mode">paper</span></div>
    </div>
    <div style="text-align:right">
      <div class="live"><span class="live-dot" style="background:var(--green)"></span>LIVE</div>
      <div class="clock" id="clock">--:--:--</div>
    </div>
  </div>

  <!-- Crypto Ticker -->
  <div class="ticker" id="ticker">
    <div class="ticker-item"><div class="t-asset">BTC</div><div class="t-price" id="btc-price">--</div><div class="t-move" id="btc-move">--</div></div>
    <div class="ticker-item"><div class="t-asset">ETH</div><div class="t-price" id="eth-price">--</div><div class="t-move" id="eth-move">--</div></div>
    <div class="ticker-item"><div class="t-asset">SOL</div><div class="t-price" id="sol-price">--</div><div class="t-move" id="sol-move">--</div></div>
  </div>

  <!-- KPI Row -->
  <div class="kpi-row">
    <div class="kpi"><div class="lbl">Equity</div><div class="val gold" id="kpi-equity">$0.00</div><div class="delta neu" id="kpi-equity-delta">--</div></div>
    <div class="kpi"><div class="lbl">Cash</div><div class="val blue" id="kpi-cash">$0.00</div><div class="delta neu" id="kpi-cash-pct">--</div></div>
    <div class="kpi"><div class="lbl">In Positions</div><div class="val green" id="kpi-invested">$0.00</div><div class="delta neu" id="kpi-invested-pct">--</div></div>
    <div class="kpi"><div class="lbl">PnL</div><div class="val" id="kpi-pnl">$0.00</div><div class="delta neu" id="kpi-pnl-pct">--</div></div>
    <div class="kpi"><div class="lbl">Trades</div><div class="val" id="kpi-trades">0</div><div class="delta neu" id="kpi-winrate">--</div></div>
    <div class="kpi"><div class="lbl">Signals</div><div class="val" id="kpi-signals">0</div><div class="delta neu" id="kpi-markets">--</div></div>
  </div>

  <!-- Equity bar -->
  <div style="margin-bottom:14px">
    <div style="display:flex;justify-content:space-between;font-size:0.58rem;color:var(--muted);margin-bottom:4px;">
      <span>Capital Allocation</span>
      <span id="eq-label">--</span>
    </div>
    <div class="equity-bar">
      <div class="eq-fill-cash" id="bar-cash" style="width:0%"></div>
      <div class="eq-fill-pos" id="bar-pos" style="width:0%"></div>
    </div>
  </div>

  <!-- Positions + Strategy -->
  <div class="cols">
    <div class="card">
      <div class="card-title">
        <span>📊 Open Positions</span>
        <span class="badge" id="pos-count">0</span>
      </div>
      <div id="positions-container">
        <div class="empty">No open positions</div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">
        <span>🎯 Strategy Stats</span>
      </div>
      <div class="strat-grid" id="strategies-container">
        <div class="empty">Loading...</div>
      </div>
    </div>
  </div>

  <!-- Recent Trades -->
  <div class="card" style="margin-bottom:14px">
    <div class="card-title">
      <span>📝 Recent Trades</span>
      <span class="badge" id="trade-count">0</span>
    </div>
    <div class="trade-list" id="trades-container">
      <div class="empty">No trades yet</div>
    </div>
  </div>

  <!-- Risk Status -->
  <div class="cols equal">
    <div class="card">
      <div class="card-title"><span>🛡️ Risk Status</span></div>
      <div id="risk-container" style="font-size:0.7rem;">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;">
          <div><span style="color:var(--muted)">Daily DD Limit:</span> <b id="risk-dd">30%</b></div>
          <div><span style="color:var(--muted)">Consec. Losses:</span> <b id="risk-losses">0/8</b></div>
          <div><span style="color:var(--muted)">Max Trades/hr:</span> <b id="risk-rate">0/60</b></div>
          <div><span style="color:var(--muted)">Status:</span> <b id="risk-status" style="color:var(--green)">OK</b></div>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-title"><span>📈 Market Scan</span></div>
      <div style="font-size:0.7rem;display:grid;grid-template-columns:1fr 1fr;gap:6px;">
        <div><span style="color:var(--muted)">Total Markets:</span> <b id="scan-total">0</b></div>
        <div><span style="color:var(--muted)">Crypto Up/Down:</span> <b id="scan-crypto">0</b></div>
        <div><span style="color:var(--muted)">Last Scan:</span> <b id="scan-time">--</b></div>
        <div><span style="color:var(--muted)">Scan Interval:</span> <b>20s</b></div>
      </div>
    </div>
  </div>
</div>

<script>
const REFRESH_MS = 5000;
let prevEquity = 0;

async function fetchStats() {
  try {
    const r = await fetch('/api/stats');
    const d = await r.json();
    render(d);
  } catch(e) {
    console.error('Fetch error:', e);
  }
}

function fmt(n, dec=2) {
  if (n === null || n === undefined) return '--';
  return Number(n).toFixed(dec);
}
function fmtUsd(n) { return '$' + fmt(n, 2); }
function timeAgo(ts) {
  if (!ts) return '--';
  const s = Math.floor(Date.now()/1000 - ts);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  return Math.floor(s/3600) + 'h ago';
}
function pnlColor(v) { return v > 0 ? 'pos' : v < 0 ? 'neg' : 'neu'; }
function pnlSign(v) { return v >= 0 ? '+' : ''; }

function render(d) {
  // Clock
  document.getElementById('clock').textContent = new Date().toLocaleTimeString();
  document.getElementById('mode').textContent = d.mode || 'paper';

  // Crypto ticker
  const bp = d.btc_price || 0;
  document.getElementById('btc-price').textContent = bp ? '$' + bp.toFixed(0) : '--';
  const bm = d.btc_move || 0;
  const bmEl = document.getElementById('btc-move');
  bmEl.textContent = (bm >= 0 ? '+' : '') + bm.toFixed(4) + '%';
  bmEl.style.color = bm >= 0 ? 'var(--green)' : 'var(--red)';

  // ETH/SOL not in API yet, show placeholder
  document.getElementById('eth-price').textContent = '--';
  document.getElementById('sol-price').textContent = '--';

  // KPIs
  const equity = d.bankroll || 0;
  const cash = d.cash || 0;
  const invested = equity - cash;
  const pnl = d.pnl || 0;
  const initial = 25.0;
  const pnlPct = initial > 0 ? (pnl / initial * 100) : 0;

  document.getElementById('kpi-equity').textContent = fmtUsd(equity);
  const eqDelta = document.getElementById('kpi-equity-delta');
  if (prevEquity > 0) {
    const diff = equity - prevEquity;
    eqDelta.textContent = (diff >= 0 ? '+' : '') + fmt(diff, 4);
    eqDelta.className = 'delta ' + pnlColor(diff);
  }
  prevEquity = equity;

  document.getElementById('kpi-cash').textContent = fmtUsd(cash);
  const cashPct = equity > 0 ? (cash / equity * 100) : 0;
  document.getElementById('kpi-cash-pct').textContent = cashPct.toFixed(1) + '% idle';

  document.getElementById('kpi-invested').textContent = fmtUsd(invested);
  const invPct = equity > 0 ? (invested / equity * 100) : 0;
  document.getElementById('kpi-invested-pct').textContent = invPct.toFixed(1) + '% deployed';

  const pnlEl = document.getElementById('kpi-pnl');
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + fmtUsd(pnl);
  pnlEl.className = 'val ' + (pnl >= 0 ? 'green' : 'red');
  document.getElementById('kpi-pnl-pct').textContent = (pnl >= 0 ? '+' : '') + pnlPct.toFixed(2) + '%';

  document.getElementById('kpi-trades').textContent = d.trades || 0;
  document.getElementById('kpi-winrate').textContent = (d.win_rate || 0).toFixed(1) + '% win';

  document.getElementById('kpi-signals').textContent = d.signals || 0;
  document.getElementById('kpi-markets').textContent = (d.markets || 0) + ' markets';

  // Equity bar
  document.getElementById('bar-cash').style.width = cashPct + '%';
  document.getElementById('bar-pos').style.width = invPct + '%';
  document.getElementById('eq-label').textContent =
    cashPct.toFixed(0) + '% cash / ' + invPct.toFixed(0) + '% positions';

  // Positions
  const posCont = document.getElementById('positions-container');
  const positions = d.open_positions || [];
  document.getElementById('pos-count').textContent = positions.length;
  if (positions.length === 0) {
    posCont.innerHTML = '<div class="empty">No open positions</div>';
  } else {
    let html = '<table class="tbl"><thead><tr>' +
      '<th>Market</th><th>Side</th><th>Entry</th><th>Shares</th><th>Invested</th><th>Strat</th><th>Opened</th>' +
      '</tr></thead><tbody>';
    for (const p of positions) {
      const ago = timeAgo(p.opened_at);
      const strat = p.strategy || '';
      html += '<tr>' +
        '<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + (p.market_question || p.market_condition_id || '').substring(0,40) + '</td>' +
        '<td class="side-' + (p.side || '') + '">' + (p.side || '') + '</td>' +
        '<td>$' + fmt(p.entry_price, 4) + '</td>' +
        '<td>' + fmt(p.shares, 1) + '</td>' +
        '<td>$' + fmt(p.invested, 2) + '</td>' +
        '<td><span class="tag ' + strat + '">' + strat + '</span></td>' +
        '<td style="color:var(--muted)">' + ago + '</td>' +
        '</tr>';
    }
    html += '</tbody></table>';
    posCont.innerHTML = html;
  }

  // Strategies
  const stratCont = document.getElementById('strategies-container');
  const strats = d.strategies || [];
  if (strats.length === 0) {
    stratCont.innerHTML = '<div class="empty">No strategies</div>';
  } else {
    let html = '';
    for (const s of strats) {
      const icon = s.name === 'scalper' ? '⚡' : s.name === 'arbitrage' ? '🔄' : '🚀';
      const wr = s.win_rate || 0;
      const pnlCls = s.pnl >= 0 ? 'green' : 'red';
      html += '<div class="strat"><div class="strat-head">' +
        '<div class="strat-name"><span class="icon">' + icon + '</span>' + s.name + '</div>' +
        '<div class="tag ' + s.name + '">' + (s.trades > 0 ? wr.toFixed(0) + '% WR' : 'idle') + '</div>' +
        '</div><div class="strat-stats">' +
        '<div class="strat-stat"><div class="s-lbl">Signals</div><div class="s-val">' + s.signals_emitted + '</div></div>' +
        '<div class="strat-stat"><div class="s-lbl">Trades</div><div class="s-val">' + s.trades + '</div></div>' +
        '<div class="strat-stat"><div class="s-lbl">W/L</div><div class="s-val">' + s.wins + '/' + s.losses + '</div></div>' +
        '<div class="strat-stat"><div class="s-lbl">PnL</div><div class="s-val ' + pnlCls + '">' + (s.pnl >= 0 ? '+' : '') + '$' + fmt(s.pnl, 4) + '</div></div>' +
        '</div></div>';
    }
    stratCont.innerHTML = html;
  }

  // Trades
  const tradeCont = document.getElementById('trades-container');
  const trades = d.recent_trades || [];
  document.getElementById('trade-count').textContent = trades.length;
  if (trades.length === 0) {
    tradeCont.innerHTML = '<div class="empty">No trades yet</div>';
  } else {
    let html = '<table class="tbl"><thead><tr>' +
      '<th>Market</th><th>Side</th><th>Entry</th><th>Exit</th><th>PnL</th><th>%</th><th>Strat</th><th>Reason</th>' +
      '</tr></thead><tbody>';
    for (const t of trades.slice().reverse()) {
      const pnlCls = t.pnl_dollar >= 0 ? 'pnl-pos' : 'pnl-neg';
      const strat = t.strategy || '';
      html += '<tr>' +
        '<td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + (t.market_question || '').substring(0,35) + '</td>' +
        '<td class="side-' + (t.side || '') + '">' + (t.side || '') + '</td>' +
        '<td>$' + fmt(t.entry_price, 4) + '</td>' +
        '<td>$' + fmt(t.exit_price, 4) + '</td>' +
        '<td class="' + pnlCls + '">' + (t.pnl_dollar >= 0 ? '+' : '') + '$' + fmt(t.pnl_dollar, 4) + '</td>' +
        '<td class="' + pnlCls + '">' + (t.pnl_percent >= 0 ? '+' : '') + fmt(t.pnl_percent, 1) + '%</td>' +
        '<td><span class="tag ' + strat + '">' + strat + '</span></td>' +
        '<td style="color:var(--muted);font-size:0.58rem;max-width:150px;overflow:hidden;text-overflow:ellipsis;">' + (t.reason || '') + '</td>' +
        '</tr>';
    }
    html += '</tbody></table>';
    tradeCont.innerHTML = html;
  }

  // Risk
  document.getElementById('risk-losses').textContent = (d.consecutive_losses || 0) + '/8';
  const riskStatus = document.getElementById('risk-status');
  if ((d.consecutive_losses || 0) >= 6) {
    riskStatus.textContent = 'WARNING';
    riskStatus.style.color = 'var(--orange)';
  } else {
    riskStatus.textContent = 'OK';
    riskStatus.style.color = 'var(--green)';
  }

  // Scan
  document.getElementById('scan-total').textContent = d.markets || 0;
  document.getElementById('scan-crypto').textContent = d.crypto_markets || 0;
}

// Init
fetchStats();
setInterval(fetchStats, REFRESH_MS);
setInterval(() => {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString();
}, 1000);
</script>
</body>
</html>"""


class HTTPServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8080, get_stats=None):
        self.host = host
        self.port = port
        self.get_stats = get_stats
        self._server = None

    async def start(self):
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        logger.info("Web dashboard on http://%s:%d", self.host, self.port)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: StreamReader, writer: StreamWriter):
        try:
            req = await asyncio.wait_for(reader.readline(), timeout=5.0)
            path = req.decode().split(" ")[1] if b" " in req else "/"

            if path == "/api/stats" and self.get_stats:
                stats = self.get_stats()
                body = json.dumps(stats, default=str).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nAccess-Control-Allow-Origin: *\r\n\r\n%s" % (len(body), body))
            elif path == "/" or path == "/dashboard":
                body = DASHBOARD_HTML.encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")
        except Exception:
            pass
        finally:
            writer.close()
