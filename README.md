# PolyClaw-Cipher v3.5.13 🔍

> Momentum-driven Polymarket bot — aggressive compounding from micro capital ($10-$25)

**Repository:** https://github.com/doyoindah7/PolyClaw-Chiper (private)
**Version:** 3.5.13 (Live-realism Tier 1 — 6 simulations for paper→live parity — 2026-06-28)
**Status:** RUNNING — 2 instances paper trading at http://3.107.53.103:8082/ + http://3.107.53.103:8083/
**Bankroll:** $25 instance (Run 2 consistency test) | $10 instance (micro-cap growth validation)
**TG Bot:** @polyclawchiper_bot — standalone container, dual-instance commands

---

## Quick Start

```bash
git clone https://github.com/doyoindah7/PolyClaw-Chiper.git
cd PolyClaw-Chiper

# Single instance ($25 bankroll)
docker compose up --build -d

# Or two instances ($25 + $10)
docker compose up --build -d                    # $25 on port 8082
docker compose -f docker-compose.ten.yaml up --build -d  # $10 on port 8083

# TG bot (standalone, 32MB RAM)
docker compose -f docker-compose.tg.yaml up --build -d

# Dashboard: http://<VPS_IP>:8082/  and  http://<VPS_IP>:8083/
# Health:    curl http://localhost:8082/api/health
```

---

## Features

### Core Architecture (v3.5.13)
- **WebSocket CLOB feed** — real-time Polymarket orderbook (134 tokens tracked)
- **WebSocket Binance feed** — BTC/ETH/SOL real-time prices
- **Async paper executor** — non-blocking, live-realism simulations (v3.5.13)
- **SQLite WAL state** — atomic, dual-instance isolated DBs
- **FastAPI HTTP server** — dashboard + REST API + Prometheus metrics
- **Daemon 24/7** — exponential backoff, stagnation detection, WAL checkpoint, SignalCheck/CashCheck/ResourceCheck
- **Pydantic config validation** — with `extra=allow` for flexible extension
- **Telegram bot** — standalone container (`polyclaw-tg-bot`), dual-instance commands, single-user allowlist

### Active Strategy
- **momentum** — Multi-timeframe odds momentum (5min + 15min) across ALL volatile markets
  - Entry sweet spot: 0.30-0.70 odds (74% WR, 93% of profit)
  - Safety: per-market 30% max exposure, position cap $500, streak protection
  - 300 markets scanned per cycle, 3s interval

### Production Safety (v3.5.13)
- **Per-market exposure limit** — max 30% bankroll in single market (prevents 350% concentration)
- **Absolute position cap** — $500 max per trade regardless of bankroll
- **Tier-based dynamic sizer** — 4 tiers with 10% hysteresis, 24h cooldown, grandfather clause
- **Wallet invariant** — bankroll == cash + invested (verified every cycle)
- **Duplicate trade detection** — zero tolerance
- **Auto-archive** — DB backup + CSV export before every reset
- **Trade analyzer** — `scripts/analyze_trades.py` learns from past runs, generates config recommendations for next cycle
- **Auto-tune at startup** — bot reads latest trade archive on boot, analyzes performance, applies config changes in-memory automatically. No manual intervention needed.
  - Analyzes: entry price sweet spots, optimal hold time, TP/SL from avg win/loss
  - Applies: in-memory config override (does NOT modify config files)
  - Logs: all changes printed to container logs with ⚙️ marker
  - Threshold: requires 20+ trades in archive to tune (skips on first run)

### Live-Realism Simulations (v3.5.13 — Tier 1)
Paper trading now simulates real-world execution friction for accurate live-readiness validation:

| Simulation | Description | Config |
|---|---|---|
| **Liquidity-based slippage** | Slippage scales with `notional / volume_24h` (30-800 bps), not fixed. Entry + exit. | `slippage_model: liquidity` |
| **Volatility-aware fill probability** | 72% base, 65% volatile (sport/crypto), 80% stable. High-confidence signals fill less. | `fill_probability_base: 0.72` |
| **On-chain settlement delay** | 3s ± 2s Polygon block time. Position is PENDING, cannot exit until CONFIRMED. | `on_chain_delay_sec: 3.0` |
| **Gas fee model** | $0.01 avg per leg, 5% spike to $0.10. Deducted from wallet. | `gas_fee_avg_usd: 0.01` |
| **API rate limit** | 10 req/s tracking, 2% random throttle (429 simulation). | `api_rate_limit_per_sec: 10` |
| **Position state sync** | PENDING → CONFIRMED lifecycle. Auto-confirm after 10s safety timeout. | `on_chain_delay_sec > 0` |

