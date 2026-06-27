# 🔍 PolyClaw-Cipher v3.1.0 — Analisis Post-Deploy

**Tanggal:** 27 Juni 2026  
**VPS:** AWS t2.small (2GB RAM) — IP 3.107.53.103  
**Versi:** v3.1.0 — container healthy, uptime 5+ menit  

---

## 📊 STATUS SAAT INI

| Metrik | Nilai | Verdict |
|--------|-------|---------|
| Bankroll | $25.80 | ✅ +$0.80 profit |
| Cash | $0.15 | 🔴 99.4% deployed |
| P&L | +$0.80 (+3.2%) | ✅ Profitable |
| Open Positions | 6 | ✅ Active |
| Closed Trades | 12 | ✅ Trading |
| Win Rate (closed) | 8W / 4L = 66.7% | ✅ OK |
| CLOB WS | 34 tokens, connected | ✅ Fixed |
| Binance WS | Connected | ✅ OK |
| RAM | 69MB | ✅ Lightweight |
| Strategies fired | momentum only | 🟠 Others silent |
| crypto Up/Down | 0 detected | 🔴 latency_arb dead |

**PROGRESS BESAR:** Bot sudah trading, profitable, CLOB WS fixed, wallet consistent. Tapi ada masalah yang harus diperbaiki sebelum bisa capai target $150-200/minggu.

---

## ✅ APA YANG DIPERBAIKI DENGAN BENER

### 1. CLOB WS Fix — WORKING ✅
- v3.0.0: 1 token subscribed (semua strategy blind)
- v3.1.0: 34 tokens subscribed via `sync_connections()`
- Fix: cancel semua tasks lama, spawn fresh dengan full token list
- Verdict: **FIX WORKING.** Strategy punya data harga sekarang.

### 2. Wallet Invariant — WORKING ✅
- v3.0.0: $15.91 "hilang" tanpa trade
- v3.1.0: bankroll = cash + invested = $0.15 + $25.65 = $25.80 ✓
- Auto-check setiap 3 detik, auto-recalculate kalau inconsistent
- Verdict: **FIX WORKING.** Wallet konsisten.

### 3. Binance WS Tuple Bug — FIXED ✅
- ticks disimpan sebagai (timestamp, price) tapi pct_move() akses sebagai float
- v3.1.0: `recent[-lookback_ticks][1]` (extract price dari tuple)
- Verdict: **FIX WORKING.** Stats cache berjalan tanpa crash.

### 4. Resolution Snipe TP/SL — IMPLEMENTED ✅
- v3.0.0: `check_exit()` selalu return False (unlimited downside)
- v3.1.0: SL -10%, TP +15% + register_entry/clear_position
- Verdict: **IMPLEMENTED.** Tapi lihat masalah di bawah.

### 5. Daemon Health Check — FIXED ✅
- v3.0.0: `0.0.0.0` invalid untuk connect, restart loop
- v3.1.0: hardcoded `127.0.0.1`
- Verdict: **FIX WORKING.** Container shows "healthy".

### 6. sortedcontainers Dependency — ADDED ✅
- v3.0.0: dipakai tapi tidak di pyproject.toml
- v3.1.0: `sortedcontainers>=2.4` terdaftar
- Verdict: **FIX WORKING.**

### 7. atomic_arb Threshold — LOWERED ✅
- v3.0.0: `min_profit_bps: 100` (1%) — never fires
- v3.1.0: `min_profit_bps: 40` (0.4%) — more realistic
- Verdict: **BETTER.** Tapi masih belum ada signal (lihat masalah).

---

## 🔴 MASALAH KRITIS YANG MASIH ADA

### MASALAH-1: 99.4% CASH DEPLOYED — SATU LOSS = TOTAL STOP

Cash = $0.15 dari $25.80. Ini **sangat bahaya**. `cash_min_pct: 0` config artinya:
- Kalau ada 1 loss yang butuh exit, cash tidak cukup untuk masuk posisi baru
- Kalau resolution_snipe position kena SL -10% ($5.63 × -10% = -$0.56), cash jadi negatif
- Compounding SIZER: `deployable = max(0.0, cash - reserve)` = max(0.0, 0.15 - 0) = $0.15
- Minimum position = $1.00. **Bot TIDAK BISA masuk trade baru sampai posisi close.**

Ini kontradiksi dengan tujuan "high frequency". Bot sekarang **terkunci** — 6 posisi terbuka, $0.15 cash, tidak bisa trade sampai ada posisi yang close.

**Fix:** Set `cash_min_pct: 10` minimal. Kalau 100% deployed dan win, bagus. Kalau lose, masih ada buffer buat entry baru. Compounding tetap agresif karena profit dari closed trade otomatis jadi cash baru.

