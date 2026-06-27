# PolyClaw-Cipher v3 — Architecture Design

> **Status:** DRAFT — menunggu approval sebelum coding dimulai
> **Target:** HFT-capable Polymarket bot dengan AI agent untuk modal kecil ($25 → aggressive compounding)
> **Deployment:** Folder terpisah `/home/ubuntu/polyclaw-cipher-v3/` di VPS 3.107.53.103, berjalan paralel dengan v2

---

## 1. Goals & Philosophy

### 1.1 Apa yang salah dengan v2 (yang ingin kita fix)

| v2 Problem | v3 Solution |
|---|---|
| CLOB REST polling tiap 3 detik (lag 3s) | WebSocket CLOB real-time (lag ~50ms) |
| `time.sleep(0.3)` blocking event loop | Async executor, no blocking calls |
| Fake market resolution (tebak dari `end_date`) | Cek field `closed`/`resolvedBy` dari Gamma API resmi |
| "Arbitrage" strategy hanya single-leg (bukan arb) | Atomic pair-trade YES+NO simultan |
| Tidak ada LLM / news agent (lagging signals only) | LLM news agent untuk leading signals |
| Config vs dashboard mismatch (hardcoded HTML) | Dashboard baca config dari API, zero hardcode |
| Wallet JSON, ~30 disk writes/menit | SQLite WAL, async, batched writes |
| Daemon restart counter tidak reset + no alert | Reset setelah uptime stabil + exponential backoff + Telegram alert |
| Port 8080 exposed ke internet (copy-trader risk) | Bind 127.0.0.1, akses via SSH tunnel |
| `CompoundingSizer` dead code | Single source of truth untuk sizing |
| Tidak ada tests | pytest unit + integration tests |
| Tidak ada git | Git init + commit history |

### 1.2 Design Principles v3

1. **WebSocket-first** — semua harga real-time via WS, REST hanya untuk initial load & polling jarang
2. **Event-driven architecture** — internal pub/sub event bus, komponen terpisah & reactive
3. **AI agent sebagai edge primer** — LLM baca news → signal leading, bukan follow harga
4. **Latency budget < 500ms** dari signal ke order (paper execution). Live target < 200ms
5. **Atomic operations** — pair-trade, batch, dan komponen async semua via `asyncio.gather`
6. **Fail-safe by default** — auto-reconnect WS, circuit breaker per strategy, graceful degradation
7. **Observable** — structured JSON logs, Prometheus metrics, per-strategy latency histograms
8. **Paper-first dengan path clear ke live** — eksekusi abstrak via interface, swap paper → live = 1 config flag

### 1.3 Non-Goals (yang sengaja TIDAK kita lakukan di v3)

- ❌ Live trading di mainnet (paper trading dulu minimal 2 minggu)
- ❌ Cross-venue arbitrage (Kalshi/PredictIt) — v4
- ❌ Orderbook imbalance ML model — v4
- ❌ Mobile app / web frontend kompleks — dashboard HTML single-page cukup
- ❌ Multi-account / multi-wallet — single wallet focus

---

## 2. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Docker Container (auto-heal daemon)                │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │              Event Bus (asyncio pub/sub, in-process)             │ │
│  │  Topics: market_scan | clob_tick | binance_tick | news_event    │ │
│  │          signal | order_fill | position_close | risk_alert      │ │
│  └───────▲──────────▲──────────▲───────────▲────────────▲─────────┘ │
│          │          │          │           │            │            │
│   ┌──────┴───┐ ┌────┴────┐ ┌───┴────┐ ┌────┴─────┐ ┌────┴─────┐    │
│   │ Scanner  │ │CLOB WS  │ │Binance │ │ LLM News │ │ HTTP API │    │
│   │(Gamma    │ │(real-   │ │  WS    │ │  Agent   │ │ + Dashb. │    │
│   │ REST 60s)│ │ time)   │ │        │ │(z-ai-sdk)│ │(FastAPI) │    │
│   └──────┬───┘ └────┬────┘ └────┬───┘ └────┬─────┘ └──────────┘    │
│          │          │           │          │                         │
│   ┌──────▼──────────▼───────────▼──────────▼─────────────────┐      │
│   │              Signal Engine (5 strategies)                  │      │
│   │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐ │      │
│   │  │LatencyArb│ │AtomicArb │ │ResolSnipe│ │  Momentum    │ │      │
│   │  │Binance→PM│ │YES+NO<$1│ │ near-90% │ │  (refined)   │ │      │
│   │  └──────────┘ └──────────┘ └──────────┘ └──────────────┘ │      │
│   │  ┌────────────────────────────────────────────────────┐  │      │
│   │  │           LLM News Strategy (leading signals)      │  │      │
│   │  └────────────────────────────────────────────────────┘  │      │
│   └──────────────────────────┬────────────────────────────────┘      │
│                              │                                        │
│   ┌──────────────────────────▼────────────────────────────────┐      │
│   │           Risk Manager (unified gate)                       │      │
│   │  Drawdown | Consec Loss | Rate Limit | Per-Strat Budget    │      │
│   │  Position Sizer (aggressive compounding, configurable)     │      │
│   └──────────────────────────┬────────────────────────────────┘      │
│                              │                                        │
│   ┌──────────────────────────▼────────────────────────────────┐      │
│   │           Execution Layer (async interface)                 │      │
│   │  ┌──────────────┐                ┌──────────────────┐     │      │
│   │  │ PaperExecut. │ ←── swap ────► │ LiveExecutor     │     │      │
│   │  │ (async, sim) │    (1 flag)    │ (py-clob-client) │     │      │
│   │  └──────────────┘                │ (v4)             │     │      │
│   │                                  └──────────────────┘     │      │
│   └──────────────────────────┬────────────────────────────────┘      │
│                              │                                        │
│   ┌──────────────────────────▼────────────────────────────────┐      │
│   │        State (SQLite WAL via aiosqlite)                     │      │
│   │  Tables: positions | trades | signals | market_snapshots  │      │
│   │          news_events | risk_state                          │      │
│   │  + JSON export for human inspection (periodic)             │      │
│   └─────────────────────────────────────────────────────────────┘      │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  Observability: structlog JSON + Prometheus metrics (/metrics)  │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  Alerts: Telegram (trade / pnl / drawdown / crash / ws-down)    │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
                              ↑
                              │ bind 127.0.0.1:8080
                              │ (akses via SSH tunnel)
                              │
                   ssh -L 8080:localhost:8080 ubuntu@3.107.53.103
