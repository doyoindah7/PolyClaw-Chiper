Review trading bot Polymarket aku: https://github.com/doyoindah7/PolyClaw-Chiper (private repo, v3.2.0)

**Konteks singkat:**
- Bot HFT untuk Polymarket, target $25 → $150-200/week via aggressive compounding
- Stack: Python 3.11 + asyncio + FastAPI + SQLite WAL + Docker + WebSocket (CLOB + Binance)
- 5 strategi: latency_arb, atomic_arb, resolution_snipe, momentum, news_llm(stub)
- Paper trading aktif di VPS t2.small, 4 strategi jalan, 34 CLOB tokens tracked real-time
- Sudah lewati 2 iterasi review AI lain (V3_ANALYSIS.md + V31_ANALYSIS.md), 12+ bug fixed

**Yang sudah aku kerjakan:**
- WebSocket real-time (60x faster dari REST polling v2)
- Real resolution detection (pakai field `closed` + `resolvedBy`, bukan tebak dari end_date)
- Async executor (non-blocking, fix v2 `time.sleep()` bug)
- Atomic pair-trade arbitrage (YES+NO simultan, fix v2 single-leg fake arb)
- Market category filter (skip sports_match — random outcome, bug v2 berulang)
- Per-strategy risk budget + circuit breaker
- SQLite WAL state (replaces v2 JSON dengan 30 writes/min)
- Wallet invariant check (bankroll == cash + invested, verified every 3s)
- Cash buffer 10% (fix bug bot stuck 99.4% deployed)

**Sizing per strategi:**
- latency_arb: 25% bankroll/trade (Binance→PM odds lag 200-500ms)
- atomic_arb: 40% (YES+NO<$1 risk-free, threshold 40 bps)
- resolution_snipe: 15% (near-certain markets 0.90-0.97 + SL -10%/TP +15%)
- momentum: 15% (multi-timeframe 30s+2m, TP 8%/SL 4%)
- news_llm: 10% (stub, butuh z-ai-web-dev-sdk API key)

**Risk config:**
- max_daily_drawdown: 50%
- max_consecutive_losses_global: 8
- max_trades_per_hour: 60
- per-strategy circuit breaker

**Beri tanggapan jujur dan kritis tentang:**

1. **Target $25 → $150-200/week** — realistic atau fantasy? Aku percaya achievable karena binary options + small cap + compounding + high frequency. Banyak bot Polymarket di X share wallet profit ribuan persen. Tapi mau dengar kritik kamu.

2. **Strategy mix** — 5 strategi dengan sizing di atas. Ada yang kurang/berlebih? Strategi lain yang seharusnya aku pertimbangkan (mean reversion, orderbook imbalance, cross-venue Kalshi/PredictIt)?

3. **Risk management** — 50% daily DD + 8 consec loss + 60 trades/hour. Too aggressive untuk paper trading dengan target high growth, atau akan blow up saat live?

4. **Pending issues yang belum fix:**
   - 0 crypto Up/Down markets detected (latency_arb dead — scanner timing issue)
   - sync_connections() setiap 60s disruptive (cancel+respawn WS = gap data)
   - Event bus ada tapi strategies tidak subscribe (still pull-based 1s loop, target <50ms)
   
   Prioritas fix yang mana dulu?

5. **LLM agent implementation** — untuk news-driven leading signals (trade SEBELUM odds adjust). Pakai z-ai-web-dev-sdk atau OpenAI/Anthropic? News source: Nitter + RSS cukup, atau perlu Twitter API berbayar? Latency target <30s realistic?

6. **Switch ke live trading (v4)** — pakai py-clob-client official Polymarket SDK? Wallet security best practices? Apa yang harus di-test sebelum live?

7. **Arsitektur** — ada yang over-engineering atau under-engineering? SQLite WAL cukup untuk production atau harus PostgreSQL? Event bus worth refactor atau premature optimization?

8. **Code quality** — kalau kamu clone dan baca source code, ada code smell / anti-pattern yang obvious?

Jawab to-the-point, no fluff. Kalau ada yang bagus bilang bagus, kalau ada yang bullshit bilang bullshit. Aku butuh kritik tajam, bukan validasi.
