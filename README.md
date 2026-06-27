# PolyClaw-Cipher v3.4.3 🔍

> HFT-capable Polymarket bot with AI agent — aggressive compounding for small capital ($25+)

**Repository:** https://github.com/doyoindah7/PolyClaw-Chiper (private)
**Version:** 3.4.3
**Status:** RUNNING (paper trading, deployed at http://3.107.53.103:8082/)
**Bankroll:** $54.17 (+116.7% from $25 initial)

---

## Quick Start

```bash
# Clone
git clone https://github.com/doyoindah7/PolyClaw-Chiper.git
cd PolyClaw-Chiper

# Setup
cp .env.example .env

# Build & deploy
docker-compose up --build -d

# Dashboard (5s after start)
# http://<VPS_IP>:8082/

# Check health
curl http://localhost:8082/api/health
# {"status":"ok","version":"3.4.3","uptime_sec":...}

# View logs
docker logs -f polyclaw-cipher-v3
```

---

## Features

### Core Architecture
- **WebSocket CLOB feed** — real-time Polymarket orderbook (60x faster than REST polling)
- **WebSocket Binance feed** — BTC/ETH/SOL real-time prices + dynamic volatility
- **Event-driven architecture** — in-process pub/sub event bus
- **Real resolution detection** — uses `closed` field + price-based winner detection
- **Async paper executor** — non-blocking, with leg-delay simulation for atomic_arb
- **SQLite WAL state** — atomic, queryable, async
- **FastAPI HTTP server** — dashboard + REST API + Prometheus metrics
- **JSON structured logs** — via structlog
- **Daemon 24/7** — exponential backoff, deep health check, never gives up
- **Wallet invariant check** — bankroll == cash + invested (verified every 3s)
- **Pydantic config validation** — strict type/range checking on startup
- **Test suite** — pytest unit tests (Wallet, RiskManager, LatencyArb CDF)

### Strategies (5 total, 4 active)
1. **latency_arb** — Binance price → PM odds lag (log-normal CDF probability model)
2. **atomic_arb** — YES+NO < $1 risk-free pair trade (40 bps threshold, leg-delay simulated)
3. **resolution_snipe** — Near-certain markets at 0.88-0.97 + TP/SL + CLOB WS real-time prices
4. **momentum** — Multi-timeframe odds momentum (30s + 2m, sports_total allowed)
5. **news_llm** — LLM news agent (stub, for future AI integration)

### Risk Management
- Per-strategy capital budget + circuit breaker
- Correlation-aware exposure limits (net directional per asset)
- Dynamic cash buffer (15% default → 25% when over-deployed)
- Emergency mode (prevents deadlock when cash low)
- Cash reservation pipeline (prevents over-allocation races)
- Double-close race condition lock

### Market Category Filter
- `sports_total` (O/U goals, Poisson, predictable) — allowed for momentum
- `sports_spread` (handicap, random) — excluded (1 goal = flip)
- `sports_match` (winner/draw, random) — excluded
- `crypto`, `economics`, `politics`, `other` — allowed

---

## Configuration

### Environment Variables (.env)

```env
BOT_MODE=paper
INITIAL_BANKROLL_USD=25.00
DATABASE_URL=sqlite+aiosqlite:///data/cipher_v3.db
HTTP_HOST=0.0.0.0
HTTP_PORT=8082
LOG_LEVEL=INFO
LOG_FORMAT=json
CLOB_WS_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
BINANCE_WS_URL=wss://stream.binance.com:9443
GAMMA_API_URL=https://gamma-api.polymarket.com
```

### Config File (config/default.yaml)

Key settings:
- `strategies.*.enabled` — enable/disable strategy
- `strategies.*.max_position_pct` — max % bankroll per trade
- `risk.max_daily_drawdown_pct` — daily DD limit (50% aggressive)
- `risk.per_strategy.*.max_consecutive_losses` — circuit breaker
- `risk.sizer.cash_min_pct` — cash buffer (15%, dynamic to 25%)

Restart after config change:
```bash
docker restart polyclaw-cipher-v3
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard HTML |
| GET | `/api/stats` | Full stats (bankroll, positions, trades, strategies, risk, ws_status) |
| GET | `/api/health` | Health check (`{status, version, uptime_sec}`) |
| GET | `/api/config` | Effective config |
| GET | `/metrics` | Prometheus metrics |

---

## Deployment

- **VPS:** AWS EC2 t2.small (1 vCPU, 2GB RAM, Ubuntu)
- **IP:** 3.107.53.103
- **Port:** 8082 (public)
- **Container:** `polyclaw-cipher-v3` (Docker, restart=unless-stopped)
- **Resource limit:** 1GB RAM, 1 CPU
- **Dashboard:** http://3.107.53.103:8082/

```bash
# Start / Stop / Rebuild
docker-compose up -d
docker-compose down
docker-compose up --build -d

# Logs / Stats / Health
docker logs -f polyclaw-cipher-v3
curl http://localhost:8082/api/stats | python3 -m json.tool
curl http://localhost:8082/api/health
```

---

## Project Structure

```
PolyClaw-Chiper/
├── README.md                    # This file
├── CHANGELOG.md                 # Version history (v3.0.0 → v3.4.3)
├── HANDOFF_AUTOCRAW.md          # Guide for autoclaw AI agent
├── ARCHITECTURE.md              # Design document
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── config/
│   ├── default.yaml             # Main config
│   └── paper.yaml               # Paper mode overlay
├── scripts/
│   └── daemon.py                # Auto-heal daemon (24/7)
├── src/polyclaw_cipher_v3/
│   ├── bot.py                   # Orchestrator
│   ├── config.py                # Pydantic config validation
│   ├── core/
│   │   ├── types.py             # Pydantic models + market categories
│   │   ├── event_bus.py         # Async pub/sub
│   │   ├── scanner.py           # Gamma API + fetch_market
│   │   ├── resolution.py        # Price-based winner detection
│   │   ├── binance_ws.py        # Binance WS + dynamic volatility
│   │   ├── clob_ws.py           # Polymarket CLOB WS + set-based sync
│   │   └── http_server.py       # FastAPI + dashboard + metrics
│   ├── strategy/
│   │   ├── base.py
│   │   ├── latency_arb.py       # CDF probability model
│   │   ├── atomic_arb.py        # Pair trade + leg delay
│   │   ├── resolution_snipe.py  # CLOB WS real-time + TP/SL
│   │   └── momentum.py          # Multi-timeframe + category filter
│   ├── execution/
│   │   └── paper.py             # Async executor + leg simulation
│   ├── risk/
│   │   ├── manager.py           # Unified gate + correlation limits
│   │   └── sizer.py             # Dynamic cash buffer + emergency mode
│   ├── state/
│   │   ├── db.py                # SQLite WAL + atomic transactions
│   │   ├── wallet.py            # Cash reservation + overdraft guard
│   │   └── repository.py
│   ├── agent/llm_client.py      # STUB (for future LLM)
│   ├── alerts/__init__.py       # STUB (for future Telegram)
│   └── observability/logs.py    # JSON structured logs
├── tests/
│   └── test_bot_logic.py        # Unit tests (Wallet, RiskManager, CDF)
├── docs/
│   ├── reviews/                 # AI review files (Claude, Lisa, Grok)
│   └── analysis/                # Analysis docs (V3, V31, target, recommendations)
└── archive/
    └── v2-legacy/               # v2 source code (stopped, for reference)
```

---

## Roadmap

### ✅ Completed (v3.0.0 → v3.4.3)
- v3.0.0: Complete rewrite from v2 (WebSocket, event bus, real resolution, async executor)
- v3.1.0: v2 stopped, dashboard v3-only, CLOB WS fix, wallet invariant
- v3.2.0: Market category filter, atomic_arb pair fix, cash buffer
- v3.3.0: Multi-AI review consensus (8 fixes: category split, config conflict, record split, untrack, etc.)
- v3.3.1: Autoclaw hotfix (atomic_arb category filter + sizer deadlock)
- v3.4.0: Critical bug fixes (double-close lock, overdraft guard, DB cache, resolution sync)
- v3.4.1: Strategy improvements (CDF model, correlation limits, cash reservation, state restoration)
- v3.4.2: Production hardening (tests, config validation, Prometheus metrics, graceful shutdown)
- v3.4.3: Critical resolution detection fix (resolved markets now close → cash freed)

### ⏸️ Pending
- **MASALAH-6:** 0 crypto Up/Down detection (latency_arb dead — scanner threshold mismatch)
- **Event bus wiring:** Strategies still pull-based (1s loop), target <50ms
- **LLM agent:** Test CryptoPanic latency real before commit
- **Sample size:** Track 30-50 unique markets per strategy (not total trades)
- **Telegram alerts:** Stub ready, needs real implementation
- **Live trading (v4):** After paper trading proven profitable ≥14 days

---

## Documentation

| File | Description |
|------|-------------|
| `CHANGELOG.md` | Full version history with details |
| `HANDOFF_AUTOCRAW.md` | Guide for autoclaw AI to extend bot |
| `ARCHITECTURE.md` | 700-line design document |
| `docs/reviews/` | AI reviews (Claude, Lisa/Qwen, Grok) + discussion rounds |
| `docs/analysis/` | Analysis docs (V3, V31, target, v2 recommendations) |
| `archive/v2-legacy/` | v2 source code (stopped, for reference) |

---

## Safety

- ✅ Paper trading only (`BOT_MODE=paper`)
- ✅ No API keys in code (via `.env`, gitignored)
- ✅ Circuit breaker per strategy (auto-disable on loss streak)
- ✅ Daily drawdown limit (50%, configurable)
- ✅ Wallet overdraft guard (`InsufficientFundsError`)
- ✅ Double-close race condition lock
- ✅ Wallet invariant check (every 3s)
- ✅ Health check + auto-restart (daemon 24/7)

### Before Live Trading (v4)
- [ ] Paper trading ≥ 14 days profitable
- [ ] Win rate ≥ 50% per strategy
- [ ] Max drawdown ≤ 30%
- [ ] 30-50 unique markets sample per strategy
- [ ] Unit tests pass
- [ ] Telegram alerts active
- [ ] `py-clob-client` integration + leg-risk stress test

---

## License

Private project. All rights reserved.

---

## Links

- **Repository:** https://github.com/doyoindah7/PolyClaw-Chiper
- **Dashboard:** http://3.107.53.103:8082/
- **VPS:** 3.107.53.103 (AWS t2.small)