```

---

## 3. Module Breakdown

### 3.1 Folder Structure

```
polyclaw-cipher-v3/
├── ARCHITECTURE.md              ← this file
├── README.md
├── CHANGELOG.md
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .gitignore
├── config/
│   ├── default.yaml             # Main config (aggressive defaults)
│   ├── paper.yaml               # Paper trading overlay
│   └── live.yaml                # Live trading overlay (v4, disabled)
├── src/polyclaw_cipher_v3/
│   ├── __init__.py
│   ├── __main__.py              # Entry: `python -m polyclaw_cipher_v3`
│   ├── bot.py                   # Orchestrator
│   ├── config.py                # YAML + env loader
│   ├── core/
│   │   ├── __init__.py
│   │   ├── types.py             # Pydantic models (Market, Signal, Position, Trade, News)
│   │   ├── event_bus.py         # In-process pub/sub
│   │   ├── scanner.py           # Gamma API scanner (event-driven, 60s poll)
│   │   ├── clob_ws.py           # WebSocket CLOB subscriber + local orderbook
│   │   ├── binance_ws.py        # Binance WS (refined)
│   │   ├── http_server.py       # FastAPI app + dashboard HTML
│   │   └── resolution.py        # Real resolution checker (closed/resolvedBy fields)
│   ├── strategy/
│   │   ├── __init__.py
│   │   ├── base.py              # BaseStrategy with event subscription
│   │   ├── latency_arb.py       # Binance → Polymarket latency arbitrage
│   │   ├── atomic_arb.py        # YES + NO atomic arbitrage
│   │   ├── resolution_snipe.py  # Near-certain market sniping
│   │   ├── momentum.py          # Refined momentum (faster trigger)
│   │   └── news_llm.py          # LLM news agent strategy
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── base.py              # Executor interface
│   │   ├── paper.py             # Async paper executor (no time.sleep blocking)
│   │   └── live.py              # Live CLOB executor (stub, v4)
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── manager.py           # Unified risk gate
│   │   └── sizer.py             # Aggressive compounding sizer (actually used)
│   ├── state/
│   │   ├── __init__.py
│   │   ├── db.py                # aiosqlite + WAL, schema migrations
│   │   ├── wallet.py            # Wallet state (bankroll, cash)
│   │   └── repository.py        # Position/Trade/Signal repos
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── llm_client.py        # z-ai-web-dev-sdk LLM wrapper (backend only)
│   │   ├── news_scraper.py      # X filter + RSS scraper (web-search + web-reader skills)
│   │   ├── market_analyzer.py   # LLM analyze market question vs news
│   │   └── signal_router.py     # Route LLM output to strategy signals
│   ├── alerts/
│   │   ├── __init__.py
│   │   └── telegram.py          # Real Telegram alerter (no stub)
│   └── observability/
│       ├── __init__.py
│       ├── logs.py              # structlog JSON config
│       └── metrics.py           # Prometheus client + custom collectors
├── scripts/
│   ├── daemon.py                # Auto-heal daemon (fixed)
│   ├── migrate.py               # DB migration script
│   └── backup_wallet.py         # Periodic JSON export
└── tests/
    ├── conftest.py
    ├── test_event_bus.py
    ├── test_risk_manager.py
    ├── test_paper_executor.py
    ├── test_atomic_arb.py
    ├── test_resolution_checker.py
    └── fixtures/
        └── sample_markets.json
```

### 3.2 Komponen Inti

#### `core/event_bus.py` — In-process Pub/Sub

Single instance, shared across all modules. Pure asyncio.

```python
# Pseudocode
class EventBus:
    async def publish(topic: str, payload: Any)
    def subscribe(topic: str, handler: Callable) -> Subscription
    def unsubscribe(sub: Subscription)
    async def close()  # drain all handlers

# Topics:
# - "market_scan"      → payload: list[Market]
# - "clob_tick"        → payload: TickUpdate (token_id, price, ts, bid, ask)
# - "binance_tick"     → payload: BinanceTick (symbol, price, ts)
# - "news_event"       → payload: NewsEvent (source, headline, url, ts, summary)
# - "signal"           → payload: Signal
# - "order_fill"       → payload: Position
# - "position_close"   → payload: Trade
# - "risk_alert"       → payload: RiskAlert (level, message)
# - "ws_status"        → payload: WSStatus (source, connected, lag_ms)
```

**Kenapa event bus?**
- Strategy tidak perlu tahu darimana data datang. Subscribe topik, react.
- Gampang nambah strategy baru tanpa sentuh core
- Gampang testing: publish mock event, assert signal emitted
- Backpressure: jika strategy lambat, queue di event bus (bounded, drop + alert kalau overflow)

#### `core/scanner.py` — Gamma API Scanner (slower, smarter)

**Bedanya dengan v2:**
- Polling 60s (bukan 15s) — WS CLOB handle real-time prices
- Track market lifecycle via field `closed` dan `resolvedBy` (bukan tebak dari `end_date`)
- Emit `market_scan` event ke bus, strategies react
- Cache market list in-memory, only re-fetch if changed (ETag / last_modified)

**Resolution detection (fix bug v2):**
```python
def is_resolved(item: dict) -> tuple[bool, str | None]:
    """Return (is_resolved, winning_side)."""
    if not item.get("closed", False):
        return False, None
    # Check resolvedBy (array of winning token IDs)
    resolved_by = item.get("resolvedBy") or []
    if not resolved_by:
        # Market closed but not yet resolved — wait
        return False, None
    # Map winning token ID to YES/NO
    clob = json.loads(item.get("clobTokenIds", "[]"))
    if len(clob) >= 2:
        if clob[0] in resolved_by:
            return True, "YES"
        if clob[1] in resolved_by:
            return True, "NO"
    return False, None
