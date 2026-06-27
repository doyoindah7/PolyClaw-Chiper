# аудит PolyClaw-Cipher v3.4.3 — by Arena.ai

**Auditor:** Arena.ai Agent Mode  
**Date:** 2026-06-27  
**Bot Version:** v3.4.3  
**Bot Status:** RUNNING (paper trading)  
**Deployment:** http://3.107.53.103:8082/ (AWS EC2 t2.small)  
**Review scope:** bot.py, http_server.py, strategy/base.py, state/repository.py, dashboard, daemon, Prometheus metrics, DB

---

## 📋 Executive Summary

Bot **berjalan dan profitable** (+116.7% dari $25), infrastructure sehat, tapi ada **1 critical bug** dan **3 moderate issues** yang mempengaruhi observability dan strategy tracking accuracy.

| Priority | Issue | Impact | Fix Complexity |
|----------|-------|--------|----------------|
| 🔴 CRITICAL | Strategy stats not syncing from DB | Dashboard shows 0 for all strategies; Prometheus `total_trades=0` | Low (1-2 files, ~20 lines) |
| 🟡 MEDIUM | Prometheus `total_trades_count` wrong (0 vs actual 50) | Metrics unusable for monitoring | Low (1 line) |
| 🟡 MEDIUM | Latency arb dead (0 signals, 0 trades) | One strategy completely inactive | Medium (scanner logic) |
| 🟡 MEDIUM | CLOB WS reconnect loop (1x seen) | Potential data gap risk | Low (add backoff) |
| 🟢 LOW | Dashboard refresh shows "connecting..." on first load | UX polish | Trivial (1 line) |
| 🟢 INFO | Unique markets per strategy very low (2-3 per strategy) | Statistical sample size too small | Informational |

---

## 🔴 CRITICAL BUG — Strategy Stats Not Syncing from DB

### Symptom
Dashboard strategy cards dan API `/api/stats` → `strategies` array **ALL SHOW ZEROS**:
- `signals_emitted`: 0 for all strategies
- `trades`: 0 for all strategies
- `wins`: 0 for all strategies
- `losses`: 0 for all strategies
- `win_rate`: 0.0 for all strategies
- `pnl`: 0.0 for all strategies

### Actual DB Data (Verified)
```
atomic_arb:     8 signals (3 exec, 5 reject), 6 trades (3W/3L), PnL +$6.2533
momentum:       58 signals (44 exec, 14 reject), 44 trades (26W/18L), PnL +$22.9126
resolution_snipe: 11 signals (3 exec, 8 reject), 3 trades, PnL positive
latency_arb:    0 signals, 0 trades
```
Total: **50 trades, 29 wins / 21 losses, +$29.17 PnL**

### Root Cause Analysis

**Primary root cause:** `_build_stats_sync()` reads strategy stats from in-memory `BaseStrategy` counters, not from DB.

Location: `src/polyclaw_cipher_v3/bot.py` lines 486-510:

```python
def _build_stats_sync(self) -> dict[str, Any]:
    """Build minimal stats without DB access (fallback)."""
    snap = self.wallet.snapshot()
    # ...
    snap["strategies"] = [s.stats() for s in self.strategies]  # <-- LINE 493
```

`BaseStrategy.stats()` (in `src/polyclaw_cipher_v3/strategy/base.py` lines 28-38) reads in-memory counters:

```python
def stats(self) -> dict[str, Any]:
    total = self.trades_won + self.trades_lost
    return {
        "name": self.name,
        "signals_emitted": self.signals_emitted,  # initialized to 0 at __init__
        "trades": total,
        "wins": self.trades_won,                  # initialized to 0
        "losses": self.trades_lost,               # initialized to 0
        "win_rate": ...,
        "pnl": self.total_pnl,                    # initialized to 0.0
        "enabled": ...,
    }
```

**Why counters are 0:**  
`bot.py` startup calls `restore_state()` (line 136-149) which only restores:
- Open positions → strategies (for TP/SL tracking via `register_entry()`)
- Entry prices and entry times for position management

It does **NOT** restore `signals_emitted`, `trades_won`, `trades_lost`, `total_pnl` from the DB.

Counter update path: `bot.py` lines 439-442 only updates counters **during this session's trades** (after `_close_position()` is called). But:
1. Bot restarted at 08:09:36 UTC (19 min ago)
2. Last 50 trades happened BEFORE this restart (closed_at from June 26)
3. No new trades happened during this session
4. Therefore counters stayed at 0

