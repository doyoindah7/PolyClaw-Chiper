# Rekomendasi Perbaikan Daemon — PolyClaw-Cipher v3.4.3

**Auditor:** Arena.ai Agent Mode  
**Date:** 2026-06-27  
**File:** `scripts/daemon.py`  
**Version reviewed:** v3.3.0 daemon

---

## 📋 Executive Summary

Daemon v3.3.0 saat ini **HANYA memeriksa apakah bot hidup (HTTP responds)**, TIDAK memeriksa apakah bot **produktif** (menghasilkan sinyal, open posisi, eksekusi trades). Ini menyebabkan masalah utama:

> **Bot bisa "hidup" (HTTP 200) tapi stagnan — tidak ada signals, tidak ada trades, bankroll congel.**  
> Daemon tidak mendeteksi ini dan tidak melakukan restart.

Root cause: Daemon adalah "health check daemon" bukan "activity monitor daemon".

---

## 🔴 Problem Statement

### Observed Symptom
Bot berjalan, container healthy, daemon restart count 0 — tapi:
- Bankroll tidak berubah selama berjam-jam
- Tidak ada open positions baru
- Tidak ada closed trades baru
- 0 signals generated despite 300 markets scanned

### Why Current Daemon Fails

Current daemon only checks:
1. ✅ HTTP `/api/health` responds 200 → bot "healthy"
2. ✅ WS `clob_connected` + `binance_connected` → "connected"
3. ✅ `clob_tokens > 0` → "data flowing"

**But these checks are INSUFFICIENT because:**

| Check | What it verifies | What it DOESN'T verify |
|-------|-----------------|----------------------|
| HTTP 200 | HTTP server alive | Scanner running, strategies evaluating |
| WS connected | WebSocket alive | Signals being generated, trades executing |
| clob_tokens > 0 | Tokens subscribed | Market data actually updating, scanner loop working |
| No crash | Process not dead | Bot not stuck in deadlock, infinite loop, or deadlock |

**Bot can fail silently in these ways and daemon won't know:**

1. **Scanner loop frozen** — `scan_interval_sec: 60` but loop silently dies after exception. Bot continues running, HTTP responds, but 0 new markets loaded.

2. **Strategy evaluation deadlocked** — All strategies in infinite loop or blocked on something. HTTP responds, WS connected, but 0 signals.

3. **Cash exhausted / stuck in emergency mode** — `sizer` returns 0 for all requests. Bot "working" but cannot open positions. Bankroll frozen.

4. **Position resolution stuck** — Positions never close (the v3.4.3 bug), cash locked, bot cannot trade. Bankroll congelado.

5. **Database locked** — SQLite WAL conflict, bot can't write, silently stops processing new trades.

6. **Strategy circuit breaker tripped** — All strategies disabled by risk manager. Bot idle but HTTP responds.

---

## 🟡 Current Daemon Architecture Analysis

```
Current Daemon Loop:
├── Start bot process
├── Every 10s: health_check_ok() [basic HTTP]
├── Every 60s: deep_health_check() [HTTP + WS status]
│   ├── HTTP 200? → OK
│   ├── clob_connected? → OK
│   ├── binance_connected? → OK
│   └── clob_tokens > 0? → OK
├── On crash: restart with exponential backoff
└── NEVER checks: signals, trades, PnL, market scan activity
```

**Missing checks that cause stagnation:**
- No check on market scan activity (is scanner actually looping?)
- No check on signal generation rate (are strategies firing?)
- No check on trade execution rate (are positions being opened/closed?)
- No check on PnL change (is bankroll moving?)
- No check on strategy disabled status
- No check on cash being stuck (positions blocking cash flow)
- No check on last trade timestamp (how long since last trade?)

---

## ✅ Recommended Fixes

### Fix 1 — Activity Baseline Detection (CRITICAL)

Add a **minimum activity threshold** to detect stagnation. Bot is considered "stagnant" if:

