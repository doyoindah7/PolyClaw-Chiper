"""Live executor v2 — refactored with proper order lifecycle management.

Fixes for Batch 1 (P0 critical bugs):
- BUG-1: NameError in "live" order logging
- BUG-3: Fire-and-forget "live" orders (now tracked with timeout)
- BUG-4: close_position doesn't cancel open orders (now cancels first)
- BUG-5: _pending_close_tokens = stuck forever (replaced with proper order tracking)
- BUG-7: No allowance check before entry (added AllowanceGuard)
- BUG-8: get_clob_balance return type inconsistency (fixed)

Architecture:
    LiveExecutor
    ├── OrderManager     — tracks all active orders, handles cancel
    ├── AllowanceGuard   — checks available USDC before placing orders
    └── PriceResolver    — fallback chain for price lookup

Env vars required:
  POLYGON_RPC_URL
  PRIVATE_KEY
  POLYMARKET_API_KEY / POLYMARKET_API_SECRET / POLYMARKET_API_PASSPHRASE
  LIVE_FUNDER (deposit wallet address)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

from ..core.types import Position, Side, Signal, Trade

logger = logging.getLogger(__name__)

# ─── Constants ──────────────────────────────────────────────────────────────

ORDER_TIMEOUT_SEC = 60       # Cancel "live" orders after 60s
CANCEL_BEFORE_SELL_SEC = 1   # Wait after cancel before placing SELL
MIN_ORDER_SIZE = 5           # CLOB V2 minimum shares
GTC_ORDER_MAX_AGE_SEC = 300  # Cancel orphaned orders older than 5 min


class _ActiveOrder:
    """Internal tracking for a single order on CLOB book."""
    __slots__ = ["order_id", "token_id", "side", "price", "size",
                 "market_question", "signal_id", "strategy",
                 "created_at", "status"]

    def __init__(
        self,
        order_id: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
        market_question: str,
        signal_id: str,
        strategy: str,
        status: str,
    ):
        self.order_id = order_id
        self.token_id = token_id
        self.side = side
        self.price = price
        self.size = size
        self.market_question = market_question
        self.signal_id = signal_id
        self.strategy = strategy
        self.created_at = time.time()
        self.status = status          # "matched" | "live" | "cancelled" | "timeout"


class LiveExecutor:
    """Live Polymarket executor via CLOB V2 API — with proper order lifecycle."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._client = None
        self._initialized = False

        # Required env vars
        self._private_key = os.environ.get("PRIVATE_KEY", "")
        self._funder = os.environ.get("LIVE_FUNDER", os.environ.get("BOT_ADDRESS", ""))
        self._l2_key = os.environ.get("POLYMARKET_API_KEY", "")
        self._l2_secret = os.environ.get("POLYMARKET_API_SECRET", "")
        self._l2_passphrase = os.environ.get("POLYMARKET_API_PASSPHRASE", "")

        # Fee rate in bps (0.25% taker = 25 bps)
        self._fee_rate_bps = self.config.get("fee_rate_bps", 25)

        # ─── Order Lifecycle Management ─────────────────────────────────────
        # order_id -> _ActiveOrder
        self._active_orders: dict[str, _ActiveOrder] = {}
        # token_id -> set of order_ids (for fast lookup)
        self._token_orders: dict[str, set[str]] = {}
        # Latency tracking
        self._order_latency: list[float] = []

        # ─── Exit State Tracking (v3.5.19: FOK exit + retry cap) ────────────
        # token_id -> {"last_attempt": ts, "retry_count": int}
        self._exiting_tokens: dict[str, dict] = {}
        EXIT_MAX_RETRIES = 3
        EXIT_BACKOFF_SEC = [5, 15, 45]
        # position IDs needing human attention (retries exhausted)
        self._manual_review: set[str] = set()
        # token_id -> shares reserved for in-flight exit (release-on-exception guard)
        self._exit_reserved: dict[str, float] = {}

        # ─── PENDING Position Tracking (BUG FIX: live orders invisible) ─────
        # order_id -> position_id for "live" orders (not yet matched)
        self._live_order_to_pos: dict[str, str] = {}
        # position IDs whose CLOB order timed out (need cleanup)
        self._timed_out_pos_ids: set[str] = set()
        self._exit_retry_delay_sec = self.config.get("exit_retry_delay_sec", 5)

        # ─── Rate Limiter (prevent CLOB API 429) ──────────────────────────
        self._order_semaphore = asyncio.Semaphore(1)  # 1 order at a time
        self._min_order_gap_sec = 0.5  # 500ms between orders

        # ─── Background Tasks ───────────────────────────────────────────────
        self._cleanup_task: asyncio.Task | None = None
        self._running = False

        if not all([self._private_key, self._l2_key]):
            logger.warning(
                "LiveExecutor: missing PRIVATE_KEY or POLYMARKET_API_KEY — "
                "live trading disabled."
            )
            self.enabled = False
        else:
            self.enabled = True

    # ─── Client Lifecycle ───────────────────────────────────────────────────

    def _ensure_client(self):
        """Lazy-init CLOB client."""
        if self._initialized and self._client is not None:
            return self._client

        try:
            from py_clob_client_v2.client import ClobClient
            from py_clob_client_v2.clob_types import ApiCreds

            creds = ApiCreds(
                api_key=self._l2_key,
                api_secret=self._l2_secret,
                api_passphrase=self._l2_passphrase,
            )

            self._client = ClobClient(
                host="https://clob.polymarket.com",
                key=self._private_key,
                chain_id=137,
                creds=creds,
                signature_type=3,  # POLY_1271
                funder=self._funder,
            )

            self._initialized = True
            logger.info(
                "LiveExecutor: CLOB V2 client initialized "
                "(sig_type=3, funder=%s...%s)",
                self._funder[:10], self._funder[-6:],
            )
            return self._client

        except Exception as e:
            logger.error("LiveExecutor: failed to init CLOB client: %s", e)
            raise

    async def start(self):
        """Start background cleanup task. Call this after bot starts."""
        if not self.enabled or self._running:
            return
        self._running = True
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="live_order_cleanup"
        )
        logger.info("LiveExecutor: background cleanup started")

    async def stop(self):
        """Stop background task. Call this on bot shutdown."""
        self._running = False
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        # Cancel all active "live" orders
        await self.cancel_all_live_orders()
        logger.info("LiveExecutor: stopped")

    # ─── Public API ─────────────────────────────────────────────────────────

    async def execute_entry(
        self, signal: Signal, market_question: str, bankroll: float, **kwargs
    ) -> Position | None:
        """Place live BUY order with proper lifecycle management.

        Flow:
            1. Check allowance (free USDC on CLOB)
            2. Place BUY order (GTC)
            3. If "matched": create Position, return it
            4. If "live": track order, start timeout, return None
            5. If timeout: auto-cancel order
        """
        if not self.enabled:
            logger.warning("LiveExecutor: disabled, skipping entry")
            return None

        client = self._ensure_client()

        # ─── FIX BUG-2: Safe token_id extraction ──────────────────────────
        token_id = ""
        if signal.token_id:
            token_id = signal.token_id
        elif signal.legs and len(signal.legs) > 0:
            token_id = signal.legs[0].token_id
        if not token_id:
            logger.error("LiveExecutor: no token_id in signal (legs=%d)", len(signal.legs) if signal.legs else 0)
            return None

        order_usd = signal.suggested_size_usd

        # ─── FIX BUG-7: Allowance check before entry ──────────────────────
        available = await self._get_available_usdc()
        if available < order_usd:
            logger.warning(
                "LIVE ENTRY BLOCKED: available $%.2f < order $%.2f "
                "(active_orders locking $%.2f)",
                available, order_usd, self._get_locked_usdc(),
            )
            return None

        # SAFETY: order size must not exceed bankroll
        if order_usd > bankroll:
            logger.error(
                "LIVE ENTRY REJECTED: order $%.2f > bankroll $%.2f — DANGER GUARD",
                order_usd, bankroll,
            )
            return None

        # ─── FIX BUG-6: Proper sizing (fee is taken from payout, not entry)
        price = signal.suggested_price
        size = self._usd_to_shares(order_usd, price)

        if size <= 0:
            logger.error("LIVE ENTRY REJECTED: size=0 (price=%.3f, usd=%.2f)", price, order_usd)
            return None

        if size < MIN_ORDER_SIZE:
            logger.warning(
                "LIVE ENTRY REJECTED: size %.2f < min %d shares "
                "(price=%.3f, usd=%.2f, need $%.2f)",
                size, MIN_ORDER_SIZE, price, order_usd, MIN_ORDER_SIZE * price,
            )
            return None

        logger.info(
            "LIVE ENTRY: BUY %s %.0f @ %.3f ($%.2f) | %s",
            token_id[:12], size, price, order_usd, market_question[:50],
        )

        async with self._order_semaphore:
            try:
                from py_clob_client_v2.clob_types import OrderArgs, OrderType, CreateOrderOptions
                from py_clob_client_v2.order_builder.constants import BUY

                # Get market metadata
                try:
                    tick_size_str = str(client.get_tick_size(token_id))
                except Exception:
                    tick_size_str = "0.01"
                try:
                    neg_risk = client.get_neg_risk(token_id)
                except Exception:
                    neg_risk = False

                order_args = OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=BUY,
                )
                options = CreateOrderOptions(tick_size=tick_size_str, neg_risk=neg_risk)

                t0 = time.time()
                signed = client.create_order(order_args, options)
                t_sign = (time.time() - t0) * 1000

                t1 = time.time()
                result = client.post_order(signed, OrderType.GTC)
                t_post = (time.time() - t1) * 1000

                await asyncio.sleep(self._min_order_gap_sec)

                self._order_latency.append((time.time() - t0) * 1000)

                status = result.get("status", "?")
                order_id = result.get("orderID", "")
                tx_hashes = result.get("transactionsHashes", [])

                logger.info(
                    "LIVE ORDER: %s | status=%s | sign=%.0fms post=%.0fms | tx=%s",
                    order_id[:16] if order_id else "no-id",
                    status, t_sign, t_post, tx_hashes,
                )

                if status == "matched":
                    # Order filled immediately — create position
                    self._track_order(order_id, token_id, "BUY", price, size,
                                      market_question, signal.id, signal.strategy_name, "matched")
                    return self._build_position(signal, token_id, price, size, market_question)

                elif status == "live":
                    # ─── FIX BUG-1 + BUG-3: Track "live" orders with timeout ──
                    self._track_order(order_id, token_id, "BUY", price, size,
                                      market_question, signal.id, signal.strategy_name, "live")

                    logger.info(
                        "LIVE ORDER on book (status=live) %s | %.0f shares @ $%.4f = $%.2f | "
                        "timeout=%ds",
                        order_id[:12] if order_id else "?", size, price, order_usd,
                        ORDER_TIMEOUT_SEC,
                    )

                    # Build PENDING position BEFORE timeout task starts
                    # BUG FIX: return position so bot.py can track it in position_repo
                    # _pending_positions in bot.py handles exit-safety (skip exits for 10s)
                    pending_pos = self._build_position(signal, token_id, price, size, market_question)
                    self._live_order_to_pos[order_id] = pending_pos.id

                    # Start timeout task — auto-cancel if not filled
                    asyncio.create_task(
                        self._order_timeout_task(order_id, ORDER_TIMEOUT_SEC),
                        name=f"order_timeout_{order_id[:8]}",
                    )
                    return pending_pos

                else:
                    logger.warning("LIVE ORDER unknown status: %s — treating as failed", status)
                    return None

            except Exception as e:
                logger.error("LIVE ENTRY FAILED: %s", e, exc_info=True)
                return None

    async def close_position(
        self, pos: Position, exit_price: float, reason: str, **kwargs
    ) -> Trade | None:
        """Close position via FOK (Fill-or-Kill) — never rests on book.

        Why FOK instead of GTC for exits:
        - GTC exit can end up "live" (unmatched) → nyangkut di book → lock
          allowance → retry berikutnya gagal "not enough balance" karena
          shares masih dikomit ke order lama.
        - FOK fills completely right now, or fails immediately.
          No resting order → no need for cancel-before-sell.

        Retry: max 3 attempts, exponential backoff 5/15/45s.
        Exhausted → cancel_all() + flag manual review.
        """
        if not self.enabled:
            return self._fake_trade(pos, exit_price, reason)

        token_id = pos.token_id

        if pos.shares < MIN_ORDER_SIZE:
            logger.warning(
                "LIVE CLOSE SKIP: %s has %.2f shares < min %d",
                pos.id[:8], pos.shares, MIN_ORDER_SIZE,
            )
            return None

        exit_state = self._exiting_tokens.get(token_id)
        retry_count = exit_state["retry_count"] if exit_state else 0

        if exit_state and (time.time() - exit_state["last_attempt"]) < self._exit_retry_delay_sec:
            logger.debug("LIVE CLOSE SKIP: %s in backoff (retry %d/3)",
                        token_id[:12], retry_count)
            return None

        if retry_count >= 3:
            await self._exhaust_exit_retries(pos, token_id, reason)
            return None

        # Set backoff for this attempt before we know outcome
        backoff = [5, 15, 45][min(retry_count, 2)]
        self._exit_retry_delay_sec = backoff
        self._exiting_tokens[token_id] = {"last_attempt": time.time(), "retry_count": retry_count + 1}

        client = self._ensure_client()
        exit_side = "SELL" if pos.side == Side.YES else "BUY"

        logger.info(
            "LIVE CLOSE (FOK): %s %s %.0f @ %.3f | %s (attempt %d/3)",
            exit_side, token_id[:12], pos.shares, exit_price, reason, retry_count + 1,
        )

        # Reserve shares for this attempt — guaranteed release in finally
        self._exit_reserved[token_id] = pos.shares

        try:
            from py_clob_client_v2.clob_types import OrderArgs, OrderType, CreateOrderOptions
            from py_clob_client_v2.order_builder.constants import BUY, SELL

            side_const = SELL if pos.side == Side.YES else BUY

            tick_size_str = "0.01"
            neg_risk = False
            try:
                tick_size_str = str(client.get_tick_size(token_id))
                neg_risk = client.get_neg_risk(token_id)
            except Exception:
                pass

            order_args = OrderArgs(
                token_id=token_id,
                price=exit_price,
                size=pos.shares,
                side=side_const,
            )
            options = CreateOrderOptions(tick_size=tick_size_str, neg_risk=neg_risk)

            t0 = time.time()
            signed = client.create_order(order_args, options)
            t_sign = (time.time() - t0) * 1000

            t1 = time.time()
            result = client.post_order(signed, OrderType.FOK)
            t_post = (time.time() - t1) * 1000

            self._order_latency.append((time.time() - t0) * 1000)

            status = result.get("status", "?")
            order_id = result.get("orderID", "")

            logger.info(
                "LIVE CLOSE FOK: %s | status=%s | sign=%.0fms post=%.0fms",
                order_id[:16] if order_id else "no-id", status, t_sign, t_post,
            )

            if status == "matched":
                making_amount = float(result.get("makingAmount", 0) or 0)
                self._exiting_tokens.pop(token_id, None)
                trade = self._build_trade(pos, exit_price, making_amount, reason)
                trade.closed_locally = True
                trade.closed_locally_ts = time.time()
                return trade

            # FOK by definition either fills or dies — no resting order
            logger.warning(
                "LIVE CLOSE FOK did not fill: status=%s — retry %d/3 in %ds",
                status, retry_count + 1, backoff,
            )
            return None

        except Exception as e:
            logger.error(
                "LIVE CLOSE FAILED: %s (exit_price=%.3f) — retry %d/3 in %ds",
                e, exit_price, retry_count + 1, backoff,
            )
            return None

        finally:
            self._exit_reserved.pop(token_id, None)

    async def _exhaust_exit_retries(self, pos: Position, token_id: str, reason: str) -> None:
        """Last resort: cancel everything + flag for human review."""
        client = self._ensure_client()
        try:
            cancelled = await asyncio.to_thread(client.cancel_all)
            logger.critical(
                "EXIT RETRY EXHAUSTED for %s (token=%s, reason=%s). "
                "cancel_all() removed %s open orders. FLAGGING FOR MANUAL REVIEW.",
                pos.id, token_id[:12], reason, cancelled,
            )
        except Exception as e:
            logger.critical(
                "EXIT RETRY EXHAUSTED for %s AND cancel_all() failed: %s. "
                "MANUAL INTERVENTION REQUIRED.", pos.id, e,
            )
        self._manual_review.add(pos.id)
        self._exiting_tokens.pop(token_id, None)

    def get_manual_review_positions(self) -> set[str]:
        """bot.py should poll this and alert."""
        return self._manual_review.copy()

    async def resolve_position(
        self, pos: Position, winning_side: str
    ) -> Trade | None:
        """Resolve position at market close (win/lose payout)."""
        won = (pos.side == Side.YES and winning_side == "YES") or \
              (pos.side == Side.NO and winning_side == "NO")
        exit_price = 1.0 if won else 0.0
        return await self.close_position(pos, exit_price, f"resolved:{winning_side}")

    # ─── Order Management ───────────────────────────────────────────────────

    def _track_order(self, order_id: str, token_id: str, side: str, price: float,
                     size: float, market_question: str, signal_id: str,
                     strategy: str, status: str) -> None:
        """Track an active order."""
        if not order_id:
            return
        order = _ActiveOrder(
            order_id=order_id, token_id=token_id, side=side,
            price=price, size=size, market_question=market_question,
            signal_id=signal_id, strategy=strategy, status=status,
        )
        self._active_orders[order_id] = order
        self._token_orders.setdefault(token_id, set()).add(order_id)

    async def _cancel_order(self, order_id: str) -> bool:
        """Cancel a single order by ID. Returns True if success."""
        try:
            client = self._ensure_client()
            await asyncio.to_thread(client.cancel_orders, [order_id])
            if order_id in self._active_orders:
                self._active_orders[order_id].status = "cancelled"
            logger.debug("Cancelled order %s", order_id[:12])
            return True
        except Exception as e:
            logger.warning("Cancel order %s failed: %s", order_id[:12], str(e)[:80])
            return False

    async def _cancel_orders_for_token(self, token_id: str) -> int:
        """Cancel all open orders for a token. Returns count cancelled."""
        order_ids = list(self._token_orders.get(token_id, set()))
        if not order_ids:
            # Also try from CLOB directly (orders we don't know about)
            try:
                return await self._cancel_from_clob(token_id)
            except Exception:
                return 0

        cancelled = 0
        for oid in order_ids:
            order = self._active_orders.get(oid)
            if order and order.status == "live":
                if await self._cancel_order(oid):
                    cancelled += 1
        return cancelled

    async def _cancel_from_clob(self, token_id: str) -> int:
        """Query CLOB for open orders and cancel ones matching token_id."""
        try:
            client = self._ensure_client()
            from py_clob_client_v2.clob_types import OpenOrderParams
            open_orders = await asyncio.to_thread(client.get_open_orders, OpenOrderParams())
            if not open_orders:
                return 0

            to_cancel = []
            for o in open_orders:
                if isinstance(o, dict) and o.get("asset_id") == token_id:
                    oid = o.get("id", o.get("order_id", ""))
                    if oid:
                        to_cancel.append(oid)

            if to_cancel:
                await asyncio.to_thread(client.cancel_orders, to_cancel)
                logger.info("Cancelled %d orders for %s from CLOB query", len(to_cancel), token_id[:12])
            return len(to_cancel)
        except Exception as e:
            logger.warning("Cancel from CLOB failed: %s", str(e)[:80])
            return 0

    async def cancel_all_live_orders(self) -> int:
        """Cancel all tracked "live" orders. Used on shutdown."""
        live_orders = [
            oid for oid, o in self._active_orders.items()
            if o.status == "live"
        ]
        if not live_orders:
            return 0

        logger.info("Cancelling %d live orders on shutdown...", len(live_orders))
        cancelled = 0
        for oid in live_orders:
            if await self._cancel_order(oid):
                cancelled += 1
        return cancelled

    async def _order_timeout_task(self, order_id: str, timeout_sec: int):
        """Background task: cancel order if not filled after timeout."""
        await asyncio.sleep(timeout_sec)
        order = self._active_orders.get(order_id)
        if not order or order.status != "live":
            return  # Already filled or cancelled

        logger.warning(
            "Order %s timeout (%ds) — cancelling unfilled order",
            order_id[:12], timeout_sec,
        )
        await self._cancel_order(order_id)
        order.status = "timeout"

        # Mark position for cleanup (bot.py will remove from position_repo + credit wallet)
        pos_id = self._live_order_to_pos.pop(order_id, None)
        if pos_id:
            self._timed_out_pos_ids.add(pos_id)
            logger.info("Position %s marked for cleanup (order %s timed out)", pos_id[:8], order_id[:12])

    async def _cleanup_loop(self):
        """Background loop: periodically clean up stale orders."""
        while self._running:
            try:
                await asyncio.sleep(30)  # Every 30 seconds
                await self._cleanup_stale_orders()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Cleanup loop error: %s", e)

    async def _cleanup_stale_orders(self):
        """Cancel "live" orders older than GTC_ORDER_MAX_AGE_SEC."""
        now = time.time()
        stale = [
            oid for oid, o in self._active_orders.items()
            if o.status == "live" and (now - o.created_at) > GTC_ORDER_MAX_AGE_SEC
        ]
        for oid in stale:
            logger.info("Cleaning up stale order %s (age=%.0fs)",
                       oid[:12], now - self._active_orders[oid].created_at)
            await self._cancel_order(oid)

    # ─── Exit State Management (v3.5.19: FOK dict-based) ────────────────

    def is_exiting(self, token_id: str) -> bool:
        """Check if a token is currently being exited."""
        exit_state = self._exiting_tokens.get(token_id)
        if exit_state and (time.time() - exit_state["last_attempt"]) < self._exit_retry_delay_sec:
            return True
        # FOK never rests on book — no need to check active SELL orders for exit
        return False

    def clear_exiting(self, token_id: str):
        """Clear exiting state (called when position confirmed closed)."""
        self._exiting_tokens.pop(token_id, None)

    # ─── AllowanceGuard ───────────────────────────────────────────────────────

    async def _get_available_usdc(self) -> float:
        """Get available USDC (free balance minus locked by open orders).

        FIX BUG-7 + BUG-8: Proper allowance check before entry.
        """
        try:
            client = self._ensure_client()
            from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

            bal = await asyncio.to_thread(
                client.get_balance_allowance,
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL),
            )

            # FIX BUG-8: Handle inconsistent return types
            raw_balance = "0"
            if isinstance(bal, dict):
                raw_balance = str(bal.get("balance", "0"))
            elif isinstance(bal, str):
                raw_balance = bal

            balance_usd = int(raw_balance) / 1_000_000

            # Subtract locked cash from our tracked "live" BUY orders
            locked = self._get_locked_usdc()
            available = max(0.0, balance_usd - locked)

            return available

        except Exception as e:
            logger.error("LiveExecutor: _get_available_usdc failed: %s", e)
            return 0.0


    async def get_clob_balance(self) -> float:
        """Get raw CLOB USDC balance (free collateral)."""
        try:
            client = self._ensure_client()
            from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
            bal = await asyncio.to_thread(
                client.get_balance_allowance,
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL),
            )
            raw_balance = "0"
            if isinstance(bal, dict):
                raw_balance = str(bal.get("balance", "0"))
            elif isinstance(bal, str):
                raw_balance = bal
            return int(raw_balance) / 1_000_000
        except Exception as e:
            logger.error("LiveExecutor: get_clob_balance failed: %s", e)
            return 0.0

    def _get_locked_usdc(self) -> float:
        """Calculate USDC locked by tracked 'live' BUY orders."""
        locked = 0.0
        for order in self._active_orders.values():
            if order.status == "live" and order.side == "BUY":
                locked += order.price * order.size
        return locked

    # ─── Price Resolver ─────────────────────────────────────────────────────

    def get_price(self, token_id: str) -> float:
        """Get best-effort price for a token. Override this in bot wiring.

        This is a placeholder — actual implementation should use:
        1. clob_feed.get_price(token_id) — real-time, 134 tokens
        2. Position.current_price from Data API reconcile — for open positions
        3. Gamma API lastPrice — fallback
        """
        return 0.0

    # ─── Helpers ──────────────────────────────────────────────────────────────

    def _usd_to_shares(self, usd_amount: float, price: float) -> float:
        """Convert USD to shares.

        FIX BUG-6: Removed (1 + fee) divisor. Polymarket taker fee is
        deducted from payout on exit, not from entry cost. So:
            shares = usd_amount / price
        """
        if price <= 0 or price >= 1:
            return 0
        return usd_amount / price

    def get_timed_out_positions(self) -> set[str]:
        """Return and clear set of position IDs whose CLOB order timed out.
        
        bot.py should call this each cycle and:
        1. Remove position from position_repo
        2. Credit wallet
        """
        result = self._timed_out_pos_ids.copy()
        self._timed_out_pos_ids.clear()
        return result

    def _build_position(self, signal: Signal, token_id: str, price: float,
                        size: float, market_question: str) -> Position:
        """Build Position object for a filled order."""
        return Position(
            id=f"live-{uuid.uuid4().hex[:8]}",
            market_condition_id=signal.market_condition_id,
            market_question=market_question,
            side=signal.side,
            token_id=token_id,
            entry_price=price,
            shares=size,
            invested=signal.suggested_size_usd,
            strategy=signal.strategy_name,
            opened_at=time.time(),
        )

    def _build_trade(self, pos: Position, exit_price: float,
                     making_amount: float, reason: str) -> Trade:
        """Build Trade object for a closed position."""
        pnl_dollar = making_amount - pos.invested
        pnl_pct = (pnl_dollar / pos.invested * 100) if pos.invested > 0 else 0

        return Trade(
            id=f"close-{uuid.uuid4().hex[:8]}",
            market_condition_id=pos.market_condition_id,
            market_question=pos.market_question,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            shares=pos.shares,
            invested=pos.invested,
            pnl_dollar=round(pnl_dollar, 4),
            pnl_percent=round(pnl_pct, 2),
            opened_at=pos.opened_at,
            closed_at=time.time(),
            strategy=pos.strategy,
            reason=reason,
        )

    def _fake_trade(self, pos: Position, exit_price: float, reason: str) -> Trade:
        """Fallback trade when live executor disabled."""
        pnl = (exit_price - pos.entry_price) * pos.shares
        return Trade(
            id=f"fake-{uuid.uuid4().hex[:8]}",
            market_condition_id=pos.market_condition_id,
            market_question=pos.market_question,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            shares=pos.shares,
            invested=pos.invested,
            pnl_dollar=round(pnl, 4),
            pnl_percent=round(pnl / pos.invested * 100, 2) if pos.invested > 0 else 0,
            opened_at=pos.opened_at,
            closed_at=time.time(),
            strategy=pos.strategy,
            reason=f"{reason} [fallback]",
        )

    def avg_latency_ms(self) -> float:
        """Average order latency in ms."""
        if not self._order_latency:
            return 0
        return sum(self._order_latency) / len(self._order_latency)

    def get_stats(self) -> dict:
        """Return executor stats for monitoring."""
        live_count = sum(1 for o in self._active_orders.values() if o.status == "live")
        return {
            "active_orders": len(self._active_orders),
            "live_orders": live_count,
            "exiting_tokens": len(self._exiting_tokens),
            "locked_usdc": round(self._get_locked_usdc(), 4),
            "avg_latency_ms": round(self.avg_latency_ms(), 2),
        }