### MASALAH-2: MOMENTUM MASUK SPORTS MARKET — SAMA SEPERTI BUG v2

Closed trades menunjukkan:
- "Will Saudi Arabia win?" YES → SL -8.7% → **-$0.29**
- "Will Saudi Arabia win?" YES → SL -15.1% → **-$0.47** 
- "Will Uruguay vs. Spain end in a draw?" YES → SL -5.7% → **-$0.24**
- "Will Cabo Verde win?" NO → SL -4.1% → **-$0.12**

Dan open positions:
- "Will Uruguay vs. Spain end in a draw?" NO @ 0.8371
- "Will Cabo Verde win?" NO @ 0.8170
- "Will Spain win?" NO @ 0.2556 (currently at 0.001 = -99.9%!)

Ini **PERSIS** masalah v2 yang lost -35% di "Will Saudi Arabia win?". Sports market = random outcome. Odds movement di sports prediction market TIDAK predictive — hanya reflect bookmaker odds, bukan momentum yang bisa di-trade.

**Impact:** Dari 12 closed trades, 4 loss = semuanya dari sports market. Yang menang juga dari sports (Uruguay O/U 2.5) — tapi itu kebetulan, bukan edge.

**Fix URGENT:** Tambah market category filter ke momentum. Skip semua market yang question-nya mengandung:
- "win on 2026-", "vs.", "end in a draw", "O/U", "spread"
- Pattern: political elections, sports games, entertainment awards

Atau sebaliknya: **whitelist** hanya market yang punya edge yang bisa di-analisa:
- Crypto threshold markets
- Economic data markets (CPI, GDP, Fed rate)
- Crypto Up/Down daily markets

### MASALAH-3: "WILL SPAIN WIN?" NO @ 0.2556 → CURRENT $0.001

Open position "Will Spain win?" NO @ 0.2556, invested $4.39, current price $0.001.  
Unrealized P&L = **-$4.37 (-99.6%)**. Kalau position ini close sekarang, bankroll turun dari $25.80 ke $21.43 (-$4.37 loss).

Kenapa ini terjadi? Spain jadi favorit, odds YES naik dari ~0.72 ke ~0.999. Momentum masuk NO karena "odds dropped" tapi sebenarnya trend KEBALIKAN — Spain makin kuat.

Ini contoh sempurna kenapa momentum di sports market tidak kerja: odds movement mengikuti informasi fundamental (Spain lebih kuat), bukan momentum yang bisa di-follow.

**Fix:** Stop-loss sudah kerja (4% SL), tapi ini masuk di 0.2556 = sudah low probability. Perlu filter: **skip market dengan entry price < 0.30 atau > 0.70** untuk momentum. Sudah ada `min_entry_price: 0.05` tapi terlalu rendah.

### MASALAH-4: ATOMIC ARB BUKAN ARB — SINGLE-LEG PAIR TRADE

Open position: "Will Spain win?" YES @ 0.7218, is_pair=true, strategy=atomic_arb.

Tapi **tidak ada NO side position** untuk market yang sama! Paper executor hanya membuat SATU position dari signal pair — first leg saja. Tanpa NO side, ini BUKAN arbitrage. Ini just a directional bet.

Masalah di `paper.py`:
```python
# Use first leg as primary position identifier
primary_leg, primary_price = filled_legs[0]
pos = Position(
    side=signal.side,  # ← Hanya side dari leg pertama
    ...
)
```

Signal atomic_arb punya 2 legs (YES + NO), tapi executor hanya membuat 1 position. Untuk arb yang real, harus buat 2 positions yang saling hedge.

**Fix:** Paper executor harus membuat 2 positions untuk pair signals:
1. Position YES: shares = notional / yes_ask, invested = shares × yes_ask
2. Position NO: shares = notional / no_ask, invested = shares × no_ask

Atau: buat single position yang track combined value dari kedua legs.

### MASALAH-5: RESOLUTION SNIPE DI SPORTS MARKET

2 open resolution_snipe positions:
- "Will Panama win?" NO @ 0.9373
- "Will New Zealand win?" NO @ 0.9373

Ini memang near-certain (Panama dan NZ underdogs), tapi sports itu random. Satu upset dan -93.7% loss. SL -10% tidak cukup cepat di market yang resolve binary (harga bisa lompat dari 0.94 ke 0.50 dalam satu gol).

**Fix:** Resolution snipe harus punya filter tambahan:
- Skip sports markets (sama seperti momentum filter)
- ATAU: hanya snipe market yang punya deterministic resolution (crypto threshold: "Will BTC be above $100k?" ketika BTC sudah di $105k)
- ATAU: waktu hold harus sangat singkat (< 2 jam sebelum close)

### MASALAH-6: 0 CRYPTO UP/DOWN — MASIH PERSIST

Log: `"Markets: 300 total, 0 crypto Up/Down"` setiap 60 detik.