```python
# In daemon.py — add to deep_health_check()
def activity_check(host="127.0.0.1", port=8082, timeout=5.0) -> tuple[bool, str]:
    """
    v3.5.0: Check if bot is PRODUCTIVE, not just alive.
    
    Bot is stagnant if:
    - No signals in last 10 minutes
    - No trades in last 30 minutes (when bankroll > $30)
    - Bankroll unchanged in last 30 minutes
    - Markets tracked dropping to 0
    """
    import urllib.request
    
    try:
        url = f"http://{host}:{port}/api/stats"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            stats = json.loads(resp.read().decode())
        
        now = time.time()
        
        # Check 1: Market scan activity
        markets = stats.get("markets", 0)
        if markets == 0:
            return False, "0 markets tracked (scanner dead?)"
        
        # Check 2: Signal rate (last 10 minutes)
        signals_10m = stats.get("signals", 0)
        # Note: signals is recent_signals_count(last hour) = rough estimate
        # Need better metric — see Fix 4
        
        # Check 3: Open positions + last activity
        open_pos = len(stats.get("open_positions", []))
        
        # Check 4: Strategy disabled status
        disabled = stats.get("risk", {}).get("disabled_strategies", [])
        if len(disabled) >= 3:
            return False, f"ALL strategies disabled: {disabled}"
        
        # Check 5: Cash vs deployed ratio (is cash stuck?)
        cash = stats.get("cash", 0)
        bankroll = stats.get("bankroll", 0)
        deployed = stats.get("deployed", 0)
        
        if bankroll > 30 and cash < 1.0 and deployed > bankroll * 0.9:
            # High bankroll but almost no cash + high deployment = cash stuck
            return False, f"Cash stuck: ${cash:.2f} cash, ${deployed:.2f} deployed"
        
        return True, f"OK (markets={markets}, cash=${cash:.2f}, deployed=${deployed:.2f})"
    
    except Exception as e:
        return False, f"activity check failed: {e}"
```

**Integration in main loop:**

```python
# In main() while proc.poll() is None loop:
activity_interval = 120  # Check activity every 2 minutes
last_activity_check = 0.0

while proc.poll() is None and not _shutdown_requested:
    # ... existing 10s loop ...
    
    # NEW: Activity check (every 2 min)
    if now - last_activity_check >= activity_interval:
        last_activity_check = now
        active, reason = activity_check(health_host, port)
        if not active:
            logger.error(
                "STAGNATION DETECTED: %s — restarting bot to recover",
                reason
            )
            kill_bot_gracefully(proc)
            break
```

---

### Fix 2 — Stagnation Detection via State Deltas (HIGH VALUE)

Track state changes over time. If nothing changes for X minutes, restart.

```python
class StagnationDetector:
    """Track state deltas to detect bot inactivity."""
    
    def __init__(self, check_interval=120):
        self.check_interval = check_interval
        self.history = {
            "bankroll": [],
            "open_positions": [],
            "signals": [],
            "trades": [],
        }
        self.last_stagnant_warn = 0.0
    
    def record(self, stats: dict) -> None:
        now = time.time()
        self.history["bankroll"].append((now, stats.get("bankroll", 0)))
        self.history["open_positions"].append((now, len(stats.get("open_positions", []))))
        self.history["signals"].append((now, stats.get("signals", 0)))
        self.history["trades"].append((now, stats.get("trades", 0)))
    
    def is_stagnant(self, stats: dict, threshold_min=15) -> tuple[bool, str]:
        """Check if state has been stable (no change) for threshold_min minutes."""
        now = time.time()
        threshold = threshold_min * 60
        
        # Clean old history
        for key in self.history:
            self.history[key] = [(ts, v) for ts, v in self.history[key] if now - ts < 7200]
        
        # Check 1: Bankroll unchanged
        br_hist = self.history["bankroll"]
        if len(br_hist) >= 2:
            if br_hist[-1][0] - br_hist[0][0] >= threshold:
                if abs(br_hist[-1][1] - br_hist[0][1]) < 0.01:
                    return True, f"Bankroll unchanged for {threshold_min}m (stuck at ${br_hist[-1][1]:.2f})"
        
        # Check 2: Open positions unchanged
        pos_hist = self.history["open_positions"]
        if len(pos_hist) >= 4:  # at least 4 samples
            unique_vals = set(v for _, v in pos_hist[-4:])
            if len(unique_vals) == 1 and pos_hist[-1][0] - pos_hist[0][0] >= threshold:
                count = unique_vals.pop()
                if count > 0:  # positions stuck open
                    return True, f"Open positions stuck at {count} for {threshold_min}m (resolution dead?)"
        
        # Check 3: No signals generated
        sig_hist = self.history["signals"]
        if len(sig_hist) >= 3:
            if sig_hist[-1][0] - sig_hist[0][0] >= threshold:
                if sig_hist[-1][1] == sig_hist[0][1]:
                    return True, f"No signals generated for {threshold_min}m (strategies dead?)"
        
        return False, "OK"
```