```

#### `core/clob_ws.py` — WebSocket CLOB Subscriber

Connect ke `wss://ws-subscriptions-clob.polymarket.com/ws/`. Subscribe channels:
- `market` — orderbook snapshot per token
- `price_change` — tick updates
- `tick_size_change` — rare, ignore
- `last_trade_price` — last trade

Maintain local orderbook per token:
```python
@dataclass
class LocalOrderbook:
    token_id: str
    bids: SortedDict[float, float]  # price → size
    asks: SortedDict[float, float]
    last_price: float = 0.0
    last_update: float = 0.0
    
    def apply_snapshot(snapshot: dict)
    def apply_delta(delta: dict)
    def best_bid() -> float
    def best_ask() -> float
    def mid() -> float
    def spread_bps() -> float
```

**Auto-reconnect:** exponential backoff 1s → 60s, reset on success. Emit `ws_status` event setiap status change (untuk dashboard & alert).

**Batching:** subscribe up to 100 tokens per WS connection (Polymarket limit). Kalau track 200 tokens = 2 connections. Manager auto-balance.

**Heartbeat:** WS send ping tiap 10s, expect pong dalam 5s. Kalau tidak, reconnect.

#### `core/binance_ws.py` — Binance WS (refined v2)

Sama seperti v2 tapi:
- Tambah `@depth5` stream (top 5 orderbook) untuk latency_arb
- Tambah `@aggTrade` untuk volume spike detection
- Track per-tick timestamp untuk latency measurement
- Emit `binance_tick` event ke bus (bukan langsung ke strategy)

#### `core/http_server.py` — FastAPI + Config-Driven Dashboard

Pakai **FastAPI** (bukan hand-rolled HTTP server v2). Endpoint:

| Method | Path | Deskripsi |
|---|---|---|
| GET | `/` | Dashboard HTML (config-driven, no hardcoded values) |
| GET | `/api/stats` | Overview: bankroll, P&L, win rate, signals |
| GET | `/api/positions` | Open positions dengan current_value real-time |
| GET | `/api/trades` | Trade history (filter by strategy, limit, since) |
| GET | `/api/signals` | Signal log (filter by strategy, since) |
| GET | `/api/markets` | Active markets tracked |
| GET | `/api/risk` | Risk manager status (config + current state) |
| GET | `/api/config` | Effective config (untuk dashboard baca, no hardcode) |
| GET | `/api/health` | `{status: "ok", uptime_sec, ws_status}` untuk Docker healthcheck |
| GET | `/metrics` | Prometheus metrics |

**Bind ke `127.0.0.1:8080`** (bukan `0.0.0.0`). Akses dari luar hanya via SSH tunnel.

**Dashboard HTML:** single page, vanilla JS, fetch `/api/stats` + `/api/config` tiap 3 detik. Tidak ada hardcoded `25.0`, `/8`, `30%` dll. Semua dari API.

### 3.3 Strategies (5 aktif)

Setiap strategy inherit `BaseStrategy` dan subscribe ke topik event bus yang relevan.

#### Strategy 1: `LatencyArb` — Binance → Polymarket

**Edge:** Polymarket crypto Up/Down odds adjust 200-500ms **setelah** Binance price move. Window itu = profit.

**Cara kerja:**
1. Subscribe `binance_tick` untuk BTC/ETH/SOL
2. Maintain rolling window 60 ticks (~1 menit) per asset
3. Untuk tiap crypto Up/Down market yang aktif:
   - Parse question: "Will Bitcoin be above $100k on June 27?"
   - Threshold = $100k, asset = BTC
   - Compute implied probability dari Binance current price vs threshold
   - Bandingkan dengan Polymarket YES price
   - Jika gap > 2% (misal Binance imply 70% YES, Polymarket YES 65%) → fire signal BUY YES
4. Exit: market close (binary resolution) atau TP 5% / SL 3%

**Sizing:** 25% bankroll per trade (aggressive, edge tinggi)
**Risk:** low — edge cepat close sendiri karena odds adjust
**Expected frequency:** 10-30 trades/hari per asset
**Config:**
```yaml
latency_arb:
  enabled: true
  min_edge_pct: 2.0          # Min gap Binance-implied vs PM price
  max_position_pct: 0.25     # 25% bankroll per trade
  max_positions: 3           # Max 3 concurrent
  take_profit_pct: 5.0
  stop_loss_pct: 3.0
  exit_before_close_sec: 30  # Exit 30s sebelum market close
```

#### Strategy 2: `AtomicArb` — YES + NO < $1 (risk-free)

**Edge:** Kadang YES ask + NO ask < $1 di market yang sama (misal 0.48 + 0.49 = 0.97). Beli keduanya, collect $1 di resolution. Profit = $0.03 per share.

**Cara kerja:**
1. Subscribe `clob_tick` untuk semua tracked tokens
2. Untuk tiap market, compute `yes_ask + no_ask`
3. Jika < 0.99 (profit > 100bps setelah fee), fire **pair signal**:
   - Buy YES @ ask
   - Buy NO @ ask
   - Simultan via `asyncio.gather`
4. Exit: market resolution (collect $1 from winning side)

**Sizing:** sampai 40% bankroll per arb (low risk)
**Risk:** minimal — profit lock di entry
**Expected frequency:** 5-20 arbs/hari (small but consistent)
**Implementation note:** butuh pair-trade support di executor. `Signal` model di-update untuk support multi-leg:

```python
class Leg(BaseModel):
    token_id: str
    side: Side  # YES or NO
    price: float
    size_usd: float

class Signal(BaseModel):
    ...
    legs: list[Leg] = []  # Default single-leg (backward compatible)
    is_pair: bool = False
```

