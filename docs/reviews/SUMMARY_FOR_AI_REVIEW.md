# 📋 Summary for AI Review (ChatGPT / Claude)

> **Project:** PolyClaw-Cipher v3.2.0 — Polymarket HFT trading bot
> **Repo:** https://github.com/doyoindah7/PolyClaw-Chiper (private)
> **Status:** Paper trading, deployed at http://3.107.53.103:8082/
> **Target:** $25 → $150-200/week via aggressive compounding

---

## 🎯 Context untuk AI Review

Aku sedang develop trading bot untuk Polymarket dengan target aggressive growth dari modal $25. Bot sudah sampai versi v3.2.0 setelah melewati beberapa iterasi berdasarkan feedback dari multiple AI (Z.ai Code sebagai main dev, plus 1 AI lain yang kasih analisa kritis).

Aku butuh tanggapan kamu tentang:
1. Arsitektur bot
2. Strategi trading yang dipilih
3. Target $25 → $150-200/week — realistic atau tidak?
4. Bug fixes yang sudah dilakukan
5. Roadmap ke depan (LLM agent, live trading)

---

## 📊 Current State (v3.2.0)

### Bot Stats (live)
- **Bankroll:** $25.00 (paper trading)
- **Cash:** $19.47 (77% idle — cash buffer working)
- **Open positions:** 1
- **Closed trades:** 0 (new session after v3.2.0 deploy)
- **WebSocket CLOB:** 34 tokens subscribed, real-time
- **WebSocket Binance:** connected (BTC/ETH/SOL)
- **Container:** healthy, 60 MB RAM / 1 GB limit
- **Dashboard:** http://3.107.53.103:8082/ (public, auto-refresh 5s)

### Deployment
- **VPS:** AWS EC2 t2.small (1 vCPU, 2GB RAM)
- **Stack:** Python 3.11 + asyncio + FastAPI + SQLite WAL + Docker
- **Container:** `polyclaw-cipher-v3` (restart=unless-stopped)

---

## 🏗️ Architecture

```
Docker Container (auto-heal daemon)
└── Event Bus (asyncio pub/sub, in-process)
    ├── Scanner (Gamma API, 60s poll, real resolution detection)
    ├── BinanceFeed (WS, BTC/ETH/SOL real-time)
    ├── CLOBFeed (WS, Polymarket orderbook real-time, 36 tokens)
    ├── LLM Agent (STUB — for future AI agent integration)
    ├── Signal Engine (4 active + 1 stubbed strategies)
    │   ├── latency_arb     (Binance → PM odds lag arbitrage)
    │   ├── atomic_arb      (YES+NO < $1 risk-free pair trade)
    │   ├── resolution_snipe (near-certain markets + TP/SL)
    │   ├── momentum        (multi-timeframe odds momentum)
    │   └── news_llm        (STUB — LLM news agent, interface ready)
    ├── Risk Manager (unified gate, per-strategy budget + circuit breaker)
    ├── Paper Executor (async, non-blocking, pair-trade support)
    ├── State (SQLite WAL via aiosqlite)
    ├── HTTP Server (FastAPI, dashboard + REST API)
    └── Observability (structlog JSON logs)
```

---

## 📈 Strategies (4 active, 1 stubbed)

### 1. Latency Arbitrage (`latency_arb`)
**Edge:** Polymarket crypto Up/Down odds adjust 200-500ms **after** Binance price move. Bot detects Binance move, computes implied probability, compares with PM YES/NO price. If gap > 2%, fire signal.

- Entry: Binance-implied prob vs PM price gap > `min_edge_pct` (2%)
- Exit: TP 5%, SL 3%, or exit 30s before market close
- Sizing: 25% bankroll per trade (aggressive, edge tinggi)
- Status: ⚠️ Currently DEAD — 0 crypto Up/Down markets detected (scanner timing issue, MASALAH-6 pending fix)

### 2. Atomic Arbitrage (`atomic_arb`)
**Edge:** Risk-free profit ketika YES ask + NO ask < $1. Beli kedua sisi simultan via pair-trade. Profit = $1 - combined_cost.

- Entry: `combined_ask < 1.0 - min_profit_bps/10000` (40 bps = 0.4%)
- Exit: Market resolution (collect $1 dari winning side)
- Sizing: 40% bankroll per arb (low risk, lock profit di entry)
- v3.2.0 FIX: Now creates BOTH legs (previously only first leg — not real arbitrage)

### 3. Resolution Snipe (`resolution_snipe`)
**Edge:** Market yang 99% pasti resolve YES/NO sering trade di 0.90-0.97 karena holders malas. Beli di 0.93, hold ke resolution, collect $1. Profit ~7%.

