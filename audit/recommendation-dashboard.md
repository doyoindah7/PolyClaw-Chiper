# Rekomendasi Perbaikan Dashboard — PolyClaw-Cipher v3.4.3

**Auditor:** Arena.ai Agent Mode  
**Date:** 2026-06-27  
**File:** `src/polyclaw_cipher_v3/core/http_server.py` (embedded HTML)  
**Version reviewed:** Dashboard embedded in http_server.py, REFRESH_MS=5000

---

## 📋 Executive Summary

Dashboard sudah functional dengan layout basic (KPI cards, positions table, strategy cards, trades table, risk/system status). Tapi ada **3 category issues**:

1. **🔴 Critical Bug:** Strategy stats all zeros (documented in `audit-by-arena.md`) — affects dashboard strategy cards
2. **🟡 Missing Information:** Tidak ada stagnation alerts, activity metrics, cash flow visualization, historical context
3. **🟢 UX/Polish:** Version display wrong, better alerts, improved tables, missing quality-of-life features

---

## 🔴 Critical: Strategy Stats Showing Zeros

**Already documented** in `audit-by-arena.md`. Dashboard strategy cards show all zeros because `_build_stats_sync()` reads in-memory counters (reset on restart) instead of DB.

**Fix:** Implement Option A from audit-by-arena.md — enrich `_build_stats_sync()` with `per_strategy_stats()` from DB.

**After fix, update dashboard strategy cards to show:**
- Real signals count from DB
- Real trades/W/L/PnL from DB  
- Win rate % with color coding (green ≥55%, yellow 40-54%, red <40%)
- Confidence avg (from signals table)
- Execution rate (executed/signals %)

---

## 🟡 Missing Information — Key Metrics Not Shown

### Issue 1: No Activity/Stagnation Indicators

**Current:** Dashboard shows LIVE/OFFLINE only based on API fetch success.  
**Missing:** No indication if bot is "alive but stuck" (stagnation).

**Recommended additions:**
```javascript
// Add to renderSystem() or create new renderActivity()
<div class="status-item">
  <div class="s-lbl">Last Signal</div>
  <div class="s-val" id="act-last-signal">--</div>
</div>
<div class="status-item">
  <div class="s-lbl">Last Trade</div>
  <div class="s-val" id="act-last-trade">--</div>
</div>
<div class="status-item">
  <div class="s-lbl">Signals/Min</div>
  <div class="s-val" id="act-signal-rate">--</div>
</div>
<div class="status-item">
  <div class="s-lbl">Status</div>
  <div class="s-val" id="act-bot-status">--</div>  // ACTIVE / STAGNANT / IDLE
</div>
```

**Bot status logic:**
- ACTIVE: Recent activity (trade/signal in last 5 min)
- IDLE: No positions, no recent trades, cash available (legitimate)
- STAGNANT: No signals/trades for 15+ min despite opportunities (PROBLEM)

### Issue 2: No Cash Flow / Position Age Info

**Current:** Shows invested amount, shows unrealized P&L.  
**Missing:** How long positions have been open, cash locked %, expected resolution time.

**Recommended additions to positions table:**
```javascript
// New columns for positions table
<th>Opened</th>         // When (timestamp or relative)
<th>Hours Open</th>     // Calculated: (now - opened_at) / 3600
<th>Expected Close</th> // From market data (if available)
<th>Cash Locked</th>    // This position's invested amount
```

**Color coding for position age:**
- < 1h: green (fresh)
- 1-6h: yellow (normal)
- 6-24h: orange (getting old)
- > 24h: red (potential resolution stuck)

### Issue 3: No Recent Signals Log

**Current:** Only shows closed trades.  
**Missing:** Recent signal generation — what strategies are firing, what markets triggered signals, what was rejected and why.