**Config:**
```yaml
atomic_arb:
  enabled: true
  min_profit_bps: 100       # Min 1% profit setelah fee
  max_position_pct: 0.40    # 40% bankroll per arb (low risk)
  max_concurrent: 5
  scan_interval_sec: 1      # Check setiap 1s (event-driven, fast)
```

#### Strategy 3: `ResolutionSnipe` — Near-Certain Market Discount

**Edge:** Market yang 99% pasti resolve YES (e.g., "Has Bitcoin reached $100k?" ketika BTC sudah $105k) sering trade di 0.93-0.97 karena orang malas hold. Beli 0.95, hold, collect $1. Profit 5% per trade.

**Cara kerja:**
1. Tiap scan, filter market dengan `end_date < 24h` dan `yes_price > 0.90`
2. **LLM agent** analyze question: "Apakah outcome market ini sudah near-certain?"
   - LLM baca question + context (current price, news)
   - Output: `{confidence: 0.0-1.0, reasoning: "..."}`
3. Jika LLM confidence > 0.85, fire signal BUY YES (atau BUY NO kalau near-certain NO)
4. Exit: market resolution

**Sizing:** 10-20% bankroll per trade (modal terkunci sampai resolve)
**Risk:** LOW kalau LLM benar identifikasi near-certain. HIGH kalau LLM salah. → Conservative sizing awal, scale up kalau track record bagus.
**Expected frequency:** 3-10 trades/hari
**Config:**
```yaml
resolution_snipe:
  enabled: true
  min_odds: 0.90             # Min YES/NO price
  max_odds: 0.97             # Max (above = no edge)
  max_hours_to_close: 24
  llm_min_confidence: 0.85
  max_position_pct: 0.15     # 15% bankroll per trade (conservative)
  max_concurrent: 5
```

#### Strategy 4: `Momentum` — Refined v2 Universal

**Edge:** Volatile market dengan sustained momentum akan continue moving short-term. Trend follow.

**Bedanya dengan v2:**
- Pakai CLOB WS ticks (bukan REST polling) → react 60x lebih cepat
- Multi-timeframe: 30s + 2m + 10m (lebih sensitif)
- Volume confirmation via trade print (bukan tick count)
- Exit otomatis di 5 menit (bukan 20 menit v2)

**Cara kerja:**
1. Subscribe `clob_tick` per token
2. Maintain rolling window 600 ticks (~5 menit)
3. Compute momentum: `(current - price_30s_ago) / price_30s_ago * 100`
4. Trigger jika: |momentum_30s| > 1.0% DAN |momentum_2m| > 0.5% (trend confirmation)
5. Direction: follow momentum (YES jika YES naik, NO jika NO naik)
6. Exit: TP 8% / SL 4% / max hold 5 menit

**Sizing:** 15% bankroll per trade (moderate aggressive)
**Risk:** medium — momentum bisa reverse
**Expected frequency:** 5-15 trades/hari
**Config:**
```yaml
momentum:
  enabled: true
  lookback_short_sec: 30
  lookback_long_sec: 120
  min_momentum_short_pct: 1.0
  min_momentum_long_pct: 0.5
  take_profit_pct: 8.0
  stop_loss_pct: 4.0
  max_hold_sec: 300         # 5 menit
  max_position_pct: 0.15
  max_positions: 3
  cooldown_sec: 30
```

#### Strategy 5: `NewsLLM` — LLM News Agent (EDGE PRIMER)

**Edge:** LLM baca breaking news, identifikasi impact ke Polymarket market, trade **SEBELUM** odds adjust. Window 10-60 detik.

**Ini yang membedakan bot kamu dari momentum-follower biasa.**

**Cara kerja:**

```
News Sources (parallel):
  ├── Twitter/X filter (@Polymarket, @binance, crypto news accounts)
  ├── RSS feeds (CoinDesk, The Block, Bloomberg Crypto, Reuters)
  └── Polymarket activity (large trades via CLOB WS)

       │
       ▼
  news_scraper.py
       │
       ▼
  NewsEvent {source, headline, body, url, ts}
       │
       ▼ (publish to "news_event" topic)
       │
  news_llm.py (subscribed)
       │
       ├──► LLM call #1: "Is this news breaking/significant?"
       │    (filter noise, only act on significant events)
       │
       ├──► LLM call #2: "Which active Polymarket markets are affected?"
       │    (LLM has list of active markets as context)
       │
       ├──► LLM call #3: "What's the implied probability shift?"
       │    Output: [{condition_id, side, new_implied_prob, confidence, reasoning}]
       │
       ▼
  Signal(s) emitted
       │
       ▼
  Risk Manager → Execution
```

**LLM implementation:**
- Pakai `z-ai-web-dev-sdk` (LLM skill) — **backend only**, jangan di client
- Streaming mode untuk latency rendah
- Cache market list sebagai context (refresh tiap 60s dari scanner)
- Prompt engineering: few-shot examples dari historical news → market reaction
- Fallback: kalau LLM lambat > 30s, skip signal (opportunity lost, better than late)

**Sizing:** 10% bankroll per trade (highest risk strategy, conservative sizing)
**Risk:** HIGH — LLM bisa hallucinate, news interpretation subjective
**Expected frequency:** 2-8 trades/hari (rare but high-impact)
**Config:**
```yaml
news_llm:
  enabled: true
  llm_model: "glm-4.5"            # via z-ai-web-dev-sdk
  max_llm_latency_sec: 30
  min_confidence: 0.70
  max_position_pct: 0.10           # 10% bankroll per trade
  max_positions: 2
  take_profit_pct: 15.0            # News moves can be big
  stop_loss_pct: 8.0
  max_hold_sec: 600                # 10 minutes (news cycle)
  sources:
    twitter_accounts:
      - "Polymarket"
      - "binance"
      - "CoinDesk"
      - "TheBlock__"
    rss_feeds:
      - "https://www.coindesk.com/arc/outboundfeeds/rss/"
      - "https://www.theblock.co/rss.xml"
```

### 3.4 Risk Manager (Unified Gate)

