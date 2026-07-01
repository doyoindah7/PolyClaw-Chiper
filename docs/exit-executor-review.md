# PolyClaw-Cipher v3.5.19 — Exit Executor Code Review

> **Context:** Dry run execution confirmed working — entry + exit ke CLOB real berhasil (Claude Fable, $2.48 breakeven).
> Tapi ada bug kambuhan: orphaned CLOB orders makan allowance, exit gagal spam retry, phantom positions di DB.

---

## ⚠️ Root Cause (Diagnosis)

Entry berhasil, exit pipeline berfungsi. Tapi:
1. **Order "live" (GTC, gak langsung matched) nyangkut di CLOB book → makan allowance**
2. **Pas exit dipanggil, `cancel_all()` cuma cancel order di token spesifik — bukan SEMUA open order**
3. **Kalau allowance habis → exit gagal dengan `"not enough balance / allowance"` → retry loop 5 detik**
4. **Reconcile kadang detect posisi sebagai "phantom" dan hapus dari DB, padahal baru aja closed di CLOB**

---

## Kode Utama — 3 File

### 1. `execution/live.py` — `close_position()` (line ~350-470)

```python
async def close_position(self, pos, exit_price, reason, **kwargs):
    # ...
    # BUG-5: Check exiting state with retry delay
    last_exit = self._exiting_tokens.get(token_id)
    if last_exit and (time.time() - last_exit) < self._exit_retry_delay_sec:
        return None  # Skip — masih dalam cooldown

    self._exiting_tokens[token_id] = time.time()
    client = self._ensure_client()
    exit_side = "SELL" if pos.side == Side.YES else "BUY"

    try:
        # BUG-4: Cancel open orders BEFORE placing SELL
        cancelled = await self._cancel_orders_for_token(token_id)
        if cancelled > 0:
            await asyncio.sleep(CANCEL_BEFORE_SELL_SEC)  # 2 detik

        # ... create order, sign, post ...
        result = client.post_order(signed, OrderType.GTC)

        if status == "matched":
            return self._build_trade(pos, exit_price, making_amount, reason)
        elif status == "live":
            # ⚠️ PROBLEM: order "live" → return None → bot retries 5s later
            # Bot tidak tahu allowance sudah terpakai untuk order close ini
            self._track_order(order_id, token_id, exit_side, ...)
            return None
        else:
            return None

    except Exception as e:
        # ⚠️ PROBLEM: 'not enough balance' caught here → return None → retry loop
        logger.error("LIVE CLOSE FAILED: %s — will retry in %ds", e, ...)
        return None
```

### 2. `bot.py` — `_manage_positions()` (line ~553-710)

```python
async def _manage_positions(self):
    for pos in positions:
        # Skip PENDING (< 10s)
        if self._is_position_pending(pos.id):
            continue

        # TP/SL check
        strat = self._find_strategy(pos.strategy)
        if strat and hasattr(strat, "check_exit"):
            if self.executor.is_exiting(pos.token_id):
                continue  # Skip — exit in progress

            current = self._get_price_fallback(pos)  # CLOB WS → reconcile → entry

            should_exit, exit_reason = strat.check_exit(pos.id, pos.market_condition_id, current)
            if should_exit:
                trade = await self.executor.close_position(pos, current, exit_reason)
                # ⚠️ trade bisa None (gagal) → _close_position punya 60s cooldown retry
                await self._close_position(pos, trade, strat_name=pos.strategy)

        # Force-close stale (>30min) / dead (>15min with 0% PnL)
        # ...
```

### 3. `bot.py` — `_close_position()` (line ~961-1011)

```python
async def _close_position(self, pos, trade, strat_name):
    async with self._position_lock:
        if trade is None:
            # ⚠️ 60s cooldown sebelum retry — mencegah spam tapi delay exit
            if now_ts - last < 60:
                return  # Skip
            self._close_retry_cooldown[pos.id] = now_ts
            return

        # Persist trade, credit wallet, update risk
        await self.position_repo.close_position(pos.id)
        await self.trade_repo.add_trade(trade)
        await self.wallet.credit(pos.invested + trade.pnl_dollar)
```

