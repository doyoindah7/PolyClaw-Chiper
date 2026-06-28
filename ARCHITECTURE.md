# PolyClaw-Cipher v3.5.12 — System Architecture

> **Status:** PRODUCTION (paper trading)
> **Last updated:** 2026-06-28
> **Version:** 3.5.12

---

## 1. Overview

PolyClaw-Cipher v3 is an async Python Polymarket trading bot designed for aggressive compounding from micro capital ($10-$25). It uses WebSocket price feeds for real-time market data and a momentum-driven strategy to capture odds swings in volatile prediction markets.

### 1.1 Current State

- **2 instances running** on t2.small VPS (3.107.53.103)
- **Strategy:** Momentum-only (atomic_arb disabled for live-readiness, latency_arb blocked by market availability)
- **Mode:** Paper trading with 70bps slippage simulation
- **Tier 1 locked** for aggressive growth testing ($25/$10 → $300+)
- **Consistency testing** via multiple reset cycles

### 1.2 Key Metrics (Run 1, pre-safety-fixes)
- 387 trades in ~12h, $25 → $8,170
- Momentum: 71% WR, +$8,091 PnL
- Entry sweet spot: 0.30-0.70 odds (74% WR, 93% of profit)
- **Note:** Inflated by World Cup event — normal market expected $25 → $50-100/week

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Docker Container (auto-heal daemon)           │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐  ┌──────────────┐ │
│  │ Scanner  │  │ CLOB WS  │  │ Binance WS │  │ HTTP Server  │ │
│  │ (300 mkt)│  │ (134 tok)│  │ (BTC/ETH)  │  │ (Dashboard)  │ │
│  └────┬─────┘  └────┬─────┘  └─────┬──────┘  └──────┬───────┘ │
│       │              │              │                 │          │
│       └──────────────┼──────────────┼─────────────────┘          │
│                      │              │                             │
│              ┌───────▼──────────────▼───────┐                    │
│              │        Market Context        │                    │
│              │  (prices, changes, volume)   │                    │
│              └───────────────┬──────────────┘                    │
│                              │                                    │
│              ┌───────────────▼──────────────┐                    │
│              │     Strategy Engine          │                    │
│              │  ┌────────────────────────┐  │                    │
│              │  │  Momentum (active)     │  │                    │
│              │  │  - Multi-timeframe     │  │                    │
│              │  │  - Per-market 30% cap  │  │                    │
│              │  │  - Streak protection   │  │                    │
│              │  ├────────────────────────┤  │                    │
│              │  │  AtomicArb (disabled)  │  │                    │
│              │  │  LatencyArb (disabled) │  │                    │
│              │  │  ResolutionSnipe (off) │  │                    │
│              │  └────────────────────────┘  │                    │
│              └───────────────┬──────────────┘                    │
│                              │                                    │
│              ┌───────────────▼──────────────┐                    │
│              │       Risk Pipeline          │                    │
│              │  RiskManager → TierManager   │                    │
│              │       → CompoundingSizer     │                    │
│              └───────────────┬──────────────┘                    │
│                              │                                    │
│              ┌───────────────▼──────────────┐                    │
│              │    Paper Executor (70bps)    │                    │
│              └───────────────┬──────────────┘                    │
│                              │                                    │
│              ┌───────────────▼──────────────┐                    │
│              │   Wallet + SQLite WAL DB     │                    │
│              │   (invariant check every Ns) │                    │
│              └──────────────────────────────┘                    │
└─────────────────────────────────────────────────────────────────┘
```

### 2.1 Data Flow (Per Cycle)

1. **Scanner** (every 15s): Polls Gamma API → 300 active markets → categorizes (sports_match, crypto, politics, etc.)
2. **CLOB WS** (continuous): Real-time orderbook updates for 134 tracked tokens → price + % change
3. **Strategy evaluate** (every 2s): Iterates 300 markets → checks momentum thresholds → fires signals
4. **Risk pipeline**: RiskManager checks circuit breakers → TierManager determines tier → Sizer computes position size
5. **Executor**: Opens paper positions with 70bps slippage → monitors TP/SL → closes on trigger
6. **Daemon** (every 60s): SignalCheck (0 signals alert), CashCheck (cash < 1% bankroll), ResourceCheck (WAL checkpoint)

---

## 3. Safety Systems (v3.5.12)

### 3.1 Position Controls
| Control | Value | Purpose |
|---------|-------|---------|
| Per-market exposure limit | 30% bankroll | Prevents concentration (was 350% in Run 1) |
| Absolute position cap | $500 | Prevents unrealistic paper sizes ($1,900 → capped) |
| Max positions (momentum) | 6 | Distributed across markets |
| Slippage simulation | 70 bps | Realistic live fill simulation |

### 3.2 Tier-Based Dynamic Sizer
4 tiers with 10% hysteresis and 24h cooldown:
- Tier 1 ($25-$275): 20%/trade, min $3.00
- Tier 2 ($275-$1,100): 12%/trade, min $10.00
- Tier 3 ($1,100-$5,500): 8%/trade, min $25.00
- Tier 4 ($5,500+): 5%/trade, min $50.00

### 3.3 Daemon Watchdog
- **SignalCheck**: Alerts if any strategy emits 0 signals for 1h+
- **CashCheck**: Alerts if cash < 1% bankroll
- **ResourceCheck**: Auto-triggers WAL checkpoint at >5MB
- **Stagnation guard**: Restarts bot if no signals + no positions for 10m
- **Crash loop detection**: Switches to 300s retry intervals after 10 crashes/hour

### 3.4 Wallet Integrity
- Invariant: bankroll == cash + invested (verified every cycle)
- Overdraft guard: `InsufficientFundsError` before execution
- Double-close lock: prevents race condition on position close

---

## 4. Instance Architecture

```
VPS (t2.small, 2GB RAM)
│
├── polyclaw-cipher-v3 (port 8082)
│   ├── Config: default.yaml + paper.yaml
│   ├── Data: /data/cipher_v3.db (Docker volume)
│   ├── Bankroll: $25
│   └── RAM: ~130MB
│
├── polyclaw-fifteen (port 8084)
│   ├── Config: default.yaml + fifteen.yaml
│   ├── Data: /app/data (separate volume)
│   ├── Bankroll: $15
│   └── RAM: ~90MB
│
└── Total RAM: ~250MB used / 1.9GB
```

Both instances share the same codebase and strategy config — only bankroll differs.

---

## 5. Database Schema

```
wallet:     id, bankroll, cash, initial_bankroll, updated_at
trades:     id, market_condition_id, market_question, strategy, side,
            entry_price, exit_price, invested, pnl_dollar, pnl_percent,
            reason, opened_at, closed_at
