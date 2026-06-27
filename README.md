# PolyClaw-Cipher v3.2.0 🔍

> HFT-capable Polymarket bot with AI agent — aggressive compounding for small capital ($25+)

**Repository:** https://github.com/doyoindah7/PolyClaw-Chiper (private)
**Version:** 3.1.0
**Status:** RUNNING (paper trading, deployed at http://3.107.53.103:8082/)

---

## Table of Contents

1. [Overview](#overview)
2. [Features](#features)
3. [Quick Start](#quick-start)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Strategies](#strategies)
7. [Architecture](#architecture)
8. [Dashboard](#dashboard)
9. [API Endpoints](#api-endpoints)
10. [Deployment](#deployment)
11. [Development](#development)
12. [Roadmap](#roadmap)
13. [Documentation](#documentation)
14. [Safety](#safety)
15. [Changelog](#changelog)

---

## Overview

PolyClaw-Cipher v3.2.0 adalah trading bot untuk [Polymarket](https://polymarket.com) yang dirancang untuk **aggressive compounding** dari modal kecil ($25+). Bot ini menggunakan WebSocket real-time untuk eksekusi cepat, event-driven architecture untuk reactivity maksimal, dan multiple strategies untuk diversifikasi edge.

**Target:** $25 → $150-200/week via compounding + high frequency + good signals.

Dibangun di atas pelajaran dari v2 (yang punya bug kritis seperti fake resolution, blocking executor, fake arbitrage). v3 adalah rewrite lengkap dengan arsitektur HFT-capable.

---

## Features

### Core
- ✅ **WebSocket CLOB feed** — real-time Polymarket orderbook (60x lebih cepat dari REST polling)
- ✅ **WebSocket Binance feed** — BTC/ETH/SOL real-time prices
- ✅ **Event-driven architecture** — in-process pub/sub event bus
- ✅ **Real resolution detection** — uses `closed` + `resolvedBy` fields (fixes v2 fake resolution bug)
- ✅ **Async paper executor** — `await asyncio.sleep()` (fixes v2 `time.sleep()` blocking)
- ✅ **Atomic pair-trade arbitrage** — YES+NO simultan (fixes v2 fake single-leg "arb")
- ✅ **SQLite WAL state** — atomic, queryable, async (replaces v2 JSON)
- ✅ **Unified risk manager** — per-strategy budget + circuit breaker
- ✅ **FastAPI HTTP server** — proper framework (replaces v2 hand-rolled HTTP)
- ✅ **JSON structured logs** — via structlog
- ✅ **Daemon with exponential backoff** — 5s → 300s, reset after 1h stable
- ✅ **Wallet invariant check** — bankroll == cash + invested (verified every 3s)

### Strategies (5 total, 4 active)
1. **latency_arb** — Binance price move → PM odds lag arbitrage
2. **atomic_arb** — YES+NO < $1 risk-free (threshold 40 bps)
3. **resolution_snipe** — Near-certain markets + TP/SL exit
4. **momentum** — Multi-timeframe odds momentum (30s + 2m)
5. **news_llm** — LLM news agent (stub, for autoclaw to implement)

### Dashboard
- ✅ **Full-width layout** — 6 KPI cards, capital allocation bar
- ✅ **Open positions** dengan unrealized P&L real-time
- ✅ **Per-strategy cards** dengan 5 stats
- ✅ **Recent trades** dengan full details
- ✅ **Risk + System status** grid
- ✅ **Auto-refresh 5s** dengan retry + fallback
- ✅ **Connection status indicator** (green/orange/red)

---

## Quick Start

```bash
# Clone repo
git clone https://github.com/doyoindah7/PolyClaw-Chiper.git
cd PolyClaw-Chiper

# Copy env template
cp .env.example .env
# Edit .env jika perlu (default OK untuk paper trading)

# Build & deploy
docker-compose up --build -d

# Akses dashboard (5s setelah container start)
# Public: http://<VPS_IP>:8082/
# Contoh: http://3.107.53.103:8082/

# Check logs
docker logs -f polyclaw-cipher-v3

# Check health
curl http://localhost:8082/api/health
# Expected: {"status":"ok","version":"3.1.0","uptime_sec":...}
```

---

## Installation

### Prerequisites

- Docker 20+ dan Docker Compose 2+
- VPS dengan minimal 1GB RAM (recommended: 2GB untuk headroom)
- Port 8082 terbuka di firewall/security group (untuk public dashboard access)
- (Optional) AWS EC2 t2.small atau equivalent

### Steps

1. **Clone repository**
   ```bash
   git clone https://github.com/doyoindah7/PolyClaw-Chiper.git
   cd PolyClaw-Chiper
   ```

2. **Setup environment**
   ```bash
   cp .env.example .env
   # Edit .env untuk set API keys (LIHAT Configuration section)
   ```

3. **Build & start container**
   ```bash
   docker-compose up --build -d
   ```

4. **Verify deployment**
   ```bash
   docker ps | grep polyclaw-cipher-v3
   # Should show: polyclaw-cipher-v3 ... Up X seconds (healthy)

   curl http://localhost:8082/api/health
   # Should return: {"status":"ok","version":"3.1.0",...}
   ```

5. **Akses dashboard**
   - Public: `http://<VPS_IP>:8082/`
   - Localhost: `http://localhost:8082/` (kalau run di local)

---

## Configuration

### Environment Variables (.env)

```env
# Bot mode: paper (default) | live (disabled in v3)
BOT_MODE=paper
INITIAL_BANKROLL_USD=25.00

# Database
DATABASE_URL=sqlite+aiosqlite:///data/cipher_v3.db

# HTTP server (public access)
HTTP_HOST=0.0.0.0
HTTP_PORT=8082

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json

# WebSocket endpoints
CLOB_WS_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
BINANCE_WS_URL=wss://stream.binance.com:9443

# Gamma API
GAMMA_API_URL=https://gamma-api.polymarket.com

# LLM (z-ai-web-dev-sdk) — DI-STUB untuk sekarang
# Akan diisi oleh autoclaw nanti:
# ZAI_API_KEY=
# LLM_MODEL=glm-4.5
# LLM_MAX_LATENCY_SEC=30

# Telegram alerts — DI-SKIP sesuai request user
# TELEGRAM_BOT_TOKEN=
# TELEGRAM_CHAT_ID=
```

### Config File (config/default.yaml)

Edit `config/default.yaml` untuk tune strategy parameters, risk limits, dan execution settings. Lihat comments di file untuk penjelasan setiap parameter.

Key settings:
- `strategies.*.enabled` — enable/disable strategy
- `strategies.*.max_position_pct` — max % bankroll per trade
- `risk.max_daily_drawdown_pct` — daily DD limit (default 50%, aggressive)
- `risk.per_strategy.*.max_consecutive_losses` — circuit breaker per strategy
- `execution.paper.slippage_bps` — simulated slippage

Restart container setelah ubah config:
```bash
docker restart polyclaw-cipher-v3
```

---

## Strategies

### 1. Latency Arbitrage (`latency_arb`)
**Edge:** Polymarket crypto Up/Down odds adjust 200-500ms **after** Binance price move.

Bot mendeteksi Binance price move, compute implied probability, compare dengan PM YES/NO price. Kalau gap > 2%, fire signal.

- **Entry:** Binance-implied prob vs PM price gap > `min_edge_pct` (2%)
- **Exit:** TP 5%, SL 3%, atau exit 30s sebelum market close
- **Sizing:** 25% bankroll per trade (aggressive, edge tinggi)
- **Config:** `strategies.latency_arb` di default.yaml

### 2. Atomic Arbitrage (`atomic_arb`)
**Edge:** Risk-free profit ketika YES ask + NO ask < $1.

Beli kedua sisi simultan via pair-trade. Profit = $1 - combined_cost. V3.1.0 threshold diturunkan dari 100 → 40 bps (Polymarket markets efficient, real arbs 20-50 bps).

- **Entry:** `combined_ask < 1.0 - min_profit_bps/10000` (40 bps = 0.4%)
- **Exit:** Market resolution (collect $1 dari winning side)
- **Sizing:** 40% bankroll per arb (low risk, lock profit di entry)
- **Config:** `strategies.atomic_arb`

### 3. Resolution Snipe (`resolution_snipe`)
**Edge:** Market yang 99% pasti resolve YES/NO sering trade di 0.90-0.97 karena holders malas.

Beli di 0.93, hold ke resolution, collect $1. Profit ~7%. V3.1.0 menambahkan **stop-loss (-10%) dan take-profit (+15%)** — sebelumnya hold-only (unlimited downside).

- **Entry:** YES/NO price di 0.90-0.97, market close < 24h
- **Exit:** SL -10%, TP +15%, atau market resolution
- **Sizing:** 15% bankroll per trade (modal terkunci)
- **LLM hook:** `set_llm_client()` ready untuk autoclaw inject LLM-assisted confidence
- **Config:** `strategies.resolution_snipe`

### 4. Momentum (`momentum`)
**Edge:** Sustained odds momentum akan continue short-term.

Multi-timeframe confirmation: 30s + 2m harus agree. V3 pakai CLOB WS (60x lebih cepat dari v2 REST polling).

- **Entry:** |momentum_30s| > 1.0% AND |momentum_2m| > 0.5%
- **Exit:** TP 8%, SL 4%, max hold 5 menit
- **Sizing:** 15% bankroll per trade
- **Config:** `strategies.momentum`

### 5. News LLM (`news_llm`) — STUB
**Edge:** LLM baca breaking news → trade **sebelum** odds adjust. Window 10-60s.

Interface siap di `agent/llm_client.py`. Autoclaw akan implement dengan z-ai-web-dev-sdk.

- **Status:** Disabled (`.enabled: false`)
- **Sizing:** 10% bankroll per trade (highest risk strategy)
- **Config:** `strategies.news_llm`
- **Implementation:** Lihat `HANDOFF_AUTOCRAW.md` section 2

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                Docker Container (auto-heal daemon)            │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              Event Bus (asyncio pub/sub)                 │ │
│  │  Topics: market_scan | clob_tick | binance_tick | ...    │ │
│  └───────▲──────────▲──────────▲───────────▲───────────────┘ │
│          │          │          │           │                  │
│   ┌──────┴───┐ ┌────┴────┐ ┌───┴────┐ ┌────┴─────┐           │
│   │ Scanner  │ │CLOB WS  │ │Binance │ │ LLM News │           │
│   │(Gamma    │ │(real-   │ │  WS    │ │  Agent   │           │
│   │ REST 60s)│ │ time)   │ │        │ │ (stub)   │           │
│   └──────┬───┘ └────┬────┘ └────┬───┘ └────┬─────┘           │
│   ┌──────▼──────────▼───────────▼──────────▼─────────────┐   │
│   │              Signal Engine (5 strategies)              │   │
│   │  latency_arb | atomic_arb | resolution_snipe | ...    │   │
│   └──────────────────────────┬────────────────────────────┘   │
│   ┌──────────────────────────▼────────────────────────────┐   │
│   │           Risk Manager (unified gate)                   │   │
│   └──────────────────────────┬────────────────────────────┘   │
│   ┌──────────────────────────▼────────────────────────────┐   │
│   │           Execution Layer (async interface)             │   │
│   │  PaperExecutor (async) ←── swap ──► LiveExecutor (v4)  │   │
│   └──────────────────────────┬────────────────────────────┘   │
│   ┌──────────────────────────▼────────────────────────────┐   │
│   │        State (SQLite WAL via aiosqlite)                 │   │
│   └────────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  Observability: structlog JSON + Prometheus /metrics    │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

Lihat `ARCHITECTURE.md` untuk design document lengkap (700 baris).

---

## Dashboard

**URL:** `http://<VPS_IP>:8082/` (public access)

### Layout
- **Header:** Title, connection status, last update timestamp, clock
- **6 KPI Cards:** Bankroll, P&L Total, Cash, Deployed, Open Positions, Win Rate
- **Capital Allocation Bar:** Cash vs Deployed %
- **Open Positions Table:** Market, Side, Strategy, Entry, Current, Invested, Current Value, **Unrealized P&L**, Age
- **Per-Strategy Cards:** Signals, Trades, W/L, PnL, Win Rate
- **Recent Trades:** Market, Strategy, Side, Entry, Exit, PnL $, PnL %, Reason, When
- **Risk Status Grid:** DD limit, Consec losses, Rate limit, Daily P&L, Session age, Disabled strategies
- **System Status Grid:** Markets, Crypto, CLOB WS, Binance WS, BTC price, Uptime

### Features
- Auto-refresh setiap 5 detik
- Retry 2x dengan backoff 500ms/1000ms kalau fetch gagal
- Fallback ke data terakhir kalau API unreachable
- Timeout 8 detik per request
- Connection status indicator (green/orange/red)
- Last update timestamp

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard HTML |
| GET | `/api/stats` | Full stats (bankroll, positions, trades, strategies, risk, ws_status) |
| GET | `/api/health` | Health check (`{status, version, uptime_sec}`) |
| GET | `/api/config` | Effective config |
| GET | `/metrics` | Prometheus metrics (stub) |

### Contoh response `/api/health`
```json
{
  "status": "ok",
  "version": "3.1.0",
  "uptime_sec": 1234
}
```

### Contoh response `/api/stats` (excerpt)
```json
{
  "bankroll": 25.80,
  "cash": 0.15,
  "deployed": 25.65,
  "pnl": 0.80,
  "trades": 12,
  "wins": 8,
  "losses": 4,
  "win_rate": 66.67,
  "open_positions": [...],
  "recent_trades": [...],
  "strategies": [...],
  "risk": {...},
  "ws_status": {
    "clob_connected": true,
    "clob_tokens": 36,
    "binance_connected": true
  }
}
```

---

## Deployment

### VPS Info
- **AWS EC2 t2.small** (1 vCPU, 2GB RAM)
- **IP:** 3.107.53.103
- **OS:** Ubuntu 24.04
- **Port:** 8082 (public)

### Container Specs
- **Name:** `polyclaw-cipher-v3`
- **Restart policy:** `unless-stopped`
- **Memory limit:** 1GB
- **CPU limit:** 1.0
- **Health check:** `curl -fsS http://127.0.0.1:8082/api/health` every 30s

### Commands

```bash
# Start
docker-compose up -d

# Stop
docker-compose down

# Rebuild (setelah code change)
docker-compose up --build -d

# View logs
docker logs -f polyclaw-cipher-v3

# Restart
docker restart polyclaw-cipher-v3

# Check resource usage
docker stats polyclaw-cipher-v3 --no-stream

# Backup database
docker exec polyclaw-cipher-v3 sqlite3 /app/data/cipher_v3.db ".backup /app/data/backup_$(date +%Y%m%d).db"
```

---

## Development

### Local Development

```bash
# Setup virtualenv
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Run bot locally (without Docker)
python -m polyclaw_cipher_v3

# Run tests (TODO: tests belum ada, infrastructure ready)
pytest
```

### Project Structure

```
PolyClaw-Chiper/
├── .env.example               # Environment template
├── .gitignore
├── .dockerignore
├── ARCHITECTURE.md            # Design doc (700 lines)
├── CHANGELOG.md               # Semantic versioning changelog
├── HANDOFF_AUTOCRAW.md        # Guide for autoclaw AI
├── README.md                  # This file
├── V3_ANALYSIS.md             # AI review of v3.0.0 bugs
├── V3_REVISED_TARGET.md       # Revised target analysis
├── RECOMMENDATIONS_v2.md      # v2 analysis
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── config/
│   ├── default.yaml           # Main config
│   └── paper.yaml             # Paper mode overlay
├── scripts/
│   └── daemon.py              # Auto-heal daemon
├── src/polyclaw_cipher_v3/
│   ├── __init__.py
│   ├── __main__.py
│   ├── bot.py                 # Orchestrator
│   ├── config.py              # Config loader
│   ├── core/
│   │   ├── types.py           # Pydantic models
│   │   ├── event_bus.py       # Pub/sub
│   │   ├── scanner.py         # Gamma API
│   │   ├── resolution.py      # Real resolution check
│   │   ├── binance_ws.py      # Binance WebSocket
│   │   ├── clob_ws.py         # Polymarket CLOB WebSocket
│   │   └── http_server.py     # FastAPI + dashboard
│   ├── strategy/
│   │   ├── base.py
│   │   ├── latency_arb.py
│   │   ├── atomic_arb.py
│   │   ├── resolution_snipe.py
│   │   └── momentum.py
│   ├── execution/
│   │   ├── base.py
│   │   └── paper.py           # Async paper executor
│   ├── risk/
│   │   ├── manager.py         # Unified risk gate
│   │   └── sizer.py           # Position sizer
│   ├── state/
│   │   ├── db.py              # SQLite WAL
│   │   ├── wallet.py
│   │   └── repository.py
│   ├── agent/
│   │   └── llm_client.py      # STUB for autoclaw
│   ├── alerts/
│   │   └── __init__.py        # STUB for Telegram
│   └── observability/
│       └── logs.py            # JSON structured logs
└── tests/                     # TODO: belum ada tests
```

---

## Roadmap

### ✅ v3.2.0 (current)
- **Market category filter** — skip random-outcome markets (sports match winner, entertainment)
  - 6 categories: sports_match, sports_derivative, politics, economics, crypto, entertainment
  - Momentum: allows crypto, sports_derivative (O/U goals), economics, other
  - Resolution_snipe: only crypto, economics, other (skip all sports)
- **Atomic_arb pair execution fix** — executor now creates BOTH legs (YES + NO)
  - Previously only created first leg (not real arbitrage)
  - Now: `take_pair_sibling()` returns second position, bot persists both
  - Pair shares calculated from combined_ask
- **Cash buffer** — `cash_min_pct: 0 → 10` (keep 10% cash for new entries)
  - Previously bot got stuck at $0.15 cash (99.4% deployed, couldn't trade)
- **min_entry_price raised** — `0.05 → 0.30` (skip low-probability entries that often lose)
- **Strategy stats fix** — `_find_strategy()` None-safe + debug logging
- **Market categories logged** in scan output

### ✅ v3.1.0
- v2 stopped, all resources to v3
- Dashboard v3-only (full width, detailed)
- atomic_arb threshold 100 → 40 bps
- resolution_snipe SL + TP
- CLOB WS fix (36 tokens)
- Wallet invariant check
- Daemon + Binance WS bug fixes

### ⏸️ Week 1 remaining (for autoclaw)
- Connect strategies to event bus (currently pull-based 1s, target <50ms)

### ⏸️ Week 2-3
- Improve `_implied_prob_above()` (time decay + vol model)
- Add BNB/XRP/DOGE to Binance feed
- Implement Telegram alerts
- Market category filter for momentum
- Implement LLM agent (news_llm strategy)
- News scraper (Nitter + RSS)

### ⏸️ Week 4+
- Prometheus metrics implementation
- Unit tests (pytest)
- Live trading adapter (py-clob-client)
- Cross-venue arbitrage (Kalshi/PredictIt)
- Backtest framework

Lihat `V3_REVISED_TARGET.md` untuk roadmap lengkap 5 minggu ke $150-200/week.

---

## Documentation

| File | Description |
|------|-------------|
| `README.md` | This file — quick start + overview |
| `ARCHITECTURE.md` | 700-line design document |
| `CHANGELOG.md` | Semantic versioning changelog |
| `HANDOFF_AUTOCRAW.md` | Guide for autoclaw AI to extend bot |
| `V3_ANALYSIS.md` | AI review of v3.0.0 (bug list) |
| `V3_REVISED_TARGET.md` | Target $150-200/week analysis + roadmap |
| `RECOMMENDATIONS_v2.md` | v2 analysis by another AI |

---

## Safety

- ⚠️ **Dashboard public** — read-only monitoring, no trade execution exposed
- ❌ **No API keys in code** — via `.env` (gitignored)
- ✅ **Paper trading only** — `BOT_MODE=paper` hard-coded
- ✅ **Circuit breaker per strategy** — auto-disable on loss streak
- ✅ **Daily drawdown limit** — 50% (configurable)
- ✅ **Atomic writes** — SQLite WAL, no corruption risk
- ✅ **Wallet invariant check** — bankroll == cash + invested
- ✅ **Health check** — Docker restarts if unhealthy

### ⚠️ Before Live Trading (v4)
- [ ] Paper trading ≥ 14 hari profitable
- [ ] Win rate ≥ 50% per strategi
- [ ] Max drawdown ≤ 30% dalam paper
- [ ] WebSocket uptime ≥ 99%
- [ ] Latency signal → fill ≤ 500ms average
- [ ] Unit tests pass
- [ ] Backup wallet private key (offline)
- [ ] Telegram alerts active
- [ ] Stop-loss per trade ≤ 5% bankroll

---

## Changelog

Lihat `CHANGELOG.md` untuk semua perubahan. Highlights:

- **v3.2.0** (2026-06-27): Market category filter (skip sports), atomic_arb pair execution fix, cash buffer 10%, min_entry_price 0.30
- **v3.1.0** (2026-06-27): v2 stopped, dashboard v3-only, atomic_arb threshold lowered, resolution_snipe SL/TP, multiple bug fixes
- **v3.0.0** (2026-06-27): Initial v3 release — complete rewrite from v2

---

## License

Private project. All rights reserved.

---

## Contact

- **Repository:** https://github.com/doyoindah7/PolyClaw-Chiper
- **VPS:** 3.107.53.103 (AWS t2.small)
- **Dashboard:** http://3.107.53.103:8082/

