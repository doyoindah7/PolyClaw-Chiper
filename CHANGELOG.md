# Changelog — PolyClaw-Cipher v3

All notable changes to PolyClaw-Cipher v3 are documented here.
Format: Keep a Changelog, Adheres to Semantic Versioning.

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

### From V3_REVISED_TARGET.md (Week 1 remaining)
- ⏸️ Connect strategies to event bus (currently pull-based, 1s loop)
  - latency_arb should subscribe to `binance_tick` topic
  - momentum should subscribe to `clob_tick` topic
  - Target: <50ms reaction vs current 1s

### From V3_REVISED_TARGET.md (Week 2-4)
- ⏸️ Improve `_implied_prob_above()` — add time decay + volatility model
- ⏸️ Add BNB/XRP/DOGE to Binance feed
- ⏸️ Implement Telegram alerts (currently stub)
- ⏸️ Market category filter for momentum (skip sports, focus on predictable)

### From V3_REVISED_TARGET.md (Week 3-5)
- ⏸️ Implement LLM agent (news_llm strategy)
- ⏸️ News scraper (Nitter + RSS)
- ⏸️ LLM-assisted resolution_snipe

### From V3_ANALYSIS.md (lower priority)
- ⏸️ Periodic resolution check (every 10-15s for markets <1h to close)
- ⏸️ Cache trade stats in memory (reduce DB queries)
- ⏸️ Prometheus metrics implementation
- ⏸️ Unit tests (pytest infrastructure ready)
