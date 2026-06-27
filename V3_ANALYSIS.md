# 🔍 PolyClaw-Cipher v3 — Analisis Kritis & Rekomendasi

**Tanggal:** 27 Juni 2026  
**VPS:** AWS t2.small (2GB RAM) — IP 3.107.53.103  
**Status Bot v3:** RUNNING (Docker, port 8082, healthy)  
**Dibuat oleh:** Z.ai Code (sesi 2026-06-27)  

---

## 📊 STATUS SAAT INI

| Metrik | Nilai |
|--------|-------|
| Bankroll | $25.00 |
| Cash | $9.09 ← **⬅️ 64% already deployed!** |
| Total Trades | 0 |
| Signals Emitted | 0 (semua strategy) |
| Markets | 300 total, 3 crypto Up/Down |
| CLOB WS | Connected, 1 token tracked |
| Container RAM | 71.7MB / 1GB |
| CPU | 13.28% |
| Uptime | ~11 menit |
| Health | OK |

**⚠️ RED FLAG #1:** Cash = $9.09 tapi 0 open positions dan 0 trades. **$15.91 hilang tanpa trade!** Iniwallet inconsistency — kemungkinan bug di debit/credit logic.

**⚠️ RED FLAG #2:** 0 signals dari 4 strategi aktif selama 11 menit. Market ada 300, tapi tidak ada satupun strategy yang menembakkan signal.

---

## 🏗️ ARSITEKTUR — APA YANG BAGUS

v3 adalah **upgrade signifikan** dari v2 di banyak aspek:

### ✅ Major Improvements vs v2

| Area | v2 | v3 | Verdict |
|------|----|----|---------|
| CLOB data | REST poll 3-5s lag | WebSocket real-time | ✅ 60x faster |
| Resolution | Fake (tebak dari end_date) | Real (`closed` + `resolvedBy`) | ✅ Bug fixed |
| Executor | `time.sleep()` blocking | `await asyncio.sleep()` | ✅ Non-blocking |
| Arbitrage | Single-leg (bukan arb) | Atomic pair YES+NO | ✅ Real arb |
| State | JSON ~30 writes/min | SQLite WAL async | ✅ Atomic & queryable |
| Risk | Global only | Per-strategy budget + circuit breaker | ✅ Much better |
| Sizer | Dead code | Properly integrated | ✅ Working |
| Types | Basic | Multi-leg (Leg, is_pair, TickUpdate, NewsEvent) | ✅ Extensible |
| Logs | Plain text | JSON structured (structlog) | ✅ Observable |
| Dashboard | Inline HTML | FastAPI + unified v2/v3 | ✅ Better |
| Scanner | No `no_bid`/`no_ask` | Parses `bestBidNo`/`bestAskNo` | ✅ More data |
| Event Bus | None | In-process pub/sub | ✅ Decoupled |
| Daemon | Basic restart | Exponential backoff + health check | ✅ More robust |

---

## 🔴 BUG KRITIS

### BUG-1: WALLET INCONSISTENCY — $15.91 HILANG TANPA TRADE

**Severity:** 🔴 CRITICAL

Cash = $9.09, bankroll = $25.00, tapi 0 open positions dan 0 trades. Artinya ada $15.91 yang "hilang" — tidak di cash, tidak di positions.

**Kemungkinan penyebab:**

1. **Stats cache staleness.** `_stats_cache` di-refresh setiap 2 detik oleh background task. Tapi cache mungkin membaca stale data dari SQLite setelah restart.

2. **`wallet.debit()` dipanggil tanpa matching `position_repo.open_position()`.** Kalau executor fill ditolak setelah debit, cash berkurang tapi position tidak terbuat.

3. **`_update_position_values()` mengupdate current_value tapi tidak mengupdate bankroll secara konsisten.** Stats loop menghitung `bankroll = cash + invested` tapi kalau ada race condition antara stats refresh dan position update, bisa inconsistent.