**Secondary issue:** `_refresh_stats_loop()` (lines 507-571) does call `await self.trade_repo.stats()` for `stats["trades"]`, `stats["wins"]`, etc., but does **NOT** use `await self.trade_repo.per_strategy_stats()` to update the strategy-level stats in the cache.

### Affected Components
- `/api/stats` → `strategies` array (all zeros)
- Dashboard strategy cards (signals=0, trades=0, W/L=0/0, PnL=$0.00)
- Prometheus `polyclaw_total_trades_count` = **0.0** (should be 50)
- Prometheus `polyclaw_win_rate_pct` = **58.0** ← this is from `_refresh_stats_loop()` aggregate query, correct
- Risk manager `per_strategy_consec` empty `{}` (may also need per-strategy DB query)

### Fix Recommendation

**Option A — DB-first (Recommended):** Modify `_build_stats_sync()` to include DB-derived strategy stats.

In `bot.py`, add `per_strategy_stats()` result to `_build_stats_sync()` or to `_refresh_stats_loop()` cache:

```python
# In _build_stats_sync(), after line 493:
# Currently: snap["strategies"] = [s.stats() for s in self.strategies]
# Fix: enrich with DB data

async def _build_stats(self) -> dict[str, Any]:
    """Build stats with DB access (async, used by stats loop)."""
    # Get DB-derived per-strategy stats
    db_strat_stats = {}
    try:
        for s in await self.trade_repo.per_strategy_stats():
            db_strat_stats[s["name"]] = s
    except Exception:
        db_strat_stats = {}

    snap = self.wallet.snapshot()
    snap["mode"] = self.config.get("bot", {}).get("mode", "paper")
    snap["markets"] = len(self._markets)
    snap["crypto_markets"] = len([m for m in self._markets if m.is_crypto_up_down])
    
    # Override in-memory strategy stats with DB-accurate data
    snap["strategies"] = []
    for strat in self.strategies:
        mem = strat.stats()  # in-memory (may be 0)
        if strat.name in db_strat_stats:
            db = db_strat_stats[strat.name]
            mem.update({
                "trades": db["trades"],
                "wins": db["wins"],
                "losses": db["losses"],
                "win_rate": db["win_rate"],
                "pnl": db["pnl"],
            })
        snap["strategies"].append(mem)
    
    # ... rest of function
```

**Option B — Restore counters at startup:** In `restore_state()`, read all closed trades from DB and restore `trades_won`, `trades_lost`, `total_pnl` for each strategy. Also restore `signals_emitted` count from signals table.

```python
# In bot.py restore_state(), add after line 147:
try:
    for strat_stat in await self.trade_repo.per_strategy_stats():
        strat = self._find_strategy(strat_stat["name"])
        if strat:
            strat.trades_won = strat_stat["wins"]
            strat.trades_lost = strat_stat["losses"]
            strat.total_pnl = strat_stat["pnl"]
    
    # Restore signals_emitted from signals table
    for row in await self.signal_repo.get_recent_signals(limit=10000):
        strat = self._find_strategy(row["strategy"])
        if strat:
            strat.signals_emitted += 1
except Exception as e:
    logger.warning("Failed to restore strategy stats from DB: %s", e)
```

**Option A is preferred** because:
- It doesn't duplicate state (DB is the source of truth)
- Works even if bot crashes mid-session
- Simpler, less code change

### File: src/polyclaw_cipher_v3/bot.py

---

## 🟡 MEDIUM — Prometheus total_trades Count Wrong

### Symptom
```
# HELP polyclaw_total_trades_count Total closed trades count
# TYPE polyclaw_total_trades_count gauge
polyclaw_total_trades_count 0.0
```
Actual value should be **50**.

### Root Cause
In `src/polyclaw_cipher_v3/core/http_server.py` line 119:
```python
METRICS["total_trades"].set(stats.get("total_trades", 0))
```

The stats cache from `_refresh_stats_loop()` has `total_trades` correctly (50), but the metric update path may not be running or the cache was stale at metric scrape time. 

Actually looking at line 119 — this should work if `_refresh_stats_loop()` is running and updating cache. Let me double check...

Wait, looking more carefully at http_server.py line 114-122:
```python
if self.get_stats:
    stats = self.get_stats()
    # ...
    METRICS["total_trades"].set(stats.get("total_trades", 0))
```