**Bedanya dengan v2:**
- Single source of truth — semua strategy lewat sini
- Per-strategy risk budget (config-driven)
- Position sizer **actually dipakai** (v2 dead code)
- Circuit breaker per strategy (auto-disable kalau loss streak)
- Daily reset, session rotation (tetap ada, tapi configurable)

**Decision flow:**
```
Signal from strategy
    │
    ▼
RiskManager.evaluate(signal, strategy_state, wallet_state)
    │
    ├──► Check 1: Global drawdown limit (e.g., 40% daily DD → block ALL)
    ├──► Check 2: Per-strategy consecutive loss (e.g., 5 losses → block this strategy)
    ├──► Check 3: Rate limit per strategy (e.g., 20 trades/hour)
    ├──► Check 4: Max concurrent positions per strategy
    ├──► Check 5: Per-strategy capital allocation (e.g., news_llm max 30% total)
    │
    ▼ (all pass)
Sizer.size(bankroll, cash, strategy_config, confidence)
    │
    ▼
Approved signal with notional → Executor
```

**Config:**
```yaml
risk:
  initial_bankroll_usd: 25.00
  max_daily_drawdown_pct: 50.0      # Aggressive (paper trading)
  max_consecutive_losses_global: 8
  max_trades_per_hour_global: 60
  session_rotation_min: 240         # 4 hours
  
  per_strategy:
    latency_arb:
      max_consecutive_losses: 5
      max_trades_per_hour: 30
      max_capital_pct: 0.60         # Max 60% bankroll allocated
    atomic_arb:
      max_consecutive_losses: 3     # Arb should never lose — if 3, something wrong
      max_trades_per_hour: 50
      max_capital_pct: 0.50
    resolution_snipe:
      max_consecutive_losses: 3
      max_trades_per_hour: 20
      max_capital_pct: 0.40
    momentum:
      max_consecutive_losses: 5
      max_trades_per_hour: 40
      max_capital_pct: 0.45
    news_llm:
      max_consecutive_losses: 3     # Conservative — LLM errors
      max_trades_per_hour: 15
      max_capital_pct: 0.20

  sizer:
    type: "aggressive_compounding"  # atau "kelly_fraction"
    cash_min_pct: 0                 # 100% deployment
    max_pct_per_trade: 0.25         # Hard cap per trade
    min_position_usd: 1.00          # Lower than v2 ($2.50) for finer sizing
```

### 3.5 State — SQLite WAL

**Schema:**

```sql
-- Wallet state (single row, id=1)
CREATE TABLE wallet (
    id INTEGER PRIMARY KEY DEFAULT 1,
    bankroll REAL NOT NULL,
    cash REAL NOT NULL,
    initial_bankroll REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Open positions
CREATE TABLE positions (
    id TEXT PRIMARY KEY,
    market_condition_id TEXT NOT NULL,
    market_question TEXT,
    side TEXT NOT NULL,             -- YES / NO
    token_id TEXT,
    entry_price REAL NOT NULL,
    shares REAL NOT NULL,
    invested REAL NOT NULL,
    strategy TEXT NOT NULL,
    opened_at REAL NOT NULL,
    current_price REAL,
    current_value REAL,
    is_pair INTEGER DEFAULT 0,      -- 1 if part of pair trade
    pair_id TEXT                    -- links pair legs
);
CREATE INDEX idx_positions_strategy ON positions(strategy);
CREATE INDEX idx_positions_market ON positions(market_condition_id);

-- Closed trades (historical)
CREATE TABLE trades (
    id TEXT PRIMARY KEY,
    market_condition_id TEXT NOT NULL,
    market_question TEXT,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    shares REAL NOT NULL,
    invested REAL NOT NULL,
    pnl_dollar REAL NOT NULL,
    pnl_percent REAL NOT NULL,
    strategy TEXT NOT NULL,
    reason TEXT,
    opened_at REAL NOT NULL,
    closed_at REAL NOT NULL
);
CREATE INDEX idx_trades_strategy ON trades(strategy);
CREATE INDEX idx_trades_closed_at ON trades(closed_at);

-- Signals log (for analysis)
CREATE TABLE signals (
    id TEXT PRIMARY KEY,
    market_condition_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    side TEXT NOT NULL,
    suggested_price REAL,
    suggested_size_usd REAL,
    confidence REAL,
    reason TEXT,
    timestamp REAL NOT NULL,
    executed INTEGER DEFAULT 0,     -- 1 if led to a trade
    rejected_reason TEXT            -- if not executed, why
);
CREATE INDEX idx_signals_strategy ON signals(strategy);
CREATE INDEX idx_signals_ts ON signals(timestamp);

-- Market snapshots (for backtest)
CREATE TABLE market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT NOT NULL,
    yes_price REAL,
    no_price REAL,
    yes_bid REAL,
    yes_ask REAL,
    volume_24h REAL,
    timestamp REAL NOT NULL
);
CREATE INDEX idx_snap_market_ts ON market_snapshots(condition_id, timestamp);

-- News events (for LLM agent audit trail)
CREATE TABLE news_events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,           -- "twitter" / "rss" / "polymarket"
    headline TEXT NOT NULL,
    body TEXT,
    url TEXT,
    timestamp REAL NOT NULL,
    llm_analyzed INTEGER DEFAULT 0,
    llm_summary TEXT,
    signals_emitted INTEGER DEFAULT 0
);
CREATE INDEX idx_news_ts ON news_events(timestamp);
```

**Mode:** WAL (Write-Ahead Logging) — concurrent reads + writes, no lock contention.
**Backup:** periodic export `wallet` + `trades` ke `data/wallet_export.json` tiap 5 menit (untuk human inspection & backup).

### 3.6 LLM Agent Module (`agent/`)

**`agent/llm_client.py`** — wrapper untuk `z-ai-web-dev-sdk`:
```python
class LLMClient:
    async def analyze_news_impact(news: NewsEvent, markets: list[Market]) -> list[NewsSignal]:
        """Returns list of (condition_id, side, implied_prob, confidence, reasoning)."""
    
    async def assess_near_certainty(market: Market, context: dict) -> NearCertaintyAssessment:
        """For resolution_snipe strategy."""
```