Kenapa? Scanner scan `active=true, closed=false` — ini berarti hanya market yang BELUM resolve. Crypto Up/Down market di Polymarket biasanya daily, dan resolve cepat. Pada saat scan, market mungkin sudah close atau belum di-create untuk hari berikutnya.

v2 menunjukkan 3 crypto Up/Down — kemungkinan karena scan interval lebih cepat (15s vs 60s) jadi sempat catch market sebelum close.

**Impact:** latency_arb 100% dead karena filter `if not market.crypto_asset: return None`.

**Fix:**
1. Tambah scan khusus untuk crypto markets: query Gamma API dengan filter kategori
2. Atau: scan lebih sering (30s bukan 60s) untuk crypto-specific markets
3. Atau: relax filter — latency_arb bisa kerja di threshold markets ("Will BTC be above $100k?") yang tidak terdeteksi sebagai "crypto Up/Down" pattern

### MASALAH-7: STRATEGY STATS SEMUA 0 TAPI ADA TRADES

API menunjukkan semua strategy: signals_emitted=0, trades=0. Tapi ada 12 closed trades dan 6 open positions.

Kenapa? Bot.py update `strat.trades_won` dan `strat.trades_lost` di `_close_position()`, tapi strategy objects yang di-loop di `_get_stats()` mungkin berbeda dari yang di-referenced di `_close_position()` — atau stats tidak ke-update karena `_find_strategy()` return None.

Perlu cek: apakah `_find_strategy()` bisa menemukan strategy yang benar berdasarkan `pos.strategy` string.

### MASALAH-8: sync_connections() SETIAP 60 DETIK — DISRUPTIVE

`sync_connections()` dipanggil setiap scan cycle (60 detik). Ini cancel semua existing WS connections dan spawn baru. Artinya setiap 60 detik ada gap beberapa detik di mana CLOB data tidak ada.

Untuk strategi yang bergantung pada real-time data (momentum, atomic_arb), gap ini = missed signals.

**Fix:** Hanya call sync_connections() kalau token list benar-benar berubah. Sekarang sudah ada `_last_synced_token_count` check tapi ini tidak cukup — perlu compare actual token IDs, bukan hanya count.

---

## 🟡 MASALAH MEDIUM

### MEDIUM-1: BinanceFeed pct_move() Parameter Change

v3.0.0: `pct_move()` pakai tick count (legacy dari v2)  
v3.1.0: `pct_move(lookback_ticks: int = 60)` — parameter sekarang integer

Tapi `bot.py` `_build_stats_sync()` memanggil:
```python
snap["btc_move"] = round(self.binance_feed.get_pct_move("BTC", 60), 4)
```

Ini passing `60` sebagai `lookback_ticks` — artinya 60 ticks ≈ 1 menit lookback. OK untuk stats display. Tapi latency_arb mungkin perlu time-based lookback. Sudah ada `get_pct_move_over_sec()` tapi latency_arb tidak pakai.

### MEDIUM-2: Event Bus MASIH TIDAK DIPAKAI OLEH STRATEGI

Sama seperti v3.0.0 — strategies masih pull-based. Event bus publish `binance_tick` dan `clob_tick` tapi tidak ada subscriber yang menggunakannya untuk trading decisions.

Ini mengurangi latency advantage v3. Saat ini reaction time = loop interval (1 detik). Dengan event subscription = <50ms.

### MEDIUM-3: Config Comment Masih Bilang "runs alongside v2"

```yaml
# Optimized for: t2.small (2GB RAM) VPS, runs alongside v2
```

v2 sudah distop di v3.1.0. Comment outdated.

---

## 📊 TRADE ANALYSIS — BREAKDOWN

### Closed Trades (12)

| # | Market | Side | Entry | Exit | PnL% | PnL$ | Strategy | Result |
|---|--------|------|-------|------|------|------|----------|--------|
| 1 | Uruguay O/U 2.5 | NO | 0.787 | 0.855 | +8.6% | +$0.29 | momentum | ✅ WIN |
| 2 | Saudi Arabia | YES | 0.2356 | 0.215 | -8.7% | -$0.29 | momentum | ❌ LOSS |
| 3 | Uruguay O/U 2.5 | NO | 0.7669 | 0.835 | +8.9% | +$0.27 | momentum | ✅ WIN |
| 4 | Cabo Verde | NO | 0.787 | 0.755 | -4.1% | -$0.12 | momentum | ❌ LOSS |
| 5 | Saudi Arabia | YES | 0.2356 | 0.200 | -15.1% | -$0.47 | momentum | ❌ LOSS |
| 6 | Uruguay O/U 2.5 | NO | 0.787 | 0.875 | +11.2% | +$0.30 | momentum | ✅ WIN |
| 7 | Cabo Verde | NO | 0.817 | 0.845 | +3.4% | +$0.12 | momentum | ✅ WIN |
| 8 | Uruguay O/U 2.5 | NO | 0.8471 | 0.915 | +8.0% | +$0.29 | momentum | ✅ WIN |
| 9 | Saudi Arabia | NO | 0.7669 | 0.805 | +5.0% | +$0.15 | momentum | ✅ WIN |
| 10 | Uruguay O/U 2.5 | NO | 0.8471 | 0.925 | +9.2% | +$0.25 | momentum | ✅ WIN |
| 11 | Uruguay O/U 2.5 | NO | 0.7669 | 0.835 | +8.9% | +$0.27 | momentum | ✅ WIN |
| 12 | Uruguay Draw | YES | 0.1855 | 0.175 | -5.7% | -$0.24 | momentum | ❌ LOSS |