**Recommended: Add "Recent Signals" panel (collapsible)**
```javascript
// New panel after Recent Trades
<div class="card" style="margin-bottom:14px">
  <div class="card-title" style="cursor:pointer" onclick="togglePanel('signals-panel')">
    <span>📡 Recent Signals</span>
    <span class="badge" id="signal-count">0</span>
    <span style="font-size:0.6rem;color:var(--muted)">[click to expand]</span>
  </div>
  <div class="scroll-list" id="signals-panel" style="display:none">
    <!-- signals table -->
  </div>
</div>

// New renderSignals function
function renderSignals(d) {
  const signals = d.recent_signals || [];  // Add to API
  const cont = document.getElementById('signals-panel');
  document.getElementById('signal-count').textContent = signals.length;
  if (signals.length === 0) {
    cont.innerHTML = '<div class="empty">No signals generated</div>';
    return;
  }
  // Show: timestamp, strategy, side, price, size, confidence, executed/rejected, reason
}
```

### Issue 4: No Strategy Performance Deep Dive

**Current:** 4 stat boxes per strategy (Signals, Trades, W/L, PnL).  
**Missing:** Historical performance, unique markets, execution rate, avg confidence.

**Recommended: Expand strategy cards with more metrics**
```javascript
// New per-strategy metrics:
- Signals Generated (from DB)
- Signals Executed (from DB)
- Execution Rate % = executed / signals * 100  // Key metric!
- Signals Rejected (from DB)
- Rejection Rate % = rejected / signals * 100  // High = problem
- Unique Markets (from DB: COUNT DISTINCT market_condition_id)
- Avg Confidence (from DB: AVG confidence)
- Best Trade, Worst Trade
- Avg Hold Time (if trackable)
- Strategy-specific: 
  - latency_arb: edge_pct avg, btc_move correlation
  - atomic_arb: pair count, avg profit bps
  - resolution_snipe: avg hold hours, resolution rate
  - momentum: avg momentum_pct, TP/SL ratio
```

### Issue 5: No Market Category Distribution

**Current:** System status shows "Markets Tracked: 300" and "Crypto Up/Down: 18".  
**Missing:** Breakdown by category (sports, crypto, economics, politics, other).

**Recommended: Add market category pie/bar chart**
```javascript
// Add to system status or create new Market Distribution section
<div class="card">
  <div class="card-title"><span>📈 Market Distribution</span></div>
  <div id="market-dist" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
    <!-- Generated from d.market_categories: {sports_match: 132, crypto: 27, ...} -->
  </div>
</div>

function renderMarketDist(d) {
  const cats = d.market_categories || {};
  const total = Object.values(cats).reduce((a,b)=>a+b,0);
  const cont = document.getElementById('market-dist');
  let html = '';
  const colors = {
    'sports_match': '#ff6b6b',
    'sports_total': '#ffa94d', 
    'sports_spread': '#ff8780',
    'crypto': '#ffd43b',
    'economics': '#69db7c',
    'politics': '#74c0fc',
    'other': '#b197fc',
    'entertainment': '#f783ac',
  };
  for (const [cat, count] of Object.entries(cats)) {
    const pct = total > 0 ? (count/total*100).toFixed(1) : 0;
    html += `<div class="cat-chip" style="background:${colors[cat]||'#888'}22;color:${colors[cat]||'#888'}">
      ${cat.replace('_',' ')}: ${count} (${pct}%)
    </div>`;
  }
  cont.innerHTML = html;
}
```

---

## 🟢 UX/Polish Improvements

### Issue 6: Version Display Wrong

**Current:** Dashboard header shows "PolyClaw-Cipher v3.4.4" but bot is actually v3.4.3.

**Fix:** Make version dynamic from API:
```javascript
// In refresh():
const version = d.version || 'v3.x.x';  // Bot should return version in stats
document.querySelector('h1').textContent = '🔍 PolyClaw-Cipher ' + version;
```

**Bot change needed:** Add `version` field to `_build_stats_sync()`:
```python
snap["version"] = self.config.get("bot", {}).get("version", "3.4.x")
```

### Issue 7: No Alert/Warning Banners

**Current:** Status items show colors but no prominent alerts.  
**Missing:** Banner alerts for critical states.

