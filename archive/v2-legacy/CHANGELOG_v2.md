# PolyClaw-Cipher v2.0 - Aggressive Growth Upgrade
# ================================================
# Target: $25 → $150-200/week via compounding on volatile Polymarket markets
# Optimized for: t2.small (2GB RAM) VPS on AWS
# Last Updated: 2026-06-27
# ================================================

## PERUBAHAN UTAMA

### 1. CONFIG - AGGRESSIVE GROWTH PARAMETERS

| Parameter | v1.0 | v2.0 | Alasan |
|-----------|------|------|--------|
| loop_interval_sec | 1 | 2 | Kurangi CPU usage 50% |
| scan_interval_sec | 5 | 15 | Kurangi API spam 67% |
| max_open_positions | 8 | 6 | Fokus kualitas trade |
| entry_window_sec | 25,200 (7h) | 43,200 (12h) | Sweet spot entry |
| min_price_move_pct | 0.001% | 0.05% | Hindari noise |
| cooldown_sec | 0 | 30 | Anti-churn protection |
| take_profit_pct | 20% | 15% | Realistis Polymarket |
| stop_loss_pct | 12% | 8% | Tighter risk mgmt |
| max_daily_drawdown_pct | 50% | 40% | Proteksi lebih baik |
| max_consecutive_losses | 10 | 5 | Stop cepat saat buruk |
| max_trades_per_hour | 120 | 30 | Prevent overtrading |
| min_position_usd | $3.00 | $2.50 | Fleksibilitas sizing |
| slippage_bps | 30 | 25 | Lebih realistis |
| fill_probability_base | 0.80 | 0.85 | Optimistic |


### 2. SCALPER STRATEGY v2.0

Improvements:
- Volume Confirmation: Minimal 5 ticks dari Binance WS
- Confidence-based Sizing: Size otomatis menyesuaikan confidence (0.7x - 1.27x)
- Streak Protection: Kurangi confidence 20% setelah 3 loss berturut-turut
- Anti-churn: 30 detik cooldown antar sinyal
- Tick Activity Check: Skip market yang terlalu sepi

Entry Logic:
1. Harus crypto Up/Down market
2. 12 jam sebelum close
3. Price move >= 0.05%
4. Minimal 5 ticks (volume confirmation)
5. Max 3 posisi scalper
6. Cooldown 30 detik
7. Confidence >= 0.42
8. Anti-self-hedge check


### 3. UNIVERSAL STRATEGY v2.0

Improvements:
- Multi-Timeframe Analysis: 5 menit + 15 menit CLOB data
- Trend Confirmation: Kedua timeframe harus agree
- Adaptive Volatility Filter: Soft penalty untuk market choppy
- Time-based Exit: Max 20 menit hold time
- Better Confidence: Scale dengan trend alignment

Entry Logic:
1. Volume >= $1000, Liquidity >= $300
2. Price change >= 1.5% (5m) DAN >= 0.8% (15m)
3. Max 3 posisi universal
4. Cooldown 60 detik
5. Confidence >= 0.40
6. Volatility < 8%


### 4. RISK MANAGER v2.0

New Features:
- Daily Auto-Reset: Otomatis reset setiap 24 jam
- Session Rotation: Reset setiap 4 jam untuk fresh start
- Session PnL Tracking: Monitor performa per sesi
- Daily Stats: Wins/losses per hari

Limits:
- Daily Drawdown: 40% max
- Consecutive Losses: 5 max (lalu pause)
- Trades/Hour: 30 max
- Session Rotation: 4 jam


### 5. TELEGRAM ALERTS (NEW MODULE)

Fitur:
- Startup Notification: Info saat bot mulai
- Trade Alerts: Notifikasi setiap trade (rate limited)
- PnL Updates: Alert saat PnL berubah signifikan ($5+)
- Drawdown Warning: Alert saat drawdown tinggi
- Daily Summary: Ringkasan akhir hari

Cara Setup:
1. Buat bot di Telegram via @BotFather
2. Dapatkan BOT_TOKEN
3. Dapatkan CHAT_ID (kirim pesan ke @userinfobot)
4. Tambahkan ke .env:

   TELEGRAM_BOT_TOKEN=your_token_here
   TELEGRAM_CHAT_ID=your_chat_id_here
   TELEGRAM_ALERT_TRADE=true
   TELEGRAM_PNL_THRESHOLD=5.0
   TELEGRAM_INTERVAL_MIN=30


