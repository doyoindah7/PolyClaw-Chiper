# PolyClaw-Cipher v2.1 — Technical Documentation

> Last updated: 2026-06-27 | Bot version: 2.1 | VPS: AWS t2.small (2GB RAM)

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Component Reference](#component-reference)
3. [Configuration Guide](#configuration-guide)
4. [Strategy Deep-Dive](#strategy-deep-dive)
5. [Risk Management](#risk-management)
6. [Deployment](#deployment)
7. [Monitoring & Debugging](#monitoring--debugging)
8. [Known Issues (Current)](#known-issues-current)
9. [Roadmap](#roadmap)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                 Docker Container (auto-heal daemon)          │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │           PolyClawCipher v2.1 (asyncio orchestrator)   │  │
│  │                                                        │  │
│  │  ┌──────────┐  ┌──────────────┐  ┌─────────────────┐  │  │
│  │  │ Scanner  │  │ Binance WS   │  │ Polymarket CLOB │  │  │
│  │  │ (Gamma   │  │ PriceFeed    │  │ REST Feed       │  │  │
│  │  │  keyset) │  │ (BTC/ETH/SOL)│  │ (top 15 markets)│  │  │
│  │  └────┬─────┘  └──────┬───────┘  └────────┬────────┘  │  │
│  │       │               │                   │           │  │
│  │  ┌────▼───────────────▼───────────────────▼────────┐  │  │
│  │  │          Signal Engine (2 active strategies)    │  │  │
│  │  │  ┌──────────┐  ┌──────────────┐                │  │  │
│  │  │  │ Scalper  │  │  Universal   │                │  │  │
│  │  │  │ (crypto) │  │  (volatile)  │                │  │  │
│  │  │  └────┬─────┘  └──────┬───────┘                │  │  │
│  │  └───────┼───────────────┼─────────────────────────┘  │  │
│  │          │               │                            │  │
│  │  ┌───────▼───────────────▼─────────────────────────┐  │  │
│  │  │   CompoundingSizer + DrawdownLimiter            │  │  │
│  │  │   (properly integrated in v2.1)                 │  │  │
│  │  └───────────────────┬────────────────────────────┘  │  │
│  │                      │                                │  │
│  │  ┌───────────────────▼────────────────────────────┐  │  │
│  │  │   PaperExecutor (ASYNC — no event loop block)  │  │  │
│  │  └───────────────────┬────────────────────────────┘  │  │
│  │                      │                                │  │
│  │  ┌───────────────────▼────────────────────────────┐  │  │
│  │  │   Wallet (JSON, disk flush every 30s)          │  │  │
│  │  └────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │         HTTP Dashboard (port 8080) + JSON API         │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Every 15s:** Scanner polls Gamma API `/markets/keyset` → list of active markets
2. **Real-time:** Binance WS streams BTC/ETH/SOL trade + kline data → PriceFeed ticks
3. **Every 5s:** CLOB REST polls top 15 markets' orderbook → TokenFeed ticks
4. **Every 2s:** Main loop iterates over all markets, runs strategies → signals
5. **On signal:** CompoundingSizer calculates size → PaperExecutor simulates fill → Wallet records
6. **Every loop:** Check open positions for TP/SL/timeout/resolution
7. **Every 30s:** Wallet heartbeat flushed to disk (daemon health check)
8. **Every 5min:** P&L alert via Telegram stub

---

## Component Reference

### Core Modules

| Module | Purpose | Key Classes | Disk I/O |
|--------|---------|-------------|----------|
| `bot.py` | Orchestrator | `PolyClawCipher` | None |
| `config.py` | YAML + env config loader | `load_config()` | Read on startup |
| `scanner.py` | Gamma API market scanner | `MarketScanner` | None |
| `price_feed.py` | Binance WS price feed | `PriceFeed`, `AssetFeed` | None |
| `clob_feed.py` | Polymarket CLOB REST feed | `CLOBFeed`, `TokenFeed` | None |
| `http_server.py` | Dashboard + JSON API | `HTTPServer` | None |
| `types.py` | Pydantic data models | `Market`, `Signal`, `Position`, `Trade`, `Side` | None |

### Strategy Modules

| Module | Status | Markets | Signal Source |
|--------|--------|---------|---------------|
| `scalper.py` | ✅ Active | Crypto Up/Down only | Binance price move + volume |
| `universal.py` | ✅ Active | All volatile markets | CLOB 5m+15m momentum |
| `arbitrage.py` | ❌ Disabled | All (YES+NO < $1) | Combined cost anomaly |
| `momentum.py` | ❌ Disabled | All volatile | CLOB odds shift |

### Execution Modules

| Module | Purpose | Key Detail |
|--------|---------|------------|
| `paper.py` | Paper trading executor | Async fill simulation with slippage + fill probability |
| `sizer.py` | Position sizing | CompoundingSizer: cash/slots with confidence scaling |
| `limits.py` | Risk limits | DrawdownLimiter: daily DD, consecutive losses, rate limit |

### State Modules

| Module | Purpose | Disk Writes |
|--------|---------|-------------|
| `wallet.py` | Trading state persistence | On position open/close + every 30s heartbeat |

---

## Configuration Guide

### default.yaml Structure

```yaml
bot:
  mode: paper                    # paper | live
  loop_interval_sec: 2           # Main loop frequency
  scan_interval_sec: 15          # Market scan frequency
  max_open_positions: 6          # Total max concurrent positions

market:
  min_volume_24h_usd: 500        # Minimum 24h volume to consider
  min_liquidity: 200             # Minimum liquidity
  api_page_size: 500             # Gamma API page size
  max_pages: 3                   # Max pages to fetch (1500 markets max)
  volatility_threshold: 0.02     # 2% minimum volatility

strategies:
  scalper:
    enabled: true
    entry_window_sec: 43200      # 12h before close
    min_price_move_pct: 0.05     # 0.05% minimum Binance move
    min_confidence: 0.42         # Aggressive threshold
    take_profit_pct: 15.0
    stop_loss_pct: 8.0
    max_positions: 3
    cooldown_sec: 30

  universal:
    enabled: true
    lookback_sec: 300            # 5m momentum
    secondary_lookback_sec: 900  # 15m confirmation
    min_price_change_pct: 1.5    # 1.5% in 5m
    take_profit_pct: 12.0
    stop_loss_pct: 6.0
    max_positions: 3
    cooldown_sec: 60
    min_entry_price: 0.10        # Skip penny markets
    max_entry_price: 0.90        # Skip near-certain

  arbitrage:
    enabled: false               # RECOMMENDATION: enable for risk-free profits
    min_profit_bps: 30           # 0.3% minimum

  momentum:
    enabled: false

risk:
  initial_bankroll_usd: 25.00
  max_daily_drawdown_pct: 40.0   # 40% max daily DD
  max_consecutive_losses: 5      # Stop after 5 consecutive losses
  max_trades_per_hour: 30
  max_positions: 6
  min_position_usd: 2.50
  max_position_pct: 0.40         # 40% bankroll max per position
  cash_min_pct: 5                # 5% cash reserve
  compound_reinvest_pct: 100     # 100% reinvest

execution:
  paper:
    slippage_bps: 25             # 0.25% simulated slippage
    fill_probability_base: 0.85
    fill_probability_at_bid_low: 0.95
    fill_probability_at_bid_high: 0.65
    queue_position_factor: 0.6
    simulated_latency_sec: 0.2   # 200ms simulated latency

monitoring:
  log_level: INFO
  web:
    enabled: true
    host: 0.0.0.0
    port: 8080
  heartbeat_sec: 5
  telegram:
    enabled: false               # Set true + add token in .env
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_MODE` | `paper` | Trading mode (paper/live) |
| `INITIAL_BANKROLL_USD` | `25.00` | Starting capital |
| `CONFIG_DIR` | `config` | Config directory path |
| `VERIFY_SSL` | `false` | SSL verification (set `true` for production) |
| `LOG_LEVEL` | `INFO` | Logging level |

---

## Strategy Deep-Dive

### Crypto Scalper

**Target:** Daily crypto Up/Down markets (BTC, ETH, SOL)

**Entry Logic:**
1. Market must be crypto Up/Down type (detected via regex on question text)
2. Must be within 12-hour window before market close
3. Skip if odds > 97% (too one-sided)
4. Binance price move must exceed 0.05%
5. At least 5 price ticks available for analysis
6. No more than 3 concurrent scalper positions
7. 30-second cooldown between signals on same market

**Direction Logic:**
- Up/Down market: YES if price move > 0, NO if < 0
- Threshold market ("above $X"): YES if price move > 0
- Dip market ("dip to $X"): NO if price move > 0 (price didn't dip)

**Confidence Calculation:**
```
base = 0.50 + |pct_move| * 5.0  (capped at 0.95)
+ tick_boost = min(0.10, ticks / 500)
- vol_penalty = min(0.20, (realized_vol - 0.002) * 20)
- streak_penalty = 0.8x if 3 consecutive losses
```

**Exit:**
- Market resolution (binary outcome)
- Take profit: +15%
- Stop loss: -8%

### Universal Scalper

**Target:** Any high-volume volatile market (sports, politics, tech, etc.)

**Entry Logic:**
1. Volume > $1,000/24h and liquidity > $300
2. Entry price between 0.10–0.90 (skip extremes)
3. CLOB 5-minute change > 1.5%
4. CLOB 15-minute change > 0.8% (trend confirmation)
5. Both timeframes must agree on direction
6. Volatility < 8% (skip choppy markets)
7. No more than 3 concurrent universal positions
8. One position per market
9. 60-second cooldown

**⚠️ Known Weakness (v2.1):**
Universal strategy enters markets based purely on price momentum, without fundamental analysis or news context. This led to a -100% loss on "Will Saudi Arabia win?" — a sports market that can't be predicted from odds movement alone.

**Recommendation:** Add market category filter. Only enter markets with:
- Predictable catalysts (crypto, economic data releases)
- Sufficient CLOB depth (bid+ask > $500)
- Avoid sports/entertainment markets (random outcomes)

---

## Risk Management

### CompoundingSizer (v2.1 — now properly integrated)

```python
def size(bankroll, open_positions, max_positions, confidence, cash):
    deployable = cash - (bankroll * cash_min_pct / 100)
    free_slots = max(1, max_positions - open_positions)
    notional = deployable / free_slots
    notional = min(notional, bankroll * max_pct_per_trade)
    return notional if notional >= min_position_usd else 0.0
```

**Key behavior:**
- Uses CASH (not bankroll) as deployable capital
- Divides equally among remaining position slots
- Always deploys up to max_pct_per_trade (40% of bankroll)
- 5% cash reserve maintained
- Returns 0 if notional < min_position ($2.50)

### DrawdownLimiter

| Check | Threshold | Action |
|-------|-----------|--------|
| Daily drawdown | 40% of day-start bankroll | Block all trading |
| Consecutive losses | 5 | Block all trading |
| Trades per hour | 30 | Block all trading |
| Session rotation | Every 4 hours | Reset consecutive losses |

**Auto-reset:** Daily stats reset at midnight (24h from init).

---

## Deployment

### Build & Run

```bash
cd ~/polyclaw-cipher
docker compose up --build -d
```

### View Logs

```bash
docker logs -f polyclaw-cipher        # Follow logs
docker logs --tail 50 polyclaw-cipher # Last 50 lines
```

### Check Status

```bash
curl http://localhost:8080/api/stats  # JSON API
# Or open http://3.107.53.103:8080/ in browser for dashboard
```

### Restart

```bash
cd ~/polyclaw-cipher
docker compose restart
```

### Reset Wallet (Fresh Start)

```bash
docker compose down
sudo rm -f data/wallet.json
cd ~/polyclaw-cipher
docker compose up -d
# Wallet auto-initializes with $25.00
```

### Stop

```bash
cd ~/polyclaw-cipher
docker compose down
```

---

## Monitoring & Debugging

### API Endpoints

| Endpoint | Returns |
|----------|---------|
| `GET /` | HTML dashboard |
| `GET /api/stats` | JSON stats snapshot |

### Key Metrics to Watch

| Metric | Healthy Range | Warning |
|--------|---------------|---------|
| Bankroll | ≥ initial ($25) | < 60% initial ($15) |
| Open positions | 1-6 | 0 (idle) or 6 (maxed) |
| Consecutive losses | 0-2 | ≥ 4 |
| Trades/hour | 5-30 | 0 (no signals) or 30 (rate limit) |
| Daily PnL | Positive | < -10% |
| CLOB feed last_update | < 10s ago | > 30s (stale) |
| RAM usage | < 100MB | > 200MB |

### Log Levels

```bash
# In docker-compose.yml or .env:
LOG_LEVEL=DEBUG   # Verbose — all strategy evaluations
LOG_LEVEL=INFO    # Normal — signals, trades, scans
LOG_LEVEL=WARNING # Quiet — only warnings and errors
```

### Daemon Health Check

The daemon monitors `data/wallet.json` heartbeat field. If heartbeat is stale > 30 seconds, daemon kills and restarts the bot. Maximum 50 restarts before giving up.

---

## Known Issues (Current)

1. **Scalper rarely gets signals** — Only 3 crypto Up/Down markets exist on Polymarket at any time. Need more crypto asset coverage (BNB, XRP, DOGE) and possibly hourly/4H windows.

2. **Universal strategy quality** — Enters markets based on odds momentum alone, without fundamental context. Can enter sports/entertainment markets that are essentially random. **Recommendation: add category filter.**

3. **Arbitrage disabled** — Risk-free strategy is turned off. Polymarket markets are usually efficient (YES+NO ≈ $1.00), but occasional inefficiencies exist, especially in new or illiquid markets.

4. **No trailing stop** — Fixed TP/SL doesn't lock in profits as price moves favorably.

5. **Telegram alerts are stub** — No actual notifications sent. Need bot token + chat ID in `.env`.

6. **SSL verification disabled** — `VERIFY_SSL=false` in docker-compose. Should be `true` for production.

7. **Dashboard HTML is inline** — ~3KB embedded in Python source. Could be external template for easier customization.

---

## Roadmap

### Phase 1: Immediate (next 1-2 days)
- [ ] Enable Arbitrage strategy for risk-free profits
- [ ] Add market category filter to Universal (skip sports/entertainment)
- [ ] Add BNB, XRP, DOGE to PriceFeed Binance streams
- [ ] Implement proper Telegram alerts (real bot token)

### Phase 2: Short-term (1 week)
- [ ] Add trailing stop mechanism
- [ ] Add Binance order book imbalance signal for Scalper
- [ ] Migrate wallet from JSON to SQLite (atomic, queryable)
- [ ] Add rate limiter for API calls (max 5 req/sec)

### Phase 3: Medium-term (2-4 weeks)
- [ ] Add backtesting framework
- [ ] Implement LiveExecutor for real Polygon mainnet trading
- [ ] Add Kelly Criterion option for sustainable sizing
- [ ] Enable SSL verification
- [ ] Add non-root Docker user

### Phase 4: Advanced
- [ ] WebSocket CLOB feed (replace REST polling)
- [ ] Multi-instance deployment
- [ ] News/sentiment integration for market analysis
- [ ] Advanced signal confirmation (EMA crossover, RSI, volume profile)

---

## File Structure (v2.1)

```
polyclaw-cipher/
├── CHANGELOG.md              ← This version's changelog
├── RECOMMENDATIONS.md        ← Analysis & recommendations
├── DOCS.md                   ← This file
├── ARCHITECTURE.md           ← Original architecture doc (v2.0)
├── README.md
├── CHANGELOG_v2.md           ← Old changelog (v2.0)
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── config/
│   ├── default.yaml          ← Main configuration
│   ├── default.yaml.backup
│   ├── default.yaml.v1
│   └── paper.yaml            ← Paper trading overrides
├── src/
│   └── polyclaw_cipher/
│       ├── __init__.py
│       ├── __main__.py
│       ├── bot.py             ← v2.1 orchestrator (FIXED)
│       ├── config.py
│       ├── alerts.py          ← Telegram stub
│       ├── core/
│       │   ├── types.py
│       │   ├── scanner.py
│       │   ├── price_feed.py  ← v2.1 (buffer optimized)
│       │   ├── clob_feed.py   ← v2.1 (API load optimized)
│       │   └── http_server.py
│       ├── strategy/
│       │   ├── base.py
│       │   ├── scalper.py
│       │   ├── universal.py
│       │   ├── arbitrage.py
│       │   └── momentum.py
│       ├── execution/
│       │   └── paper.py       ← v2.1 (async, no blocking)
│       ├── risk/
│       │   ├── sizer.py       ← CompoundingSizer
│       │   └── limits.py      ← DrawdownLimiter
│       └── state/
│           └── wallet.py      ← v2.1 (reduced disk I/O)
├── scripts/
│   └── daemon.py              ← Auto-healing daemon
├── data/
│   ├── wallet.json            ← Runtime state
│   └── heartbeat.json         ← Daemon heartbeat
└── src.backup.20260627_013312/  ← Pre-v2.1 backup
```