**Recommended: Add alert banner system**
```javascript
function renderAlerts(d) {
  const alerts = [];
  
  // Stagnation warning
  if (d.bot_status === 'STAGNANT') {
    alerts.push({ level: 'error', msg: '⚠️ BOT STAGNANT — No activity for 15+ minutes. Daemon restarting...' });
  }
  
  // Strategy disabled
  const disabled = d.risk?.disabled_strategies || [];
  if (disabled.length > 0) {
    alerts.push({ level: 'warn', msg: `🛡️ Strategies disabled: ${disabled.join(', ')}` });
  }
  
  // Cash stuck
  if (d.cash_flow?.cash_stuck) {
    alerts.push({ level: 'error', msg: '💰 Cash stuck — positions may be blocking trade execution' });
  }
  
  // High deployment
  const invPct = (d.deployed / d.bankroll * 100) || 0;
  if (invPct > 85) {
    alerts.push({ level: 'warn', msg: `📊 High deployment: ${invPct.toFixed(0)}% — consider cash buffer` });
  }
  
  // WS issues
  if (!d.ws_status?.clob_connected) {
    alerts.push({ level: 'error', msg: '🔌 CLOB WebSocket disconnected' });
  }
  if (!d.ws_status?.binance_connected) {
    alerts.push({ level: 'error', msg: '📊 Binance WebSocket disconnected' });
  }
  
  // Render
  const alertDiv = document.getElementById('alerts-container');
  if (alerts.length === 0) {
    alertDiv.style.display = 'none';
    return;
  }
  alertDiv.style.display = 'block';
  alertDiv.innerHTML = alerts.map(a => 
    `<div class="alert-banner alert-${a.level}">${a.msg}</div>`
  ).join('');
}

// Add to HTML:
<div class="wrap">
  <div id="alerts-container" style="display:none;margin-bottom:10px"></div>
  <div class="hdr">...
```

**CSS:**
```css
.alert-banner {
  padding: 10px 16px;
  border-radius: 6px;
  font-size: 0.75rem;
  font-weight: 600;
  margin-bottom: 6px;
}
.alert-error { background: rgba(255,87,34,0.15); border: 1px solid var(--red); color: var(--red); }
.alert-warn { background: rgba(255,193,7,0.15); border: 1px solid #ffc107; color: #ffc107; }
.alert-info { background: rgba(33,150,243,0.15); border: 1px solid var(--blue); color: var(--blue); }
```

### Issue 8: Trades Table — Better Sorting and Filtering

**Current:** Shows last 20 trades in reverse chronological order.  
**Missing:** Sort options (by PnL, by date, by strategy), filter by strategy, pagination.

**Recommended:**
```javascript
// Add sort/filter controls
<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
  <span style="font-size:0.65rem;color:var(--muted)">Sort:</span>
  <select id="trade-sort" onchange="renderTrades(lastData)">
    <option value="date">Most Recent</option>
    <option value="pnl_asc">PnL (worst first)</option>
    <option value="pnl_desc">PnL (best first)</option>
  </select>
  <span style="font-size:0.65rem;color:var(--muted)">Filter:</span>
  <select id="trade-filter" onchange="renderTrades(lastData)">
    <option value="all">All</option>
    <option value="momentum">Momentum</option>
    <option value="atomic_arb">Atomic Arb</option>
    <option value="resolution_snipe">Resolution Snipe</option>
  </select>
</div>
```

### Issue 9: Better Trade Summary Stats

**Current:** Only shows "X closed trades" in Win Rate KPI.  
**Missing:** Total P&L, best/worst trade, avg hold time, profit factor.

**Recommended: Add trade summary row above trades table**
```javascript
function renderTradeSummary(d) {
  const trades = d.recent_trades || [];
  if (trades.length === 0) return '';
  
  const totalPnl = trades.reduce((s, t) => s + (t.pnl_dollar || 0), 0);
  const wins = trades.filter(t => t.pnl_dollar > 0).length;
  const losses = trades.filter(t => t.pnl_dollar < 0).length;
  const best = Math.max(...trades.map(t => t.pnl_dollar || 0));
  const worst = Math.min(...trades.map(t => t.pnl_dollar || 0));
  
  return `<div class="trade-summary">
    <span>PnL: <b class="${pnlColor(totalPnl)}">${pnlSign(totalPnl)}$${Math.abs(totalPnl).toFixed(2)}</b></span>
    <span>Wins: <b style="color:var(--green)">${wins}</b> / Losses: <b style="color:var(--red)">${losses}</b></span>
    <span>Best: <b style="color:var(--green)">+$${best.toFixed(2)}</b></span>
    <span>Worst: <b style="color:var(--red)">$${worst.toFixed(2)}</b></span>
    <span>Avg: <b>${pnlSign(totalPnl/trades.length)}$${Math.abs(totalPnl/trades.length).toFixed(2)}</b></span>
  </div>`;
}
```