**Usage in daemon:**

```python
stagnation_detector = StagnationDetector(check_interval=120)
last_stagnation_check = 0.0

# In main loop:
if now - last_stagnation_check >= stagnation_detector.check_interval:
    last_stagnation_check = now
    stagnant, reason = stagnation_detector.is_stagnant(stats, threshold_min=15)
    if stagnant:
        # Rate-limit restart (don't restart more than once per 30 min for stagnation)
        if now - stagnation_detector.last_stagnant_warn > 1800:
            logger.error("STAGNATION: %s — initiating bot restart", reason)
            stagnation_detector.last_stagnant_warn = now
            kill_bot_gracefully(proc)
            break
```

---

### Fix 3 — Add Endpoints for Daemon Monitoring (MEDIUM)

Add new API endpoints specifically for daemon health monitoring:

```python
# In http_server.py — add new endpoint

@app.get("/api/daemon_health")
async def daemon_health():
    """
    v3.5.0: Detailed health metrics for daemon monitoring.
    Returns everything daemon needs to make restart decisions.
    """
    stats = get_stats()  # from cache
    
    # Activity metrics
    recent_trades = await trade_repo.count_trades_since(time.time() - 1800)  # last 30 min
    recent_signals = await signal_repo.count_since(time.time() - 600)  # last 10 min
    
    # Market freshness
    markets = len(self._markets) if hasattr(self, '_markets') else stats.get("markets", 0)
    
    # Strategy health
    disabled_strats = risk.stats.get("disabled_strategies", [])
    all_disabled = len(disabled_strats) >= 3
    
    # Cash flow
    cash = stats.get("cash", 0)
    deployed = stats.get("deployed", 0)
    bankroll = stats.get("bankroll", 0)
    
    # Decision flags
    return {
        "stagnant": False,  # compute from other fields
        "needs_restart": False,
        "reasons": [],
        "activity": {
            "signals_10m": recent_signals,
            "trades_30m": recent_trades,
            "min_signals_expected": 2,  # config
            "min_trades_expected": 1,   # config (when bankroll > 30)
        },
        "strategy_health": {
            "disabled_count": len(disabled_strats),
            "disabled_strategies": disabled_strats,
            "all_disabled": all_disabled,
        },
        "cash_flow": {
            "cash": cash,
            "deployed": deployed,
            "bankroll": bankroll,
            "cash_stuck": bankroll > 30 and cash < 1.0 and deployed > bankroll * 0.9,
        },
        "scanner": {
            "markets": markets,
            "scanner_stale": markets == 0,
        },
        "ws": stats.get("ws_status", {}),
    }
```

**Daemon calls this endpoint instead of trying to infer from `/api/stats`.**

---

### Fix 4 — Improve Signal Tracking in Bot (PRE-REQUISITE)

For stagnation detection to work, bot needs better signal tracking. The current `signals` field in stats is `recent_signals_count(time.time() - 3600)` — a 1-hour aggregate. This is too coarse.