- Entry: YES/NO price di 0.90-0.97, market close < 24h
- Exit: SL -10%, TP +15%, atau market resolution
- Sizing: 15% bankroll per trade (modal terkunci)
- v3.2.0 FIX: Category filter — only crypto/economics/other (skip ALL sports — upset risk)
- LLM hook: `set_llm_client()` ready untuk future AI-assisted confidence

### 4. Momentum (`momentum`)
**Edge:** Sustained odds momentum akan continue short-term. Multi-timeframe confirmation: 30s + 2m harus agree.

- Entry: |momentum_30s| > 1.0% AND |momentum_2m| > 0.5%
- Exit: TP 8%, SL 4%, max hold 5 menit
- Sizing: 15% bankroll per trade
- v3.2.0 FIX: Category filter skip `sports_match` (random outcome), `min_entry_price` raised 0.05 → 0.30 (skip low-probability entries)

### 5. News LLM (`news_llm`) — STUB
**Edge:** LLM baca breaking news → trade **sebelum** odds adjust. Window 10-60s. Interface ready, butuh z-ai-web-dev-sdk + API key untuk activate.

---

## 🐛 Bug History (yang sudah fix)

### v3.2.0 Fixes (based on V31_ANALYSIS.md review)
1. **99.4% cash deployed** → `cash_min_pct: 0 → 10` (keep buffer)
2. **Momentum masuk sports market** → category filter skip `sports_match` + `entertainment`
3. **"Will Spain win?" NO @ 0.2556 → -99.6% loss** → `min_entry_price: 0.05 → 0.30`
4. **Atomic_arb single-leg** → executor creates BOTH legs via `take_pair_sibling()`
5. **Resolution_snipe di sports** → category filter — only crypto/economics
6. **Strategy stats semua 0** → `_find_strategy()` None-safe + debug logging

### v3.1.0 Fixes (based on V3_ANALYSIS.md review)
1. **CLOB WS only tracked 1 token** → `sync_connections()` batches all tokens (36 subscribed)
2. **Wallet inconsistency ($15.91 "lost")** → invariant check every 3s
3. **resolution_snipe no stop-loss** → SL -10%, TP +15%
4. **Daemon restart loop** → health check uses 127.0.0.1 (0.0.0.0 invalid for connect)
5. **Binance WS tuple bug** → stats cache crash fixed
6. **atomic_arb threshold too high** → 100 → 40 bps

### v3.0.0 Fixes (vs v2)
1. **Fake resolution** (tebak dari end_date) → uses `closed` + `resolvedBy` fields
2. **`time.sleep()` blocking event loop** → `await asyncio.sleep()` non-blocking
3. **Fake single-leg "arb"** → atomic pair-trade YES+NO
4. **REST polling 3s lag** → WebSocket real-time
5. **JSON state ~30 writes/min** → SQLite WAL async
6. **No per-strategy risk** → unified risk manager with circuit breaker

---

## 🎯 Target Analysis: $25 → $150-200/week

### Math (from V3_REVISED_TARGET.md)

**Conservative:**
- avg_ev = 1.5% per trade (after slippage/fees)
- trades_per_week = 100
- Growth = 1.015^100 = 4.43x
- $25 × 4.43 = $110.75/week

**Aggressive:**
- avg_ev = 2% per trade
- trades_per_week = 120
- Growth = 1.02^120 = 10.9x
- $25 × 10.9 = $272.50/week

### Key Insight
Compounding + frequency = EXPONENTIAL growth. Bukan linear. Semakin besar bankroll, semakin besar notional per trade, semakin cepat growth.

### What Successful Polymarket Bots Do (from observation)
1. **WebSocket CLOB, BUKAN REST polling** — lag ~50ms vs 3s (60x faster)
2. **Latency arbitrage** Binance → Polymarket (200-500ms window)
3. **AI Agent / LLM news scraping** — baca Twitter/news, trade SEBELUM odds adjust
4. **Atomic YES+NO arbitrage** — risk-free profit
5. **Resolution sniping** — near-certain markets at discount
6. **Cross-venue arbitrage** — Kalshi/PredictIt price discrepancy
7. **Reward farming** — LP rewards + volume programs

---

## ⏸️ Pending Issues (yang masih perlu fix)

### Dari V31_ANALYSIS.md (v3.2.0 remaining)
1. **MASALAH-6: 0 crypto Up/Down detection** — scanner timing issue
   - Crypto markets resolve cepat, scan 60s kadang miss
   - Fix needed: scan lebih sering untuk crypto-specific markets, atau relax filter