### Issue 10: Enhanced Strategy Cards — Color-Coded Performance

**Current:** Strategy cards show basic stats with colored PnL.  
**Missing:** Performance rating, confidence indicator, execution rate bar.

**Recommended:**
```javascript
// Add to each strategy card
// 1. Execution rate bar
const execRate = s.signals_emitted > 0 ? (s.trades / s.signals_emitted * 100) : 0;
// Show: ████████░░ 76%

// 2. Win rate confidence
const wrStars = wr >= 60 ? '⭐⭐' : wr >= 50 ? '⭐' : '—';

// 3. Performance badge
const perfBadge = s.pnl > 0 ? '💹' : s.pnl < -1 ? '📉' : '➖';

// 4. Last activity indicator
const lastActivity = s.last_signal_at ? timeAgo(s.last_signal_at) : 'never';
```

### Issue 11: PnL History Chart (Bonus)

**Nice to have:** Small sparkline chart showing PnL progression over last 24h.

**Can use simple ASCII or SVG:**
```javascript
// Simple bar chart using div widths
function renderPnLChart(d) {
  const pnlHistory = d.pnl_history || [];  // Need to add to API
  if (pnlHistory.length < 2) return '';
  
  const max = Math.max(...pnlHistory.map(p => Math.abs(p)));
  let html = '<div class="pnl-chart">';
  for (const p of pnlHistory) {
    const h = max > 0 ? Math.abs(p / max * 100) : 0;
    const cls = p >= 0 ? 'bar-pos' : 'bar-neg';
    html += `<div class="bar ${cls}" style="height:${h}%"></div>`;
  }
  html += '</div>';
  return html;
}
```

### Issue 12: Keyboard Shortcuts

**Quality of life:**
- `R` = force refresh
- `S` = toggle strategy details
- `T` = toggle trade details  
- `P` = pause auto-refresh
- `D` = toggle dark/light mode

```javascript
document.addEventListener('keydown', (e) => {
  switch(e.key.toLowerCase()) {
    case 'r': refresh(); break;
    case 's': /* toggle strategy expand */ break;
    case 'p': 
      paused = !paused;
      if (paused) clearInterval(refreshInterval);
      else refreshInterval = setInterval(refresh, REFRESH_MS);
      break;
  }
});
```

---

## 📊 API Changes Needed

To support the dashboard improvements, these API changes are needed:

### 1. Add to `/api/stats` response:

```python
# In _build_stats_sync or _refresh_stats_loop:
stats["version"] = "3.4.3"  # From config

# Activity tracking
stats["last_signal_at"] = self._last_signal_time or None
stats["last_trade_at"] = self._last_trade_time or None
stats["bot_status"] = self._compute_bot_status()

# Market distribution
stats["market_categories"] = self._market_categories or {}

# Cash flow
stats["cash_flow"] = {
    "cash_stuck": bankroll > 30 and cash < 1.0,
    "cash_locked_pct": (deployed / bankroll * 100) if bankroll > 0 else 0,
}

# Per-strategy detailed (from DB)
stats["strategy_details"] = await self.trade_repo.per_strategy_stats_extended()

# Recent signals (last 20)
stats["recent_signals"] = await self.signal_repo.get_recent_signals(limit=20)
```

### 2. Add new endpoint `/api/daemon_health`:

(Already documented in `recommendation-daemon.md` Fix 3)

### 3. Enhanced strategy stats from DB:

```python
# In repository.py — extend per_strategy_stats:
async def per_strategy_stats_extended(self) -> list[dict]:
    """Enhanced per-strategy stats with signals data."""
    # JOIN trades + signals for comprehensive view
    # Return: name, trades, wins, losses, pnl, 
    #         signals_total, signals_executed, signals_rejected,
    #         unique_markets, avg_confidence, avg_hold_time
```

