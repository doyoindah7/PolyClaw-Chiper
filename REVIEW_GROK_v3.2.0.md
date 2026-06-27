**PolyClaw-Chiper v3.2.0 — Review Lengkap & Rekomendasi Realistis**

### 1. Target Realistis ($25 → $X/week)

Untuk eksperimen paper trading dengan modal kecil ($25), **target realistis awal adalah $80–120/week** (3–5x compounded) pada fase stabil setelah bug fixes. $100–150/week bisa dicapai secara sporadis dengan edge bagus + compounding agresif, tapi **$150–200/week konsisten** masih terlalu optimis dan berisiko fantasy jangka menengah.

Alasan: Polymarket efisien, edge latency/arb tipis, resolution snipe punya tail risk. Compounding bekerja sangat baik di awal (modal kecil), tapi volatilitas tinggi membuatnya tidak sustainable tanpa drawdown besar. Karena ini eksperimen (bukan hedge fund), agresif dengan risk menengah-besar di paper trading **sangat masuk akal**. 

**Rekomendasi adaptif**:
- Mulai target $100–120/week.
- Setelah bankroll >$100–150, naikkan target bertahap sambil menurunkan risk ratio.
- Compounding tetap utama, tapi hybrid: setelah modal naik signifikan, shift ke **bet sizing lebih besar tapi risk % lebih rendah** (misal max 15–20% per trade) untuk stabilitas. Ini memungkinkan fast growth tanpa blow-up mudah.

### 2. Strategy Mix

Mix 5 strategi (4 aktif) sudah cukup baik untuk diversifikasi edge:
- **Kuat**: Atomic arb (low risk anchor), resolution snipe (carry edge), latency arb (HFT), momentum (short-term).
- **Weakness**: Latency_arb masih butuh model prob lebih baik; News_llm stub belum aktif (potensi leading edge terbesar).

**Saran tambahan**:
- Pertahankan 4–5 strategi.
- Tambah mean reversion pada extreme mispricing (v4).
- Hindari over-exposure: correlation check antar strategi.
- Sizing saat ini (25–40%) terlalu tinggi — turunkan ke 15–25% max per trade, global exposure cap 70%.

### 3. Risk Management

Saat ini terlalu agresif (50% daily DD, 8 consec losses, 60 trades/jam). Untuk paper experiment dengan risk menengah-besar, **turunkan max_daily_drawdown ke 25–30%** dan per-strategy budget lebih ketat. Wallet invariant + cash buffer sudah bagus.

Setelah modal growth, dynamically adjust risk (misal risk % turun seiring bankroll naik) untuk kestabilan sambil tetap fast-growing.

### 4. Pending Issues & Prioritas

**Immediate Fixes (hari ini–besok)**:
1. CLOB WS `_spawn_connections()` rebalancing — blocker utama hampir semua strategi.
2. Wallet inconsistency (cash hilang).
3. 0 crypto Up/Down markets (scanner + filter).

**High Priority Selanjutnya**:
- Hubungkan strategi ke event bus (buat benar-benar reactive/HFT).
- Improve implied probability model + lower atomic arb threshold.
- Periodic resolution check.

Setelah itu baru LLM news agent.

### 5. LLM Agent

Gunakan **z-ai-web-dev-sdk** sesuai handoff. News source Nitter + RSS + targeted search cukup. Latency target <45–60 detik lebih realistis daripada <30s. Ini akan jadi pembeda terbesar untuk leading signals.

### 6. Switch ke Live Trading (v4)

- Pakai **py-clob-client official**.
- Paper trading minimal 2–4 minggu stabil dengan signal & trade real volume.
- Security: env rahasia, minimal privilege, dedicated VPS.
- Test ketat: slippage, disconnect, resolution, invariant.

### 7. Arsitektur

**Kelebihan**: Async, event bus, SQLite WAL, FastAPI, Docker, separation of concerns — jauh lebih baik dari v2.  
**Kekurangan**: Event bus underutilized, CLOB subscription rapuh, masih semi-pull. SQLite cukup untuk sekarang. Refactor event-driven strategies adalah next big win. Tidak over-engineered secara berlebihan.

### 8. Code Quality

Struktur modular bagus, types & logging baik. Code smells: race condition potensial di wallet/stats, dependency missing, magic numbers, subscription logic lemah. Secara keseluruhan clean untuk stage ini.

### Kesimpulan & Roadmap Eksperimen

v3.2.0 adalah fondasi solid dengan banyak improvement dari v2, tapi saat ini belum optimal karena bug CLOB & wallet. Fokus dulu bikin bot **bisa generate signal dan trade konsisten di paper** dalam 3–7 hari.

**Strategi Fast Growth Maksimal tapi Adaptif**:
- Pertahankan compounding agresif di fase modal kecil (<$100–200).
- Setelah growth, turunkan risk % per trade + naikkan bet absolute size.
- Monitor mingguan: jika drawdown sering, pivot ke hybrid (lebih stabil tapi tetap agresif).
- Target awal $100–120/week realistis untuk eksperimen ini. Naikkan bertahap setelah proven.

Bot ini punya potensi tinggi untuk eksperimen agresif. Fix critical bugs, aktifkan event-driven & LLM, lalu iterate cepat. Kalau semua berjalan, fast growth semaksimal mungkin sambil mengurangi risiko blow-up seiring modal membesar sangat masuk akal. 

Siap bantu implementasi fix prioritas mana dulu. Sukses!