2. **MASALAH-8: sync_connections() setiap 60s** — disruptive
   - Cancel + respawn connections = gap data beberapa detik
   - Fix needed: only sync kalau token list actually berubah (compare IDs, bukan count)

3. **MEDIUM-2: Event bus tidak dipakai strategi** — pull-based 1s, target <50ms
   - latency_arb should subscribe ke `binance_tick`
   - momentum should subscribe ke `clob_tick`

### Roadmap (dari V3_REVISED_TARGET.md)
- **Week 1 (remaining):** Connect strategies to event bus
- **Week 2-3:** Improve prob model, add BNB/XRP/DOGE, Telegram alerts, LLM agent
- **Week 4+:** Prometheus metrics, unit tests, live trading adapter

---

## 🤔 Pertanyaan untuk AI Review

1. **Target $25 → $150-200/week realistic?** Atau terlalu aggressive?
   - Aku percaya achievable karena Polymarket binary options + small capital + aggressive compounding + high frequency
   - Tapi mau dengar pendapat kamu

2. **Strategy mix yang dipilih (5 strategi):**
   - latency_arb (25% sizing)
   - atomic_arb (40% sizing)
   - resolution_snipe (15% sizing)
   - momentum (15% sizing)
   - news_llm (10% sizing, stub)
   
   Apakah ada strategi lain yang seharusnya ditambahkan? Atau sizing yang perlu di-adjust?

3. **Risk management:**
   - max_daily_drawdown: 50% (aggressive)
   - max_consecutive_losses_global: 8
   - max_trades_per_hour: 60
   - per-strategy circuit breaker
   
   Terlalu aggressive? Atau OK untuk paper trading dengan target high growth?

4. **Bug fixes yang sudah dilakukan** — ada yang miss atau kurang optimal?

5. **Roadmap LLM agent** — bagaimana cara terbaik implement news-driven signals?
   - Pakai z-ai-web-dev-sdk atau OpenAI/Anthropic langsung?
   - RSS + Nitter untuk news source, atau ada yang lebih baik?
   - LLM call latency target < 30s — realistic?

6. **Switch ke live trading (v4):**
   - Pakai `py-clob-client` official Polymarket SDK?
   - Wallet security best practices?
   - Apa yang harus di-test sebelum live?

7. **Arsitektur event bus** — saat ini publish events tapi strategies tidak subscribe (masih pull-based 1s loop). Worth refactor ke event-driven, atau over-engineering untuk paper trading?

8. **SQLite WAL** — cukup untuk production, atau harus migrate ke PostgreSQL/MySQL?

---

## 📁 Files di Repo (kalau mau clone review)

```
PolyClaw-Chiper/
├── README.md                  # Comprehensive docs (580 lines)
├── ARCHITECTURE.md            # Design doc (700 lines)
├── CHANGELOG.md               # Semantic versioning
├── HANDOFF_AUTOCRAW.md        # Guide untuk AI lain
├── V3_ANALYSIS.md             # Bug analysis v3.0.0
├── V31_ANALYSIS.md            # Bug analysis v3.1.0
├── V3_REVISED_TARGET.md       # Target analysis + roadmap
├── RECOMMENDATIONS_v2.md      # v2 analysis
├── config/default.yaml        # Main config
├── src/polyclaw_cipher_v3/
│   ├── bot.py                 # Orchestrator
│   ├── core/
│   │   ├── types.py           # Pydantic models + market category classifier
│   │   ├── event_bus.py       # Async pub/sub
│   │   ├── scanner.py         # Gamma API
│   │   ├── resolution.py      # Real resolution detection
│   │   ├── binance_ws.py      # Binance WebSocket
│   │   ├── clob_ws.py         # Polymarket CLOB WebSocket
│   │   └── http_server.py     # FastAPI + dashboard
│   ├── strategy/              # 5 strategies
│   ├── execution/paper.py     # Async paper executor with pair support
│   ├── risk/                  # Manager + sizer
│   ├── state/                 # SQLite WAL
│   ├── agent/llm_client.py    # STUB
│   └── observability/logs.py  # JSON structured logs
└── scripts/daemon.py          # Auto-heal daemon
```

Repo private, but kalau mau akses untuk review code detail, beri tahu aku.

---

**TL;DR:** Bot Polymarket v3.2.0 sudah jalan dengan 4 strategi aktif. Sudah fix 12+ bug dari 2 iterasi AI review. Target $25 → $150-200/week. Mau dengar pendapat kamu tentang: target realistic gak, strategi yang dipilih, risk management, dan roadmap LLM agent + live trading.
