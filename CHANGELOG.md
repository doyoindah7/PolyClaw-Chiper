# Changelog — PolyClaw-Cipher v3

All notable changes to PolyClaw-Cipher v3 are documented here.
Format: Keep a Changelog, Adheres to Semantic Versioning.

---

## [3.2.0] — 2026-06-27 (Session: market category filter + atomic_arb pair fix)

### ✨ Added
- **Market category classification system** (`core/types.py`):
  - 6 categories: `sports_match`, `sports_derivative`, `politics`, `economics`, `crypto`, `entertainment`
  - `CATEGORY_PATTERNS` dict dengan regex patterns untuk klasifikasi otomatis
  - `classify_market(question)` function — standalone classifier
  - `Market.classify()` method — cached classification
  - `Market.is_random_outcome` property — True untuk sports_match + entertainment
  - `Market.market_category` field — stored di parse time
- **Category filter untuk momentum & resolution_snipe**:
  - `skip_random_outcome: true` config
  - `allowed_categories` list config
  - Momentum: allows crypto, sports_derivative (O/U goals predictable), economics, other
  - Resolution_snipe: HANYA crypto, economics, other (skip ALL sports — upset risk)
- **Atomic_arb pair execution** (`execution/paper.py`):
  - `take_pair_sibling()` method — returns second position untuk pair signals
  - Executor sekarang creates BOTH legs (YES + NO) untuk atomic_arb
  - Pair shares calculated dari `combined_ask` (same shares on both sides)
  - If any leg fails fill, entire pair rejected (atomic)
- **Bot pair sibling handling** (`bot.py`):
  - Setelah `execute_entry()`, cek `take_pair_sibling()` untuk second position
  - Persist sibling position ke DB + debit wallet + register entry di strategy
  - Log: "PAIR SIBLING: YES/NO @ price | $invested"
- **Market categories logging** di scan cycle:
  - `Counter(m.classify() for m in self._markets)` — tampilkan kategori breakdown
  - Example: `categories: {'sports_match': 126, 'other': 111, 'crypto': 9, ...}`

