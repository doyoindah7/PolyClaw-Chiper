# PolyClaw-Cipher v3.2.0 — AI Review (Claude)

**Reviewer:** Claude (Anthropic)
**Tanggal review:** 2026-06-27
**Versi di-review:** v3.2.0 (commit terbaru, `main`)
**Metode:** Clone repo penuh + baca source code langsung (`bot.py`, `scanner.py`, `latency_arb.py`, `atomic_arb.py`, `momentum.py`, `clob_ws.py`, `event_bus.py`, `risk/manager.py`, `risk/sizer.py`, `execution/paper.py`, `core/types.py`, `config/default.yaml`) — bukan cuma baca README/docs. Sudah dicek silang dengan `V3_ANALYSIS.md` dan `V31_ANALYSIS.md` biar nggak redundant dengan temuan AI review sebelumnya.

> Catatan jujur dari awal: kritik di bawah tajam sesuai permintaan ("jangan validasi, kalau bullshit bilang bullshit"). Tapi arsitektur dasarnya (async, event-driven skeleton, real resolution detection, wallet invariant check) itu beneran solid untuk level proof-of-concept — lebih baik dari kebanyakan "AI-generated trading bot" yang saya lihat. Masalahnya bukan di fondasi, tapi di beberapa logic strategi yang nggak nyambung dengan data yang sebenarnya tersedia, dan di ekspektasi return yang nggak realistis secara matematis.

---

## Executive Summary

| Area | Status |
|---|---|
| Target $25 → $150-200/minggu | ❌ Tidak realistis secara matematis (lihat §1 + Update) |
| Strategy mix (5 strategi) | ⚠️ 2 dari 4 aktif kemungkinan tanpa edge riil saat ini |
| Risk management | ⚠️ Ada bug konkret + circuit breaker terlalu lemah |
| Pending issues | 🔍 Root cause baru ditemukan untuk bug "0 crypto Up/Down" |
| LLM agent (news_llm) | ⏸️ Belum worth dibangun di skala modal sekarang |
| Live trading readiness | ❌ Leg-risk atomic_arb belum teratasi — jangan live dulu |
| Arsitektur | ✅ Fondasi bagus, event bus premature tapi correctly designed |
| Code quality | ✅ Rapi secara umum, beberapa dead code + config yang saling override |

---

## 1. Target "$25 → $150-200/minggu" — realistic atau fantasy?

**Fantasy secara matematis**, bukan soal pesimis terhadap strategi kamu.

- Target itu = **+500-700% bankroll per minggu**. Untuk konteks: fund quant top dunia senang kalau dapat **20-30% per TAHUN**. Tidak ada strategi — apalagi yang belum live-tested — yang bertahan dengan compounding rate segini. Kalau benar-benar bisa, $25 jadi >$1,000,000 dalam ~10 minggu.
- Wallet "profit ribuan persen" yang sering dishare di X itu **survivorship bias murni** — yang nge-post adalah yang kebetulan menang di sampel kecil ($25-100), bukan representative sample dari semua orang yang coba. Satu trade beruntung di modal $25 bisa keliatan "+400%" padahal itu cuma noise statistik, bukan edge yang repeatable.
- Modal $25 itu sendiri masalah struktural, bukan cuma soal target ketinggian: dengan `min_position_usd: 2.00` dan cap 25%, ukuran trade kamu cuma **$2-6**. Slippage simulasi kamu sendiri (25 bps) sudah memakan **>50% dari edge atomic_arb** (40 bps) — di live, real slippage + leg risk kemungkinan lebih besar dari itu.

### Update setelah diskusi — "turunin ke $100-150/minggu, naikkan lagi setelah growth"

Ide menurunkan **angka dollar** target itu langkahnya benar, tapi perlu dikoreksi: **$100-150/minggu dari basis $25 itu masih +400-600%/minggu — secara matematis sama tidak realistisnya** dengan target awal. Yang kelihatan beda cuma framing (dollar kecil vs persentase besar), bukan substansinya.

**Target yang realistis untuk fase sekarang (modal $25-75, strategi belum proven):**

| Basis bankroll | Target realistis/minggu | Catatan |
|---|---|---|
| $25-75 (sekarang) | **+15-40% bankroll** ($25 → $29-35) | Sudah aggressive untuk strategi belum proven |
| $100-150/minggu | Baru masuk akal kalau basis sudah **$300-500+** | Bukan dari $25 |