**Penting:** z-ai-web-dev-sdk **WAJIB di backend** (Python), bukan di frontend. LLM call tidak pernah expose API key ke client.

**`agent/news_scraper.py`** — multi-source scraper:
- Twitter/X: pakai skill `web-search` atau `web-reader` untuk ambil tweet terbaru dari account list
- RSS: parser `feedparser`, poll tiap 60s
- Polymarket large trades: dari CLOB WS, filter trade size > $10k
- Dedup: hash headline + body, skip if seen
- Rate limit: max 10 sources polled concurrently

**`agent/market_analyzer.py`** — LLM context builder:
- Refresh market list tiap 60s
- Untuk LLM context, format market sebagai: `[{id, question, yes_price, no_price, end_date}]`
- Truncate ke top 50 markets by volume (LLM context window limit)
- Cache result (market list tidak berubah cepat)

**`agent/signal_router.py`** — LLM output → Signal:
- Parse LLM JSON output
- Validate: condition_id exists? side in {YES, NO}? confidence 0-1?
- Build Signal object
- Publish to `signal` event bus topic

### 3.7 Observability

**Logs (`observability/logs.py`):**
- `structlog` dengan JSON output
- Field wajib: `timestamp`, `level`, `event`, `module`, `strategy?`, `market_id?`, `latency_ms?`
- Level config via env `LOG_LEVEL=INFO`
- Contoh:
```json
{"timestamp": "2026-06-27T00:30:15.123Z", "level": "info", "event": "signal_emitted", "module": "latency_arb", "strategy": "latency_arb", "market_id": "0xff37...", "side": "YES", "confidence": 0.78, "notional": 6.25, "latency_ms": 142}
```

**Metrics (`observability/metrics.py`):**
- `prometheus_client` + `aiohttp` middleware
- Counter: `signals_emitted_total{strategy}`, `trades_executed_total{strategy,side}`, `trades_won_total{strategy}`, `trades_lost_total{strategy}`
- Gauge: `bankroll`, `open_positions`, `ws_connected{source}`
- Histogram: `signal_to_fill_latency_seconds{strategy}`, `llm_latency_seconds`, `ws_lag_ms{source}`
- Endpoint: `/metrics` (Prometheus format)

### 3.8 Alerts (`alerts/telegram.py`)

Real implementation (bukan stub v2). Events:
- `notify_startup(bankroll, strategies, version)`
- `notify_trade(position)` — setiap trade open
- `notify_trade_close(trade)` — setiap trade close dengan P&L
- `notify_pnl_milestone(pnl, threshold)` — setiap $5 P&L change
- `notify_drawdown_warning(current_dd, max_dd)`
- `notify_ws_disconnect(source, downtime_sec)` — WS down > 30s
- `notify_crash(error, traceback)` — dari daemon
- `notify_daily_summary(trades, pnl, win_rate)`

**Rate limit:** per-event-type, configurable. Misal `notify_trade` max 1 per 10s (burst), `notify_pnl_milestone` max 1 per 5 menit.

### 3.9 Daemon (`scripts/daemon.py`) — Fixed

**Bedanya dengan v2:**
- `restart_count` reset setelah uptime > 1 jam
- Exponential backoff: `delay = min(300, 5 * 2^min(restart_count, 6))` (5s, 10s, 20s, 40s, 80s, 160s, 300s)
- Telegram alert saat crash (lewat subprocess env var Telegram token)
- Health check via `/api/health` endpoint (bukan hanya heartbeat file)
- Max restarts dalam window 1 jam = 10 (bukan 50 lifetime)

```python
# Pseudocode
while True:
    proc = start_bot()
    start_time = time.time()
    
    while proc.poll() is None:
        time.sleep(5)
        # Health check via HTTP
        if not health_check_ok():
            proc.kill()
            break
    
    uptime = time.time() - start_time
    if uptime > 3600:
        restart_count = 0  # Reset after 1h stable
    
    restart_count += 1
    if restart_count_in_last_hour >= 10:
        send_telegram("Bot crash loop detected, giving up")
        sys.exit(1)
    
    delay = min(300, 5 * (2 ** min(restart_count, 6)))
    send_telegram(f"Bot crashed (exit={proc.returncode}), restart in {delay}s")
    time.sleep(delay)
```

---

## 4. Data Flow — End-to-End Example

**Scenario:** BTC price naik 1% di Binance dalam 10 detik.

```
1. Binance WS push tick (BTC=$105,000)
   → binance_ws.py receive
   → publish "binance_tick" event
   → 4 subscribers receive in parallel:

2a. latency_arb.py receive binance_tick
    → compute implied prob for "Will BTC be above $100k on June 27?"
    → implied_prob = 99.5% (BTC way above $100k)
    → check Polymarket YES price via clob_ws local orderbook
    → PM YES = 0.94 (lagging!)
    → edge = 5.5% > 2% threshold
    → fire Signal(BUY YES, confidence=0.92, notional=$6.25)
    → publish "signal" event

2b. momentum.py receive binance_tick
    → check if any crypto market has odds moving
    → no PM odds movement yet → skip

2c. news_llm.py — not subscribed to binance_tick → ignore

2d. observability metrics — record tick latency

3. risk_manager receive "signal" event
   → check global drawdown: OK
   → check latency_arb consecutive losses: OK
   → check rate limit: OK
   → check max concurrent: OK
   → sizer: notional = min($6.25, 25% * $25 = $6.25) = $6.25
   → approve signal, pass to executor

4. paper_executor receive approved signal
   → async sleep 0.2s (simulated latency, NON-blocking)
   → fill_probability = 0.85 (BTC YES at low price)
   → random.random() < 0.85 → filled!
   → insert Position to SQLite
   → update wallet (cash -= $6.25)
   → publish "order_fill" event
   → telegram alert: "⚡ New trade: BUY YES @ 0.94, $6.25"

5. dashboard fetches /api/stats every 3s
   → sees new position, updates UI
   → user sees: "Latency Arb | YES @ 0.94 | $6.25 | BTC=$105k"

6. (2 minutes later) PM odds adjust to 0.97
   → clob_ws receive tick, update local orderbook
   → publish "clob_tick" event
   → latency_arb receives, checks exit condition
   → TP reached (+3.2%) → close position
   → executor close_position(price=0.97)
   → PnL = $0.20 (3.2%)
   → publish "position_close" event
   → telegram alert: "✅ Closed: +$0.20 (3.2%)"
```