### 🔧 Changed
- **`cash_min_pct: 0 → 10`** — keep 10% cash buffer untuk new entries
  - Reason: v3.1.0 bot got stuck at $0.15 cash (99.4% deployed, couldn't trade)
  - With 10% buffer, selalu ada room untuk entry baru setelah profit close
- **`min_entry_price: 0.05 → 0.30`** (momentum)
  - Reason: skip low-probability entries yang sering loss
  - v3.1.0 ada "Will Spain win?" NO @ 0.2556 → turun ke $0.001 = -99.6% loss
  - 0.30 = skip market dengan odds < 30% (terlalu risky untuk momentum)
- **`min_position_usd: 1.00 → 2.00`** — minimum trade size raised
- **Strategy stats tracking improved** (`bot.py`):
  - `_find_strategy()` sekarang None-safe (handles empty name)
  - Debug logging kalau strategy name tidak ditemukan
- **Config comment updated** — "runs alongside v2" → "v2 stopped, v3 only"

### 🐛 Fixed
- **MASALAH-1 (V31_ANALYSIS.md): 99.4% cash deployed** — bot terkunci
  - Fix: `cash_min_pct: 10` ensures 10% cash buffer always available
- **MASALAH-2 (V31_ANALYSIS.md): Momentum masuk sports market** — sama seperti bug v2
  - Fix: Category filter skip `sports_match` dan `entertainment`
  - Sports winner/draw = random outcome, momentum tidak punya edge
- **MASALAH-3 (V31_ANALYSIS.md): "Will Spain win?" NO @ 0.2556 → -99.6% loss
  - Fix: `min_entry_price: 0.30` skip entries di bawah 30%
- **MASALAH-4 (V31_ANALYSIS.md): Atomic_arb single-leg** — bukan arbitrage real
  - Fix: Executor creates BOTH legs via `take_pair_sibling()`
  - Bot persists sibling position + debits wallet untuk kedua legs
- **MASALAH-5 (V31_ANALYSIS.md): Resolution_snipe di sports market**
  - Fix: Category filter — hanya snipe crypto/economics/other (deterministic resolution)
- **MASALAH-7 (V31_ANALYSIS.md): Strategy stats semua 0**
  - Fix: `_find_strategy()` None-safe + debug logging

### 📊 Verified Working (post-deploy)
- Container healthy, uptime 8+ menit
- Market categories logged: sports_match=126, sports_derivative=30, crypto=9, economics=15, politics=6, entertainment=3, other=111
- Bankroll: $25.00, cash: $19.47 (77% idle — cash buffer working)
- 4 signals emitted, 1 open position, 0 closed trades (new session)
- 0 errors in logs

### ⏸️ Still Pending (MASALAH yang belum fix)
- **MASALAH-6: 0 crypto Up/Down detection** — scanner timing issue
  - Crypto markets resolve cepat, scan 60s kadang miss
  - Fix needed: scan lebih sering untuk crypto-specific markets, atau relax filter
- **MASALAH-8: sync_connections() setiap 60s** — disruptive
  - Cancel + respawn connections = gap data beberapa detik
  - Fix needed: only sync kalau token list actually berubah (compare IDs, bukan count)
- **MEDIUM-2: Event bus masih tidak dipakai strategi** — pull-based 1s, target <50ms

---

## [3.1.0] — 2026-06-27 (Session: v2 sunset + strategy hardening)

### 🗑️ Removed
- **v2 container stopped** — `polyclaw-cipher` (port 8080) stopped & set to `restart=no`.
  Source code kept at `/home/ubuntu/polyclaw-cipher/` for documentation.
  Reason: free up VPS resources (t2.small, 2GB RAM) for v3 focus.
- **v2 side-by-side dashboard** — removed dual-column v2/v3 comparison layout.
  Dashboard is now v3-only, full-width, more detailed.
- **`/api/v2/stats` proxy endpoint** — no longer needed (v2 stopped).
- **`V2_API_URL` env var** — removed from docker-compose.yml, .env, config.

### ✨ Added
- **Stop-loss + take-profit for resolution_snipe** strategy:
  - `stop_loss_pct: 10.0` — exit if odds drop -10% from entry (previously unlimited downside)
  - `take_profit_pct: 15.0` — exit if odds rise +15% (take early profit, don't wait for resolution)
  - `register_entry()` and `clear_position()` methods implemented
  - Market resolution still handled separately by `resolve_position()`
- **Wallet invariant check** in stats refresh loop (BUG-1 fix from V3_ANALYSIS.md):
  - Every 3s: verify `bankroll == cash + total_invested`
  - If violated (> $0.01 diff), log error + recalculate from DB truth
- **Dashboard v3-only layout** (full rewrite of http_server.py HTML):
  - 6 KPI cards full width: Bankroll, P&L, Cash, Deployed, Open Positions, Win Rate
  - Capital allocation bar (cash vs deployed %)
  - Open positions table with **unrealized P&L** column (real-time)
  - Per-strategy cards with 5 stats (Signals, Trades, W/L, PnL, WR)
  - Recent trades with full details (entry, exit, PnL $, PnL %, reason, age)
  - Risk status grid (6 items): DD limit, consec losses, rate, daily P&L, session, disabled
  - System status grid (6 items): markets, crypto, CLOB WS, Binance WS, BTC, uptime
  - `is_pair` badge for atomic_arb positions
  - Hover tooltips on truncated market questions

### 🔧 Changed
- **atomic_arb threshold lowered**: `min_profit_bps: 100 → 40`
  - Reason: Polymarket markets are efficient, real arbs are 20-50 bps
  - Previous 100 bps (1%) threshold meant strategy never fired
  - Now will detect smaller but real arbitrage opportunities
- **Dashboard auto-refresh**: 3s → 5s (stable, less API hammering)
- **Stats cache refresh**: 2s → 3s (less DB load, still real-time feel)
- **Bot orchestration**: `clob_feed.sync_connections()` called after all `track()` done
  - Previously `_spawn_connections()` called per-track() → only 1 token ever subscribed (BUG-2)
  - Now batches ALL tracked tokens into proper WS connections
  - Verified: 36 tokens subscribed (was 1 before)
- **Daemon health check**: uses `127.0.0.1` always (not `HTTP_HOST` env var)
  - Reason: `0.0.0.0` valid for BINDING but not for CONNECTING
  - Previously caused restart loop (health check always failed)
- **Binance WS `pct_move()`**: fixed tuple vs float bug
  - `ticks` stored as `(timestamp, price)` tuples but `pct_move()` accessed as float
  - Caused `_refresh_stats_loop()` to crash every 2s → stats cache stale
  - Fixed: `recent[-lookback_ticks][1]` (extract price from tuple)

### 🐛 Fixed
- **BUG-1 (V3_ANALYSIS.md): Wallet inconsistency** — $15.91 "lost" without trade
  - Root cause: stats cache crash from Binance WS tuple bug → cache never updated
  - Fix: tuple bug fixed + wallet invariant check added
- **BUG-2 (V3_ANALYSIS.md): CLOB WS only tracked 1 token** — all CLOB-dependent strategies blind
  - Root cause: `_spawn_connections()` returned early after first connection
  - Fix: `sync_connections()` batches all tokens, restarts connections with full list
- **BUG-6 (V3_ANALYSIS.md): resolution_snipe no stop-loss** — unlimited downside
  - Root cause: `check_exit()` returned `(False, "")` always
  - Fix: implemented TP/SL exit logic
- **Daemon restart loop** — bot uptime stuck at 2s
  - Root cause: health check used `0.0.0.0` as connect destination (invalid)
  - Fix: hardcode `127.0.0.1` for health check

### 📊 Verified Working (post-deploy)
- CLOB WS: 36 tokens subscribed (was 1)
- Bankroll invariant: $25.00 = $6.79 cash + $18.21 deployed ✓
- 4 open positions visible in dashboard with unrealized P&L
- 7 signals emitted (4 executed, 3 rejected)
- 0 errors in logs
- Dashboard public: http://3.107.53.103:8082/ (HTTP 200, 2ms response)
- Auto-refresh 5s with retry + fallback to last good data

---

## [3.0.0] — 2026-06-27 (Initial v3 release)

### ✨ Added
- **Complete rewrite** from v2 with HFT-capable architecture
- **WebSocket CLOB feed** (replaces v2 REST polling, 60x faster)
- **Event-driven architecture** — in-process pub/sub event bus
- **4 active strategies**: latency_arb, atomic_arb, resolution_snipe, momentum
- **1 stubbed strategy**: news_llm (interface ready for autoclaw to implement LLM)
- **Real resolution detection** — uses `closed` + `resolvedBy` fields (fixes v2 fake resolution bug)
- **Async paper executor** — `await asyncio.sleep()` (fixes v2 `time.sleep()` blocking)
- **Atomic pair-trade arbitrage** — YES+NO simultan (fixes v2 fake single-leg "arb")
- **SQLite WAL state** — atomic, queryable, async (replaces v2 JSON with ~30 writes/min)
- **Unified risk manager** — per-strategy budget + circuit breaker
- **FastAPI HTTP server** — proper framework (replaces v2 hand-rolled HTTP parser)
- **JSON structured logs** — via structlog
- **Daemon with exponential backoff** — 5s → 300s, reset after 1h stable
- **Wallet invariant check** — bankroll == cash + invested
- **Multi-leg Signal model** — supports pair trades
- **HANDOFF_AUTOCRAW.md** — guide for autoclaw to extend bot
- **ARCHITECTURE.md** — 700-line design document

### 🔧 Configuration
- `config/default.yaml` — main config with 5 strategies, risk budgets, execution params
- `config/paper.yaml` — paper mode overlay
- `.env.example` — environment variable template
- Docker setup: `Dockerfile`, `docker-compose.yml`, `.dockerignore`

### 📊 Deployment
- Container: `polyclaw-cipher-v3` (Docker, restart=unless-stopped)
- Port: 0.0.0.0:8082 (public access, like v2)
- Resource limit: 1GB RAM, 1 CPU (t2.small friendly)
- Health check: `/api/health` endpoint
- Runs alongside v2 (port 8080) for comparison — v2 later stopped in 3.1.0

---

## Pending (for autoclaw / future sessions)

### From V31_ANALYSIS.md (v3.2.0 remaining)
- ⏸️ **MASALAH-6: Fix 0 crypto Up/Down detection** — scanner timing issue
  - Scan crypto markets lebih sering, atau relax filter
- ⏸️ **MASALAH-8: Optimize sync_connections()** — only sync when token list actually changes
  - Compare actual token IDs, bukan hanya count
- ⏸️ **MEDIUM-2: Connect strategies ke event bus** — currently pull-based 1s, target <50ms

### From V3_REVISED_TARGET.md (Week 2-4)
- ⏸️ Improve `_implied_prob_above()` — add time decay + volatility model
- ⏸️ Add BNB/XRP/DOGE to Binance feed
- ⏸️ Implement Telegram alerts (currently stub)
- ✅ ~~Market category filter for momentum~~ — DONE in v3.2.0

### From V3_REVISED_TARGET.md (Week 3-5)
- ⏸️ Implement LLM agent (news_llm strategy)
- ⏸️ News scraper (Nitter + RSS)
- ⏸️ LLM-assisted resolution_snipe

### From V3_ANALYSIS.md (lower priority)
- ⏸️ Periodic resolution check (every 10-15s for markets <1h to close)
- ⏸️ Cache trade stats in memory (reduce DB queries)
- ⏸️ Prometheus metrics implementation
- ⏸️ Unit tests (pytest infrastructure ready)