Hitungan konkret kenapa: atomic_arb (strategi dengan edge paling solid secara teori) — edge 40bps, slippage simulasi 25bps → net realized ~15bps per cycle. Di notional $6 (25% dari $25), itu **$0.009 per arb**. Mau berapa kali pun fire dalam seminggu, kontribusinya ke bankroll receh secara matematis. Jadi "naikkan target setelah asset growth" itu **arahnya benar** — yang harus naik itu basis dollarnya, bukan rate persentasenya.

### Soal "compounding tidak bertahan lama, ganti metode jadi lebih stabil"

Perlu dipisahkan dua hal yang sering ketuker:

1. **Position sizing method** (compounding %, fixed $, tiered, Kelly) — ini cuma ngatur **variance / risk of ruin**, bukan menentukan apakah strategi profitable.
2. **Edge** (apakah strategi menang lebih sering dari yang dibayar) — ini yang menentukan apakah ada uang yang bisa di-compound sama sekali.

Ganti metode sizing **tidak bisa** mengubah strategi tanpa edge jadi profitable. Yang bisa diubah cuma seberapa cepat kamu sadar (atau seberapa cepat blow up) kalau edge-nya nggak ada. Karena ini paper trading, eksperimen sizing aggressive itu fine — tapi jangan sampai $ growth yang kelihatan bagus di paper bikin percaya edge-nya nyata, padahal cuma 2-3 trade beruntung di sample kecil.

**Saran konkret — tiered risk schedule** (mengganti `max_pct_per_trade` statis di `risk/sizer.py` jadi fungsi dari bankroll, bukan config tetap):

| Bankroll | Max % per trade | Rationale |
|---|---|---|
| $25-75 | 20-25% (sekarang) | Modal kecil, eksperimen, OK aggressive |
| $75-200 | 12-15% | Mulai ada sesuatu yang worth dijaga |
| $200-500 | 6-10% | Statistical signal sudah lebih jelas, mulai konservatif |
| $500+ | 3-5% | Mendekati skala serius |

Ini lebih jujur secara matematis daripada compounding murni (risk $ per trade naik terus tanpa batas relatif) maupun fixed-$ murni (nggak responsive ke growth).

**Yang lebih penting dari target angka: ukuran sample.** Di $25 dengan 4-10 trade/minggu, satu trade menang/kalah bisa swing 20-30% bankroll — itu noise, bukan edge. Milestone yang lebih jujur: kumpulkan **minimal 30-50 trade closed per strategi** sebelum percaya angka win rate/PnL. Sebelum itu tercapai, treat semua angka $ sebagai belum bermakna secara statistik — mau naik mau turun.

---

## 2. Strategy mix — ada yang kurang/berlebih? Tambah strategi lain?

- **atomic_arb** — satu-satunya dengan edge yang secara teori paling solid (risk-free arb), tapi implementasinya **bukan atomic** dalam arti eksekusi nyata (detail di §6).
- **latency_arb** — edge thesis (Binance leads PM odds 200-500ms) plausible, tapi parsing-nya rusak secara struktural (detail di §4).
- **resolution_snipe** — konsepnya oke (lazy holders di near-certain market), tapi ini sebenarnya **selling tail risk untuk premium kecil**: 7% upside vs -10% SL berarti butuh win rate jauh di atas 90% biar profitable jangka panjang. Kalau kena SL 3x beruntun (`max_consecutive_losses: 3`), itu sinyal model confidence-nya salah — bukan cuma bad luck.
- **momentum** — paling spekulatif dari 4 strategi aktif. Trend-following pada probability mendekati resolusi itu riset campur (ada bukti momentum *dan* mean-reversion di prediction market, depending on time-to-resolution). Tanpa backtest, ini murni hipotesis.
- Kandidat tambahan yang kamu sebut (mean reversion, orderbook imbalance, cross-venue Kalshi/PredictIt): **jangan ditambah sekarang.** Checklist README kamu sendiri sudah benar — 4 strategi yang ada harus lulus profitable ≥14 hari paper dulu. Nambah strategi sekarang cuma nambah permukaan bug, bukan nambah edge.
  - Cross-venue Kalshi/PredictIt secara teknis menarik, tapi liquidity-nya jauh lebih kecil dari Polymarket untuk market yang overlap — realistically low priority.

