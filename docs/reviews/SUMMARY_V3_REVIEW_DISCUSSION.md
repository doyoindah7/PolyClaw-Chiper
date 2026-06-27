# 📋 V3.2.0 Multi-AI Review — Summary & Final Decisions

**Tanggal:** 2026-06-27
**Bot version reviewed:** v3.2.0
**Reviewers:** Claude (Anthropic), Lisa/Qwen, Grok
**Method:** Independent reviews → cross-discussion → consensus

---

## 🎯 Context

Setelah v3.2.0 deploy, bot profit +$10.73 (+42.9%) dalam ~1 jam dari $25 modal.
Tapi audit data menunjukkan: **100% profit dari 1 strategy (momentum) di 1 market
("Spread: Belgium (-2.5)")** — 20 trades, semua sports spread.

Ini trigger request review ke 3 AI berbeda untuk validate:
1. Target $25 → $150-200/week realistic atau fantasy?
2. Strategy mix optimal?
3. Risk management too aggressive?
4. Code bugs?
5. Roadmap LLM agent + live trading?

---

## 👥 Reviewer Profiles

| Reviewer | Style | Target view | Key strength |
|---|---|---|---|
| **Claude** (Anthropic) | Konservatif, code-level tajam | +15-40%/week realistic | Nemuin 3 bug konkret di code yang lain miss |
| **Lisa/Qwen** | Optimis, strategic framework | $100-150/week Phase 1 | 3-phase approach + diversification logic |
| **Grok** | Middle-ground, balanced | $80-120/week stable | Hybrid risk schedule + exposure caps |

---

## 🔍 Initial Reviews — Key Points

### Claude (Konservatif)
- **Target $25→$150-200/week = fantasy matematis** (bahkan $100-150 masih +400-600%/week)
- **3 bug konkret ditemukan:**
  1. 3-layer config conflict (`strategies.*.max_position_pct` dead + `risk.per_strategy.*.max_capital_pct` + `risk.sizer.max_pct_per_trade` saling override, cap efektif 25% bukan 40-60%)
  2. `record_trade(strategy, 0)` double-count (entry + close both call, rate limit polluted, 60 trades/hr = 30 real)
  3. `untrack()` dead code (0 call sites, root cause sync_connections() disruptive)
- **Root cause "0 crypto Up/Down":** `_extract_threshold()` cuma match "above $X", tapi scanner match "Up or Down — [date]" — 2 market type beda disambung
- **Sample size:** 30-50 trade per strategi sebelum percaya angka win rate
- **atomic_arb live risk:** Polymarket no native multi-leg, leg risk > edge (40bps)
- **LLM agent:** Skip sampai bankroll besar
- **Verdict:** Fondasi arsitektur solid, tapi 2 dari 4 strategi (latency_arb, momentum) tanpa edge riil

### Lisa/Qwen (Optimis)
- **Target FANTASY di awal, tapi ADJUSTABLE dengan 3-phase approach:**
  - Phase 1 (Week 1-3): $25 → $100-150 (300-500%), resolution_snipe 70%
  - Phase 2 (Week 4-6): $150 → $400-500 (200-250%), momentum + resolution 50/50
  - Phase 3 (Week 7-10): $500 → $1000-1500 (100-200%), diversified
- **Strategy shift:** Phase 1 disable momentum + atomic_arb + latency_arb, fokus resolution_snipe
- **Risk per trade:** 15-20% Phase 1, turun sesuai growth
- **LLM agent:** GPT-4o-mini + Nitter + RSS + CryptoPanic API (free tier)
- **Action items:** Fix scanner, write tests, backtest, adjust target, test live dengan $50
- **Code smells:** Magic numbers, inconsistent error handling, God class, no correlation ID

### Grok (Middle)
- **Target $80-120/week realistic, $150-200 sporadic**
- **Strategy mix:** Pertahankan 4-5 strategi, lower sizing ke 15-25%
- **Risk:** Daily DD 25-30%, global exposure cap 70-75%
- **Immediate fixes priority:** (1) CLOB WS rebalancing, (2) wallet inconsistency, (3) 0 crypto markets
- **LLM:** z-ai-web-dev-sdk, latency <45-60s (lebih konservatif dari <30s)
- **Arsitektur:** Tidak over-engineered, SQLite cukup, refactor event-driven = next big win

---

## 💬 Discussion — Conflicts Identified

Setelah initial reviews, aku forward pertanyaan lanjutan ke masing-masing reviewer. 5 konflik utama muncul:

### Konflik 1: Cash Buffer (10% vs 25-30%)
- **Lisa:** Keep 10% (paper phase, aggressive = learn faster)
- **Grok:** 25-30% (10% terlalu tipis kalau 5-6 positions agresif)
- **Claude:** Tidak spesifik, tapi emphasize tiered risk schedule

### Konflik 2: Point Spread vs O/U Goals
- **Claude:** Split `sports_derivative` jadi `sports_total` (O/U, predictable Poisson) vs `sports_spread` (random, 1 gol = flip). Exclude spread dari momentum.
- **Lisa:** Awalnya bilang sports_derivative predictable (Poisson) — tapi ini generalize, point spread beda
- **Grok:** Tidak address distinction ini

### Konflik 3: Resolution Snipe — Add Sports atau Relax Price/Time?
- **Lisa:** Add politics + sports (dengan exclude_sports_if_volatile flag)
- **Claude:** JANGAN add sports (tail risk -93%), relax price 0.88-0.97 + time 72h saja, keep category filter
- **Grok:** "Relax filter" tapi vague

### Konflik 4: Sample Size Metric
- **Claude:** 20 trade di 1 market = 1 sample (clustered sampling), bukan 20 independent. Track unique markets, threshold 30-50 unique markets.
- **Lisa:** Tidak address statistical independence
- **Grok:** Tidak address

