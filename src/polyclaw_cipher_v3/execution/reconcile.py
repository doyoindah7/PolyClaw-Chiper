"""Live CLOB reconciliation — sync local DB with Data API (source of truth).

Called at startup and periodically to prevent state drift.
Architecture per Claude's recommendation: Data API /positions = ground truth, CLOB balance = cash.
"""
from __future__ import annotations

import asyncio, logging, os, time
from typing import Any

logger = logging.getLogger(__name__)


async def fetch_data_api_positions(funder_address: str) -> list[dict]:
    """Fetch all open positions from Polymarket Data API (ground truth)."""
    import aiohttp
    url = f"https://data-api.polymarket.com/positions?user={funder_address}"
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.error("Data API /positions returned %d", resp.status)
                return []
            data = await resp.json()
    return [p for p in data if float(p.get("size", 0)) > 0.001]


def _derive_side(token_id: str, markets: list) -> str:
    """Derive YES/NO side from markets list."""
    for m in markets:
        if m.yes_token_id == token_id:
            return "YES"
        if m.no_token_id == token_id:
            return "NO"
    return "YES"  # default, will be corrected if market found later


async def reconcile_from_clob(executor, wallet, position_repo, trade_repo, markets: list = None) -> dict:
    """Reconcile local state with CLOB + Data API.
    
    1. Query real CLOB balance (free cash)
    2. Query open orders (locked cash)
    3. Query Data API /positions (ground truth for open positions)
    4. Update wallet + DB to match reality
    5. Auto-redeem resolved positions
    
    Returns dict with reconciliation stats.
    """
    stats = {"balance": 0.0, "open_orders": 0, "positions_synced": 0, "positions_redeemed": 0, "corrected": False}
    
    if not executor or not executor.enabled:
        return stats
    
    try:
        # 1. Get real CLOB balance (free cash)
        real_balance = await executor.get_clob_balance()
        stats["balance"] = real_balance
        
        if real_balance <= 0:
            logger.warning("CLOB reconcile: balance is $0 — skipping")
            return stats
        
        # 2. Get open orders from CLOB
        client = executor._ensure_client()
        from py_clob_client_v2.clob_types import OpenOrderParams
        
        try:
            open_orders_data = await asyncio.to_thread(client.get_open_orders, OpenOrderParams())
        except Exception as e:
            logger.error("CLOB reconcile: get_open_orders failed: %s", e)
            open_orders_data = []
        
        stats["open_orders"] = len(open_orders_data) if open_orders_data else 0
        
        # Calculate locked cash from open BUY orders
        locked_cash = 0.0
        if open_orders_data:
            for o in open_orders_data:
                if isinstance(o, dict) and o.get("side") == "BUY":
                    price = float(o.get("price", 0) or 0)
                    size = float(o.get("original_size", o.get("size", 0)) or 0)
                    locked_cash += price * size
        
        available_cash = max(0.0, real_balance - locked_cash)
        
        # ─── 3. DATA API /positions — GROUND TRUTH ───
        # Get funder from executor (deposit wallet address)
        funder = getattr(executor, '_funder', None) or os.environ.get("LIVE_FUNDER", "")
        api_positions = await fetch_data_api_positions(funder)
        
        total_current_value = 0.0
        total_invested_cost = 0.0
        redeemable_tokens = []
        zombie_token_ids = set()
        
        if api_positions:
            local_positions = await position_repo.get_open_positions()
            local_token_ids = {p.token_id for p in (local_positions or []) if p.token_id}
            stats["positions_synced"] = len(api_positions)
            
            from ..core.types import Position, Side
            import uuid, time as _time
            
            for ap in api_positions:
                tid = ap.get("asset", "")
                size = float(ap.get("size", 0))
                avg_price = float(ap.get("avgPrice", 0))
                cur_price = float(ap.get("curPrice", 0))
                init_val = float(ap.get("initialValue", 0))
                cur_val = float(ap.get("currentValue", 0))
                pnl = float(ap.get("cashPnl", 0))
                title = ap.get("title", f"Token {tid[:14]}...")
                condition_id = ap.get("conditionId", "")
                redeemable = ap.get("redeemable", False)
                
                total_current_value += cur_val
                total_invested_cost += init_val
                
                if redeemable:
                    redeemable_tokens.append(ap)
                    # If position is redeemable with near-zero value, it's a ZOMBIE — skip entirely
                    # These can't be closed via UI and have no economic value
                    if cur_val < 0.01:
                        zombie_token_ids.add(tid)
                        if tid in local_token_ids:
                            # Already in DB: close it + record loss
                            for p in local_positions:
                                if p.token_id == tid:
                                    from ..core.types import Trade
                                    trade = Trade(
                                        id=f"loss-{p.id[:8]}",
                                        market_condition_id=p.market_condition_id,
                                        market_question=p.market_question,
                                        side=p.side,
                                        entry_price=p.entry_price,
                                        exit_price=0.0,
                                        shares=p.shares,
                                        invested=p.invested,
                                        pnl_dollar=-p.invested,
                                        pnl_percent=-100.0,
                                        strategy=p.strategy,
                                        reason="Market resolved: loss",
                                        opened_at=p.opened_at,
                                        closed_at=time.time(),
                                    )
                                    await trade_repo.add_trade(trade) if trade_repo else None
                                    await position_repo.close_position(p.id)
                                    logger.info("CLOB reconcile: closed dead position %s | %s | loss=$%.2f",
                                               p.id[:8], title[:30], -p.invested)
                                    break
                        local_token_ids.discard(tid)  # Don't create/update, it's dead
                        stats["positions_closed"] = stats.get("positions_closed", 0) + 1
                        continue  # Skip to next Data API position
                
                # Determine side
                side_str = _derive_side(tid, markets or [])
                side = Side.YES if side_str == "YES" else Side.NO
                
                if tid in local_token_ids:
                    # Update existing position with real prices from Data API
                    for p in local_positions:
                        if p.token_id == tid:
                            p.shares = size  # Ground truth shares
                            p.entry_price = avg_price if avg_price > 0 else p.entry_price
                            p.current_price = cur_price  # Always update, even 0 (resolved market)
                            p.invested = init_val
                            p.market_question = title
                            if condition_id:
                                p.market_condition_id = condition_id
                            p.side = side
                            await position_repo.update_position(p)
                            logger.info("CLOB reconcile: updated Position %s | %s | %.2f %s @ $%.4f→$%.4f (PnL: %+.2f)",
                                       p.id[:8], title[:30], size, side.value, avg_price, cur_price, pnl)
                            break
                else:
                    # v3.6.0: Skip zombie positions (near-zero value, can't be closed from UI)
                    if cur_val < 0.01:
                        zombie_token_ids.add(tid)
                        logger.warning("CLOB reconcile: SKIPPING ZOMBIE pos %.12s | %s | cur=$%.4f — skip creating",
                                      tid, title[:40], cur_val)
                        stats["positions_skipped"] = stats.get("positions_skipped", 0) + 1
                        continue
                    # Create new position from Data API
                    now_ts = _time.time()
                    pos = Position(
                        id=f"clob-{uuid.uuid4().hex[:8]}",
                        market_condition_id=condition_id or tid,
                        market_question=title,
                        side=side,
                        token_id=tid,
                        shares=size,
                        entry_price=avg_price,
                        current_price=cur_price,
                        invested=init_val,
                        strategy="momentum",
                        opened_at=now_ts,
                    )
                    # v3.6.0: GUARD — if position size exceeds 1.5x sizer cap, auto-close it.
                    # This handles legacy/rogue positions from CLOB that violate current config.
                    sizer_cap = (wallet.bankroll * 0.20) * 1.5 if wallet.bankroll > 0 else 999
                    if init_val > sizer_cap and executor and executor.enabled:
                        logger.warning(
                            "CLOB reconcile: ROGUE POSITION DETECTED %s | %s | invested=$%.2f > cap=$%.2f → AUTO-CLOSING",
                            pos.id[:8], title[:40], init_val, sizer_cap
                        )
                        try:
                            await executor.close_position(pos, cur_price, "reconcile: rogue position exceeds sizer cap")
                            stats["positions_closed"] = stats.get("positions_closed", 0) + 1
                            continue  # Skip adding to DB
                        except Exception as e:
                            logger.error("CLOB reconcile: auto-close failed for rogue position: %s", e)
                    continue  # FIXED: skip rogue position even if close fails
                    logger.info("CLOB reconcile: created Position %s | %s | %.2f %s shares @ $%.4f (PnL: %+.2f)",
                               pos.id[:8], title[:40], size, side.value, avg_price, pnl)
                    stats["positions_created"] = stats.get("positions_created", 0) + 1
        else:
            # Fallback: use local DB positions (Data API failed)
            local_positions = await position_repo.get_open_positions()
            total_current_value = sum(p.invested for p in (local_positions or []))
            total_invested_cost = total_current_value
            logger.warning("CLOB reconcile: Data API returned no positions, using DB fallback")
        
        # ─── 4. AUTO-REDEEM resolved positions ───
        if redeemable_tokens:
            logger.warning("CLOB reconcile: %d positions are redeemable (resolved market)", len(redeemable_tokens))
            for rp in redeemable_tokens:
                tid = rp.get("asset", "")
                title = rp.get("title", "")
                cur_val = float(rp.get("currentValue", 0))
                logger.warning("  Redeemable: %s | %s | current=$%.2f", tid[:14], title[:40], cur_val)
                # TODO: implement actual redeem via relayer API
                # For now, log the unlocked capital
            
            stats["positions_redeemed"] = len(redeemable_tokens)
            stats["locked_in_redeemable"] = sum(
                float(rp.get("currentValue", 0)) for rp in redeemable_tokens
            )
        
        # ─── 4b. CLEANUP: Remove DB positions not in Data API (prevent phantom positions) ───
        # Only delete positions older than 5 min — new positions need time for Data API indexing
        if api_positions:
            api_token_ids = {p.get("asset", "") for p in api_positions}
            now = time.time()
            for p in (local_positions or []):
                if p.token_id not in api_token_ids and (now - p.opened_at) > 300:
                    await position_repo.close_position(p.id)
                    logger.info("CLOB reconcile: removed phantom position %s | %s | age=%.0fs (not in Data API)",
                               p.id[:8], p.market_question[:30], now - p.opened_at)
                    stats["positions_removed"] = stats.get("positions_removed", 0) + 1
        
        # ─── 5. Reconcile wallet ───
        old_bankroll = wallet.bankroll
        old_cash = wallet.cash
        
        # v3.6.0: FIX — Detect legacy/poison positions from before wallet reset.
        # If wallet was freshly initialized (bankroll ≈ initial_bankroll before sync),
        # treat ALL existing positions as inherited — DON'T inflate bankroll with them.
        # This prevents old overspend-era positions from blowing up the sizer.
        # FIXED: Only block position value if it would inflate bankroll > 2x initial (zombie storm)
        # Normal positions with real value should always be counted
        projected_bankroll = available_cash + total_current_value
        is_fresh_wallet = (old_bankroll <= wallet.initial_bankroll * 1.02
                          and abs(old_bankroll - wallet.initial_bankroll) < 2.0)
        is_zombie_storm = (total_current_value > wallet.initial_bankroll * 2.0)
        
        if is_fresh_wallet and total_current_value > 0 and old_cash > 0 and is_zombie_storm:
            logger.warning(
                "CLOB reconcile: ZOMBIE STORM detected (bankroll=$%.2f, initial=$%.2f, pos_value=$%.2f) — "
                "%d positions will be SAFE-LISTED but NOT counted in bankroll.",
                old_bankroll, wallet.initial_bankroll, total_current_value, len(api_positions or [])
            )
            total_current_value = 0.0
        
        # Bankroll = free cash + current position values (equity)
        new_bankroll = real_balance + total_current_value  # FIXED: use real CLOB balance, not available_cash (locked orders skew it)
        
        # Also calculate cost-basis equity for reference
        cost_equity = available_cash + total_invested_cost
        
        if abs(old_bankroll - new_bankroll) > 0.01 or abs(old_cash - available_cash) > 0.01:
            logger.warning(
                "CLOB reconcile: bankroll $%.2f→$%.2f, cash $%.2f→$%.2f "
                "(locked: $%.2f, current_value: $%.2f, cost_basis: $%.2f, equity: $%.2f)",
                old_bankroll, new_bankroll, old_cash, available_cash,
                locked_cash, total_current_value, total_invested_cost, cost_equity,
            )
            # Use current value for bankroll (market-equity for sizer)
            await wallet.sync_from_clob(real_balance, total_current_value)
            wallet._cash = real_balance  # FIXED: use real CLOB balance
            await wallet._save()
            stats["corrected"] = True
        
        # ─── 6. Cancel orphaned open orders ───
        if open_orders_data:
            for o in open_orders_data:
                if isinstance(o, dict):
                    order_id = o.get("id", o.get("order_id", ""))
                    created = o.get("created_at", 0)
                    age = time.time() - float(created) if created else 999
                    
                    if age > 300:  # Older than 5 min
                        logger.warning("CLOB reconcile: cancelling orphaned order %s (age=%.0fs)", order_id[:12], age)
                        try:
                            await asyncio.to_thread(client.cancel_orders, [order_id])
                        except Exception as e:
                            logger.warning("CLOB reconcile: cancel orphan ignored: %s", str(e)[:80])
        
        # v3.6.1: After reconcile, clear only FILLED close tokens (not all)
        # Reconcile found which positions closed — remove only those from pending
        if hasattr(executor, '_pending_close_tokens') and api_positions:
            active_token_ids = {ap.get("asset", "") for ap in api_positions}
            # Remove tokens that no longer appear in Data API (position closed/filled)
            executor._pending_close_tokens = {
                t for t in executor._pending_close_tokens
                if t in active_token_ids and t not in zombie_token_ids
            }
            # v3.6.1 BATCH1: Also clear exiting state for closed/zombie tokens
            if hasattr(executor, 'clear_exiting'):
                for t in list(executor._pending_close_tokens):
                    if t not in active_token_ids or t in zombie_token_ids:
                        executor.clear_exiting(t)
        
        logger.info(
            "CLOB reconcile OK: balance=$%.2f, positions=%d, current_value=$%.2f, "
            "equity=$%.2f, cash=$%.2f%s",
            real_balance, len(api_positions), total_current_value,
            cost_equity, available_cash,
            f", REDEEMABLE={len(redeemable_tokens)}" if redeemable_tokens else "",
        )
        
    except Exception as e:
        logger.error("CLOB reconcile failed: %s", e, exc_info=True)
    
    return stats