**Fix:** Tambah invariant check: `bankroll harus == cash + total_invested`. Kalau tidak, log error dan recalculate.

### BUG-2: CLOB WS HANYA TRACK 1 TOKEN — HARUSNYA 50-100

**Severity:** 🔴 CRITICAL

Dari log: `"CLOB WS[0] connected: 1 tokens subscribed"`. Padahal config `track_max_markets: 50`. 

**Kenapa?** Di `bot.py`:
```python
top_markets = sorted(self._markets, key=lambda m: m.volume_24h, reverse=True)[:track_max]
for m in top_markets:
    self.clob_feed.track(m.yes_token_id, m.condition_id, "YES")
    self.clob_feed.track(m.no_token_id, m.condition_id, "NO")
```

Tapi `clob_ws.py` `track()` memanggil `_spawn_connections()` yang membuat task baru untuk setiap batch. **Masalah:** `_spawn_connections()` cek `if len(self._tasks) >= n_conns: return` — jadi kalau sudah ada 1 task, dia tidak spawn baru meskipun token list sudah berubah.

Artinya: **Hanya token pertama yang di-track.** Semua strategy yang bergantung pada CLOB WS (momentum, atomic_arb, latency_arb) **tidak punya data harga** untuk hampir semua market.

**Fix:** `_spawn_connections()` harus recalculate batch dari full `_tracked_tokens` list, bukan dari snapshot saat pertama kali dipanggil. Atau: jangan batasi jumlah tasks, restart semua connections saat token list berubah.

### BUG-3: 0 CRYPTO UP/DOWN MARKETS (LOG: "0 crypto Up/Down")

**Severity:** 🟠 HIGH

v2 menunjukkan 3 crypto Up/Down, tapi v3 menunjukkan 0. Scanner pattern sama persis — kemungkinan:

1. **Gamma API timing.** v3 scan setiap 60 detik (vs v2 setiap 15 detik). Mungkin saat scan v3, market crypto belum muncul.
2. **Minimum volume filter berbeda.** v3 pakai `m.volume_24h >= self.min_volume` POST-parse (v2 skip ini). Kalau crypto markets punya volume < 500, mereka di-skip.

**Impact:** `latency_arb` strategy TIDAK BISA berfungsi karena memfilter `if not market.crypto_asset: return None`. Tanpa crypto markets, latency_arb = dead strategy.

### BUG-4: `_implied_prob_above()` TERLALU SEDERHANA

**Severity:** 🟠 HIGH (latency_arb strategy)

```python
def _implied_prob_above(self, current_price: float, threshold: float, asset: str) -> float:
    distance_pct = (current_price - threshold) / current_price
    if distance_pct > 0.05:
        return min(0.99, 0.85 + distance_pct)
    if abs(distance_pct) < 0.01:
        return 0.50
    if distance_pct < -0.05:
        return max(0.01, 0.15 + distance_pct)
    return 0.50 + distance_pct * 5
```

Ini model probabilitas yang **sangat naif** — tidak mempertimbangkan:
- **Waktu tersisa** ke market close (1 jam vs 24 jam = probabilitas sangat berbeda)
- **Volatilitas historis** asset (BTC volatil, prob distance 2% bisa ditembak dalam 1 jam)
- **Mean reversion** — harga yang sudah jauh dari threshold cenderung revert

**Contoh masalah:** BTC = $105,000, threshold = $100,000 (5% above). Model bilang prob = 0.90. Tapi kalau market close dalam 5 menit vs 24 jam, probabilitas seharusnya sangat berbeda.

**Fix:** Tambahkan time decay + volatility-based model. Sederhana:
```python
# Time-adjusted probability
hours_left = sec_to_close / 3600
time_decay = min(1.0, hours_left / 24.0)  # More time = more uncertain
# Volatility adjustment
daily_vol = 0.03  # ~3% daily vol for BTC
prob = norm.cdf(distance_pct / (daily_vol * sqrt(hours_left/24)))
```