**Total latency:** Binance tick → signal → fill = ~150ms (paper). Real-world live target = <300ms.

---

## 5. Configuration System

### 5.1 Config Hierarchy (priority tinggi → rendah)

1. CLI args (future)
2. Environment variables (`BOT_MODE`, `INITIAL_BANKROLL_USD`, `TELEGRAM_BOT_TOKEN`, etc.)
3. Mode-specific overlay (`config/{mode}.yaml`)
4. Default config (`config/default.yaml`)

Deep merge, validated dengan Pydantic Settings.

### 5.2 Default Config (`config/default.yaml`)

Lihat section 3.3-3.4 untuk full config. Highlights:
- 5 strategies aktif
- Per-strategy risk budget
- Aggressive compounding sizer
- Paper executor default
- 127.0.0.1 bind HTTP server
- Telegram alerts optional (env var)

### 5.3 .env.example

```env
# Bot mode
BOT_MODE=paper
INITIAL_BANKROLL_USD=25.00

# Database
DATABASE_URL=sqlite+aiosqlite:///data/cipher_v3.db

# HTTP server
HTTP_HOST=127.0.0.1
HTTP_PORT=8081                  # Different port from v2 (8080)

# Telegram (optional)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_ALERT_TRADE=true
TELEGRAM_ALERT_PNL_THRESHOLD=5.0
TELEGRAM_ALERT_INTERVAL_MIN=10

# LLM (z-ai-web-dev-sdk)
ZAI_API_KEY=                    # Required for news_llm strategy
LLM_MODEL=glm-4.5
LLM_MAX_LATENCY_SEC=30

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json                 # atau "text" untuk human-readable

# WebSocket
CLOB_WS_URL=wss://ws-subscriptions-clob.polymarket.com/ws/
BINANCE_WS_URL=wss://stream.binance.com:9443

# Gamma API
GAMMA_API_URL=https://gamma-api.polymarket.com
```

---

## 6. Deployment

### 6.1 Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install deps tanpa build-essential (tidak perlu untuk pure Python)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ARCHITECTURE.md /app/
COPY src/ /app/src/
COPY config/ /app/config/
COPY scripts/ /app/scripts/

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

RUN mkdir -p /app/data

ENV BOT_MODE=paper
ENV INITIAL_BANKROLL_USD=25.00
ENV CONFIG_DIR=/app/config
ENV PYTHONPATH=/app/src
ENV LOG_FORMAT=json

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8081/api/health || exit 1

CMD ["python", "scripts/daemon.py"]
```

### 6.2 docker-compose.yml

```yaml
services:
  polyclaw-cipher-v3:
    build: .
    container_name: polyclaw-cipher-v3
    restart: unless-stopped
    ports:
      - "127.0.0.1:8081:8081"        # LOCALHOST ONLY — akses via SSH tunnel
    volumes:
      - ./data:/app/data
      - ./config:/app/config:ro
    environment:
      - BOT_MODE=paper
      - INITIAL_BANKROLL_USD=25.00
      - HTTP_HOST=127.0.0.1
      - HTTP_PORT=8081
      - LOG_FORMAT=json
    env_file:
      - .env
    dns:
      - 8.8.8.8
      - 1.1.1.1
    mem_limit: 1g                    # Hard limit untuk t2.small
    cpus: 1.0
    network_mode: bridge