---

## 3. Risk management — terlalu aggressive atau akan blow up saat live?

Ditemukan **bug konkret**, bukan cuma opini soal "terlalu aggressive":

### (a) Konflik 3 lapis "max % per trade" yang saling override secara tidak jelas
Ada tiga setting berbeda:
- `strategies.atomic_arb.max_position_pct: 0.40` (dead code — sizer selalu dipakai, jadi value ini nggak pernah benar-benar jalan)
- `risk.per_strategy.atomic_arb.max_capital_pct: 0.50` (dipassing sebagai `strategy_max_pct` ke sizer)
- `risk.sizer.max_pct_per_trade: 0.25` (global)

Karena `CompoundingSizer.size()` melakukan `min(notional, bankroll*max_pct_per_trade)` **sebelum** `min(notional, bankroll*strategy_max_pct)` (`risk/sizer.py` baris 58-59), yang menang adalah **angka terkecil**. Cap efektif untuk hampir semua strategi sebenarnya **25%** (global), bukan 40-60% yang dikonfig di `risk.per_strategy`, dan bukan juga angka yang ditulis di README. Tiga sumber kebenaran, tiga angka berbeda — perlu disederhanakan jadi satu source of truth.

### (b) Circuit breaker terlalu lemah
Di `risk/manager.py` baris 132-135: begitu strategi yang di-disable menang **satu kali**, langsung di-re-enable. Plus `_rotate_session()` (tiap 4 jam) memanggil `_strategy_disabled.clear()` — disabled state direset paksa tiap 4 jam apa pun alasannya. Kalau atomic_arb kena 3x loss beruntun (yang menurut komentar kamu sendiri "should never lose — something wrong"), breaker trip, tapi begitu menang sekali atau 4 jam berlalu, strategi yang sedang bermasalah secara fundamental langsung jalan lagi tanpa investigasi apa pun.

### (c) Bug: `record_trade(strategy, 0)` ikut mencemari rate-limit counter
`bot.py` baris 275: tiap entry, `risk.record_trade(strat.name, 0)` dipanggil — menambah timestamp ke `_trade_times` (dipakai rate limit/jam). Saat posisi ditutup, `record_trade()` dipanggil **lagi**. Jadi satu trade riil = 2 entry di rate limiter. `max_trades_per_hour_global: 60` efektifnya jadi ~30 trade riil/jam.

### (d) Daily DD 50%
Oke untuk paper (tujuannya belajar), tapi jangan dibawa ke live tanpa diturunkan jauh — di live dengan $25, 50% DD = $12.50 hilang dalam satu hari sebelum bot berhenti sendiri. Turunkan ke 10-15% dulu sampai ada track record nyata.

---

## 4. Pending issues — root cause analysis (belum disinggung di review AI sebelumnya)

### "0 crypto Up/Down markets detected" — bukan cuma "scanner timing issue"
`latency_arb.py` baris 22-25: `_extract_threshold()` cuma match regex `"above $X"` / `"over $X"` di question text. Tapi `scanner.py`'s `CRYPTO_PATTERNS` pattern utama (yang dipakai untuk men-tag `is_crypto_up_down`) match pertanyaan model **"Bitcoin Up or Down — [tanggal]"** — market jenis ini **tidak punya angka dollar threshold sama sekali** (resolusi berdasarkan harga naik/turun relatif ke harga open, bukan vs angka absolut).

Konsekuensinya: walaupun scanner berhasil men-tag market itu sebagai crypto Up/Down, `latency_arb._extract_threshold()` akan **selalu** return `(None, None)` untuk market jenis itu — nggak ada teks "above $X" di pertanyaannya. Dua bagian kode ini didesain untuk dua jenis market crypto yang berbeda (threshold-style vs directional Up/Down) tapi disambungkan seolah sama. Ini kemungkinan kontributor utama kenapa latency_arb selalu 0 sinyal, terlepas dari bug volume-filter/scan-timing yang sudah dicurigai di `V3_ANALYSIS.md`.

→ **Saran:** log 5-10 contoh raw `question` text dari market yang `is_crypto_up_down=True`, lalu putuskan: redesign `_implied_prob_above` untuk market directional (pakai harga open period, bukan threshold absolut), atau ganti target latency_arb ke market threshold-style yang memang ada di Polymarket.

