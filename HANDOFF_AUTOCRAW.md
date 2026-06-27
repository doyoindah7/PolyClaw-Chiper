# HANDOFF — PolyClaw-Cipher v3 → Autoclaw

> Dokumen ini adalah panduan untuk **autoclaw** (bot AI paralel) untuk melanjutkan
> development & operasional bot Polymarket v3.
>
> Dibuat oleh: Z.ai Code (sesi 2026-06-27)
> Target pembaca: autoclaw agent
> Status v3 saat handoff: **v3.4.3 — Critical resolution detection bug fixed**

---

## 1. Status Saat Ini (Snapshot v3.4.3)

### ✅ Yang sudah jalan
- **Bot v3.4.3** running di Docker container `polyclaw-cipher-v3` di VPS 3.107.53.103
- **4 strategi aktif:** `latency_arb`, `atomic_arb`, `resolution_snipe`, `momentum`
- **WebSocket feeds:** Binance (BTC/ETH/SOL) + Polymarket CLOB (34 tokens, real-time)
- **Dashboard v3-only** di http://3.107.53.103:8082/ (protected by HTTP Basic Auth)
- **SQLite WAL** state, async paper executor, risk manager dengan per-strategy budget
- **Daemon v3.4.3** dengan exponential backoff + deep health check + graceful shutdown (SIGTERM)
- **Wallet invariant check** — bankroll == cash + invested, verified every 3s
- **Prometheus Metrics endpoint** `/metrics` dengan real-time Gauges (bankroll, cash, open positions, win rate, uptime, dll)
- **Test suite** dengan pytest (`tests/test_bot_logic.py`) lulus 100%
- **Pydantic Settings** config validation pada startup
- **GitHub repo:** https://github.com/doyoindah7/PolyClaw-Chiper (private)

### 🆕 Baru di v3.4.3 (Critical Resolution Fix)
- **Resolved markets now detected** — 3 stacked bugs fixed:
  1. Scanner fetches closed markets for open positions not in active scan
  2. `fetch_market()` queries closed markets batch + filters by conditionId
  3. `get_winning_side()` uses outcome prices (≈1.0/≈0.0) instead of resolvedBy (oracle address)
- **6 positions resolved immediately** after deploy → $35.29 cash freed
- **Bankroll: $47.91 → $54.17 (+116.7%)**, cash: $4.18 → $39.47
- Bot resumed trading (cash available for new entries)

### 🆕 Baru di v3.4.3 (Production Hardening)
- **Comprehensive test suite** (`tests/test_bot_logic.py`): Unit tests untuk `Wallet`, `RiskManager` exposure limit, dan `LatencyArbStrategy` CDF log-normal.
- **Config validation with Pydantic Settings** (`config.py`): Mencegah bot start dengan config invalid.
- **Dashboard Basic Authentication** (`http_server.py`): Proteksi dashboard dengan password configurable di `default.yaml`. Bypasses localhost agar daemon check tetap jalan.
- **Daemon Graceful Shutdown** (`daemon.py`): Mengirim `SIGTERM` ke bot untuk close connection & WAL checkpoint secara rapi sebelum hard kill `SIGKILL`.
- **Prometheus Metrics** (`http_server.py`): `/metrics` endpoint menyajikan data live bot untuk dashboard Grafana.

### 🆕 Baru di v3.4.1 (Phase 2 Strategy & Risk Improvements)
- **Time-weighted CDF Model** (`latency_arb.py`): Mengganti naive linear probability dengan log-normal CDF probabilistik standar berbasis volatilitas aset.
- **Dynamic Volatility Tracking** (`binance_ws.py`): `BinanceFeed` menghitung standard deviasi log returns dari tick history untuk dynamic daily volatility.
- **Correlation-Aware Exposure Limits** (`risk/manager.py`): Batas exposure net directional ($YES - $NO) per asset (50% bankroll) untuk mencegah krisis modal terkorelasi.
- **Cash Reservation Pipeline** (`wallet.py` & `bot.py`): Lock cash saat trade async berjalan agar sizer strategi lain melihat sisa cash aktual, mencegah race condition balance.
- **Startup State Restoration** (`bot.py`): `_entry_prices` & `_entry_times` di-load dari DB saat startup agar TP/SL checks jalan normal setelah crash/restart.
- **EventBus Cleanup**: Mengurangi overhead event publish kosong saat zero subscribers.