```

### 6.3 Cara Akses Dashboard

Karena bind `127.0.0.1`, akses dari laptop user via SSH tunnel:

```bash
ssh -L 8081:localhost:8081 -i ~/.ssh/t2small.pem ubuntu@3.107.53.103
# Lalu buka http://localhost:8081 di browser
```

Atau via Caddy reverse proxy dengan basic auth (opsional, setup terpisah).

### 6.4 Run Alongside v2

v3 pakai port 8081, v2 pakai 8080. Keduanya bisa jalan simultan untuk perbandingan:
- v2: `docker logs polyclaw-cipher`
- v3: `docker logs polyclaw-cipher-v3`

Setelah v3 stabil & proven, v2 bisa di-stop: `docker stop polyclaw-cipher`.

---

## 7. Migration Path v2 → v3

**Tidak ada migrasi data.** v3 mulai fresh dengan $25 paper bankroll baru. v2 tetap jalan untuk perbandingan.

Manual comparison checklist (paper trading 1-2 minggu):
- [ ] v3 generate more signals than v2?
- [ ] v3 win rate higher than v2?
- [ ] v3 latency (signal → fill) < 500ms average?
- [ ] v3 WebSocket CLOB uptime > 99%?
- [ ] v3 LLM agent emit ≥ 2 signals/hour during active news?
- [ ] v3 atomic_arb find real arbs (YES+NO < $1)?
- [ ] v3 resolution_snipe find near-certain markets?
- [ ] v3 no fake-resolution bugs (verify via trade reasons)?

Jika semua pass → v2 di-stop, v3 jadi production paper trader.
Jika ada fail → iterate v3, ulang paper trading.

---

## 8. Development Roadmap

### Phase 1: Foundation (3-5 hari)
- [ ] Project skeleton (pyproject.toml, Dockerfile, docker-compose)
- [ ] Core: event_bus, types, config
- [ ] State: SQLite schema, migrations, async repository
- [ ] Scanner (with real resolution detection)
- [ ] HTTP server + dashboard (config-driven)
- [ ] Paper executor (async, no time.sleep blocking)
- [ ] Risk manager + sizer
- [ ] Deploy ke VPS, verify baseline berjalan

**Phase 1 deliverable:** Bot jalan, scan markets, tampilkan dashboard, **tapi 0 strategi aktif** (semua disabled). Verifikasi infrastructure.

### Phase 2: WebSocket Feeds (2-3 hari)
- [ ] Binance WS (port dari v2, lebih rapi)
- [ ] CLOB WS subscriber + local orderbook
- [ ] Auto-reconnect dengan exponential backoff
- [ ] Health check + metrics untuk WS

**Phase 2 deliverable:** Dashboard menampilkan real-time prices dari WS. Masih 0 strategi.

### Phase 3: Core Strategies (3-5 hari)
- [ ] Momentum (port dari v2 universal, pakai WS)
- [ ] AtomicArb (pair-trade, baru)
- [ ] LatencyArb (Binance → PM, baru)
- [ ] ResolutionSnipe **tanpa LLM dulu** (manual threshold only)

**Phase 3 deliverable:** 4 strategi jalan, generate signals & trades. Paper trading mulai有意义.

### Phase 4: LLM Agent (3-5 hari)
- [ ] `agent/llm_client.py` dengan z-ai-web-dev-sdk
- [ ] `agent/news_scraper.py` (RSS + Twitter via skills)
- [ ] `agent/market_analyzer.py`
- [ ] `agent/signal_router.py`
- [ ] `news_llm` strategy aktif
- [ ] LLM-assisted resolution_snipe (upgrade Phase 3)

**Phase 4 deliverable:** LLM agent emit signals dari real news. Full v3 feature set.

### Phase 5: Hardening (2-3 hari)
- [ ] Telegram alerts (all events)
- [ ] Daemon fix (restart counter reset, exponential backoff)
- [ ] Prometheus metrics
- [ ] Structured logs
- [ ] Unit tests (risk manager, executor, atomic_arb)
- [ ] Integration tests (WS reconnect, event bus)
- [ ] Git init + commit history
- [ ] Documentation: README update

**Phase 5 deliverable:** Production-ready paper trading bot.

**Total estimasi:** 13-21 hari kerja (paralel bisa lebih cepat).

---

## 9. Key Decisions & Trade-offs

### 9.1 Kenapa FastAPI, bukan raw asyncio HTTP (v2)?

- v2 hand-rolled HTTP parser vulnerable ke slowloris, no POST body, no query string parsing
- FastAPI dapat: validation, OpenAPI docs, async, middleware, testing utilities
- Trade-off: tambah dependency ~10MB. Worth it.

### 9.2 Kenapa SQLite, bukan PostgreSQL atau JSON?

- SQLite WAL: concurrent reads, no server, file-based (mudah backup)
- JSON (v2): ~30 disk writes/menit untuk heartbeat alone, no query capability
- PostgreSQL: overkill untuk single-bot, butuh server terpisah
- Trade-off: SQLite tidak cocok untuk high-concurrency writes (>100/sec). Untuk paper trading 1 bot, more than enough.

### 9.3 Kenapa z-ai-web-dev-sdk, bukan OpenAI/Anthropic langsung?

- Lebih murah / gratis untuk trial
- Available di sandbox ini (skill `LLM`)
- GLM-4.5 cukup capable untuk news analysis
- Trade-off: kalau mau switch provider, perlu adapter layer. Sudah di-abstract di `llm_client.py`.

### 9.4 Kenapa 5 strategi, bukan 1-2 yang fokus?

- Polymarket market types beragam — 1 strategi tidak cover semua
- Aggressive compounding butuh volume → multi-strategy = lebih banyak opportunity
- Tiap strategi punya risk profile berbeda → diversifikasi
- Trade-off: complexity. Mitigasi: per-strategy config, per-strategy metrics, easy disable.

### 9.5 Kenapa bind 127.0.0.1, bukan 0.0.0.0 + auth?

- SSH tunnel lebih secure daripada app-layer auth (no credentials in code/config)
- Tidak ada attack surface publik
- Trade-off: kurang convenient (butuh SSH tunnel). Mitigasi: dokumentasi jelas, atau setup Caddy reverse proxy dengan basic auth (opsional).

### 9.6 Kenapa port 8081, bukan 8080?

- v2 masih jalan di 8080 untuk perbandingan
- Setelah v2 di-stop, v3 bisa pindah ke 8080 kalau mau

---

## 10. Open Questions (perlu input kamu)

1. **Twitter/X access:** skill `web-search` bisa search tweet, tapi real-time monitoring account spesifik mungkin perlu Twitter API berbayar. Alternative: RSS feed dari account via nitter/instances. OK kalau begitu?

2. **Telegram bot token:** apakah kamu sudah punya? Kalau belum, aku bisa skip Telegram alerts di Phase 1-3, baru aktifkan di Phase 5.

3. **z-ai-web-dev-sdk API key:** apakah sudah ada? Kalau belum, aku bisa stub LLM client di Phase 4 dengan mock, baru kamu isi key-nya.

4. **Initial bankroll paper:** v2 pakai $25. v3 pakai berapa? Aku suggest $25 sama, biar comparable.

5. **Stop v2 atau tetap jalan:** aku default biarkan v2 jalan untuk perbandingan. OK?

6. **Git remote:** push ke GitHub private repo kamu? Atau lokal aja di VPS?

---

## 11. Approval

Kalau architecture ini OK, aku mulai Phase 1 (Foundation) — project skeleton + core modules + deploy ke VPS. Estimasi 3-5 hari.

Kalau ada yang mau diubah/ditambah/dikurangi, bilang sebelum aku mulai coding.

**Specifically minta review pada:**
- [ ] Daftar 5 strategi (ada yang kurang/tidak perlu?)
- [ ] Sizing per strategi (latency_arb 25%, atomic_arb 40%, resolution_snipe 15%, momentum 15%, news_llm 10%) — terlalu agresif/kurang?
- [ ] Risk limits (50% daily DD, 8 consec loss global, 60 trades/hour global) — OK?
- [ ] Port 8081 + SSH tunnel approach — OK atau prefer Caddy + basic auth?
- [ ] Roadmap Phase 1-5 — ada yang mau di-skip/di-cepatin?