### 4. `execution/reconcile.py` — `reconcile_from_clob()`

```python
async def reconcile_from_clob(executor, wallet, position_repo, trade_repo, markets=None):
    # 1. Get CLOB balance
    # 2. Get open orders → hitung locked cash
    # 3. Data API /positions → sync ke DB
    #    - Update existing, create new
    #    - ⚠️ Auto-close zombie (cur_val < $0.01)
    #    - ⚠️ Remove phantom (not in Data API, age > 5min)
    # 4. Reconcile wallet = real_balance + total_current_value
    # 5. Cancel orphaned orders (>5min)
```

---

## Log Dry Run Terbaru (Bukti Bug)

```
09:10:10 LIVE ENTRY: BUY 102670331913 11 @ 0.225 ($2.50) | LoL T1 vs Team Liquid
09:10:21 Auto-registered entry for live-60d: @ $0.2250
09:10:32 LIVE CLOSE: SELL 102670331913 11 @ 0.280 | Momentum TP: +24.4%
09:10:32 LIVE CLOSE FAILED: not enough balance / allowance → retry in 5s
09:10:37 LIVE CLOSE: SELL 102670331913 11 @ 0.295 | Momentum TP: +31.1%
09:10:37 LIVE CLOSE FAILED: not enough balance / allowance → retry in 5s
... (20x retry loop, setiap 5 detik, harga naik-turun) ...
09:12:11 LIVE CLOSE: SELL 102670331913 11 @ 0.320 | Momentum TP: +42.2%
09:12:11 LIVE CLOSE FAILED: not enough balance
```

```
09:15:13 LIVE ENTRY: BUY 747985720549 36 @ 0.069 ($2.50) | Claude Fable 5
09:15:24 Auto-registered entry for live-599: @ $0.0685
09:16:12 reconcile: updated Position live-599 | 36.49 NO @ $0.068→$0.0665 (PnL: -0.05)
09:17:12 reconcile: updated Position live-599 | 36.49 NO @ $0.068→$0.0714 (PnL: +0.12)
09:20:14 reconcile: removed phantom position live-599 | age=301s (not in Data API)
```

**Poly Data API (source of truth):**
- BUY 36.49sh $2.48 (4m ago) ✅
- SELL 36.49sh $2.48 (1m ago) ✅ → breakeven, closed successfully
- **0 active positions** — semua sudah closed

---

## Pertanyaan untuk Reviewer

1. **Bagaimana mencegah orphaned GTC orders makan allowance?**
   - Haruskah kita pakai `OrderType.FOK` (Fill-or-Kill) instead of GTC?
   - Atau cancel ALL open orders (bukan cuma token spesifik) sebelum setiap exit?

2. **Bagaimana handle order status "live" di close_position?**
   - Saat ini return None → bot retry tiap 5 detik → spam
   - Apakah sebaiknya polling order status sampai matched/timeout?

3. **Bagaimana menghindari phantom position di reconcile?**
   - Position closed di CLOB dalam < 5 menit → Data API belum update → reconcile anggap phantom
   - Apakah perlu delay/timestamp verification sebelum hapus?

4. **Apakah ada approach yang lebih baik untuk allowance tracking?**
   - Track total allowance terpakai di memory
   - Atau query CLOB balance sebelum setiap order?

5. **Apakah retry loop exit sebaiknya di-cap?**
   - Saat ini unlimited retry → kalau allowance habis, spam forever
   - Mungkin max 3 retry, lalu fallback ke manual intervention?

---

## Environment
- **Wallet:** Proxy Smart Contract `0xf9f38a...` (signer `0x034F...`, sig_type=3 POLY_1271)
- **CLOB:** `https://clob.polymarket.com`, min $1.00 / 5 shares
- **VPS:** Ubuntu t2.small Ireland (no geo-block)
- **Balance:** ~$4.62 USDC on CLOB
- **Config:** TP 3%, SL 3%, max_hold 150s, max_entry $0.50
