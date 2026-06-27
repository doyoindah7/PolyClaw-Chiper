# PolyClaw-Cipher Architecture

> **Goal:** Fast capital growth from $25 → $150-200/week via aggressive compounding on volatile Polymarket markets.

## Design Philosophy

Lessons from 3 bots:
- **Kimi**: Multi-strategy approach good, but TS/Node adds overhead. SQLite via better-sqlite3 works.
- **GLM 5.2**: Best architecture — modular Python, Binance WS, fill-probability model, T-minus scheduler. But single strategy = missed opportunities.
- **Opus**: Docker + auto-healing daemon excellent. But bugs from rushed code, API deprecation, no fallback data sources.

**PolyClaw-Cipher = GLM architecture + Kimi multi-strategy + Opus Docker daemon, stripped to essentials.**

## Core Principles

1. **Capital velocity > position size** — many small fast trades beat few big slow ones
2. **Always deployed** — 100% cash deployment, no idle cash, compound every trade
3. **Multi-signal** — don't rely on one strategy. Scalp + Arbitrage + Momentum = more opportunities
4. **Fail-safe** — auto-heal, crash recovery, state persistence
5. **Observable** — JSON API + Rich CLI dashboard, not a black box

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                Docker Container (auto-heal daemon)       │
│  ┌───────────────────────────────────────────────────┐  │
│  │            PolyClawCipher (orchestrator)           │  │
│  │  ┌─────────┐ ┌──────────┐ ┌───────────────────┐  │  │
│  │  │ Scanner │ │ Binance  │ │  Polymarket REST  │  │  │
│  │  │ (keyset)│ │ PriceFeed│ │  (fallback prices)│  │  │
│  │  └────┬────┘ └────┬─────┘ └────────┬──────────┘  │  │
│  │       │           │                │              │  │
│  │  ┌────▼───────────▼────────────────▼──────────┐  │  │
│  │  │          Signal Engine (3 strategies)       │  │  │
│  │  │  ┌─────────┐ ┌──────────┐ ┌─────────────┐ │  │  │
│  │  │  │ Scalper │ │ Arb 101  │ │ Momentum    │ │  │  │
│  │  │  │(crypto) │ │(risk-free)│ │(volatile)   │ │  │  │
│  │  │  └────┬────┘ └────┬─────┘ └──────┬──────┘ │  │  │
│  │  └───────┼───────────┼──────────────┼────────┘  │  │
│  │          │           │              │            │  │
│  │  ┌───────▼───────────▼──────────────▼────────┐  │  │
│  │  │     Risk Manager + Compounding Sizer       │  │  │
│  │  └───────────────────┬───────────────────────┘  │  │
│  │                      │                           │  │
│  │  ┌───────────────────▼───────────────────────┐  │  │
│  │  │    Paper Executor (fill-probability sim)   │  │  │
│  │  └───────────────────┬───────────────────────┘  │  │
│  │                      │                           │  │
│  │  ┌───────────────────▼───────────────────────┐  │  │
│  │  │     State DB (SQLite) + Heartbeat          │  │  │
│  │  └───────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │         Web API (port 8080) + Dashboard           │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## Strategies

### 1. Crypto Scalper (primary — 60% of capital)
- **Market**: Daily crypto Up/Down (BTC/ETH/SOL)
- **Entry**: Wide window (T-7h to T-30s before close)
- **Signal**: Binance price move > 0.01% + RSI + EMA confirmation
- **Confidence**: Boosted base (0.55+) for small moves, penalties for high volatility
- **Sizing**: cash / remaining_slots (100% deployment)
- **Exit**: Market close (binary resolution) or position switch
- **Target**: 3-8% per trade, 10-30 trades/day

### 2. Arbitrage 101 (risk-free — 20% of capital)
- **Market**: Any binary market where YES + NO < $0.98
- **Entry**: Instant when spread detected
- **Exit**: Immediate (virtual merge at $1)
- **Target**: 0.5-2% per trade, 20-100 trades/day
- **Key**: Scan ALL markets, not just crypto

### 3. Momentum Hunter (aggressive — 20% of capital)
- **Market**: High-volume volatile markets (any category)
- **Entry**: Price change > 5% in last hour + volume spike
- **Exit**: Take profit 10%, stop loss 5%, max hold 30 min
- **Target**: 5-15% per trade, 5-15 trades/day

