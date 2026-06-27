# PolyClaw-Cipher v3 🔍

> HFT-capable Polymarket bot with AI agent — aggressive compounding for small capital ($25+)

## TL;DR

v3 adalah rewrite lengkap dari v2 dengan fokus:
- **WebSocket real-time** (bukan REST polling) — 60x lebih cepat
- **Event-driven architecture** — komponen terpisah via pub/sub
- **5 strategi** (4 aktif + 1 LLM stub untuk autoclaw)
- **Dashboard v3-only** — full width, detailed, auto-refresh 5s
- **Fix semua bug kritis v2** (fake resolution, blocking executor, dll)

## Status

- **v3:** RUNNING (port 8082, public access)
- **v2:** STOPPED (source code kept at `/home/ubuntu/polyclaw-cipher/` for documentation)
- **Dashboard:** http://3.107.53.103:8082/ (v3-only, auto-refresh 5s)

## Quick Start

```bash
# Build & deploy (di VPS)
cd /home/ubuntu/polyclaw-cipher-v3
docker-compose up --build -d

# Akses dashboard dari MANA SAJA:
#   http://3.107.53.103:8082/

# Check logs
docker logs -f polyclaw-cipher-v3

# Check health
curl http://3.107.53.103:8082/api/health
```

## Architecture

Lihat `ARCHITECTURE.md` untuk design lengkap.

```
Event Bus (asyncio pub/sub)
  ├── Scanner (Gamma API, 60s poll, real resolution detection)
  ├── BinanceFeed (WS, BTC/ETH/SOL real-time)
  ├── CLOBFeed (WS, Polymarket orderbook real-time)
  ├── Strategies (4 active: latency_arb, atomic_arb, resolution_snipe, momentum)
  ├── RiskManager (unified gate, per-strategy budget)
  ├── PaperExecutor (async, non-blocking)
  ├── State (SQLite WAL, async)
  ├── HTTPServer (FastAPI, 127.0.0.1:8081, unified dashboard)
  └── Alerter (stub — Telegram deferred)
```

## Strategi

| # | Nama | Edge | Sizing | Status |
|---|---|---|---|---|
| 1 | `latency_arb` | Binance price move → PM odds lag 200-500ms | 25% per trade | ✅ Active |
| 2 | `atomic_arb` | YES ask + NO ask < $1 = risk-free (threshold 40 bps) | 40% per trade | ✅ Active |
| 3 | `resolution_snipe` | Near-certain markets di 0.90-0.97 + TP/SL exit | 15% per trade | ✅ Active (threshold mode) |
| 4 | `momentum` | Sustained odds momentum (30s + 2m) | 15% per trade | ✅ Active |
| 5 | `news_llm` | LLM baca news → trade sebelum odds adjust | 10% per trade | ⏸️ Stub (autoclaw) |

## Fix v2 Bugs

| v2 Bug | v3 Fix |
|---|---|
| Fake resolution (tebak dari end_date) | Cek `closed` + `resolvedBy` fields resmi |
| `time.sleep(0.3)` blocking event loop | `await asyncio.sleep()` — non-blocking |
| Port 8080 exposed ke internet | Bind 127.0.0.1, akses via SSH tunnel |
| "Arb" single-leg (bukan arb) | Atomic pair-trade YES+NO |
| REST polling 3s lag | WebSocket real-time |
| Config vs dashboard mismatch | Dashboard baca dari /api/config |
| JSON state ~30 writes/menit | SQLite WAL, async batched |
| Daemon no backoff, no reset | Exponential backoff + reset after 1h stable |

## Autoclaw Handoff

Bot v3 dirancang untuk diteruskan ke **autoclaw** (bot AI lain yang jalan paralel).
Lihat `HANDOFF_AUTOCRAW.md` untuk:
- Cara aktifkan LLM agent (news_llm strategy)
- Cara setup Telegram alerts
- Cara extend strategi baru
- Cara switch ke live trading (v4)

## Config

Edit `config/default.yaml`:
```yaml
strategies:
  latency_arb:
    min_edge_pct: 2.0       # Min gap Binance-implied vs PM price
    max_position_pct: 0.25  # 25% bankroll per trade
  atomic_arb:
    min_profit_bps: 100     # Min 1% profit after fees
  # ... (lihat default.yaml untuk full config)
```

## Monitoring

- **Dashboard:** http://localhost:8081 (via SSH tunnel)
- **API stats:** `/api/stats` (v3), `/api/v2/stats` (proxy ke v2)
- **Health:** `/api/health`
- **Metrics:** `/metrics` (Prometheus format, stub)
- **Logs:** JSON structured, ke stdout

## Deployment

- **Folder:** `/home/ubuntu/polyclaw-cipher-v3/`
- **Port:** 0.0.0.0:8082 (public access)
- **Container:** `polyclaw-cipher-v3` (Docker, restart=unless-stopped)
- **Resource limit:** 1GB RAM, 1 CPU (t2.small friendly)
- **v2 stopped** — source kept at `/home/ubuntu/polyclaw-cipher/` for docs
- **Dashboard:** `http://3.107.53.103:8082/` (v3-only, auto-refresh 5s)

## Safety

- ⚠️ **Dashboard public** (seperti v2) — read-only monitoring, no trade execution exposed
- ❌ **Tidak ada API key di code** — via .env
- ✅ **Paper trading only** — `BOT_MODE=paper`
- ✅ **Circuit breaker per strategy** — auto-disable kalau loss streak
- ✅ **Daily drawdown limit** — 50% (configurable)
- ✅ **Atomic writes** — SQLite WAL, no corruption risk