**Add per-period signal counters to bot.py:**

```python
# In bot.py — add to __init__:
self._last_signal_time: dict[str, float] = {}  # last signal time per strategy
self._signals_per_interval: int = 0
self._last_signal_check: float = time.time()

# After signal is emitted (in _handle_signal):
if signal:
    for strat in self.strategies:
        # This is rough — need to track per-strategy
        pass
```

**Better: Add signal tracking to DB with timestamp, then daemon queries:**

```python
# In daemon.py:
async def get_signal_rate(host, port) -> tuple[int, float]:
    """Get signals in last N minutes and trend."""
    # Query DB for signals in last 10 min
    # Compare to previous 10 min period
    # Return (signals_10m, trend_pct)
    pass
```

Or simplify: Add `last_signal_at` and `last_trade_at` fields to the stats API.

```python
# In bot.py _get_stats — add these fields:
stats["last_signal_at"] = max(
    s._last_signal_at.get(s.name, 0) 
    for s in self.strategies
) or None
stats["last_trade_at"] = self._last_trade_time or None
```

Then daemon can check: `if now - last_signal_at > 600: restart` (10 min no signals).

---

### Fix 5 — Enhanced Deep Health Check

Replace current deep_health_check with more comprehensive checks:

```python
def enhanced_health_check(host="127.0.0.1", port=8082, timeout=5.0) -> tuple[bool, str]:
    """v3.5.0: Comprehensive health check including activity metrics."""
    
    checks = {
        "http": False,
        "clob_ws": False,
        "binance_ws": False,
        "market_scan": False,
        "strategies_enabled": False,
        "cash_flow": False,
    }
    reasons = []
    
    try:
        # HTTP basic
        url = f"http://{host}:{port}/api/health"
        with urllib.request.urlopen(url, timeout=3) as resp:
            checks["http"] = resp.status == 200
        
        # Detailed stats
        url2 = f"http://{host}:{port}/api/stats"
        with urllib.request.urlopen(url2, timeout=timeout) as resp:
            stats = json.loads(resp.read().decode())
        
        ws = stats.get("ws_status", {})
        
        # WS checks
        checks["clob_ws"] = ws.get("clob_connected", False) and ws.get("clob_tokens", 0) > 0
        checks["binance_ws"] = ws.get("binance_connected", False)
        
        # Market scan check
        markets = stats.get("markets", 0)
        checks["market_scan"] = markets >= 10  # at least 10 markets tracked
        
        # Strategy check
        disabled = stats.get("risk", {}).get("disabled_strategies", [])
        checks["strategies_enabled"] = len(disabled) < 3  # not all disabled
        
        # Cash flow check
        cash = stats.get("cash", 0)
        bankroll = stats.get("bankroll", 0)
        deployed = stats.get("deployed", 0)
        checks["cash_flow"] = not (bankroll > 30 and cash < 1.0 and deployed > bankroll * 0.9)
        
        # Collect failures
        for check, passed in checks.items():
            if not passed:
                reasons.append(check)
        
        all_passed = all(checks.values())
        status = "OK" if all_passed else f"FAIL: {', '.join(reasons)}"
        return all_passed, status
        
    except Exception as e:
        return False, f"check exception: {e}"
```

---

### Fix 6 — Restart Reason Tracking

Add more granular restart reasons to help debug patterns:

```python
RESTART_REASONS = {
    "basic_health_fail": "HTTP health check failed",
    "clob_ws_down": "CLOB WebSocket disconnected",
    "binance_ws_down": "Binance WebSocket disconnected", 
    "no_tokens": "CLOB WS tracking 0 tokens",
    "stagnation_bankroll": "Bankroll unchanged for 15+ min",
    "stagnation_signals": "No signals generated for 10+ min",
    "stagnation_positions": "Positions stuck open",
    "all_strategies_disabled": "All strategies circuit-broken",
    "cash_stuck": "Cash locked in positions, cannot trade",
    "scanner_dead": "0 markets tracked",
    "crash": "Bot process exited unexpectedly",
}
```

