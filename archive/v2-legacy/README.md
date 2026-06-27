# PolyClaw-Cipher 🔍

> Aggressive Polymarket trading bot — fast capital growth from $25 via 3-strategy compounding.

## TL;DR

3 strategi simultan:
1. **Crypto Scalper** — daily BTC/ETH/SOL Up/Down markets, entry 7h window, 0.01% price move trigger
2. **Arbitrage 101** — YES+NO < $1 = risk-free profit, scans ALL markets
3. **Momentum Hunter** — volatile market odds movement, TP 8% / SL 4%, max hold 30min

100% capital deployment. 100% profit reinvestment. Docker + auto-healing daemon.

## Quick Start

```bash
# Build & deploy
docker-compose up --build -d

# Check
curl http://localhost:8080/api/stats
# Dashboard
open http://localhost:8080/

# Logs
docker logs polyclaw-cipher -f
```

## Architecture

Best of 3 bots combined:
- **GLM 5.2**: Binance WS price feed, fill-probability model, T-minus scheduler
- **Kimi**: Multi-strategy approach (scalper + arb + momentum)
- **Opus**: Docker container + auto-healing daemon

## Config

Edit `config/default.yaml`:
```yaml
strategies:
  scalper:
    min_price_move_pct: 0.01  # trigger on any move
    min_confidence: 0.40      # aggressive
    entry_window_sec: 25200   # 7h
  arbitrage:
    min_profit_bps: 30        # 0.3% min arb profit
  momentum:
    min_price_change_pct: 2.0 # 2% odds move
    take_profit_pct: 8.0
    stop_loss_pct: 4.0
```

## VPS Deploy

```bash
# SCP to VPS
scp -r polyclaw-cipher/ ubuntu@3.107.53.103:/home/ubuntu/polyclaw-cipher/

# SSH & run
ssh ubuntu@3.107.53.103
cd polyclaw-cipher && docker-compose up --build -d
```

## Paper Trading Model

- Fill probability: 60-95% based on bid level (lower bid = higher fill)
- Slippage: 30bps
- Simulated latency: 300ms
- Queue position factor: 0.5

## Risk Management

- Max daily drawdown: 30%
- Max consecutive losses: 8
- Max trades/hour: 60
- Position sizing: cash / available_slots (100% deployment)