All simulations backward-compatible: set `slippage_model: fixed` or `on_chain_delay_sec: 0` for legacy behavior.

### Tier System
| Tier | Bankroll | Max/Trade | Min Position | Label |
|------|----------|-----------|-------------|-------|
| 1 | $25-$275 | 20% | $3.00 | Aggressive Growth |
| 2 | $275-$1,100 | 12% | $10.00 | Moderate Growth |
| 3 | $1,100-$5,500 | 8% | $25.00 | Preservation |
| 4 | $5,500+ | 5% | $50.00 | Stable Income |

---

## Deployment

- **VPS:** AWS EC2 t2.small (1 vCPU, 2GB RAM, Ubuntu)
- **IP:** 3.107.53.103
- **Instances:**
  - Port 8082 — main ($25 bankroll, Run 2 consistency test)
  - Port 8083 — micro ($10 bankroll, growth validation)
  - TG Bot — standalone container (`polyclaw-tg-bot`, 32MB RAM limit)
- **Container names:** `polyclaw-cipher-v3`, `polyclaw-ten`, `polyclaw-tg-bot`
- **RAM:** ~180MB total (both instances + TG bot)

```bash
# Start / Stop / Rebuild
docker compose up -d
docker compose -f docker-compose.ten.yaml up -d
docker compose -f docker-compose.tg.yaml up -d
docker compose stop
docker compose -f docker-compose.ten.yaml stop
docker compose -f docker-compose.tg.yaml stop

# Reset bankroll for new cycle (with analysis)
python3 scripts/archive_trades.py         # 1. Archive current run
python3 scripts/analyze_trades.py --apply  # 2. Analyze + generate config overlay
# 3. Reset DB (see below)
docker exec polyclaw-cipher-v3 python3 -c "
import sqlite3, time
db = sqlite3.connect('/app/data/cipher_v3.db')
db.execute('UPDATE wallet SET bankroll=25.0, cash=25.0 WHERE id=1')
db.commit()
"
docker compose restart                   # 4. Start fresh with improved config

# Logs
docker logs -f polyclaw-cipher-v3
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard HTML |
| GET | `/api/stats` | Full stats (bankroll, trades, positions, strategies, tier, ws_status) |
| GET | `/api/health` | Health check (`{status, version, uptime_sec}`) |
| GET | `/api/admin/db_stats?hours=N` | Database statistics (localhost only) |
| GET | `/api/admin/wal_checkpoint` | Force WAL checkpoint (localhost only) |
| GET | `/metrics` | Prometheus metrics |

---

## Consistency Test Results

| Run | Initial | Peak | Trades | WR | Duration | Notes |
|-----|---------|------|--------|----|----------|-------|
| 0 | $25 | $303 | 387 | 70% | ~6h | Pre-fix baseline |
| 1 | $25 | $8,170 | 387 | 71% | ~12h | World Cup volatility, no caps |
| 2 | $25 | — | ongoing | — | — | With caps, per-market limits, live-realism Tier 1 |

**Note:** Run 1 profit inflated by World Cup event. 99.98% of trades were sport markets. Normal market expected: $25 → $50-100/week.

---

## Project Structure

```
PolyClaw-Chiper/
├── README.md                    # This file
├── CHANGELOG.md                 # Full version history
├── ARCHITECTURE.md              # 800-line design document
├── HANDOFF_AUTOCRAW.md          # Guide for autoclaw AI agent
├── Dockerfile
├── docker-compose.yml           # Main instance ($25)
├── docker-compose.ten.yaml      # Micro instance ($10)
├── docker-compose.tg.yaml       # TG bot container (standalone)
├── pyproject.toml
├── .env.example
├── config/
│   ├── default.yaml             # Main config
│   ├── ten.yaml                 # $10 instance overlay
│   └── paper.yaml               # Paper mode overlay
├── scripts/
│   ├── daemon.py                # Auto-heal daemon (24/7)
│   ├── archive_trades.py        # Trade DB archiver
│   ├── analyze_trades.py        # Trade analyzer + config recommender
│   └── tg_bot.py                # Standalone TG bot (dual-instance)
├── src/polyclaw_cipher_v3/
│   ├── bot.py                   # Orchestrator + admin endpoints
│   ├── config.py                # Pydantic config + TierConfig
│   ├── core/                    # Scanner, CLOB WS, Binance WS, HTTP server
│   ├── strategy/                # Momentum, atomic_arb, latency_arb, resolution_snipe
│   ├── execution/               # Paper executor (live-realism v3.5.13)
│   ├── risk/                    # Sizer + TierManager + RiskManager
│   ├── state/                   # Wallet + DB + Repository
│   ├── alerts/                  # Alerter (log-only) + telegram stub
│   ├── agent/                   # LLM agent stub
│   └── observability/           # Structured logs
├── tests/
├── docs/
└── archive/
    └── v2-legacy/