### `sync_connections()` disruptive tiap 60s — fix sudah teridentifikasi di V31_ANALYSIS, tapi ada bagian yang hilang
`untrack()` **ada** di kode (`clob_ws.py` baris 180) tapi **nol call site** di seluruh codebase. Di `bot.py` loop, top-50 market by volume di-`track()` tiap scan, tapi market yang keluar dari top-50 nggak pernah di-`untrack()`. Jadi `_tracked_tokens` cuma membesar terus, dan check `len(token_list) == self._last_synced_token_count` hampir pasti berubah tiap scan (ranking volume bergeser → market baru masuk top-50 → total count naik) → memicu **full reconnect SEMUA koneksi WS**, bukan cuma batch yang berubah.

→ **Fix konkret:** (1) panggil `untrack()` untuk market yang keluar dari top-50, (2) bandingkan **set token ID**, bukan cuma `len()`, (3) reconnect cuma batch/connection yang affected, bukan semuanya.

### Event bus benar-benar dead code untuk trading logic
Cek `event_bus.py`: nol `.subscribe()` call di seluruh `src/`. `clob_ws.py` dan `binance_ws.py` cuma `.publish()` ke topic yang nol subscriber. Tiap tick CLOB/Binance, ada `TickUpdate` object dialokasi + queue put — murni **overhead**, tanpa benefit sama sekali saat ini.

### Prioritas fix yang disarankan
1. **Event bus wiring** untuk minimal 1 strategi (latency_arb, karena paling latency-sensitive) — fix arsitektural paling murah, paling berdampak ke target <50ms.
2. **latency_arb threshold-vs-directional mismatch** — tanpa ini, strategi nggak akan pernah fire pada market Up/Down murni.
3. **`sync_connections()` token-diff + untrack** — sudah well-understood, tinggal implementasi.

---

## 5. LLM agent implementation (news_llm)

- **z-ai-web-dev-sdk vs OpenAI/Anthropic:** untuk signal generation murni (bukan agentic tool-use), vendor kurang penting dibanding **latency**. Benchmark p50/p95 latency dulu sebelum komit — jangan asumsi GLM-4.5 lewat SDK pihak ketiga akan secepat API langsung dengan streaming.
- **Nitter instances** (`nitter.net`, `nitter.privacydev.net`) di config: **sangat tidak reliable** — instance publik Nitter sering down/rate-limited karena dependensi scraping Twitter yang terus diblokir. Untuk strategi yang depend on <30s latency, sumber unreliable lebih berbahaya daripada tidak ada sumber sama sekali (false confidence). RSS (CoinDesk/TheBlock) lebih stabil tapi lebih lambat — kontradiksi dengan tujuan "trade sebelum odds adjust".
- **Twitter API berbayar** (~$200/bulan Basic tier) **tidak proporsional** dengan modal $25. Saran: skip news_llm sepenuhnya sampai bankroll jauh lebih besar — ini strategi paling mahal untuk dibangun dengan edge paling tidak terbukti dari 5 strategi yang ada.
- **Latency <30s:** realistic untuk LLM call sendiri (1-3s dengan model cepat), tapi bottleneck sebenarnya ada di **news source discovery + dedup**, bukan LLM — itu yang harus dioptimasi duluan kalau strategi ini mau dilanjutkan.

---

## 6. Live trading (v4)

- `py-clob-client` resmi: benar, jangan reinvent.
- **Paling kritis sebelum live: atomic_arb TIDAK benar-benar atomic.** Di `execution/paper.py`, dua leg di-fill secara simulated dan instant — kalau salah satu gagal, seluruh pair dibatalkan bersih (bagus untuk paper). Tapi di live, Polymarket CLOB **tidak punya mekanisme atomic multi-leg order** bawaan — 2 order dikirim terpisah secara sequential. Antara leg 1 fill dan leg 2 fill, ada window waktu nyata di mana harga bisa bergerak. Dengan edge cuma 40 bps, **leg risk** ini sendiri lebih besar dari edge-nya — leg kedua slip 1-2 cent di token $0.50 = 200-400 bps, lebih besar dari seluruh profit yang dicari.
- Sebelum live, **harus** ditest dengan slippage model yang jauh lebih realistic daripada `slippage_bps: 25` sekarang (yang sudah memakan >50% dari edge 40bps secara teoritis).
- **Wallet security:** private key di env var minimal; idealnya pakai signer terpisah (hardware wallet / KMS) untuk live — jangan plaintext `.env` di VPS yang juga expose dashboard publik di port 8082.
- **Tambahan untuk checklist README** yang sudah bagus: test khusus leg-risk atomic_arb dengan order book replay nyata, bukan simulasi fill probability acak.