signals:    id, strategy, market_id, side, entry_price, confidence, timestamp
positions:  id, strategy, side, market_condition_id, invested, entry_price,
            current_price, opened_at, status
```

---

## 6. Configuration

### 6.1 Config Loading Pipeline
```
default.yaml → {mode}.yaml overlay → env overrides → Pydantic validation
```

### 6.2 Key Config Sections
```yaml
risk:
  initial_bankroll_usd: 25.0
  max_daily_drawdown_pct: 50.0
  sizer:
    min_position_usd: 2.00
    max_absolute_position: 500
    cash_min_pct: 15

tier:
  force_tier: 1        # 0=auto, 1-4=locked
  cooldown_hours: 24

strategies:
  momentum:
    max_per_market_pct: 0.30
    max_positions: 6
    min_momentum_short_pct: 1.5
    min_momentum_long_pct: 0.8
```

---

## 7. Known Limitations

| Issue | Impact | Status |
|-------|--------|--------|
| Latency_arb dead | 0 crypto Up/Down markets detected | Blocked — needs scanner refactor |
| Resolution_snipe disabled | 12.5% WR, dead weight | Permanently off |
| Atomic_arb disabled | Leg risk fatal for live | Off for live-readiness |
| World Cup dependency | 99.98% profit from sport | Normal market baseline TBD (Run 2) |
| Pydantic strips unknown fields | Requires `extra=allow` on models | Fixed in 3.5.12 |
| Weekend low volatility | 0 signals during Asian hours | Expected behavior |

---

## 8. Deployment

```bash
# Build & deploy both instances
cd /home/ubuntu/polyclaw-cipher-v3
docker compose up --build -d
docker compose -f docker-compose.fifteen.yaml up --build -d

# Reset for new test cycle
python3 scripts/archive_trades.py          # backup first
# Stop container, run full_reset.py, restart

# Health check
curl http://localhost:8082/api/health
curl http://localhost:8084/api/health
```

**VPS connection:**
```bash
ssh -i C:\Users\LENOVO\.ssh\t2small.pem ubuntu@3.107.53.103
```

---

## 9. Future

- Telegram alerts (stub ready, needs BOT_TOKEN + CHAT_ID)
- LLM agent for information-asymmetry edge (resolution_snipe revival)
- Mean reversion strategy (academic paper validated)
- Rate limit + realistic fill probability simulation
- Live trading (after 14+ days paper profit + all safety checks)