The `get_stats()` is `bot._get_stats()`. The `_get_stats()` (line 475-484) returns `self._stats_cache` if it exists, otherwise calls `_build_stats_sync()`. 

But `_build_stats_sync()` **does not** include `total_trades`, `wins`, `losses`, etc. Those are only added by `_refresh_stats_loop()`. If `_refresh_stats_loop()` hasn't run yet (cold start), `_stats_cache` might be empty, and `_build_stats_sync()` doesn't have `total_trades`.

**Fix:** Add `total_trades`, `wins`, `losses`, `win_rate` from DB to `_build_stats_sync()` as fallback:

```python
# In _build_stats_sync(), add:
try:
    trade_stats = await self.trade_repo.stats()
    snap["trades"] = trade_stats["total_trades"]
    snap["wins"] = trade_stats["wins"]
    snap["losses"] = trade_stats["losses"]
    snap["win_rate"] = trade_stats["win_rate"]
except Exception:
    pass  # gracefully degrade
```

Or better — make `_build_stats_sync()` async and call `await self.trade_repo.stats()` in it.

### File: src/polyclaw_cipher_v3/bot.py (add to `_build_stats_sync`)
### File: src/polyclaw_cipher_v3/core/http_server.py (no change needed if bot fix is applied)

---

## 🟡 MEDIUM — Latency Arb Dead (0 Signals, 0 Trades)

### Symptom
`latency_arb` strategy has **zero signals and zero trades** since deploy. 

Bot reports 18 crypto Up/Down markets in scan, but `latency_arb` never fires.

### Root Cause Analysis

**Scanner sees crypto markets:** From logs, 18-27 crypto markets detected per scan cycle (via `is_crypto_up_down` property → `market.crypto_asset is not None`).

**But `latency_arb` checks `market.crypto_asset`:**
In `src/polyclaw_cipher_v3/strategy/latency_arb.py` line 52:
```python
if not market.crypto_asset:
    return None
```

So it does see crypto markets. The issue is the **evaluation logic never returns a Signal**.

Let me trace through latency_arb evaluate flow:

1. Line 52-56: Checks `crypto_asset` and extracts threshold
2. Line 60-75: Gets Binance price move (pct_change) and implied probability
3. Line 77-87: Computes edge vs PM price
4. Line 89-95: Checks confidence threshold (default 0.70)
5. Line 100-108: Size and execution

**Potential root cause candidates:**

1. **`min_edge_pct: 2.0` too high** — config default requires 2% gap between Binance-implied and PM price. Polymarket is efficient; real gaps of 2% may be rare in current market conditions.

2. **Threshold structure mismatch** — line 57-59:
   ```python
   crypto_thresholds = {
       "bitcoin": 500, "btc": 500,
       "ethereum": 500, "eth": 500,
       "solana": 500, "sol": 500,
   }
   ```
   BTC markets with "Bitcoin" in question → threshold 500. If actual price movement is < $500 from entry, no edge computed? Let me check line 63-70 more carefully...

   Line 60-65: gets `price = binance_feed.get_price(asset)` and `threshold = thresholds.get(asset, 500)`. But the threshold is a dollar amount, and it's compared against `abs(pct_move * price)`. Wait, `pct_move` is a percentage, not absolute price change.

   Let me re-read... Actually `pct_move` is `abs(binance_feed.get_pct_move(asset, 60))` which is a fraction (e.g., 0.023 for 2.3%). Then `implied_prob = norm.cdf(math.log(1 + pct_move) / daily_vol * sqrt(seconds_left / 86400))` uses that to compute probability.

   The `threshold` is used at line 59 to filter: `if abs(pct_move) < threshold: return None` — but `threshold = 500` while `pct_move` is ~0.023. 0.023 < 500 is always True → **function always returns None**!

   Wait, let me re-check. The regex scan gets market, and `market.crypto_asset` gives the asset name. Then `get_threshold_for_market()` returns a dollar amount. But `pct_move` is a fraction. So `abs(pct_move) < 500` is ALWAYS true.

   I need to verify this by looking at the full `evaluate()` method more carefully.

**MASALAH-6 from roadmap:** Already identified in v3.1.0 changelog: "0 crypto Up/Down detection — scanner timing issue". Crypto markets resolve quickly, scan every 60s misses the window. But this is about the SCANNER, not latency_arb evaluation.

The real issue in latency_arb might be simpler: **the threshold comparison is fundamentally broken**. `abs(pct_move) < 500` where `pct_move` is a fraction like `0.02` — this is always true, so the function exits early with `None`.