---

## 🎯 Implementation Priority

| Priority | Feature | Impact | Effort |
|----------|---------|--------|--------|
| 🔴 **1** | Fix strategy stats (from audit) | Dashboard accuracy | Low |
| 🔴 **2** | Add bot status (ACTIVE/STAGNANT/IDLE) | Detect stagnation | Low |
| 🟡 **3** | Add alert banner system | Critical state visibility | Low |
| 🟡 **4** | Add last signal/trade timestamps | Activity tracking | Low |
| 🟡 **5** | Expand strategy cards (exec rate, rejection rate) | Strategy visibility | Medium |
| 🟡 **6** | Fix version display | Accuracy | Low |
| 🟡 **7** | Add recent signals panel | Signal visibility | Medium |
| 🟢 **8** | Add market distribution chart | Context | Medium |
| 🟢 **9** | Trade summary row | Quick stats | Low |
| 🟢 **10** | Trade sort/filter | Usability | Medium |
| 🟢 **11** | PnL history chart | Trend visibility | Medium |
| 🟢 **12** | Keyboard shortcuts | UX polish | Low |

---

## 📝 Changelog Entry Suggestion

```markdown
## [3.5.0] — TBD

### ✨ Added — Enhanced Dashboard
- Bot status indicator: ACTIVE / STAGNANT / IDLE
- Alert banner system for critical states (WS down, strategies disabled, cash stuck)
- Recent signals panel (collapsible) showing all signal attempts
- Enhanced strategy cards: execution rate, rejection rate, unique markets, avg confidence
- Market distribution breakdown (pie chart by category)
- Last signal / Last trade timestamps in system status
- Version now dynamic from bot config (not hardcoded)
- Trade summary row: total P&L, wins/losses, best/worst trade
- Trade table: sort by PnL/date, filter by strategy
- Keyboard shortcuts: R=refresh, P=pause, D=dark mode

### 🐛 Fixed — Strategy Stats from DB
- Strategy cards now show accurate stats from DB (was all zeros after restart)
```

---

## 🧪 Dashboard Testing Checklist

1. **After bot restart:** Strategy cards should show historical data from DB (not 0)
2. **Stagnation scenario:** Bot stagnant → dashboard shows "STAGNANT" status + alert banner
3. **WS disconnect:** Alert banner appears with red warning
4. **Strategy disabled:** Alert banner with warning, strategy card shows "disabled" badge
5. **Trade execution:** Recent signals panel updates in real-time
6. **Market distribution:** Correct breakdown matching scan results
7. **Version:** Matches actual bot version from config
8. **Responsive:** Works on mobile (test at 375px width)

---

## 🎨 Design Reference

### Color Palette (keep existing but add for new elements)
```css
/* Status colors */
.status-active { color: #4caf50; }      /* green */
.status-idle { color: #ff9800; }        /* orange */
.status-stagnant { color: #f44336; }    /* red */
.status-warning { color: #ffc107; }     /* yellow */

/* Alert banner */
.alert-error { background: rgba(244,67,54,0.12); border: 1px solid #f44336; color: #f44336; }
.alert-warn { background: rgba(255,193,7,0.12); border: 1px solid #ffc107; color: #ffc107; }
.alert-info { background: rgba(33,150,243,0.12); border: 1px solid #2196f3; color: #2196f3; }

/* Category chips */
.cat-crypto { background: rgba(255,213,0,0.15); color: #ffd43b; }
.cat-sports { background: rgba(255,107,107,0.15); color: #ff6b6b; }
.cat-economics { background: rgba(105,219,124,0.15); color: #69db7c; }
.cat-politics { background: rgba(116,192,252,0.15); color: #74c0fc; }
```

### Typography
```css
.font-pending { color: #9e9e9e; }     /* For stale data */
.font-active { color: #4caf50; }      /* For live data */
```

---

*Audit completed by Arena.ai Agent Mode — 2026-06-27*  
*File: `audit/recommendation-dashboard.md`*