## Key Improvements vs Opus

| Issue | Opus | Cipher |
|-------|------|--------|
| API endpoint | Deprecated /markets | /markets/keyset |
| JSON fields | Crashes on string | Auto JSON.parse |
| Entry window | 8 min only | 7h adaptive |
| Price threshold | 2% (too high) | 0.01% (any move) |
| Confidence floor | 0.55 (too high) | 0.40 (aggressive) |
| Capital deployment | Kelly (partial) | cash/slots (100%) |
| Strategies | 3 (but 0 signals) | 3 (all active) |
| Dashboard | Terminal (broken) | JSON API + minimal HTML |
| Price feed | Binance WS only | Binance WS + REST fallback |

## Tech Stack

- **Language**: Python 3.11 (same as Opus/GLM)
- **Container**: Docker + docker-compose
- **Daemon**: Auto-healing with heartbeat
- **DB**: SQLite (aiosqlite)
- **API**: httpx (async HTTP)
- **WS**: websockets (Binance), REST fallback (Polymarket)
- **Dashboard**: FastAPI or raw asyncio HTTP server
- **Config**: YAML + env vars

## File Structure

```
polyclaw-cipher/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── README.md
├── config/
│   ├── default.yaml
│   └── paper.yaml
├── src/polyclaw_cipher/
│   ├── __init__.py
│   ├── __main__.py
│   ├── bot.py              # Main orchestrator
│   ├── config.py           # Config loader
│   ├── core/
│   │   ├── __init__.py
│   │   ├── types.py        # Pydantic models
│   │   ├── scanner.py      # MarketScanner (keyset API)
│   │   ├── price_feed.py   # Binance WS + REST fallback
│   │   └── http_server.py  # JSON API + dashboard
│   ├── strategy/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── scalper.py      # Crypto Up/Down scalper
│   │   ├── arbitrage.py    # YES+NO < $1
│   │   └── momentum.py     # Volatile market momentum
│   ├── execution/
│   │   ├── __init__.py
│   │   └── paper.py        # Paper executor with fill sim
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── sizer.py        # Compounding position sizer
│   │   └── limits.py       # Drawdown limiter
│   └── state/
│       ├── __init__.py
│       ├── db.py           # SQLite state
│       └── wallet.py       # Wallet (JSON persistence)
├── scripts/
│   └── daemon.py           # Auto-healing daemon
└── tests/
    └── test_strategy.py
```

## Configuration (default.yaml)

```yaml
bot:
  mode: paper
  loop_interval_sec: 1
  scan_interval_sec: 20

market:
  min_volume_24h_usd: 1000
  scan_all_categories: true        # Don't limit to crypto
  api_endpoint: /markets/keyset    # Non-deprecated

strategies:
  scalper:
    enabled: true
    entry_window_sec: 25200        # 7h before close
    min_price_move_pct: 0.01       # 0.01% = any micro move
    min_confidence: 0.40           # Aggressive
    bid_range: [0.05, 0.95]        # Wide bid range
    max_positions: 5
    cooldown_sec: 3
    
  arbitrage:
    enabled: true
    min_profit_bps: 50             # 0.5% profit minimum
    max_concurrent: 3
    scan_interval_sec: 5
    
  momentum:
    enabled: true
    min_price_change_pct: 3.0      # 3% in last hour
    min_volume_spike: 1.5          # 1.5x average volume
    take_profit_pct: 10.0
    stop_loss_pct: 5.0
    max_hold_min: 30
    max_positions: 3

risk:
  initial_bankroll: 25.00
  max_daily_drawdown_pct: 30.0     # Aggressive
  max_consecutive_losses: 8
  compound_reinvest_pct: 100       # 100% reinvest
  cash_min_pct: 0                  # Always deployed

execution:
  paper:
    slippage_bps: 30
    fill_probability_base: 0.80
    fill_probability_at_bid_low: 0.95
    fill_probability_at_bid_high: 0.60

monitoring:
  log_level: INFO
  web:
    enabled: true
    port: 8080
  heartbeat_sec: 5
```

## Deployment

```bash
# Build & run
docker-compose up --build -d

# Check
curl http://localhost:8080/api/stats
docker logs polyclaw-cipher
```