### 🆕 Baru di v3.4.0 (Phase 1 Critical Bug Fixes)
- **Double-Close Race Lock** (`bot.py`): `asyncio.Lock` di `_close_position` mencegah double fill/exits.
- **Overdraft Wallet Guard** (`wallet.py`): `InsufficientFundsError` melempar error dan rollback jika cash balance minus.
- **O(N²) Database Cache** (`bot.py`): Cache open positions dalam loop tick untuk menghentikan query berlebihan ke disk sqlite.
- **Resolution Snipe Price Sync** (`resolution_snipe.py`): Integrasi CLOB WS feed untuk real-time price feed sniper (menghilangkan lag 60s dari API).
- **Binance WS Spam reduction** (`binance_ws.py`): Menghapus per-tick publish event ws_status.
- **Batch DB Transaction** (`db.py`): Transaksi DELETE + trade INSERT + wallet UPDATE berjalan atomic.

### 🆕 Baru di v3.3.1 (autoclaw hotfix)

### 🆕 Baru di v3.3.0 (multi-AI review consensus)

Based on cross-review by 3 AI (Claude, Lisa/Qwen, Grok). All conflicts resolved via
discussion. See `SUMMARY_V3_REVIEW_DISCUSSION.md` for full review history.

**Bug fixes (Claude's findings, all 3 AI agreed):**
- **3-layer config conflict fixed** — `risk.per_strategy.*.max_capital_pct` is now PRIMARY
  source of truth, `strategies.*.max_position_pct` is fallback only, global
  `max_pct_per_trade` raised to 0.65 as safety ceiling
- **`record_trade()` double-count fixed** — split into `record_entry()` (rate limit only)
  + `record_close()` (pnl/win-loss only). `record_trade()` kept as deprecated alias.
- **`untrack()` dead code fixed** — explicit call in bot scan cycle + set comparison
  in `sync_connections()` (was 0 call sites, token list only grew)

**Config changes (consensus):**
- **Cash buffer**: 10% → 15% (middle ground), dynamic adjust to 25% if deployed >70%
- **Market category split**: `sports_derivative` → `sports_total` (O/U, predictable) +
  `sports_spread` (spread/handicap, random). Momentum only allows `sports_total`.
- **resolution_snipe**: relax price 0.90→0.88, time 24h→72h, add politics, NO sports
- **atomic_arb leg delay**: 200-500ms between legs + ±3bps price drift simulation
  (models real-world leg risk, PnL tagged "paper-only")

**New features:**
- **Opportunity-rate tracking** for resolution_snipe (scanned/qualified/in_band counts)
- **Multi-AI review documentation** (6 files: 3 reviews + 2 discussion rounds + summary)

### 🆕 Baru di v3.2.0 (vs v3.1.0)
- Market category classification (6 kategori)
- Category filter untuk momentum & resolution_snipe
- Atomic_arb pair execution fix (BOTH legs)
- Cash buffer 10%, min_entry_price 0.30
- Strategy stats fix

### 🆕 Baru di v3.1.0 (vs v3.0.0)
- v2 stopped, all resources to v3
- Dashboard v3-only (full width, 6 KPI cards, unrealized P&L)
- atomic_arb threshold lowered 100 → 40 bps
- resolution_snipe stop-loss + take-profit
- CLOB WS fix (36 tokens, was 1)
- Wallet invariant check
- Daemon + Binance WS bug fixes

### ⏸️ Yang di-stub (tunggu autoclaw aktifkan)
- **`news_llm` strategy** — interface siap, butuh z-ai-web-dev-sdk + API key
- **`resolution_snipe` LLM mode** — sekarang threshold-only + category filter, LLM hook ready
- **Telegram alerts** — stub di `alerts/__init__.py`

### ⏸️ Pending (consensus deferred — for autoclaw)

**From v3.3.0 multi-AI review:**
- **MASALAH-6: 0 crypto Up/Down detection** — latency_arb still dead
  - Root cause (Claude): `_extract_threshold()` only matches "above $X", but scanner
    matches "Up or Down — [date]" — 2 different market types conflated
  - Fix: redesign `_implied_prob_above` for directional markets, OR change latency_arb
    target to threshold-style markets that actually exist in Polymarket
- **Event bus wiring** — strategies still pull-based (1s loop), target <50ms
  - latency_arb should subscribe to `binance_tick` topic
  - momentum should subscribe to `clob_tick` topic
  - Claude's priority: fix arsitektural paling murah, paling berdampak ke latency target
- **LLM agent** — deferred. Test CryptoPanic latency real before commit
  - Lisa admit assumed 1-2 min latency from docs, belum verify
  - Action: test CryptoPanic + compare dengan Nitter + RSS, run parallel 24 jam
- **Sample size milestone**: 30-50 UNIQUE markets per strategy (not total trades)
  - Claude's insight: 20 trades in 1 market = 1 sample (clustered), not 20 independent
  - Track `unique_markets_traded` per strategy in stats

**Lower priority:**
- Cache trade stats in memory (reduce DB queries)
- Periodic resolution check (every 10-15s for markets <1h to close)

### ❌ Yang sengaja tidak diimplementasi
- Live trading (paper only — `BOT_MODE=paper` hard-coded mindset)
- Cross-venue arbitrage (Kalshi/PredictIt) — v4

---

## 2. Cara Mengaktifkan LLM Agent (Prioritas #1)

### 2.1 Install z-ai-web-dev-sdk

```bash
# Di VPS
ssh -i ~/.ssh/t2small.pem ubuntu@3.107.53.103
cd /home/ubuntu/polyclaw-cipher-v3

# Tambahkan ke pyproject.toml
# dependencies = [
#     ...
#     "z-ai-web-dev-sdk>=0.1",  # uncomment
# ]

# Rebuild container
docker-compose down
docker-compose up --build -d
```

### 2.2 Set API Key

Edit `.env` (di VPS, `/home/ubuntu/polyclaw-cipher-v3/.env`):
```env
ZAI_API_KEY=your_api_key_here
LLM_MODEL=glm-4.5
LLM_MAX_LATENCY_SEC=30
```

### 2.3 Implement `agent/llm_client.py`

File `src/polyclaw_cipher_v3/agent/llm_client.py` sekarang adalah **stub**.
Replace dengan implementasi real menggunakan z-ai-web-dev-sdk.

**Required interface** (jangan ubah signature):
```python
class LLMClient:
    async def analyze_news_impact(
        self, news: NewsEvent, markets: list[Market]
    ) -> list[NewsSignal]:
        """Returns list of (condition_id, side, implied_prob, confidence, reasoning)."""
        # 1. Build context: list of active Polymarket markets
        # 2. Prompt LLM: "Given this news, which markets are affected?"
        # 3. Parse LLM JSON output
        # 4. Return signals

    async def assess_near_certainty(
        self, market: Market, context: dict
    ) -> NearCertaintyAssessment:
        """For resolution_snipe strategy."""
        # Returns dataclass with .confidence (0-1) and .reasoning (str)
```

**Penting:** z-ai-web-dev-sdk WAJIB di backend (Python), **jangan** di client/browser.

### 2.4 Implement `agent/news_scraper.py`

Belum ada — buat baru. Sources:
- **Nitter** (Twitter proxy): `https://nitter.net/{user}` → scrape RSS
- **RSS feeds:** CoinDesk, The Block, dll (lihat config `news_llm.sources.rss_feeds`)
- **Polymarket large trades:** dari CLOB WS, filter trade size > $10k

Implementasi:
```python
class NewsScraper:
    async def start(self) -> None:
        """Start polling sources every 60s."""

    async def stop(self) -> None: ...

    async def _poll_nitter(self, account: str) -> list[NewsEvent]:
        """Fetch recent tweets from nitter instance."""

    async def _poll_rss(self, feed_url: str) -> list[NewsEvent]:
        """Fetch RSS feed, parse to NewsEvent."""
```

Publish `NewsEvent` ke event bus topic `"news_event"`.

### 2.5 Aktifkan Strategy

Edit `config/default.yaml`:
```yaml
strategies:
  news_llm:
    enabled: true              # ← ubah dari false
    # ... (config lainnya sudah ada)

  resolution_snipe:
    llm_enabled: true          # ← ubah dari false
```

Lalu di `bot.py`, uncomment/inject:
```python
# Di __init__, setelah strategies list:
if s_conf.get("news_llm", {}).get("enabled", False):
    from .agent.llm_client import LLMClient
    from .strategy.news_llm import NewsLLMStrategy  # buat file ini
    llm = LLMClient(s_conf.get("news_llm", {}))
    news_strat = NewsLLMStrategy(s_conf.get("news_llm", {}), llm_client=llm)
    self.strategies.append(news_strat)

# Untuk resolution_snipe, inject LLM:
for s in self.strategies:
    if hasattr(s, "set_llm_client") and s.name == "resolution_snipe":
        from .agent.llm_client import LLMClient
        s.set_llm_client(LLMClient(s_conf.get("resolution_snipe", {})))
```

### 2.6 Buat `strategy/news_llm.py`

Belum ada. Template:

```python
from .base import BaseStrategy

class NewsLLMStrategy(BaseStrategy):
    name = "news_llm"

    def __init__(self, config, llm_client=None):
        super().__init__(config)
        self._llm = llm_client
        # ... config parsing

    async def evaluate(self, market, context):
        # News-driven: triggered by news_event topic, not by market scan
        # Subscribe to "news_event" in bot.py, call this strategy with news context
        # For now, return None — actual logic in news_event handler
        return None
```

Implementasi sebenarnya: bot.py harus subscribe ke `"news_event"` topic dan
memanggil `news_llm.evaluate_with_news(news, markets)` (method baru).

---

## 3. Cara Aktifkan Telegram Alerts

### 3.1 Buat Bot

1. Chat `@BotFather` di Telegram
2. `/newbot` → dapat `BOT_TOKEN`
3. Chat `@userinfobot` → dapat `CHAT_ID`

### 3.2 Set Env

Edit `.env`:
```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
TELEGRAM_ALERT_TRADE=true
TELEGRAM_PNL_THRESHOLD=5.0
```

### 3.3 Implement `alerts/__init__.py`

Sekarang stub. Replace dengan real Telegram implementation:

```python
import httpx

class Alerter:
    def __init__(self, config):
        import os
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id)
        # ... rate limiting, etc.

    async def send(self, message: str) -> bool:
        if not self.enabled:
            return False
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"}
            )
            return resp.status_code == 200

    async def notify_startup(self, bankroll, strategies, version="v3"):
        msg = f"🚀 <b>PolyClaw-Cipher {version} Started</b>\n💰 Bankroll: ${bankroll:.2f}\n🎯 Strategies: {', '.join(strategies)}"
        await self.send(msg)

    # ... implement other notify_* methods
```

---

## 4. Cara Tambah Strategi Baru

Template strategi baru:

```python
# src/polyclaw_cipher_v3/strategy/my_strategy.py
from .base import BaseStrategy
from ..core.types import Market, Side, Signal

class MyStrategy(BaseStrategy):
    name = "my_strategy"

    def __init__(self, config=None):
        super().__init__(config)
        # Parse config

    async def evaluate(self, market: Market, context: dict) -> Signal | None:
        # 1. Filter (volume, price, etc.)
        # 2. Check signal conditions
        # 3. Compute confidence
        # 4. Size via context["sizer"]
        # 5. Return Signal or None

    def check_exit(self, pos_id, condition_id, current_price) -> tuple[bool, str]:
        # TP/SL logic
        return False, ""
```

Daftarkan di `bot.py` `__init__`:
```python
if s_conf.get("my_strategy", {}).get("enabled", False):
    self.strategies.append(MyStrategy(s_conf.get("my_strategy", {})))
```

Tambah config di `config/default.yaml`:
```yaml
strategies:
  my_strategy:
    enabled: true
    max_position_pct: 0.10
    # ...
```

Tambah risk budget di `risk.per_strategy`:
```yaml
risk:
  per_strategy:
    my_strategy:
      max_consecutive_losses: 5
      max_trades_per_hour: 20
      max_capital_pct: 0.30
```

---

## 5. Switch ke Live Trading (v4)

**⚠️ HANYA setelah paper trading profitable minimal 2 minggu.**

### 5.1 Implement `execution/live.py`

Sekarang stub. Pakai `py-clob-client`:

```bash
pip install py-clob-client
```

```python
# src/polyclaw_cipher_v3/execution/live.py
from py_clob_client.client import ClobClient
from .base import BaseExecutor

class LiveExecutor(BaseExecutor):
    def __init__(self, config):
        self.client = ClobClient(
            host="https://clob.polymarket.com",
            key=config["private_key"],
            chain_id=137,  # Polygon mainnet
        )
        # ...

    async def execute_entry(self, signal, market_question, bankroll):
        # 1. Build order (BUY YES/NO at signal.price)
        # 2. Sign & submit to CLOB
        # 3. Wait for fill (with timeout)
        # 4. Return Position
```

### 5.2 Switch Config

```yaml
bot:
  mode: live  # ← ubah dari paper
```

Bot.py akan load `config/live.yaml` overlay.

### 5.3 Safety Checklist Sebelum Live

- [ ] Paper trading ≥ 14 hari profitable
- [ ] Win rate ≥ 50% per strategi
- [ ] Max drawdown ≤ 30% dalam paper
- [ ] WebSocket uptime ≥ 99%
- [ ] Latency signal → fill ≤ 500ms average
- [ ] Unit tests pass
- [ ] Backup wallet private key (offline)
- [ ] Start dengan $25 (same as paper)
- [ ] Telegram alerts active (untuk monitoring)
- [ ] Stop-loss per trade ≤ 5% bankroll

---

## 6. Operasional Sehari-hari

### 6.1 Check Status

```bash
# SSH ke VPS
ssh -i ~/.ssh/t2small.pem ubuntu@3.107.53.103

# Container status
docker ps | grep polyclaw

# v3 logs (last 50 lines)
docker logs --tail 50 polyclaw-cipher-v3

# v3 health
curl http://localhost:8082/api/health

# v3 stats
curl http://localhost:8082/api/stats | python3 -m json.tool

# Resource usage
docker stats polyclaw-cipher-v3 --no-stream
```

### 6.2 Restart v3

```bash
docker restart polyclaw-cipher-v3
# Daemon akan auto-start bot dalam ~5 detik
```

### 6.3 Update Config

```bash
cd /home/ubuntu/polyclaw-cipher-v3
vim config/default.yaml
# Edit...

# Restart untuk apply
docker restart polyclaw-cipher-v3
```

### 6.4 Rebuild Setelah Code Change

```bash
cd /home/ubuntu/polyclaw-cipher-v3
# SCP file baru (atau git pull kalau sudah setup git)
docker-compose down
docker-compose up --build -d
```

### 6.5 Backup State

```bash
# SQLite backup (safe — WAL mode)
cp /home/ubuntu/polyclaw-cipher-v3/data/cipher_v3.db /backup/cipher_v3_$(date +%Y%m%d).db

# Atau via API
curl http://localhost:8082/api/stats > /backup/v3_stats_$(date +%Y%m%d).json
```

### 6.6 Stop v2 (setelah v3 proven)

Kapan stop v2:
- v3 profitable ≥ 7 hari
- v3 generate lebih banyak signals dari v2
- v3 win rate ≥ v2 win rate

```bash
docker stop polyclaw-cipher
# v3 bisa ambil port 8080 kalau mau:
# Edit docker-compose.yml v3: port 8081 → 8080
# docker-compose up -d
```

---

## 7. Debugging

### 7.1 Bot tidak generate signals

```bash
# Check logs untuk strategy errors
docker logs polyclaw-cipher-v3 2>&1 | grep -iE "SIGNAL|error|warning" | tail -50

# Check WebSocket status
curl http://localhost:8082/api/stats | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d['ws_status'], indent=2))"

# Check markets tracked
curl http://localhost:8082/api/stats | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"markets={d['markets']}, crypto={d['crypto_markets']}\")"
```

### 7.2 WebSocket disconnect

```bash
# Coba restart container
docker restart polyclaw-cipher-v3

# Check reconnect count
curl http://localhost:8082/api/stats | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d['ws_status'], indent=2))"
```

### 7.3 Database locked

```bash
# WAL mode should prevent this, but if it happens:
docker stop polyclaw-cipher-v3
# Wait 5s
docker start polyclaw-cipher-v3
```

### 7.4 Dashboard tidak load

```bash
# Check if HTTP server running
curl http://localhost:8082/api/health

# Check if v2 reachable (untuk dashboard gabungan)
curl http://localhost:8080/api/stats

# Atau akses langsung dari browser:
# v3: http://3.107.53.103:8082/
# v2: http://3.107.53.103:8080/
```

---

## 8. File Map Cepat

```
/home/ubuntu/polyclaw-cipher-v3/
├── ARCHITECTURE.md              # Design doc lengkap (baca dulu!)
├── HANDOFF_AUTOCRAW.md          # File ini
├── README.md                    # Quick start
├── pyproject.toml               # Dependencies
├── Dockerfile
├── docker-compose.yml
├── .env                         # API keys (JANGAN commit!)
├── .env.example                 # Template
├── config/
│   ├── default.yaml             # Config utama (edit ini)
│   └── paper.yaml               # Paper mode overlay
├── data/
│   ├── cipher_v3.db             # SQLite state (auto-created)
│   └── heartbeat.json
├── src/polyclaw_cipher_v3/
│   ├── bot.py                   # Orchestrator (main)
│   ├── config.py                # Config loader
│   ├── core/
│   │   ├── types.py             # Pydantic models
│   │   ├── event_bus.py         # Pub/sub
│   │   ├── scanner.py           # Gamma API
│   │   ├── resolution.py        # Real resolution check (FIX v2 bug)
│   │   ├── binance_ws.py        # Binance WebSocket
│   │   ├── clob_ws.py           # Polymarket CLOB WebSocket
│   │   └── http_server.py       # FastAPI + dashboard
│   ├── strategy/
│   │   ├── base.py
│   │   ├── latency_arb.py       # Strategi 1
│   │   ├── atomic_arb.py        # Strategi 2
│   │   ├── resolution_snipe.py  # Strategi 3 (LLM hook ready)
│   │   └── momentum.py          # Strategi 4
│   ├── execution/
│   │   ├── base.py
│   │   └── paper.py             # Async paper executor
│   ├── risk/
│   │   ├── manager.py           # Unified risk gate
│   │   └── sizer.py             # Position sizer
│   ├── state/
│   │   ├── db.py                # SQLite WAL
│   │   ├── wallet.py
│   │   └── repository.py
│   ├── agent/
│   │   └── llm_client.py        # STUB — autoclaw implement ini
│   ├── alerts/
│   │   └── __init__.py          # STUB — autoclaw implement Telegram
│   └── observability/
│       └── logs.py              # JSON structured logs
└── scripts/
    └── daemon.py                # Auto-heal daemon (fixed)
```

---

## 9. Contact / Konteks

- **VPS:** 3.107.53.103 (AWS t2.small, Ubuntu)
- **SSH:** `ssh -i ~/.ssh/t2small.pem ubuntu@3.107.53.103`
- **GitHub repo:** https://github.com/doyoindah7/PolyClaw-Chiper (public for review)
- **v3 location:** `/home/ubuntu/polyclaw-cipher-v3/` (current, running v3.4.3)
- **v3 port:** 0.0.0.0:8082 (public access)
- **v3 dashboard:** `http://3.107.53.103:8082/` (v3.4.3, auto-refresh 5s)
- **v2 location:** `/home/ubuntu/polyclaw-cipher/` (STOPPED, source kept for docs)

**Catatan:** v2 punya bug kritis (fake resolution, blocking executor, dll) yang
sudah diperbaiki di v3. Jangan copy pattern dari v2 — lihat `ARCHITECTURE.md`
section 1.1 untuk daftar fix.

Selamat melanjutkan! 🚀