This helps identify patterns over time — which restart reason is most common?

---

## 🎯 Implementation Priority

| Priority | Fix | Impact | Effort |
|----------|-----|--------|--------|
| 🔴 **1** | Fix 1 — Activity Baseline Detection | Detect stagnation → restart | Medium |
| 🔴 **2** | Fix 2 — StagnationDetector (state delta) | Detect frozen state → restart | Medium |
| 🟡 **3** | Fix 4 — Better signal tracking | Enable stagnation detection | Low |
| 🟡 **4** | Fix 5 — Enhanced health check | More accurate health assessment | Low |
| 🟢 **5** | Fix 3 — New API endpoint | Cleaner daemon-bot interface | Medium |
| 🟢 **6** | Fix 6 — Restart reason tracking | Debugging patterns | Low |

**Minimum viable fix:** Implement Fix 1 + Fix 5 (basic). This alone will detect most stagnation cases.

---

## 📊 Recommended Thresholds

| Metric | Warning Threshold | Restart Threshold |
|--------|------------------|------------------|
| No signals (min_edge strategies) | 5 min | 10 min |
| No trades (bankroll > $30) | 15 min | 30 min |
| Bankroll unchanged | 15 min | 30 min |
| Open positions stuck | 20 min | 60 min |
| Cash stuck (high bankroll, low cash) | 5 min | 15 min |
| All strategies disabled | 1 min | 5 min |
| Markets tracked = 0 | 1 min | 5 min |
| WS disconnected | 30s | 2 min |

---

## 🔧 Suggested Daemon Config (Environment Variables)

```bash
# Daemon activity thresholds (env vars)
DAEMON_STAGNATION_THRESHOLD_MIN=15    # Restart if no activity for 15 min
DAEMON_SIGNAL_TIMEOUT_SEC=600         # Restart if no signals for 10 min  
DAEMON_TRADE_TIMEOUT_SEC=1800         # Restart if no trades for 30 min
DAEMON_CASH_STUCK_MIN=15              # Restart if cash stuck for 15 min
DAEMON_ACTIVITY_CHECK_INTERVAL=120    # Check activity every 2 min
DAEMON_STAGNATION_RESTART_COOLDOWN=1800  # Don't restart same reason within 30 min
```

---

## 📝 Changelog Entry Suggestion

```markdown
## [3.5.0] — TBD

### ✨ Added — Stagnation Detection (Daemon)
- Daemon now monitors BOT ACTIVITY, not just bot liveness
- New activity checks: signal rate, trade rate, bankroll delta, cash flow
- StagnationDetector tracks state changes over time
- Auto-restart when bot is "alive" but "stuck" for 15+ minutes
- Enhanced deep health check (6-point check vs previous 4-point)
- Configurable thresholds via environment variables

### 🐛 Fixed — Daemon blind spots
- Previously: Daemon only checked HTTP 200 + WS connected
- Now: Daemon also checks signal generation, trade execution, cash flow
- Stagnation scenarios now detected and auto-restarted
```

---

## 🧪 Testing Recommendations

1. **Simulate stagnation scenarios:**
   - Stop scanner loop → verify daemon restarts within threshold
   - Disable all strategies → verify daemon detects and restarts
   - Lock cash in positions → verify daemon detects cash stuck
   - Freeze bankroll → verify stagnation detector triggers

2. **Test false positive prevention:**
   - Low activity period (night) → verify daemon doesn't restart unnecessarily
   - Legitimate no-trades period (no opportunities) → verify no restart
   - Strategy cooling down → verify no restart

3. **Test restart cooldown:**
   - Verify daemon doesn't restart more than once per stagnation event
   - Verify cooldown works across different stagnation types

---

*Audit completed by Arena.ai Agent Mode — 2026-06-27*  
*File: `audit/recommendation-daemon.md`*