```

---

## Version History (Recent)

| Version | Date | Changes |
|---------|------|---------|
| 3.5.13 | 2026-06-28 | Live-realism Tier 1: liquidity slippage, fill probability, on-chain delay, gas fee, API rate limit, position state sync. Exit slippage fix. PENDING timeout. Standalone TG bot container. Trade analyzer script. |
| 3.5.12 | 2026-06-28 | Per-market 30% limit, position cap $500, TierConfig Pydantic fix, `extra=allow`, dual-instance support, telegram alert stub |
| 3.5.11 | 2026-06-28 | TierManager reads from config, dashboard dynamic version |
| 3.5.10 | 2026-06-28 | Daemon watchdog (SignalCheck/CashCheck/ResourceCheck), admin endpoints |
| 3.5.9 | 2026-06-28 | Tier-based dynamic sizer with hysteresis |
| 3.5.8 | 2026-06-27 | Atomic_arb 100bps, live-readiness config |
| 3.5.7 | 2026-06-27 | 3 quick wins: disable res_snipe, atomic_arb 100bps, momentum 6 max |
| 3.5.6 | 2026-06-27 | Aggressive config overlay: 200 markets, all categories |
| 3.5.5 | 2026-06-27 | Super Z forensic audit fixes (6 P0 + 3 P1) |

Full changelog: [CHANGELOG.md](CHANGELOG.md)

---

## Safety

- ✅ Paper trading only (`BOT_MODE=paper`)
- ✅ Circuit breaker per strategy (auto-disable on loss streak)
- ✅ Daily drawdown limit (50%)
- ✅ Wallet overdraft guard
- ✅ Wallet invariant check (every cycle)
- ✅ Per-market concentration limit (30%)
- ✅ Absolute position cap ($500)
- ✅ Duplicate trade detection (zero tolerance)
- ✅ Health check + auto-restart (daemon 24/7)
- ✅ Liquidity-based slippage (entry + exit)
- ✅ Fill probability simulation (72% base, volatility-aware)
- ✅ On-chain delay simulation (3s ± 2s)
- ✅ Gas fee deduction ($0.01 avg + spike)
- ✅ API rate limit simulation (10 req/s)
- ✅ Position state sync (PENDING → CONFIRMED)
- ✅ TG bot allowlist (single-user)

### Before Live Trading
- [ ] Paper trading ≥ 14 days profitable across multiple reset cycles
- [ ] Win rate ≥ 55% per strategy in normal market conditions
- [ ] Max drawdown ≤ 30%
- [ ] Telegram alerts active (crash, tier transition, drawdown)
- [ ] Limit order execution implementation
- [ ] Private Polygon RPC setup

---

## Links

- **Repository:** https://github.com/doyoindah7/PolyClaw-Chiper
- **Dashboard (main):** http://3.107.53.103:8082/
- **Dashboard (micro):** http://3.107.53.103:8083/
- **TG Bot:** @polyclawchiper_bot
- **VPS:** 3.107.53.103 (AWS t2.small)
