# Changelog — PolyClaw-Cipher v3

All notable changes to PolyClaw-Cipher v3 are documented here.
Format: Keep a Changelog, Adheres to Semantic Versioning.

---

## [3.4.4] — 2026-06-27 (Kimi + Arena audit fixes — strategy stats sync + latency_arb)

Based on dual audit by **Kimi AI** and **Arena.ai Agent Mode**.

### 🐛 Fixed
- **CRITICAL: Strategy stats not syncing from DB** (Kimi #1 + Arena CRITICAL)
  - Symptom: Dashboard showed 0 signals/trades/W/L/PnL for ALL strategies despite 50 real trades in DB
  - Root cause: `_build_stats_sync()` read in-memory counters (reset to 0 on every restart).
    `_refresh_stats_loop()` only overrode `signals_emitted` from DB, not trades/wins/losses/pnl.
  - Fix: `_refresh_stats_loop()` now calls `per_strategy_stats()` from DB and overrides ALL
    strategy fields (trades, wins, losses, win_rate, pnl) — DB is source of truth.
  - Also added default values to `_build_stats_sync()` for cold start (before cache populates).

- **MEDIUM: Prometheus total_trades=0** (Kimi #2 + Arena MEDIUM)
  - Symptom: `polyclaw_total_trades_count` metric showed 0.0 despite 50 closed trades
  - Root cause: Same as above — `_build_stats_sync()` fallback didn't include trade counts
  - Fix: Added default values (trades=0, wins=0, etc.) to `_build_stats_sync()` for cold start.
    Once `_refresh_stats_loop()` runs (3s), correct values populate from DB.

- **MEDIUM: Latency arb dead — min_edge_pct too high** (Kimi #3 + Arena MEDIUM)
  - Symptom: `latency_arb` had 0 signals despite 18+ crypto markets in scan
  - Root cause: `min_edge_pct: 2.0` (2%) too high for efficient Polymarket — real gaps rarely exceed 1%
  - Fix: Lowered to `1.0` (1%). Added debug logging for crypto markets without threshold pattern.
  - Note: Arena identified potential threshold comparison bug, but v3.4.1 CDF model already
    fixed that — the remaining issue was purely the edge threshold being too high.

### 📊 Verified Post-Deploy
- Strategy stats now show DB-accurate data:
  - momentum: 58 signals, 44 trades, 26W/18L, 59% WR, +$22.91 PnL
  - atomic_arb: 8 signals, 6 trades, 3W/3L, 50% WR, +$6.25 PnL
  - resolution_snipe: 11 signals, 0 closed trades (2 still open)
  - latency_arb: 0 signals (threshold lowered to 1.0%, monitoring)
- Prometheus: `polyclaw_bankroll_usd 54.17`, `polyclaw_win_rate_pct 58.0`
- Bankroll: $54.17 (+116.7%), Cash: $26.64
- 0 errors in logs

---

## [3.4.3] — 2026-06-27 (Critical: resolved markets never closed → cash locked forever)

### 🐛 Fixed — CRITICAL BUG (3 stacked issues)

**Symptom:** Bot appeared stuck — bankroll frozen at $47.91 for hours, 0 new signals,
0 new trades. Cash trapped at $4.18 while $43.73 locked in 8 open positions (6 of which
were in already-resolved markets from June 26).

**Root cause:** 3 bugs stacked, preventing position resolution detection:

1. **Scanner only queries `active=true`** → resolved markets (closed=true) drop out of
   scan entirely. Bot never sees them again → positions never close.
   - Fix: `_manage_positions()` now fetches market status from Gamma API for any open
     position whose condition_id is NOT in the active scan results.

2. **`fetch_market()` used wrong API approach** → Gamma API returns 422 for path
   parameter (`/markets/{condition_id}`). API doesn't support `condition_id` as query
   filter either (returns wrong market).
   - Fix: Fetch closed markets batch (`closed=true&limit=200`), filter by `conditionId`
     client-side.

3. **`get_winning_side()` checked wrong field** → `resolvedBy` field contains
   **oracle address** (`0x69c47De9D4...`), NOT token IDs. Code assumed it was token IDs,
   so matching always failed → returned None → position never resolved.
   - Fix: Use outcome prices instead (winning side ≈ 1.0, losing ≈ 0.0). Also keeps
     resolvedBy check as fallback in case Polymarket changes format.

### 📊 Impact (verified post-deploy)
- **6 positions resolved immediately** on first scan after deploy
- Bankroll: $47.91 → **$54.17** (+$6.26 profit from resolutions)
- Cash: $4.18 → **$39.47** ($35.29 freed — bot can trade again!)
- Open positions: 8 → **2** (BTC above $58k + Hormuz, still active)
- Trades: 44 → **50** (6 new closed trades)
- P&L: +$22.91 (+91.7%) → **+$29.17 (+116.7%)**
- Bot resumed generating signals (cash available for new entries)

### Files Changed
- `src/polyclaw_cipher_v3/bot.py` — `_manage_positions()`: fetch missing markets from API
- `src/polyclaw_cipher_v3/core/scanner.py` — `fetch_market()`: query closed markets batch
- `src/polyclaw_cipher_v3/core/resolution.py` — `get_winning_side()`: price-based detection

---

## [3.4.2] — 2026-06-27 (Production Hardening)

### ✨ Added
- **Comprehensive test suite** (`tests/test_bot_logic.py`): Unit tests covering `Wallet` cash reservation and debit safety guards, `RiskManager` correlation exposure checking, and `LatencyArbStrategy` log-normal CDF calculations.
- **Config validation with Pydantic Settings** (`src/polyclaw_cipher_v3/config.py`): Implemented schemas ensuring strict type, range, and layout checking of all configurations upon startup.
- **Dashboard Basic Authentication** (`src/polyclaw_cipher_v3/core/http_server.py`): Protected dashboard routes (`/`, `/api/stats`, `/api/config`, `/metrics`) under Basic HTTP authentication with custom credentials support. Bypasses auth for local requests from localhost/daemon to prevent loop issues.
- **Graceful Shutdown in Auto-Healing Daemon** (`scripts/daemon.py`): Modified daemon shutdown behavior to prioritize graceful termination (`SIGTERM`) over immediate forced kills (`SIGKILL`), allowing database WAL checkpoints and connection closeups to complete cleanly.
- **Real Prometheus Metrics Integration** (`src/polyclaw_cipher_v3/core/http_server.py`): Integrated `prometheus_client` to expose actual real-time Gauges (bankroll, cash, net PnL, open positions, closed trades, win rate, asset price, uptime) on `/metrics`.

---

## [3.4.1] — 2026-06-27 (Phase 2 Strategy & Risk Improvements)

### ✨ Added
- **Dynamic asset volatility estimation** (`src/polyclaw_cipher_v3/core/binance_ws.py`): `BinanceFeed` now tracks tick history and dynamically calculates rolling standard deviation of log returns to produce daily volatility.
- **Time-weighted CDF Model** (`src/polyclaw_cipher_v3/strategy/latency_arb.py`): Replaced naive linear probability model with a log-normal cumulative distribution function (CDF) scaling volatility over remaining seconds-to-expiry.
- **Correlation-Aware Exposure Limits** (`src/polyclaw_cipher_v3/risk/manager.py`): Added directional net exposure calculation ($YES - $NO) per cryptocurrency asset (BTC/ETH/SOL). Limit configured as `max_net_exposure_per_asset_pct` (50% of bankroll). Fully-hedged atomic arbs are automatically ignored.
- **Cash Reservation Pipeline** (`src/polyclaw_cipher_v3/state/wallet.py` & `bot.py`): Implemented stateful `_reserved_cash` to block and lock notional sizes during async trade execution steps. Strategies evaluate using `available_cash` to prevent over-allocation races.
- **Startup State Restoration** (`src/polyclaw_cipher_v3/bot.py`): Restores in-memory position metrics (`_entry_prices` and `_entry_times`) from active SQLite database rows on daemon startup, preventing hanging exit checks.

### 🗑️ Removed
- **Unused EventBus Overhead** (`src/polyclaw_cipher_v3/bot.py` & feeds): Stopped initialization of unused EventBus in the bot. Feeds now support optional event broadcasting to skip queue allocations when no subscribers exist.

---

## [3.4.0] — 2026-06-27 (Phase 1 Critical Bug Fixes)

### 🐛 Fixed
- **Double-Close Race Condition** (`src/polyclaw_cipher_v3/bot.py`): Added `asyncio.Lock` block and optimistic exists check inside `_close_position` to prevent concurrent resolution/TP/SL exits from double-crediting positions.
- **Negative Cash/Wallet Overdrafts** (`src/polyclaw_cipher_v3/state/wallet.py`): Introduced `InsufficientFundsError` guard in `Wallet.debit()` to prevent cash balances from dropping below $0 under concurrent orders. Added matching rollback handlers in `bot.py`.
- **Latency-Arb O(N²) Database bottleneck** (`src/polyclaw_cipher_v3/bot.py`): Implemented stateful open positions cache (`self._cached_open_positions`), refreshed on loops and trade executions, eliminating repetitive sqlite queries.
- **Stale Fallback Category** (`src/polyclaw_cipher_v3/strategy/momentum.py`): Fixed fallback allowed categories default from stale v3.2 `sports_derivative` to v3.3 `sports_total`.
- **Resolution Snipe Price Lag** (`src/polyclaw_cipher_v3/strategy/resolution_snipe.py`): Injected `CLOBFeed` WS subscriber so resolution snipping uses real-time ~50ms lag orderbook prices instead of stale 60-second Gamma API snapshots.
- **Binance WS tick status spam** (`src/polyclaw_cipher_v3/core/binance_ws.py`): Removed high-frequency `ws_status` event publish (5-20x/second) on every Binance tick.
- **Database Non-Atomic Commit** (`src/polyclaw_cipher_v3/state/db.py`): Added `execute_batch` and transaction commits to SQLite WAL database, ensuring multi-step position write operations are atomic.

---

## [3.3.1] — 2026-06-27 (Hotfix: atomic_arb category filter + sizer deadlock)

Two bugs caught by **autoclaw** (AI agent review of v3.3.0 deployment).

### 🐛 Fixed
- **BUG: atomic_arb had NO category filter** — traded `sports_spread` (random outcome)
  - Symptom: Bot traded "Spread: Belgium (-2.5)" via atomic_arb, locking $14.34 in gambling position
  - Root cause: `atomic_arb.py` didn't implement `skip_random_outcome` / `allowed_categories`
    (only momentum + resolution_snipe had category filter from v3.2.0)
  - Fix: Added category filter to atomic_arb (same as other strategies)
  - Config: `skip_random_outcome: true`, `allowed_categories: ["crypto", "sports_total", "economics", "politics", "other"]`

- **BUG: Dynamic cash buffer created DEADLOCK** — bot stuck, couldn't trade
  - Symptom: Cash $4.18 (8.7%), deployed 91.3% > 70% threshold → dynamic buffer forces
    25% reserve = $11.98 → `deployable = max(0, 4.18 - 11.98) = 0` → sizer returns 0
  - Impact: 3 resolution_snipe signals REJECTED, bot couldn't open new positions
  - Root cause: Dynamic buffer logic (v3.3.0) was too aggressive — when over-deployed,
    it blocked ALL new trades instead of allowing reduced-size entries
  - Fix: Emergency mode in sizer — if `deployable < min_position_usd` AND `cash > min_position_usd`,
    allow `deployable = cash * 0.5` (reduced trading, not blocked)
  - Result: Bot can trade with ~$2.09 deployable (50% of $4.18 cash), high-confidence
    signals (>0.8) execute with ~$2.42 notional. Bot stays active, generates TP/SL exits
    to free cash naturally.

### 📊 Context
- Autoclaw reviewed v3.3.0 after deploy, caught 2 bugs that 4 AI reviewers (Z.ai + Claude + Lisa + Grok) all missed
- Profit at time of fix: $47.91 (+91.7% from $25 initial)
- 8 open positions (2 resolution_snipe + 3 atomic_arb pairs), $43.73 deployed
- Cross-review between AI agents continues to be valuable

---

## [3.3.0] — 2026-06-27 (Session: multi-AI review consensus — 8 fixes)

Based on cross-review by 3 AI (Claude, Lisa/Qwen, Grok). All conflicts resolved via
discussion. See `SUMMARY_V3_REVIEW_DISCUSSION.md` for full review history + consensus.

### ✨ Added
- **Market category split** (`core/types.py`):
  - `sports_derivative` (bundled) → split into `sports_total` (O/U goals, predictable
    Poisson) + `sports_spread` (spread/handicap, random — 1 goal = flip outcome)
  - Based on Claude's insight: point spread is statistically closer to sports_match
    (random) than to O/U goals (predictable)
  - `is_random_outcome` property updated: now includes `sports_spread`
- **Dynamic cash buffer** (`risk/sizer.py`):
  - Auto-increase reserve from 15% → 25% if deployed > 70% of bankroll
  - Config: `dynamic_cash_buffer: true`, `high_deploy_threshold: 0.70`, `high_deploy_reserve: 0.25`
  - Middle ground between Lisa's 10% (too aggressive) and Grok's 25-30% (too conservative)
- **Opportunity-rate tracking** (`strategy/resolution_snipe.py`):
  - Track `_opportunity_scan_count` (total markets evaluated)
  - Track `_opportunity_qualified` (passed category + time filter)
  - Track `_opportunity_in_band` (in price band 0.88-0.97)
  - `qualify_rate_pct` in stats() — helps determine if 30-50 sample achievable
  - Claude's suggestion: empirical data collection vs theoretical estimates
- **atomic_arb leg delay simulation** (`execution/paper.py`):
  - 200-500ms delay between leg 1 and leg 2 fill
  - Price drift simulation (±3 bps) on leg 2 during delay
  - Models real-world leg risk (Polymarket has no native multi-leg atomic order)
  - PnL tagged "paper-only, leg-risk simulated" in logs
- **Explicit `untrack()` call** in bot scan cycle (`bot.py`):
  - Compute set diff of token IDs (old vs new top-50)
  - Call `untrack()` for tokens no longer in top markets
  - Fixes Claude's BUG-3: `untrack()` was 0 call sites, token list only grew
- **Multi-AI review documentation** (audit trail):
  - `REVIEW_CLAUDE_v3.2.0.md` — Claude full review
  - `REVIEW_LISA_v3.2.0.md` — Lisa/Qwen full review
  - `REVIEW_GROK_v3.2.0.md` — Grok full review
  - `REVIEW_DISCUSSION_ROUND1.md` — Round 1 Q&A
  - `REVIEW_DISCUSSION_ROUND2_FINAL.md` — Round 2 final consensus
  - `SUMMARY_V3_REVIEW_DISCUSSION.md` — Summary + final decisions

### 🔧 Changed
- **3-layer config conflict resolved** (`risk/sizer.py`):
  - `risk.per_strategy.*.max_capital_pct` → PRIMARY source of truth (per-strategy cap)
  - `strategies.*.max_position_pct` → kept as fallback only (dead code, marked)
  - `risk.sizer.max_pct_per_trade` → raised to 0.65 (safety ceiling only, was 0.25 effective cap)
  - Order of `min()` calls: per-strategy cap FIRST, then global ceiling
  - Fixes Claude's BUG-1: 3 sources of truth with different values, cap effective was 25% not 40-60%
- **`record_entry()` vs `record_close()`** (`risk/manager.py`):
  - `record_entry(strategy)` → increment rate limit counter ONLY (no pnl tracking)
  - `record_close(strategy, pnl)` → update pnl/win-loss/circuit breaker ONLY (no rate limit)
  - `record_trade()` kept as deprecated alias (calls `record_close()` only)
  - Fixes Claude's BUG-2: `record_trade(strategy, 0)` on entry + `record_trade(strategy, pnl)` on close = double-count rate limit (60 trades/hr = 30 real)
- **`sync_connections()` set comparison** (`core/clob_ws.py`):
  - Compare SET of token IDs, not just count
  - Track `_last_synced_token_ids` (set) instead of `_last_synced_token_count` (int)
  - Only reconnect if token set actually changed (catches rotation in top-50)
  - Reduces disruption from "cancel+respawn every 60s" to "only when needed"
  - Log: "+N added, -N removed" for visibility
- **Cash buffer**: `cash_min_pct: 10 → 15` (middle ground consensus)
- **atomic_arb threshold**: confirmed 40 bps (kept from v3.2.0)
- **`max_pct_per_trade`**: 0.25 → 0.65 (now safety ceiling, was effective cap)
- **`min_position_usd`**: 2.00 (kept from v3.2.0)
- **resolution_snipe config**:
  - `min_odds`: 0.90 → 0.88 (relaxed, more opportunities)
  - `max_hours_to_close`: 24 → 72 (relaxed, near-certain markets reach 0.90+ weeks before close)
  - `allowed_categories`: added `politics` (was crypto/economics/other only)
  - NO sports (tail risk -93% on upset — consensus Claude + Lisa + Grok)
- **momentum config**:
  - `allowed_categories`: `sports_derivative` → `sports_total` (exclude sports_spread)
  - Based on Claude's insight: point spread is random, O/U goals is predictable
- **Config comment**: "runs alongside v2" → "v2 stopped, v3 only" (was already done v3.2.0 but re-confirmed)
- **Version bump**: 3.2.0 → 3.3.0 in `__init__.py`, `pyproject.toml`, `http_server.py` title + health API

### 🐛 Fixed
- **Claude BUG-1 (3-layer config conflict)**: Single source of truth now per-strategy cap
- **Claude BUG-2 (record_trade double-count)**: Split into `record_entry()` + `record_close()`
- **Claude BUG-3 (untrack dead code)**: Explicit `untrack()` call in scan cycle + set comparison
- **Claude factual error caught**: Aku salah claim "clob_ws.py line 154 auto-untrack on 404" — itu v2 (REST), bukan v3 (WebSocket). Fixed by implementing explicit untrack from scratch.
- **atomic_arb leg risk**: Added delay + price drift simulation (was instant+simultan, unrealistic)
- **Sports spread in momentum**: Excluded (was bundled with O/U goals, but statistically different)

### 📊 Verified Working (post-deploy v3.3.0)
- Container healthy, dashboard title "PolyClaw-Cipher v3.3.0"
- Bankroll: $42.61 (from $25.00 initial = +70.4% return)
- Cash: $27.91 (65% idle — cash buffer 15% working)
- 2 open positions, 31 closed trades, 26 signals
- CLOB WS: 34 tokens, no reconnect storms (set comparison working)
- resolution_snipe opportunity tracking: 10200 scanned, 918 qualified, 1 in_band
  (qualify_rate ~9% — empirical data now available for sample size estimation)
- 0 errors in logs
- Category split deployed: `sports_total` + `sports_spread` recognized

### ⏸️ Still Pending (consensus deferred)
- **MASALAH-6: 0 crypto Up/Up detection** — scanner timing issue (latency_arb still dead)
  - Root cause identified by Claude: `_extract_threshold()` only matches "above $X",
    but scanner matches "Up or Down — [date]" — 2 different market types conflated
  - Fix: redesign `_implied_prob_above` for directional markets, OR change latency_arb
    target to threshold-style markets
- **Event bus wiring** — strategies still pull-based (1s loop), target <50ms
  - latency_arb should subscribe to `binance_tick`
  - momentum should subscribe to `clob_tick`
- **LLM agent** — deferred. Test CryptoPanic latency real before commit (Lisa admit assumed)
- **Tests + backtesting** — infrastructure ready, not implemented
- **Sample size milestone**: 30-50 UNIQUE markets per strategy (not total trades)
  - Claude's insight: 20 trades in 1 market = 1 sample (clustered), not 20 independent

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