**Summary:** 8W / 4L = 66.7% win rate  
**Total PnL:** +$1.07 - $1.12 = **-$0.05** (closed trades slightly negative)  
**Unrealized:** Spain NO position at -99.6% = **-$4.37**

**Net jika unrealized dihitung:** $25.80 - $4.37 = **$21.43** (-$3.57 from initial $25 = **-14.3%**)

### Pattern Analysis

**Wins:** Uruguay O/U 2.5 NO — ini market "over/under goals" yang lebih predictable (goals follow Poisson distribution). Momentum kerja di sini karena odds adjust secara smooth.

**Losses:** Saudi Arabia YES, Cabo Verde NO, Uruguay Draw YES — ini random outcome markets. Momentum TIDAK punya edge di sini.

**Key insight:** Momentum bisa profitable di market yang odds-nya adjust secara gradual dan predictable (O/U goals, crypto thresholds). Tapi LOSS di market binary outcome yang random (match winner, draw).

---

## 📋 REKOMENDASI — PRIORITAS URUT

### 🔴 URGENT (Fix sekarang, blocking profitability)

| # | Fix | Impact |
|---|-----|--------|
| 1 | **Set cash_min_pct: 10** — bot terkunci di $0.15 cash, tidak bisa trade | High |
| 2 | **Tambah market category filter ke momentum** — skip sports "win/lose/draw" | High |
| 3 | **Fix atomic_arb pair execution** — executor hanya buat 1 leg, bukan 2 | High |
| 4 | **Stop resolution_snipe di sports** — atau limit ke <2 jam sebelum close | Medium-High |

### 🟠 SHORT-TERM (1-2 hari)

| # | Fix | Impact |
|---|-----|--------|
| 5 | **Fix 0 crypto Up/Down detection** — scan crypto markets lebih sering atau relax filter | Medium |
| 6 | **Fix strategy stats tracking** — _find_strategy mungkin return None | Medium |
| 7 | **Optimize sync_connections()** — only sync when token list actually changes | Medium |
| 8 | **Raise momentum min_entry_price ke 0.30** — skip low-probability entries | Medium |

### 🟡 MEDIUM-TERM (1 minggu)

| # | Fix | Impact |
|---|-----|--------|
| 9 | Connect strategies ke event bus (subscribe binance_tick, clob_tick) | High (latency) |
| 10 | Improve latency_arb prob model (time decay + vol) | Medium |
| 11 | Implement LLM agent | High (leading signals) |
| 12 | Implement Telegram alerts | Medium (monitoring) |

---

## 🎯 KESIMPULAN

v3.1.0 adalah **progress signifikan** dari v3.0.0:
- ✅ CLOB WS fix (34 tokens, was 1)
- ✅ Wallet consistent (was broken)
- ✅ Bot actually trading (12 closed trades)
- ✅ Binance WS fix, daemon fix, resolution_snipe TP/SL

Tapi ada **2 masalah yang mengancam profitability:**

1. **Sports market exposure** — momentum dan resolution_snipe masuk market random-outcome. Ini SAMA PERSIS bug v2 yang lost -35%. Kalau Spain NO position close sekarang (-$4.37), bankroll turun ke $21.43 (-14.3%).

2. **99.4% cash deployed** — bot tidak bisa trade sampai ada posisi close. Ini menghambat frequency yang dibutuhkan untuk compounding ke target $150-200/minggu.

**Jika 2 masalah ini difix:**
- Momentum hanya trade di market predictable (O/U goals, crypto)
- Cash buffer 10% = selalu bisa masuk posisi baru
- Win rate bisa naik dari 66.7% ke 75-80%
- Frequency bisa naik dari current ke 15-25 trades/day
- Compounding bisa mulai accelerate

**Risk Level:** 🟠 SEDANG — Bot profitable di closed trades tapi unrealized loss besar dari sports exposure. Fix market filter dan cash buffer = bot bisa jadi profitable secara konsisten.