---

## 7. Arsitektur — over/under-engineering?

- **Event bus: premature, tapi correctly designed.** Bounded queue + backpressure drop-oldest itu desain yang benar secara teknis, cuma belum dikoneksikan ke konsumen mana pun. Worth diselesaikan (§4), bukan dibuang.
- **SQLite WAL: cukup untuk skala ini.** Single-process, single-writer, low write volume (~30 writes/min). PostgreSQL nggak kasih benefit nyata di skala $25 / t2.small VPS — migrasi sekarang itu over-engineering yang salah arah.
- **Yang under-engineered:** klasifikasi kategori market pakai regex (`CATEGORY_PATTERNS`, `CRYPTO_PATTERNS`) — fragile karena harus exact-match phrasing Polymarket yang bisa berubah kapan saja tanpa warning. Kalau Gamma API expose field tag/category asli (`tags`, `series`, dsb — perlu dicek di response API), itu jauh lebih robust daripada regex tebak-tebakan dari `question` text.

---

## 8. Code quality / code smell

1. **Dead config** — `strategies.*.max_position_pct` di yaml nggak pernah benar-benar dipakai karena sizer selalu di-pass. Bersihkan atau dokumentasikan jelas sebagai fallback-only.
2. **`record_trade(strategy, 0)` ganda** — mencemari rate-limit counter (§3c).
3. **`untrack()` didefinisikan tapi nol call site** — dead code yang justru jadi root cause bug aktif (§4).
4. **Fill probability simulation** di `paper.py` (`_simulate_fill`) pakai formula heuristik dengan magic number, tidak terhubung ke depth order book nyata dari CLOB WS yang sebenarnya sudah tersedia. Paper trading hasilnya bisa jauh dari realita karena fill model arbitrary, bukan derived dari liquidity aktual.
5. **`_implied_prob_above()`** di latency_arb pakai linear interpolation dengan breakpoint manual — sudah masuk roadmap sendiri ("time decay + vol model"), setuju itu prioritas kalau strategi ini dipertahankan.
6. **Yang sudah bagus:** type hints konsisten, async dipakai benar (nggak ada blocking call tersisa), error handling per-strategy di-isolasi (`try/except` di `_try_strategies` biar 1 strategi error nggak crash semua), wallet invariant check jalan tiap 3s, real resolution detection via `closed`+`resolvedBy` — semua ini perbaikan nyata dari v2, bukan cosmetic.

---

## Kesimpulan & Prioritas Aksi

**Status hari ini:** fondasi arsitektur layak dipertahankan, tapi 2 dari 4 strategi aktif (latency_arb karena parsing mismatch, momentum karena edge belum terbukti) kemungkinan tanpa edge riil saat ini. Risk config punya konflik 3-lapis yang bikin angka di kepala kamu beda dari yang benar-benar berjalan. Target $150-200/minggu dari $25 — dengan nama berapa pun dibungkus — tidak akan survive kontak dengan slippage + leg risk nyata di live trading.

**Urutan kerja yang disarankan:**

1. Fix `latency_arb` threshold-vs-directional mismatch (§4) — tanpa ini strategi mati permanen.
2. Sederhanakan risk config jadi 1 source of truth untuk "max % per trade" (§3a).
3. Wire event bus ke minimal 1 strategi (§4) — fix termurah dengan dampak terbesar ke latency target.
4. Fix `sync_connections()` token-diff + panggil `untrack()` (§4).
5. Kumpulkan 30-50 trade closed per strategi sebelum mempercayai angka win rate/PnL apa pun.
6. Baru setelah itu: revisit target growth dengan basis dollar yang sudah lebih besar dari $25, pakai tiered risk schedule (§1), bukan compounding-rate tetap.
7. Live trading (v4) ditunda sampai leg-risk atomic_arb teratasi dan checklist README terpenuhi — termasuk item baru: stress-test leg-risk dengan order book replay nyata.