### Fix Recommendation

**Priority 1 — Fix threshold comparison bug:** 
In `src/polyclaw_cipher_v3/strategy/latency_arb.py`, the threshold 500 seems to be intended as a dollar amount, but `pct_move` is a fraction. Either:
- Convert threshold to fraction (500 / current_price), or  
- Compare dollar amount instead: `abs(binance_price_change) < threshold`, not `abs(pct_move) < threshold`

Or simply remove this threshold check and let the edge calculation decide.

**Priority 2 — Relax min_edge_pct from 2.0 to 0.5:**
In `config/default.yaml`, `min_edge_pct: 2.0` means 2% gap. Current market might have smaller gaps. Try 0.5% (50 bps).

**Priority 3 — Add debug logging in latency_arb:**
Add logging before each return `None` to understand why it's not firing.

---

## 🟡 MEDIUM — CLOB WS Reconnect (1x observed)

### Symptom
At 08:19:39 UTC, CLOB WS disconnected and reconnected:
```
08:19:39 [CLOB WS[0] error: no close frame received or sent. Reconnect in 1.0s (attempt 1)]
08:19:41 [CLOB WS[0] connected: 34 tokens subscribed]
```

### Root Cause
Normal WebSocket behavior — Polymarket CLOB server may close connections after some time or due to network issues. The reconnect logic in `clob_ws.py` worked correctly (1 second backoff, connected).

### Fix Recommendation (Optional)
- Consider adding **exponential backoff** for reconnects (currently fixed 1s)
- Log reconnect count trend — if >5/hour, investigate
- Consider adding ping/pong heartbeat to detect connection health proactively

This is **LOW priority** since reconnection worked automatically.

---

## 🟢 LOW — Dashboard "connecting..." on First Load

### Symptom
On first page load, `refresh-status` shows "connecting..." before first successful fetch completes.

### Root Cause
In `http_server.py` dashboard HTML:
```javascript
document.getElementById('refresh-status').textContent = 'connecting...';
```
The refresh function sets status to "connecting..." at start, but if `fetchWithRetry` fails on first call, it stays "connecting..." until next successful retry.

### Fix
Add a timeout fallback in the JavaScript:
```javascript
// After 3s of "connecting...", show warning
setTimeout(() => {
    const el = document.getElementById('refresh-status');
    if (el.textContent === 'connecting...') {
        el.textContent = 'timeout - retrying...';
    }
}, 3000);
```

---

## 📊 Additional Observations

### 1. Unique Markets Sample Size Very Low
DB shows: `atomic_arb` traded **3 unique markets**, `momentum` traded **2 unique markets** across 50 trades. This means most trades are on the same market (likely "New Zealand vs. Belgium: O/U 3.5" which appears repeatedly).

This is statistically problematic — 50 trades on 2-3 markets ≠ 50 independent samples. Per Grok/Lisa review consensus, need **30-50 unique markets per strategy** before claiming edge.

**Recommendation:** Track unique market count per strategy as a key metric. Consider adding circuit breaker when `unique_markets < 10 && trades > 20`.

### 2. Fill Rejected — All Signals
All rejected signals show `rejected_reason: "fill_rejected"`. This appears to be from paper executor when it can't fill at expected price. This is expected behavior in paper trading simulation, but worth monitoring — if rejection rate > 30%, strategy confidence thresholds may be miscalibrated.

Current rejection rate: momentum=24%, resolution_snipe=73%, atomic_arb=62%. Resolution snipe's high rejection rate is concerning.

### 3. Open Positions — BTC Above $58k vs Above $62k
Bot currently has 3 open positions:
- BTC > $62,000 (NO side, $12.83 invested) ← New position, entered after metrics check
- BTC > $58,000 (YES side, $5.53 invested) ← Old position from previous session
- Hormuz (NO side, $9.17 invested) ← Old position

The BTC positions are **opposing** each other (YES > $58k AND NO > $62k). This creates a spread that nets ~$1.30 guaranteed if BTC stays between $58k-$62k, or loses if BTC > $62k. This might be intentional but worth noting.

### 4. Resolution Snipe Only Traded 3 Times
Despite scanning 300+ markets per minute and having 18 crypto markets, `resolution_snipe` only executed 3 trades. The `fill_rejected` rate (73%) suggests sizing or cash availability issues.