### 6. RESOURCE OPTIMIZATION

Untuk t2.small (2GB RAM):
- CLOB Polling: 2s → 3s (kurangi 33% API calls)
- Market Tracking: Max 30 market (dari 50)
- Loop Interval: 1s → 2s (kurangi 50% CPU)
- Scan Interval: 5s → 15s (kurangi 67% API calls)


## TARGET: $150-200/WEEK DARI $25

Math:
- Target: $150-200/week = ~$21-28/hari
- Dengan compounding 100% reinvest:

  Day 1: $25  → target $46-53   (+84-112%)
  Day 2: $46  → target $85-100
  Day 3: $85  → target $157-187
  Day 4: $157 → target $290-350
  Day 5-7: Continue compounding

Realistic Expectation:
- 40-60% win rate (aggressive tapi achievable)
- Average 2-5% per trade
- 20-30 trades/hari
- Compound effect sangat powerful

Risk Management:
- 40% max daily drawdown
- 5 consecutive loss limit
- 100% capital deployment (no idle cash)
- Paper trading untuk validasi dulu


## FILE STRUCTURE

polyclaw-cipher/
├── config/
│   ├── default.yaml          # UPDATED - v2.0 parameters
│   ├── default.yaml.v1       # BACKUP v1.0
│   └── paper.yaml
├── src/polyclaw_cipher/
│   ├── bot.py                # UPDATED - v2.0 orchestrator
│   ├── bot.py.v1             # BACKUP v1.0
│   ├── core/
│   │   ├── scanner.py        # (unchanged)
│   │   ├── price_feed.py     # (unchanged)
│   │   ├── clob_feed.py      # (unchanged)
│   │   ├── http_server.py    # (unchanged)
│   │   └── types.py          # (unchanged)
│   ├── strategy/
│   │   ├── scalper.py        # UPDATED v2.0
│   │   ├── scalper.py.v1     # BACKUP v1.0
│   │   ├── universal.py      # UPDATED v2.0
│   │   ├── universal.py.v1   # BACKUP v1.0
│   │   ├── arbitrage.py      # (disabled)
│   │   ├── momentum.py       # (disabled)
│   │   └── base.py           # (unchanged)
│   ├── risk/
│   │   ├── sizer.py          # (unchanged)
│   │   ├── limits.py         # UPDATED v2.0
│   │   └── limits.py.v1      # BACKUP v1.0
│   ├── execution/
│   │   └── paper.py          # (unchanged)
│   ├── state/
│   │   └── wallet.py         # (unchanged)
│   ├── alerts/               # NEW MODULE
│   │   └── __init__.py       # Telegram alerter
│   ├── config.py             # (unchanged)
│   └── __main__.py           # (unchanged)
├── scripts/
│   └── daemon.py             # (unchanged)
├── data/
│   └── wallet.json           # (runtime state)
├── CHANGELOG_v2.md           # THIS FILE
└── docker-compose.yml


## DASHBOARD API

Endpoint: GET /api/stats

Response Fields:
- bankroll: Total equity (cash + positions)
- cash: Available cash
- pnl: Total profit/loss
- trades: Total closed trades
- wins/losses: Trade breakdown
- win_rate: Percentage
- open_positions: Active positions
- recent_trades: Last 10 trades
- signals: Total signals emitted
- markets: Markets scanned
- crypto_markets: Crypto Up/Down markets
- strategies: Per-strategy stats
- risk: Risk manager status
  - consecutive_losses
  - trades_this_hour
  - daily_pnl
  - wins_today/losses_today
  - session_age_min
- btc_price/btc_move: BTC data


## MONITORING

Dashboard URL: http://3.107.53.103:8080
API Stats:     http://3.107.53.103:8080/api/stats

Docker Commands:
  docker logs --tail 50 polyclaw-cipher     # View logs
  docker restart polyclaw-cipher            # Restart bot
  docker stats polyclaw-cipher              # Resource usage


## NEXT STEPS

1. Monitor dashboard selama paper trading
2. Setup Telegram alerts (optional)
3. Paper trading 1-2 minggu untuk validasi
4. Adjust parameter jika perlu
5. Switch ke mainnet setelah yakin


## BACKUP INFO

All v1.0 files backed up with .v1 suffix.
Full src backup at: src.backup.<timestamp>
To rollback: copy .v1 files back to original names.