### Konflik 5: My Factual Error (auto-untrack)
- **Aku salah claim:** Bilang "clob_ws.py line 154 sudah ada auto-untrack on 404"
- **Claude caught:** Itu tidak ada di v3 (WebSocket, no HTTP status). Aku confuse dengan v2 (REST polling).
- **Fix:** Implementasi explicit untrack() dari nol

---

## ✅ Final Consensus (3 AI Agree)

Setelah diskusi lanjutan, semua konflik resolved:

| # | Issue | Final Decision | Origin |
|---|---|---|---|
| 1 | Auto-untrack on 404 | ❌ Tidak ada di v3. Implementasi explicit `untrack()` dari nol + compare set token ID di `sync_connections()` | Claude caught, Grok + Lisa confirm |
| 2 | Cash buffer paper phase | **15%** (middle ground), dynamic adjust ke 25% kalau deployed >70% | Grok 20% + Lisa dynamic = 15% hybrid |
| 3 | Point spread vs O/U goals | **Split**: `sports_total` (O/U, predictable) vs `sports_spread` (random). Momentum only allow `sports_total` | Claude proposed, Grok + Lisa agree |
| 4 | Resolution snipe sports | **NO sports**. Relax price 0.90→0.88, time 24h→72h. Keep crypto/economics/politics/other | Claude proposed, Grok + Lisa agree |
| 5 | Sample size metric | Track **unique markets** (30-50 threshold), bukan total trades. 20 trades di 1 market = 1 sample | Claude proposed, Grok + Lisa agree |
| 6 | CryptoPanic API latency | **Test dulu** sebelum commit. Lisa admit assumed dari docs, belum verify real latency | Lisa honest correction |

---

## 🐛 Bug Fixes Final List (Claude locked in)

Dari Claude's original review (Claude limit kredit, gak bisa lanjut chat, tapi fixes udah clear):

1. **3-layer config conflict** — keep `risk.per_strategy.*.max_capital_pct` sebagai single source of truth, buang/mark `strategies.*.max_position_pct` (fallback only), keep global `max_pct_per_trade` sebagai ceiling 60-70% + warning log
2. **`record_entry()` vs `record_close()`** — pisah method:
   - `record_entry(strategy)` → increment `_trade_times` (rate limit gate), TIDAK sentuh pnl/win-loss
   - `record_close(strategy, pnl)` → update `_consecutive_losses`, circuit breaker, TIDAK sentuh rate limit
   - Fix double-count bug
3. **`untrack()` dead code** — implementasi explicit di bot scan cycle + compare set token ID di `sync_connections()` (bukan count)
4. **atomic_arb leg risk** — add delay simulation antar leg (200-500ms) + tag PnL "paper-only, belum model leg-risk" di dashboard/log
5. **resolution_snipe opportunity-rate logging** — log "berapa market qualifying per scan cycle" selama 24-48 jam buat dapat empirical data

---

## 📊 Phase 1 Action Plan (v3.3.0)

### Batch 1: Config + Category Split (immediate)
1. Split `sports_derivative` → `sports_total` + `sports_spread` di `core/types.py`
2. Momentum: `allowed_categories: ["crypto", "sports_total", "economics", "other"]` (exclude sports_spread)
3. Resolution_snipe: relax price 0.88-0.97, time 72h, add politics, NO sports
4. Cash buffer: 15% (dynamic adjust ke 25% kalau deployed >70%)

### Batch 2: Bug Fixes (sequential)
5. `untrack()` explicit di bot scan cycle + set comparison di `sync_connections()`
6. Split `record_entry()` vs `record_close()` — fix rate limit double-count
7. Single source of truth for max_position_pct config + warning log

### Batch 3: atomic_arb Leg Delay
8. Add leg delay simulation (200-500ms) + price movement on leg 2
9. Tag atomic_arb PnL "paper-only, leg-risk not modeled" di dashboard

### Tracking Metrics (new)
- `unique_markets_traded` per strategy
- `trades_per_unique_market`
- Threshold: 30-50 unique markets sebelum claim edge

### LLM Agent: Skip dulu
- Test CryptoPanic latency real sebelum commit
- News_llm strategy tetap stub sampai verified

---

## 🎯 Target Revised (Konsensus)

| Phase | Bankroll | Target weekly | Risk/trade |
|---|---|---|---|
| **1 (now)** | $25-75 | +15-40% ($25→$29-35) | 15-25% |
| **2** | $75-200 | +12-18% | 12-15% |
| **3** | $200-500 | +8-12% | 6-10% |
| **4** | $500+ | +5-8% | 3-5% |

**Sample size milestone:** 30-50 unique markets per strategy sebelum claim edge proven.

---

## 📁 Review Files (audit trail)

- `REVIEW_CLAUDE_v3.2.0.md` — Claude full review
- `REVIEW_LISA_v3.2.0.md` — Lisa/Qwen full review
- `REVIEW_GROK_v3.2.0.md` — Grok full review
- `SUMMARY_V3_REVIEW_DISCUSSION.md` — this file

---

## 💡 Key Insight dari Multi-AI Review

**Cross-review dengan 3 AI berbeda itu BAGUS.** Mereka catch mistakes yang masing-masing lewatin:
- Claude paling tajam di code-level (nemuin 3 bug konkret + factual error aku)
- Lisa paling kuat di strategic framework (3-phase approach + diversification)
- Grok paling balanced (hybrid solutions + exposure caps)

**Lesson:** Jangan blindly trust 1 AI reviewer. Multi-AI review = lebih robust decision making.

---

**Status:** v3.2.0 confirmed running, profit +$10.73 (1 market dependency — fragile).
**Next:** Execute Phase 1 (v3.3.0) berdasarkan konsensus di atas.