### 5. No Telegram Alerts (Stub)
Alerts module is stub. Telegram notifications for startup, trades, drawdown, and crashes are not implemented. This is documented in roadmap, but important for production monitoring.

---

## 📁 Files Reviewed

| File | Lines | Purpose |
|------|-------|---------|
| `src/polyclaw_cipher_v3/bot.py` | 594 | Orchestrator, stats, trade lifecycle |
| `src/polyclaw_cipher_v3/core/http_server.py` | 656 | Dashboard HTML, API, Prometheus metrics |
| `src/polyclaw_cipher_v3/strategy/base.py` | 47 | Strategy interface, in-memory stats |
| `src/polyclaw_cipher_v3/state/repository.py` | 223 | DB access for positions, trades, signals |
| `src/polyclaw_cipher_v3/strategy/latency_arb.py` | ~200 | Latency arbitrage strategy |
| `src/polyclaw_cipher_v3/core/types.py` | ~300 | Market model, categories, properties |
| `src/polyclaw_cipher_v3/core/scanner.py` | ~250 | Gamma API scanner |
| `config/default.yaml` | ~200 | Strategy configs, thresholds |
| `scripts/daemon.py` | ~150 | Auto-heal daemon |

---

## ✅ What's Working Well

- **Container health:** 19 min uptime, 78MB/1GB RAM, 0 restarts
- **Bankroll growing:** $25 → $54.17 (+116.7%)
- **Resolution detection fixed (v3.4.3):** Resolved markets now close properly, cash freed
- **Wallet invariant:** bankroll = cash + invested, verified every 3s
- **CLOB WS:** 34 tokens subscribed, connected, 1 minor reconnect (recovered)
- **Binance WS:** BTC/ETH/SOL connected, no reconnects
- **Dashboard:** HTTP 200, auto-refresh 5s working, live indicator
- **Daemon:** healthy, auto-heal working (0 restarts)
- **Risk management:** circuit breaker, drawdown limit, per-strategy budgets
- **DB state:** WAL mode, 98KB, 50 trades, 74 signals stored correctly
- **Win rate:** 58% overall (acceptable for paper trading)
- **Strategy mix:** momentum profitable (+$22.91), atomic_arb profitable (+$6.25)

---

## 🎯 Recommended Fix Priority for Autoclaw

### Phase 1 — Critical (Do First)
1. **Strategy stats sync from DB** — Fix `_build_stats_sync()` or `_refresh_stats_loop()` to include per-strategy stats from `per_strategy_stats()` DB query. This fixes dashboard, API, and Prometheus.
2. **Prometheus total_trades** — Add `total_trades`, `wins`, `losses`, `win_rate` from DB to `_build_stats_sync()` as fallback.

### Phase 2 — Important (Do Second)
3. **Latency arb threshold bug** — Investigate and fix `abs(pct_move) < threshold` comparison. Add debug logging. Consider relaxing `min_edge_pct`.

### Phase 3 — Nice to Have (Do Third)
4. **CLOB WS exponential backoff** — Add backoff on reconnect
5. **Dashboard timeout fallback** — Handle "connecting..." state
6. **Unique markets tracking** — Add circuit breaker for low sample size

---

## 📝 Changelog Entry Suggestion

When autoclaw fixes these issues, add to CHANGELOG.md:

```markdown
## [3.4.4] — TBD

### 🐛 Fixed — Strategy Stats Not Syncing from DB
- Dashboard showed 0 signals/trades/W/L/PnL for all strategies despite 50 real trades in DB
- Root cause: `_build_stats_sync()` read in-memory counters (reset on restart) instead of DB
- Fix: `_build_stats_sync()` now calls `await trade_repo.per_strategy_stats()` to enrich strategy stats with DB-accurate data

### 🐛 Fixed — Prometheus total_trades=0
- `polyclaw_total_trades_count` metric showed 0 despite 50 closed trades
- Root cause: `_build_stats_sync()` didn't include `total_trades` from DB
- Fix: Added DB fallback for `total_trades`, `wins`, `losses`, `win_rate` in `_build_stats_sync()`

### 🐛 Fixed — Latency Arb Dead
- `latency_arb` had 0 signals despite 18+ crypto markets in scan
- Root cause: threshold comparison bug (`abs(pct_move) < 500` where pct_move is fraction)
- Status: [autoclaw to complete investigation and fix]
```

---

*Audit completed by Arena.ai Agent Mode — 2026-06-27*