### BUG-5: ATOMIC ARB MENGANDALKAN `best_ask` DARI CLOB WS YANG HAMPI KOSONG

**Severity:** 🟠 HIGH

```python
yes_ask = self._clob.get_best_ask(market.yes_token_id)
no_ask = self._clob.get_best_ask(market.no_token_id)
```

Karena CLOB WS hanya tracking 1 token (Bug #2), `get_best_ask()` return 0 untuk hampir semua market. Fallback ke `market.yes_price` (mid price, bukan ask), yang artinya **combined "ask" = yes_price + no_price ≈ 1.0**. Tidak ada arbitrage opportunity yang terdeteksi.

Bahkan kalau CLOB WS berfungsi, Polymarket markets biasanya sangat efisien (YES+NO ask ≈ $1.00-1.02 karena fees). `min_profit_bps: 100` (1%) **terlalu tinggi** untuk Polymarket — realistisnya 20-50 bps.

### BUG-6: RESOLUTION SNIPE TIDAK MEMILIKI EXIT MEKANISME

**Severity:** 🟡 MEDIUM

```python
def check_exit(self, pos_id, condition_id, current_price) -> tuple[bool, str]:
    # Hold to resolution — no TP/SL exit
    return False, ""
```

Strategy ini buy di 0.90-0.97 dan hold sampai resolution. Tapi kalau odds berubah (misalnya dari 0.95 turun ke 0.70 karena event tak terduga), posisi akan loss besar tanpa stop-loss. 

**Contoh:** Buy YES @ 0.95 → market berubah → YES turun ke 0.50 → loss -47% tanpa exit.

**Fix:** Tambahkan trailing stop atau SL jika odds turun di bawah threshold (misal -10% dari entry).

---

## 🟠 MASALAH ARSITEKTUR

### ARCH-1: `_spawn_connections()` TIDAK REBALANCE SAAT TOKEN LIST BERUBAH

**Severity:** 🟠 HIGH

CLOB WS `_spawn_connections()` hanya spawn connection BARU kalau jumlah tasks < jumlah yang dibutuhkan. Tapi:
1. Tidak mengirim subscribe message baru ke existing connection saat token list berubah
2. Tidak unsubscribe token yang sudah tidak di-track
3. Task lama tetap subscribe ke token batch pertama

**Fix:** Saat `track()` dipanggil, kirim subscribe message ke WS yang sudah ada, atau restart connections dengan token list terbaru.

### ARCH-2: STATS CACHE 2-SECOND POLL = DB QUERY SETIAP 2 DETIK

**Severity:** 🟡 MEDIUM

`_refresh_stats_loop()` melakukan 3+ DB queries setiap 2 detik:
- `position_repo.get_open_positions()`
- `trade_repo.get_recent_trades(limit=20)`
- `trade_repo.stats()`

Di t2.small dengan SQLite WAL, ini OK untuk sekarang. Tapi kalau data grows (500+ trades), query stats bisa jadi lambat.

**Fix:** Cache trade stats di memory, hanya refresh setiap 30 detik atau saat trade terjadi.

### ARCH-3: CLOB WS `sortedcontainers` DEPENDENCY TAMBAHAN

**Severity:** 🟢 LOW

`clob_ws.py` menggunakan `SortedDict` dari `sortedcontainers` untuk local orderbook. Ini menambah dependency tapi tidak terdaftar di `pyproject.toml` (kalau dilihat dari dependencies list). Perlu ditambahkan.

### ARCH-4: NO `no_bid`/`no_ask` FROM GAMMA API

**Severity:** 🟡 MEDIUM

Scanner meng-parse `bestBidNo` dan `bestAskNo`, tapi ini field yang **tidak selalu ada** di Gamma API response. Kalau tidak ada, `no_bid` dan `no_ask` = 0, yang membuat `atomic_arb` tidak bisa menghitung combined ask untuk NO side secara akurat.

### ARCH-5: EVENT BUS TIDAK DIGUNAKAN OLEH STRATEGI

**Severity:** 🟡 MEDIUM

Event bus dibangun dengan topics `market_scan`, `clob_tick`, `binance_tick`, dll. Tapi **tidak ada strategy yang subscribe ke event bus**. Semua strategy masih pakai pola pull (dipanggil dari `_try_strategies()` loop). Artinya:
- Latency arb TIDAK reaktif terhadap Binance tick events (harusnya subscribe ke `binance_tick`)
- Momentum TIDAK reaktif terhadap CLOB tick events (harusnya subscribe ke `clob_tick`)
- Event bus = infrastructure yang ada tapi tidak dipakai secara bermakna

**Ini mengurangi value prop utama v3:** "event-driven HFT bot". Saat ini v3 masih pull-based seperti v2, hanya dengan interval lebih cepat (1s vs 2s).

### ARCH-6: RESOLUTION CHECK HANYA SAAT SCAN (60 DETIK)

**Severity:** 🟡 MEDIUM

`is_closed` di Market model hanya di-update saat scanner refresh (setiap 60 detik). Kalau market resolve di antara scan, bot tidak tahu sampai scan berikutnya. Untuk resolution_snipe yang target-nya market yang close dalam < 24 jam, 60 detik delay bisa berarti terlambat masuk/exit.

**Fix:** Tambahkan periodic resolution check (setiap 10-15 detik) untuk markets yang seconds_to_close < 3600.

---

## 📊 PERBANDINGAN v2 vs v3

| Aspek | v2.1 | v3 | Winner |
|-------|------|-----|--------|
| RAM | 51MB | 72MB | v2 (lighter) |
| CPU | 3.5% | 13.3% | v2 (less load) |
| Signals | 0 (scalper rarely fires) | 0 (CLOB WS broken) | TIE (both 0) |
| Trades | 1 (lost -35%) | 0 | v3 (no loss yet) |
| CLOB data | REST, 15 markets, 5s poll | WS, 1 token, ~50ms | v3 potential (if fixed) |
| Resolution | Fake | Real | ✅ v3 |
| Risk | Global only | Per-strategy | ✅ v3 |
| State | JSON | SQLite | ✅ v3 |
| Arb | Single-leg (not real arb) | Atomic pair | ✅ v3 (when CLOB WS works) |
| Event-driven | No | Yes (but strategies don't use it) | v3 potential |
| Log format | Plain text | JSON structured | ✅ v3 |

**Verdict:** v3 punya **arsitektur lebih baik** di atas kertas, tapi saat ini **tidak menghasilkan signal apapun** karena bug CLOB WS. v2.1 juga tidak menghasilkan signal tapi setidaknya berjalan stabil dengan 51MB RAM.

---

## 📋 REKOMENDASI PRIORITAS

### 🔴 IMMEDIATE — Fix Sekarang

| # | Rekomendasi | Detail |
|---|-------------|--------|
| 1 | **Fix CLOB WS `_spawn_connections()`** | Rebalance token subscriptions saat track() dipanggil. Atau restart semua connections dengan updated token list |
| 2 | **Fix wallet inconsistency** | Tambah invariant check: bankroll == cash + invested. Recalculate setiap loop |
| 3 | **Investigate 0 crypto Up/Down** | Cek apakah min_volume filter terlalu ketat. Tambahkan logging untuk market yang di-skip |
| 4 | **Tambah `sortedcontainers` ke pyproject.toml** | Dependency dipakai tapi tidak terdaftar |

### 🟠 SHORT-TERM — Fix 1-3 Hari

| # | Rekomendasi | Detail |
|---|-------------|--------|
| 5 | **Improve `_implied_prob_above()`** | Tambah time decay + vol-based model. Tanpa ini, latency_arb akan generate banyak false signal |
| 6 | **Lower atomic_arb `min_profit_bps`** | 100 bps (1%) terlalu tinggi. Coba 30-50 bps. Polymarket markets sangat efisien |
| 7 | **Tambah stop-loss ke resolution_snipe** | Min -10% dari entry price. Kalau odds turun drastis, cut loss |
| 8 | **Connect strategies ke event bus** | Latency_arb subscribe ke `binance_tick`, momentum subscribe ke `clob_tick` |
| 9 | **Tambah periodic resolution check** | Setiap 10-15 detik, re-check markets yang < 1 jam dari close |
| 10 | **Fix CLOB WS message format** | Cek apakah Polymarket WS format sesuai assumption. `event_type`, `asset_id`, dll mungkin beda |

### 🟡 MEDIUM-TERM — Fix 1 Minggu

| # | Rekomendasi | Detail |
|---|-------------|--------|
| 11 | **Implement LLM agent** | News-driven signal = leading edge, bukan follow momentum |
| 12 | **Implement Telegram alerts** | Sekarang stub, perlu real notifikasi untuk monitoring |
| 13 | **Tambah unit tests** | Risk manager, executor, atomic_arb calculation |
| 14 | **Cache trade stats in memory** | Kurangi DB queries dari setiap 2s ke setiap 30s |
| 15 | **Tambah Prometheus metrics** | Endpoint `/metrics` sudah ada tapi kosong |

---

## 🎯 STRATEGI YANG PALING MENJANJIKAN DI v3

### 1. Resolution Snipe (paling realistis untuk profit)
- **Edge jelas:** Buy di 0.93, collect $1 saat resolve = +7.5%
- **Syarat:** Market harus benar-benar near-certain (bukan hanya harga tinggi)
- **Risk:** Event tak terduga bisa reverse odds
- **Fix yang diperlukan:** Tambah stop-loss, verify near-certainty

### 2. Atomic Arb (risk-free tapi jarang)
- **Edge jelas:** YES ask + NO ask < $1 = guaranteed profit
- **Realita:** Sangat jarang di Polymarket (< 1% dari markets)
- **Fix:** Lower threshold ke 30-50 bps, increase scan frequency

### 3. Latency Arb (potential terbesar tapi paling sulit)
- **Edge:** 200-500ms lag antara Binance price move dan PM odds adjust
- **Syarat:** CLOB WS harus working (saat ini broken), model prob harus akurat
- **Risk:** "Latency" advantage mungkin tidak exist di paper trading (simulated fill)
- **Fix:** Fix CLOB WS, improve prob model

### 4. Momentum (paling berisiko)
- **Sama seperti v2 universal** — entering markets based on odds movement alone
- **Sudah terbukti loss di v2** (-35% di trade pertama)
- **Fix:** Tambah market category filter, increase min confidence

---

## 📝 KESIMPULAN

v3 adalah **rewrite yang secara arsitektur jauh lebih baik** dari v2: WebSocket, event bus, real resolution, atomic arb, per-strategy risk, SQLite, JSON logs. Tapi ada **3 bug kritis** yang membuat bot saat ini **tidak berfungsi**:

1. **CLOB WS hanya track 1 token** → semua strategy yang bergantung CLOB = blind
2. **Wallet inconsistency** → $15.91 "hilang" tanpa trade
3. **0 crypto markets detected** → latency_arb = dead

Dan ada **1 masalah fundamental**: Event bus dibangun tapi tidak dipakai oleh strategies. v3 masih pull-based seperti v2. Tanpa event-driven strategy execution, v3 tidak lebih "HFT" dari v2 — hanya lebih cepat loop interval (1s vs 2s).

**Rekomendasi:** Fix 3 bug kritis dulu, baru pertimbangkan switch dari v2 ke v3. Jangan stop v2 sampai v3 proven profitable.

**Risk Level Saat Ini:** 🔴 TINGGI — v3 punya arsitektur bagus tapi 0 signal 0 trade, plus wallet inconsistency. Perlu debugging sebelum bisa diandalkan.
