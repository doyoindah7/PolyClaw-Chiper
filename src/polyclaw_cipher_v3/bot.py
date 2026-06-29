"""PolyClaw-Cipher v3 orchestrator — event-driven HFT bot.

Wires together:
- EventBus (in-process pub/sub)
- Scanner (Gamma API, 60s poll)
- BinanceFeed (WS, real-time BTC/ETH/SOL)
- CLOBFeed (WS, real-time Polymarket orderbook)
- Strategies (5 strategies, 4 active + 1 stubbed)
- RiskManager (unified gate, per-strategy budget)
- PaperExecutor (async, non-blocking)
- State (SQLite WAL, async)
- HTTPServer (FastAPI, unified dashboard v2+v3)
- Alerter (stub — Telegram deferred)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path
from typing import Any

from .alerts import Alerter
from .config import load_config
from .core.binance_ws import BinanceFeed
from .core.clob_ws import CLOBFeed
from .core.http_server import HTTPServer
from .core.resolution import get_winning_side, is_truly_resolved
from .core.scanner import MarketScanner
from .core.types import Market, Position, Side, Signal
from .execution.paper import PaperExecutor
from .observability.logs import setup_logging
from .risk.manager import RiskManager
from .risk.sizer import CompoundingSizer
from .risk.tier_manager import TierManager
from .state.db import Database
from .state.repository import PositionRepository, SignalRepository, TradeRepository
from .state.wallet import Wallet, InsufficientFundsError
from .strategy.atomic_arb import AtomicArbStrategy
from .strategy.convergence_scalper import ConvergenceScalper
from .strategy.latency_arb import LatencyArbStrategy
from .strategy.momentum import MomentumStrategy
from .strategy.resolution_snipe import ResolutionSnipeStrategy

logger = logging.getLogger("polyclaw-cipher-v3")


class PolyClawCipherV3:
    def __init__(self):
        self.config = load_config()
        setup_logging(
            level=self.config.get("monitoring", {}).get("log_level", "INFO"),
            fmt=os.environ.get("LOG_FORMAT", self.config.get("monitoring", {}).get("log_format", "json")),
        )

        # Core infrastructure
        self.event_bus = None
        self.db = Database(self.config.get("database_url", "sqlite+aiosqlite:///data/cipher_v3.db").replace("sqlite+aiosqlite:///", ""))
        self.wallet = Wallet(self.db, self.config.get("risk", {}).get("initial_bankroll_usd", 25.0))
        self.position_repo = PositionRepository(self.db)
        self.trade_repo = TradeRepository(self.db)
        self.signal_repo = SignalRepository(self.db)

        # Feeds
        self.scanner = MarketScanner(
            min_volume=self.config.get("market", {}).get("min_volume_24h_usd", 500),
            page_size=self.config.get("market", {}).get("api_page_size", 500),
            max_pages=self.config.get("market", {}).get("max_pages", 3),
        )
        self.binance_feed = BinanceFeed()
        self.clob_feed = CLOBFeed()

        # Risk + Sizer
        self.risk = RiskManager(self.config.get("risk", {}))
        self._tier_manager = TierManager(force_tier=self.config.get("tier", {}).get("force_tier", 0), cooldown_hours=self.config.get("tier", {}).get("cooldown_hours", 24), yaml_config=self.config.get("tier", {}))
        self.sizer = CompoundingSizer(self.config.get("risk", {}).get("sizer", {}), tier_manager=self._tier_manager)

        # Executor
        self.executor = PaperExecutor(self.config.get("execution", {}).get("paper", {}))
        # v3.5.13: Wire callbacks for live-realism simulation
        # - gas_fee_callback: deduct gas from wallet
        # - on_position_confirmed: mark position as confirmed after on-chain delay
        self.executor.gas_fee_callback = self._deduct_gas_fee
        self.executor.on_position_confirmed = self._confirm_position

        # Alerts (stub)
        self.alerter = Alerter(self.config.get("monitoring", {}))

        # Strategies
        s_conf = self.config.get("strategies", {})
        self.strategies: list[Any] = []
        if s_conf.get("latency_arb", {}).get("enabled", True):
            self.strategies.append(LatencyArbStrategy(s_conf.get("latency_arb", {})))
        if s_conf.get("atomic_arb", {}).get("enabled", True):
            self.strategies.append(AtomicArbStrategy(s_conf.get("atomic_arb", {})))
        if s_conf.get("resolution_snipe", {}).get("enabled", True):
            self.strategies.append(ResolutionSnipeStrategy(s_conf.get("resolution_snipe", {})))
        if s_conf.get("momentum", {}).get("enabled", True):
            self.strategies.append(MomentumStrategy(s_conf.get("momentum", {})))
        if s_conf.get("convergence_scalper", {}).get("enabled", False):
            self.strategies.append(ConvergenceScalper(s_conf.get("convergence_scalper", {})))

        # Inject feeds
        for s in self.strategies:
            if hasattr(s, "set_feeds"):
                s.set_feeds(self.binance_feed, self.clob_feed)
            if hasattr(s, "set_clob_feed"):
                s.set_clob_feed(self.clob_feed)
            if hasattr(s, "set_binance_feed"):
                s.set_binance_feed(self.binance_feed)

        # HTTP server
        web_conf = self.config.get("monitoring", {}).get("web", {})
        self.http_server = HTTPServer(
            host=web_conf.get("host", "0.0.0.0"),
            port=web_conf.get("port", 8082),
            get_stats=self._get_stats,
            config=self.config,
            get_db_stats=self._get_db_stats,  # v3.5.7: daemon lightweight watchdog
            wal_checkpoint=self._trigger_wal_checkpoint,  # v3.5.7
            get_trades_paginated=self._get_trades_paginated,  # v3.5.11: dashboard history
        )

        # State
        self._running = False
        self._markets: list[Market] = []
        self._last_scan: float = 0.0
        self._signals_this_cycle: int = 0
        self._last_signal_at: dict[str, float] = {}  # Track last signal time per strategy
        self._loop_stats = {"markets_eval": 0, "signals_gen": 0, "strategy_cycles": {}}  # Debug stats
        self._start_time: float = 0.0
        self._stats_cache: dict[str, Any] = {}
        # v3.4.0 FIX (BUG-C1): Position lock prevents race conditions
        # between _manage_positions() and _try_strategies()
        self._position_lock = asyncio.Lock()
        # v3.4.0 FIX (BUG-C7): Cache open positions per loop iteration
        self._cached_open_positions: list[Position] = []
        # v3.5.5 FIX (P1-05): Force-close stale/dead positions to free cash
        self.max_position_age_sec = self.config.get("risk", {}).get("max_position_age_sec", 1800)  # 30 min
        self.dead_position_age_sec = self.config.get("risk", {}).get("dead_position_age_sec", 900)  # 15 min
        # v3.5.13: Position state sync — track PENDING positions (not yet on-chain confirmed)
        # Position lifecycle: PENDING (submitted, awaiting block confirmation)
        #                     → CONFIRMED (on-chain, can exit)
        # Bot cannot exit PENDING positions — prevents "exit fails, position stuck" bug
        self._pending_positions: set[str] = set()  # position IDs in PENDING state
        self._total_gas_fees_paid: float = 0.0  # track cumulative gas for stats
        self._stats_task: asyncio.Task | None = None

    async def run(self) -> None:
        self._running = True
        self._start_time = time.time()
        self._last_summary_log: float = 0.0
        logger.info("=== PolyClaw-Cipher v3 starting ===", extra={"event": "startup", "component": "bot"})
        logger.info("Strategies: %s", [s.name for s in self.strategies])

        # Connect DB + load wallet
        await self.db.connect()
        await self.wallet.load()
        self.risk.init(self.wallet.bankroll)

        # v3.4.0 FIX (ARCH-3): Restore strategy states (entry prices/times) from open positions in DB
        # This prevents TP/SL check_exit hanging after a bot restart or daemon recovery.
        try:
            open_positions = await self.position_repo.get_open_positions()
            for pos in open_positions:
                strat = self._find_strategy(pos.strategy)
                if strat:
                    if hasattr(strat, "_entry_prices"):
                        strat._entry_prices[pos.id] = pos.entry_price
                    if hasattr(strat, "_entry_times"):
                        strat._entry_times[pos.id] = pos.opened_at
            logger.info("Restored state for %d open positions to strategies", len(open_positions))
        except Exception as e:
            logger.error("Failed to restore strategy states from DB: %s", e)

        # v3.5.13: Auto-tune from history — analyze latest archive, apply in-memory
        self._auto_tune_from_history()

        # Start services
        await self.binance_feed.start()
        await self.clob_feed.start()
        await self.http_server.start()
        # Background stats cache refresher (every 2s)
        self._stats_task = asyncio.create_task(self._refresh_stats_loop(), name="stats_cache")
        # v3.5.5 FIX (P1-03): Periodic WAL checkpoint every 30 min to flush WAL file
        self._checkpoint_task = asyncio.create_task(self._checkpoint_loop(), name="wal_checkpoint")

        await self.alerter.notify_startup(
            self.wallet.bankroll,
            [s.name for s in self.strategies],
            version="v3",
        )

        scan_interval = self.config.get("bot", {}).get("scan_interval_sec", 60)
        loop_interval = self.config.get("bot", {}).get("loop_interval_sec", 1)

        try:
            while self._running:
                try:
                    await self._loop(scan_interval, loop_interval)
                except Exception as e:
                    logger.error("Loop error: %s", e, exc_info=True)
                    await asyncio.sleep(5)
        finally:
            if self._stats_task:
                self._stats_task.cancel()
                try:
                    await self._stats_task
                except asyncio.CancelledError:
                    pass
            await self.binance_feed.stop()
            await self.clob_feed.stop()
            await self.http_server.stop()
            await self.alerter.close()
            await self.scanner.close()
            await self.db.close()
            await self.event_bus.close()
            logger.info("=== PolyClaw-Cipher v3 stopped ===")

    async def _loop(self, scan_interval: float, loop_interval: float) -> None:
        now = time.time()

        # Scan markets
        if now - self._last_scan >= scan_interval or not self._markets:
            logger.info("Scanning markets...")
            self._markets = await self.scanner.scan()
            self._last_scan = now
            crypto_markets = [m for m in self._markets if m.is_crypto_up_down]
            # Log market categories
            from collections import Counter
            cat_counts = Counter(m.classify() for m in self._markets)
            logger.info("Markets: %d total, %d crypto Up/Down, categories: %s",
                        len(self._markets), len(crypto_markets), dict(cat_counts))

            # Track top markets in CLOB WS (max 50 for t2.small)
            track_max = self.config.get("market", {}).get("track_max_markets", 50)
            top_markets = sorted(self._markets, key=lambda m: m.volume_24h, reverse=True)[:track_max]
            new_token_ids = set()
            for m in top_markets:
                self.clob_feed.track(m.yes_token_id, m.condition_id, "YES")
                self.clob_feed.track(m.no_token_id, m.condition_id, "NO")
                new_token_ids.add(m.yes_token_id)
                new_token_ids.add(m.no_token_id)

            # v3.3.0: Explicit untrack() for tokens no longer in top markets
            # Fixes Claude's BUG-3: untrack() was 0 call sites, token list only grew
            old_token_ids = set(self.clob_feed._tracked_tokens.keys())
            stale_tokens = old_token_ids - new_token_ids
            for tok in stale_tokens:
                self.clob_feed.untrack(tok)
            if stale_tokens:
                logger.debug("Untracked %d stale tokens (no longer in top-%d)", len(stale_tokens), track_max)

            # Sync WS connections with ALL tracked tokens (v3.3.0: only if set changed)
            await self.clob_feed.sync_connections()

        # v3.4.0 FIX (BUG-C7): Cache open positions once per loop iteration
        # Instead of querying DB per-market (was O(N²) — 50 markets × SELECT *)
        self._cached_open_positions = await self.position_repo.get_open_positions()

        # Check open positions for resolution / TP/SL
        await self._manage_positions()

        # Update position current values
        await self._update_position_values()

        # Run strategies on ALL markets (every loop cycle = 1s)
        self._signals_this_cycle = 0
        strat_signal_counts = {s.name: 0 for s in self.strategies}
        
        markets_tried = 0
        for market in self._markets:
            if not self._running:
                break
            markets_tried += 1
            # v3.5.1: Track per-strategy signal counts
            before = self._signals_this_cycle
            await self._try_strategies(market)
            for strat in self.strategies:
                strat_signal_counts[strat.name] += (self._signals_this_cycle - before)
        
        # v3.5.1: Log strategy evaluation summary (every ~30s to avoid spam)
        if self._signals_this_cycle > 0 or (time.time() - (self._last_summary_log or 0)) > 30:
            self._last_summary_log = time.time()
            logger.info(
                "Strategy eval cycle: %d markets, %d signals | %s",
                markets_tried, self._signals_this_cycle,
                ", ".join(f"{k}={v}" for k, v in strat_signal_counts.items())
            )

        await asyncio.sleep(loop_interval)

    async def _try_strategies(self, market: Market) -> None:
        # v3.4.0 FIX (BUG-C7): Use cached positions instead of DB query per-market
        open_positions = self._cached_open_positions

        # Risk check (global)
        can_trade, reason = self.risk.can_trade("global", self.wallet.bankroll)
        if not can_trade and self._signals_this_cycle == 0:
            return

        context = {
            "open_positions": open_positions,
            "binance_feed": self.binance_feed,
            "clob_feed": self.clob_feed,
            "bankroll": self.wallet.bankroll,
            # v3.4.0: Pass available_cash (cash minus reserved) to prevent strategies
            # from double-allocating cash for concurrent executions.
            "cash": self.wallet.available_cash,
            "sizer": self.sizer,
            "strategy_cap_pct": 0.25,  # Default, overridden per-strategy below
            # v3.5.5 FIX (P0-02): Pass total_open_positions and max_total_positions
            # so sizer can hard-block when bot is over-deployed
            "total_open_positions": len(open_positions),
            "max_total_positions": self.config.get("bot", {}).get("max_open_positions", 10),
        }

        for strat in self.strategies:
            try:
                # Per-strategy risk check
                can, why = self.risk.can_trade(strat.name, self.wallet.bankroll)
                if not can:
                    continue

                context["strategy_cap_pct"] = self.risk.get_strategy_capital_pct(strat.name)

                signal = await strat.evaluate(market, context)
                if signal:
                    self._signals_this_cycle += 1
                    # Track last signal time per strategy
                    self._last_signal_at[strat.name] = time.time()
                    await self._execute_signal(signal, market, strat)
                    logger.info(
                        "SIGNAL GENERATED: %s %s @ %.4f size=$%.2f conf=%.2f for %s",
                        strat.name, signal.side.value, signal.suggested_price,
                        signal.suggested_size_usd, signal.confidence,
                        market.question[:50]
                    )
            except Exception as e:
                logger.warning("Strategy %s error on %s: %s", strat.name, market.condition_id[:8], e)

    async def _execute_signal(self, signal: Signal, market: Market, strat: Any) -> None:
        # v3.4.4 FIX (Kimi audit #4): Cap signal size to max_position_pct BEFORE execution
        # Was: signal.suggested_size_usd could exceed per-strategy cap, causing reject
        strategy_cap_pct = self.risk.get_strategy_capital_pct(strat.name)
        max_notional = self.wallet.bankroll * strategy_cap_pct
        if signal.suggested_size_usd > max_notional:
            logger.info(
                "Signal size capped: $%.2f → $%.2f (max_position_pct=%.0f%%, bankroll=$%.2f)",
                signal.suggested_size_usd, max_notional, strategy_cap_pct * 100,
                self.wallet.bankroll,
            )
            signal = signal.model_copy(update={"suggested_size_usd": round(max_notional, 2)})

        # Final risk check
        can_trade, reason = self.risk.can_trade(strat.name, self.wallet.bankroll)
        if not can_trade:
            await self.signal_repo.log_signal(signal, executed=False, rejected_reason=reason)
            logger.warning("Signal blocked: %s", reason)
            return

        # v3.4.0 FIX (REC-3): Correlation-aware exposure limit check
        can_execute, reason = self.risk.check_exposure(
            strategy_name=strat.name,
            current_bankroll=self.wallet.bankroll,
            asset=market.crypto_asset,
            signal=signal,
            open_positions=self._cached_open_positions
        )
        if not can_execute:
            await self.signal_repo.log_signal(signal, executed=False, rejected_reason=reason)
            logger.warning("Signal blocked (correlation exposure limit): %s", reason)
            return

        # v3.4.0 FIX (STRAT-3): Cash reservation system to prevent over-allocation race
        required_cash = signal.suggested_size_usd
        if not self.wallet.has_funds(required_cash):
            await self.signal_repo.log_signal(signal, executed=False, rejected_reason="insufficient_available_cash")
            logger.warning("Signal blocked (insufficient available cash due to pending orders): need $%.2f", required_cash)
            return

        self.wallet.reserve(required_cash)

        try:
            # Execute (async, non-blocking)
            # v3.5.13: Pass market_volume_24h for liquidity-based slippage
            pos = await self.executor.execute_entry(
                signal, market.question, self.wallet.bankroll,
                market_volume_24h=getattr(market, 'volume_24h', 0.0),
            )
            if pos is None:
                await self.signal_repo.log_signal(signal, executed=False, rejected_reason="fill_rejected")
                return

            # v3.5.13: Mark position as PENDING (will be confirmed after on-chain delay)
            # Position cannot be exited until confirmed — prevents "exit fails, position stuck" bug
            self._pending_positions.add(pos.id)

            # v3.4.0 FIX (BUG-C2): Handle InsufficientFundsError from wallet.debit()
            # If concurrent signals drained cash, gracefully reject instead of crashing
            try:
                # Persist
                await self.position_repo.open_position(pos)
                await self.wallet.debit(pos.invested)

                # FIX: Handle pair sibling (atomic_arb creates 2 legs)
                sibling = self.executor.take_pair_sibling()
                if sibling:
                    await self.position_repo.open_position(sibling)
                    await self.wallet.debit(sibling.invested)
                    # v3.5.13: Mark sibling as PENDING too
                    self._pending_positions.add(sibling.id)
                    # Register sibling entry in strategy
                    if hasattr(strat, "register_entry"):
                        # v3.5.11: Pass invested for fee-aware time exit
                        try:
                            strat.register_entry(sibling.id, sibling.market_condition_id, sibling.entry_price, sibling.invested)
                        except TypeError:
                            strat.register_entry(sibling.id, sibling.market_condition_id, sibling.entry_price)
                    logger.info("PAIR SIBLING: %s @ %.4f | $%.2f",
                                sibling.side.value, sibling.entry_price, sibling.invested)
            except InsufficientFundsError as e:
                # Rollback: remove position from DB (it was already inserted)
                logger.warning("Signal rejected (insufficient funds): %s | %s", signal.strategy_name, e)
                await self.position_repo.close_position(pos.id)
                await self.signal_repo.log_signal(signal, executed=False, rejected_reason=f"insufficient_funds: {e}")
                return

            await self.signal_repo.log_signal(signal, executed=True)
            # v3.3.0: Use record_entry() for rate limit (was record_trade(strategy, 0)
            # which double-counted rate limit on entry + close)
            self.risk.record_entry(strat.name)

            # Strategy hook
            if hasattr(strat, "register_entry"):
                # v3.5.11: Pass invested for fee-aware time exit (momentum)
                try:
                    strat.register_entry(pos.id, pos.market_condition_id, pos.entry_price, pos.invested)
                except TypeError:
                    # Backward compat: strategy doesn't accept invested param
                    strat.register_entry(pos.id, pos.market_condition_id, pos.entry_price)

            # Update bankroll + refresh cached positions
            invested = await self.position_repo.total_invested()
            await self.wallet.set_bankroll(self.wallet.cash + invested)
            self._cached_open_positions = await self.position_repo.get_open_positions()

            await self.alerter.notify_trade(
                pos.side.value, pos.entry_price, pos.invested,
                signal.confidence, market.question, pos.strategy,
            )
        finally:
            self.wallet.release(required_cash)

    async def _manage_positions(self) -> None:
        """Check open positions for resolution, TP/SL.

        v3.4.3 FIX: When market is not in active scan (market_map returns None),
        fetch it from Gamma API to check if it's resolved. Previously, resolved
        markets dropped out of scan (scanner queries active=true only) and positions
        stayed open FOREVER — locking cash indefinitely.
        """
        # v3.4.0 FIX (BUG-C7): Use cached positions
        positions = self._cached_open_positions
        if not positions:
            return

        market_map = {m.condition_id: m for m in self._markets}

        for pos in positions[:]:
            # v3.5.13: Skip PENDING positions — cannot exit until on-chain confirmed
            # This prevents "exit fails, position stuck" bug in live trading
            # v3.5.13 FIX: Auto-confirm if pending > 10s (safety net for failed asyncio tasks)
            if self._is_position_pending(pos.id):
                if time.time() - pos.opened_at > 10:
                    self._pending_positions.discard(pos.id)
                    logger.warning("Position %s auto-confirmed (PENDING > 10s safety timeout)", pos.id[:8])
                else:
                    continue

            market = market_map.get(pos.market_condition_id)

            # v3.4.3 FIX: If market not in active scan, fetch from Gamma API
            # This handles resolved markets that dropped out of scan (active=true filter)
            if market is None:
                try:
                    market = await self.scanner.fetch_market(pos.market_condition_id)
                    if market and market.is_closed:
                        logger.info(
                            "Position %s market resolved (was not in active scan): %s",
                            pos.id, pos.market_question[:40],
                        )
                except Exception as e:
                    logger.debug("Failed to fetch market %s: %s", pos.market_condition_id[:8], e)

            # Resolution check — real, not fake
            if market and market.is_closed:
                winner = get_winning_side(market)
                if winner is not None:
                    trade = await self.executor.resolve_position(pos, winner.value)
                    await self._close_position(pos, trade, strat_name=pos.strategy)
                    continue

            # TP/SL check via strategy
            strat = self._find_strategy(pos.strategy)
            if strat and market and hasattr(strat, "check_exit"):
                # Get current price from CLOB WS
                current = self.clob_feed.get_price(pos.token_id)
                if current > 0:
                    should_exit, exit_reason = strat.check_exit(pos.id, pos.market_condition_id, current)
                    if should_exit:
                        trade = await self.executor.close_position(pos, current, exit_reason,
                                                                     market_volume_24h=market.volume_24h if market else 0)
                        await self._close_position(pos, trade, strat_name=pos.strategy)

            # v3.5.5 FIX (P1-05): Force-close stale positions to free cash.
            # Two-tier logic:
            #   - 30 min (max_position_age_sec): close ALL stale positions (default)
            #   - 15 min + 0% PnL (dead_position_age_sec): close "dead" positions sooner
            #     These are positions in nearly-resolved markets where price hasn't moved
            #     at all — they just trap cash without any profit potential.
            pos_age_sec = time.time() - pos.opened_at
            max_age = getattr(self, 'max_position_age_sec', 1800)
            dead_age = getattr(self, 'dead_position_age_sec', 900)  # 15 min

            if pos_age_sec > max_age:
                # Standard stale close (was 1.0h, now 30 min)
                current = self.clob_feed.get_price(pos.token_id) if self.clob_feed else 0
                if current <= 0:
                    current = pos.entry_price
                trade = await self.executor.close_position(pos, current, "Force-close stale ({:.1f}h)".format(pos_age_sec/3600),
                                                             market_volume_24h=market.volume_24h if market else 0)
                await self._close_position(pos, trade, strat_name=pos.strategy)
                logger.info("STALE CLOSE: %s age=%.1fh price=%.4f", pos.id[:8], pos_age_sec/3600, current)
                continue  # Skip further processing since position was closed

            if pos_age_sec > dead_age:
                # v3.5.5: Dead position check — if PnL is exactly 0%, market is "dead"
                # (entry_price == current_price means no movement, likely nearly-resolved)
                current = self.clob_feed.get_price(pos.token_id) if self.clob_feed else 0
                if current > 0 and abs(current - pos.entry_price) < 0.001:
                    trade = await self.executor.close_position(pos, current, "Force-close dead (0% PnL, {:.1f}h)".format(pos_age_sec/3600),
                                                                 market_volume_24h=market.volume_24h if market else 0)
                    await self._close_position(pos, trade, strat_name=pos.strategy)
                    logger.info("DEAD CLOSE: %s age=%.1fh entry=%.4f current=%.4f (no movement)",
                                pos.id[:8], pos_age_sec/3600, pos.entry_price, current)
                    continue

    # v3.5.13: Live-realism callback methods

    def _auto_tune_from_history(self) -> None:
        """v3.5.16: Auto-tune from master_history.db with decay weighting.

        Priority: master_history.db (merged archives) > trade_archive/*.db (single)
        Decay weighting: trades within 7 days get 1.0x, 14d 0.7x, 30d 0.4x, older 0.2x.
        Filter by instance label to avoid cross-contamination.
        """
        try:
            import time as _time
            now = _time.time()
            DAY = 86400
            instance_label = os.environ.get("TG_INSTANCE_LABEL", os.environ.get("BOT_MODE", "Cipher"))
            
            master_path = Path("data/master_history.db")
            
            # Try master DB first, fall back to single archive
            if master_path.exists():
                db = sqlite3.connect(str(master_path))
                db.row_factory = sqlite3.Row
                # Filter by this instance
                rows = db.execute(
                    "SELECT * FROM trades WHERE instance = ? ORDER BY closed_at",
                    (instance_label,)
                ).fetchall()
                db.close()
                if len(rows) < 20:
                    logger.info("Auto-tune: only %d trades in master DB for %s — using ALL instances",
                               len(rows), instance_label)
                    db = sqlite3.connect(str(master_path))
                    db.row_factory = sqlite3.Row
                    rows = db.execute("SELECT * FROM trades ORDER BY closed_at").fetchall()
                    db.close()
                else:
                    logger.info("Auto-tune: master DB has %d trades for %s", len(rows), instance_label)
            else:
                # Legacy: single archive
                archive_dir = Path("data/trade_archive")
                if not archive_dir.exists():
                    logger.info("Auto-tune: no archive or master DB — first run, skipping")
                    return
                archives = sorted(archive_dir.glob("cipher_v3_*.db"), reverse=True)
                if not archives:
                    logger.info("Auto-tune: no archives found — first run, skipping")
                    return
                db = sqlite3.connect(str(archives[0]))
                db.row_factory = sqlite3.Row
                try:
                    rows = db.execute("SELECT * FROM trades ORDER BY closed_at").fetchall()
                except Exception:
                    logger.info("Auto-tune: no trades table in archive — skipping")
                    db.close()
                    return
                db.close()

            if len(rows) < 20:
                logger.info("Auto-tune: only %d trades — need 20+ for tuning", len(rows))
                return

            # v3.5.16: Decay weighting
            trades_raw = [dict(r) for r in rows]
            total = len(trades_raw)
            
            # Compute weights
            weights = []
            for t in trades_raw:
                age_days = (now - t.get("closed_at", now)) / DAY
                if age_days < 7:
                    weights.append(1.0)
                elif age_days < 14:
                    weights.append(0.7)
                elif age_days < 30:
                    weights.append(0.4)
                else:
                    weights.append(0.2)
            
            total_weight = sum(weights)
            
            # Weighted stats
            weighted_wins = sum(w for tr, w in zip(trades_raw, weights) if tr["pnl_dollar"] > 0)
            wr = weighted_wins / total_weight * 100 if total_weight > 0 else 0
            weighted_pnl = sum(tr["pnl_dollar"] * w for tr, w in zip(trades_raw, weights))
            
            logger.info("Auto-tune: analyzing %d trades (weighted %.0f, WR=%.1f%%, wPnL=$%.2f)",
                        total, total_weight, wr, weighted_pnl)

            # --- 1. Weighted entry price analysis ---
            price_buckets = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0, "weight": 0.0})
            for tr, w in zip(trades_raw, weights):
                bucket = round(tr["entry_price"] * 10) / 10
                b = f"{bucket:.1f}-{bucket+0.1:.1f}"
                price_buckets[b]["trades"] += 1
                price_buckets[b]["pnl"] += tr["pnl_dollar"] * w
                price_buckets[b]["weight"] += w
                if tr["pnl_dollar"] > 0:
                    price_buckets[b]["wins"] += 1

            sorted_prices = sorted(
                [(b, v) for b, v in price_buckets.items() if v["trades"] >= 5],
                key=lambda x: -x[1]["pnl"]
            )
            if len(sorted_prices) >= 3:
                top3 = sorted_prices[:3]
                all_los = [float(b.split("-")[0]) for b, _ in top3]
                all_his = [float(b.split("-")[1]) for b, _ in top3]
                best_price_lo = min(all_los)
                best_price_hi = max(all_his)
                best_price_label = f"{best_price_lo:.1f}-{best_price_hi:.1f}"
            elif sorted_prices:
                best_price_lo = float(sorted_prices[0][0].split("-")[0])
                best_price_hi = float(sorted_prices[0][0].split("-")[1])
                best_price_label = sorted_prices[0][0]
            else:
                best_price_lo = None
                best_price_hi = None
                best_price_label = "N/A"

            # --- 2. Weighted hold time analysis ---
            hold_buckets = defaultdict(lambda: {"trades": 0, "pnl": 0.0})
            for tr, w in zip(trades_raw, weights):
                hold_sec = tr["closed_at"] - tr["opened_at"]
                if hold_sec < 60:
                    b = "0-1min"
                elif hold_sec < 180:
                    b = "1-3min"
                elif hold_sec < 300:
                    b = "3-5min"
                elif hold_sec < 600:
                    b = "5-10min"
                else:
                    b = "10min+"
                hold_buckets[b]["trades"] += 1
                hold_buckets[b]["pnl"] += tr["pnl_dollar"] * w

            best_hold = max(hold_buckets.items(), key=lambda x: x[1]["pnl"])
            hold_map = {"0-1min": 60, "1-3min": 180, "3-5min": 300, "5-10min": 600, "10min+": 1800}
            recommended_hold = hold_map.get(best_hold[0], 300)

            # --- 3. Weighted TP/SL ---
            win_pcts = []
            loss_pcts = []
            for tr, w in zip(trades_raw, weights):
                if tr["invested"] > 0:
                    pct = tr["pnl_dollar"] / tr["invested"] * 100
                    if pct > 0:
                        win_pcts.extend([pct] * max(1, int(w * 10)))  # replicate by weight
                    elif pct < 0:
                        loss_pcts.extend([abs(pct)] * max(1, int(w * 10)))

            win_pcts.sort()
            loss_pcts.sort()
            median_win_pct = win_pcts[len(win_pcts) // 2] if win_pcts else 8.0
            median_loss_pct = loss_pcts[len(loss_pcts) // 2] if loss_pcts else 4.0

            recommended_tp = max(5.0, min(15.0, round(median_win_pct * 0.9, 1)))
            recommended_sl = max(3.0, min(8.0, round(median_loss_pct * 1.2, 1)))

            logger.info("Auto-tune: w-median win=%.1f%%, loss=%.1f%% → TP=%.1f%%, SL=%.1f%%",
                        median_win_pct, median_loss_pct, recommended_tp, recommended_sl)

            # --- Apply to momentum config in-memory ---
            s_conf = self.config.get("strategies", {}).get("momentum", {})
            changes = []

            cur_min = s_conf.get("min_entry_price", 0.10)
            cur_max = s_conf.get("max_entry_price", 0.95)
            if best_price_lo is not None and abs(best_price_lo - cur_min) > 0.05:
                s_conf["min_entry_price"] = best_price_lo
                changes.append(f"min_entry_price: {cur_min} → {best_price_lo}")
            if best_price_hi is not None and abs(best_price_hi - cur_max) > 0.05:
                s_conf["max_entry_price"] = best_price_hi
                changes.append(f"max_entry_price: {cur_max} → {best_price_hi}")

            cur_hold = s_conf.get("max_hold_sec", 300)
            if abs(recommended_hold - cur_hold) > 60:
                s_conf["max_hold_sec"] = recommended_hold
                changes.append(f"max_hold_sec: {cur_hold} → {recommended_hold}")

            cur_tp = s_conf.get("take_profit_pct", 8.0)
            cur_sl = s_conf.get("stop_loss_pct", 4.0)
            if abs(recommended_tp - cur_tp) > 1.0:
                s_conf["take_profit_pct"] = recommended_tp
                changes.append(f"take_profit_pct: {cur_tp} → {recommended_tp}")
            if abs(recommended_sl - cur_sl) > 0.5:
                s_conf["stop_loss_pct"] = recommended_sl
                changes.append(f"stop_loss_pct: {cur_sl} → {recommended_sl}")

            for strat in self.strategies:
                if strat.name == "momentum":
                    if hasattr(strat, "take_profit_pct"):
                        strat.take_profit_pct = s_conf.get("take_profit_pct", strat.take_profit_pct)
                    if hasattr(strat, "stop_loss_pct"):
                        strat.stop_loss_pct = s_conf.get("stop_loss_pct", strat.stop_loss_pct)
                    if hasattr(strat, "max_hold_sec"):
                        strat.max_hold_sec = s_conf.get("max_hold_sec", strat.max_hold_sec)
                    if hasattr(strat, "min_entry_price"):
                        strat.min_entry_price = s_conf.get("min_entry_price", strat.min_entry_price)
                    if hasattr(strat, "max_entry_price"):
                        strat.max_entry_price = s_conf.get("max_entry_price", strat.max_entry_price)

            if changes:
                logger.info("Auto-tune: applied %d changes from %d trades (weighted %.0f):",
                           len(changes), total, total_weight)
                for c in changes:
                    logger.info("  ⚙️ %s", c)
            else:
                logger.info("Auto-tune: current config is optimal (no changes needed)")

            # v3.5.16: Log to master DB auto_tune_log
            try:
                if master_path.exists():
                    mdb = sqlite3.connect(str(master_path))
                    mdb.execute("""
                        INSERT INTO auto_tune_log
                        (applied_at, instance, trades_analyzed, win_rate, total_pnl,
                         tp_pct, sl_pct, min_entry, max_entry, hold_sec, changes)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        datetime.now(timezone.utc).isoformat(), instance_label, total,
                        round(wr, 1), round(weighted_pnl, 2),
                        recommended_tp, recommended_sl,
                        best_price_lo or 0.1, best_price_hi or 0.95,
                        recommended_hold, json.dumps(changes)
                    ))
                    mdb.commit()
                    mdb.close()
            except Exception:
                pass

            self._auto_tune_summary = {
                "source": "master_history.db" if master_path.exists() else "archive",
                "trades_analyzed": total,
                "weighted_count": round(total_weight, 0),
                "win_rate": round(wr, 1),
                "total_pnl": round(weighted_pnl, 2),
                "changes_applied": changes,
                "best_entry_bucket": best_price_label,
                "best_hold_bucket": best_hold[0],
                "recommended_tp": recommended_tp,
                "recommended_sl": recommended_sl,
            }

        except Exception as e:
            logger.error("Auto-tune failed (non-fatal): %s", e)
            self._auto_tune_summary = {"error": str(e)}

    def _deduct_gas_fee(self, amount: float) -> None:
        """Deduct gas fee from wallet cash. Called by PaperExecutor."""
        if amount > 0:
            try:
                self.wallet.cash -= amount
                self._total_gas_fees_paid += amount
                logger.debug("Gas fee deducted: $%.4f (total: $%.4f)",
                             amount, self._total_gas_fees_paid)
            except Exception as e:
                logger.error("Gas fee deduction failed: %s", e)

    def _confirm_position(self, pos_id: str) -> None:
        """Mark position as confirmed after on-chain delay.

        Called by PaperExecutor._confirm_position_after_delay() async task.
        Removes position from _pending_positions set, allowing exit logic to proceed.
        """
        self._pending_positions.discard(pos_id)
        logger.debug("Position %s confirmed (removed from pending set)", pos_id)

    def _is_position_pending(self, pos_id: str) -> bool:
        """Check if position is still pending (not yet on-chain confirmed)."""
        return pos_id in self._pending_positions

    async def _close_position(self, pos: Position, trade, strat_name: str) -> None:
        """Close position: persist trade, update wallet, update risk.

        v3.4.0 FIX (BUG-C1): Uses position lock to prevent race conditions
        between concurrent close attempts (e.g., resolution + TP/SL).
        """
        async with self._position_lock:
            # v3.4.0: Check position still exists before closing (optimistic lock)
            still_open = await self.position_repo.close_position(pos.id)
            if still_open is None:
                logger.debug("Position %s already closed (race avoided)", pos.id)
                return
            # Add to trades
            await self.trade_repo.add_trade(trade)
            # Credit cash (invested + pnl)
            await self.wallet.credit(pos.invested + trade.pnl_dollar)
        # Record trade in risk manager (outside lock — no DB)
        # v3.3.0: Use record_close() for pnl/win-loss (was record_trade() which
        # also incremented rate limit counter — causing double-count bug)
        self.risk.record_close(strat_name, trade.pnl_dollar)
        # Strategy hooks
        strat = self._find_strategy(strat_name)
        if strat:
            if trade.pnl_dollar > 0:
                strat.trades_won += 1
            else:
                strat.trades_lost += 1
            strat.total_pnl += trade.pnl_dollar
            if hasattr(strat, "record_result"):
                strat.record_result(trade.pnl_dollar > 0)
            if hasattr(strat, "clear_position"):
                strat.clear_position(pos.id, pos.market_condition_id)
        # Update bankroll + refresh cached positions
        invested = await self.position_repo.total_invested()
        await self.wallet.set_bankroll(self.wallet.cash + invested)
        self._cached_open_positions = await self.position_repo.get_open_positions()
        # Alert
        await self.alerter.notify_trade_close(
            strat_name, pos.side.value, trade.pnl_dollar, trade.reason,
        )

    async def _update_position_values(self) -> None:
        """Update current_price/current_value for open positions from CLOB WS."""
        positions = await self.position_repo.get_open_positions()
        for pos in positions:
            current = self.clob_feed.get_price(pos.token_id)
            if current > 0:
                current_value = pos.shares * current
                await self.position_repo.update_current_value(pos.id, current, current_value)

    def _find_strategy(self, name: str):
        if not name:
            return None
        for s in self.strategies:
            if s.name == name:
                return s
        # Debug: strategy name not found (might be from before restart)
        logger.debug("Strategy not found: %s (active: %s)", name, [s.name for s in self.strategies])
        return None

    def _get_stats(self) -> dict[str, Any]:
        """Return cached stats snapshot, with uptime computed fresh."""
        if not self._stats_cache:
            stats = self._build_stats_sync()
        else:
            # Return cached stats but always compute uptime fresh
            stats = dict(self._stats_cache)
        # Always compute uptime fresh (cache might be stale)
        stats["uptime_sec"] = int(time.time() - self._start_time) if self._start_time else 0
        return stats

    def _build_stats_sync(self) -> dict[str, Any]:
        """Build minimal stats without DB access (fallback for cold start).

        v3.4.4 FIX (Arena audit): Added default values for trades/wins/losses/signals
        so Prometheus metrics and API don't show 0 before _refresh_stats_loop runs.
        """
        snap = self.wallet.snapshot()
        
        # v3.5.12: Auto-archive when micro-cap instance ($10) reaches $25 target
        if (self.wallet.bankroll >= 25.0 and self.wallet.initial_bankroll <= 15.0 
            and not getattr(self, "_auto_archived_at_25", False)):
            self._auto_archived_at_25 = True
            import subprocess
            subprocess.run(["/usr/local/bin/python", "scripts/archive_trades.py"], 
                         cwd="/app", capture_output=True)
            logger.info("AUTO-ARCHIVE: Micro-cap BR=$%.2f (from $%.0f) reached $25 — trades saved",
                       self.wallet.bankroll, self.wallet.initial_bankroll)
        
        snap["mode"] = self.config.get("bot", {}).get("mode", "paper")
        from collections import Counter
        cat_counts = Counter(m.classify() for m in self._markets)
        snap["markets"] = len(self._markets)
        snap["crypto_markets"] = len([m for m in self._markets if m.is_crypto_up_down])
        snap["strategies"] = [s.stats() for s in self.strategies]
        snap["market_categories"] = dict(cat_counts)
        snap["risk"] = {**self.risk.stats, "config": self.risk.config}
        snap["btc_price"] = self.binance_feed.get_price("BTC")
        snap["btc_move"] = round(self.binance_feed.get_pct_move("BTC", 60), 4)
        snap["uptime_sec"] = int(time.time() - self._start_time) if self._start_time else 0
        snap["ws_status"] = {
            "clob_connected": self.clob_feed.connected,
            "clob_tokens": len(self.clob_feed.books),
            "clob_reconnects": self.clob_feed.reconnect_count,
            "binance_connected": self.binance_feed.connected,
            "binance_reconnects": self.binance_feed.reconnect_count,
        }
        # v3.4.4: Default values for cold start (before _refresh_stats_loop populates from DB)
        snap["trades"] = 0
        snap["wins"] = 0
        snap["losses"] = 0
        snap["win_rate"] = 0.0
        snap["signals"] = 0
        snap["arbs"] = 0
        snap["deployed"] = 0.0
        snap["open_positions"] = []
        snap["recent_trades"] = []
        # v3.5.0: Bot status + version (Arena.ai recommendation)
        # v3.5.5: Use __version__ from package instead of hardcoded string
        from . import __version__
        snap["version"] = __version__
        snap["tier"] = self._tier_manager.stats()
        snap["bot_status"] = "STARTING"
        snap["last_signal_at"] = None
        snap["last_trade_at"] = None
        return snap

    async def _refresh_stats_loop(self) -> None:
        """Background task: refresh stats cache every 3s with DB data.

        Includes wallet invariant check (BUG-1 fix): bankroll must == cash + invested.
        If inconsistent, log error and recalculate from DB truth.
        """
        while self._running:
            try:
                stats = self._build_stats_sync()
                # Enrich with DB data
                open_positions = await self.position_repo.get_open_positions()
                stats["open_positions"] = [
                    {
                        "id": p.id,
                        "market_condition_id": p.market_condition_id,
                        "market_question": p.market_question[:60],
                        "side": p.side.value,
                        "strategy": p.strategy,
                        "entry_price": p.entry_price,
                        "invested": p.invested,
                        "current_price": p.current_price,
                        "current_value": p.current_value,
                        "opened_at": p.opened_at,
                        "is_pair": p.is_pair,
                    }
                    for p in open_positions
                ]
                recent_trades = await self.trade_repo.get_recent_trades(limit=20)
                stats["recent_trades"] = [
                    {
                        "id": t.id,
                        "market_question": t.market_question[:60],
                        "side": t.side.value,
                        "entry_price": t.entry_price,
                        "exit_price": t.exit_price,
                        "pnl_dollar": t.pnl_dollar,
                        "pnl_percent": t.pnl_percent,
                        "strategy": t.strategy,
                        "reason": t.reason[:40] if t.reason else "",
                        "closed_at": t.closed_at,
                    }
                    for t in recent_trades
                ]
                recent_signals = await self.signal_repo.get_recent_signals(limit=20)
                stats["recent_signals"] = [
                    {"strategy": s.get("strategy", ""), "side": s.get("side", ""),
                     "suggested_price": s.get("suggested_price", 0),
                     "confidence": s.get("confidence", 0),
                     "suggested_size_usd": s.get("suggested_size_usd", 0),
                     "executed": bool(s.get("executed", 0)),
                     "rejected_reason": s.get("rejected_reason", ""),
                     "timestamp": s.get("timestamp", 0)}
                    for s in recent_signals
                ]
                trade_stats = await self.trade_repo.stats()
                stats["trades"] = trade_stats["total_trades"]
                stats["wins"] = trade_stats["wins"]
                stats["losses"] = trade_stats["losses"]
                stats["win_rate"] = trade_stats["win_rate"]
                stats["signals"] = await self.trade_repo.total_signals_count()
                stats["arbs"] = 0

                # v3.4.4 FIX (Kimi+Arena audit): Override ALL strategy stats from DB
                # In-memory counters reset to 0 on every restart — DB is source of truth
                db_signal_counts = await self.trade_repo.signals_count_per_strategy()
                db_strat_stats = await self.trade_repo.per_strategy_stats()
                db_strat_map = {s["name"]: s for s in db_strat_stats}
                for s in stats["strategies"]:
                    s["signals_emitted"] = db_signal_counts.get(s["name"], 0)
                    if s["name"] in db_strat_map:
                        db = db_strat_map[s["name"]]
                        s["trades"] = db["trades"]
                        s["wins"] = db["wins"]
                        s["losses"] = db["losses"]
                        s["win_rate"] = db["win_rate"]
                        s["pnl"] = db["pnl"]

                    # v3.5.2: Add latency_arb debug stats (MASALAH-6 fix)
                    for strat in self.strategies:
                        if hasattr(strat, 'get_debug_stats') and strat.name == 'latency_arb':
                            lat_stats = strat.get_debug_stats()
                            stats['latency_arb_debug'] = lat_stats
                            break
                    # v3.5.16: Add momentum debug stats for diagnostics
                    for strat in self.strategies:
                        if hasattr(strat, 'get_debug_stats') and strat.name == 'momentum':
                            stats['momentum_debug'] = strat.get_debug_stats()
                            break

# WALLET INVARIANT CHECK (BUG-1 fix):
                # bankroll MUST == cash + total_invested. If not, recalculate from DB truth.
                invested = await self.position_repo.total_invested()
                expected_bankroll = round(self.wallet.cash + invested, 4)
                cached_bankroll = round(self.wallet.bankroll, 4)
                if abs(expected_bankroll - cached_bankroll) > 0.01:
                    logger.error(
                        "WALLET INVARIANT VIOLATION: cash=%.4f + invested=%.4f = %.4f, but bankroll=%.4f (diff=%.4f). Recalculating.",
                        self.wallet.cash, invested, expected_bankroll, cached_bankroll,
                        expected_bankroll - cached_bankroll,
                    )
                    await self.wallet.set_bankroll(expected_bankroll)

                stats["bankroll"] = expected_bankroll
                stats["cash"] = round(self.wallet.cash, 4)
                stats["pnl"] = round(expected_bankroll - self.wallet.initial_bankroll, 4)
                stats["deployed"] = round(invested, 4)
                stats["last_stats_refresh"] = time.time()

                # v3.5.0: Bot status computation (Arena.ai recommendation)
                # v3.5.5: Use __version__ from package instead of hardcoded string
                from . import __version__
                stats["version"] = __version__
                disabled_strats = stats.get("risk", {}).get("disabled_strategies", [])
                if len(disabled_strats) >= 3:
                    stats["bot_status"] = "STAGNANT"
                elif expected_bankroll > 30 and self.wallet.cash < 1.0 and invested > expected_bankroll * 0.9:
                    stats["bot_status"] = "CASH_STUCK"
                elif len(open_positions) == 0 and trade_stats["total_trades"] == 0:
                    stats["bot_status"] = "IDLE"
                else:
                    stats["bot_status"] = "ACTIVE"

                # v3.5.0: Last activity timestamps
                if recent_trades:
                    stats["last_trade_at"] = recent_trades[0].closed_at
                else:
                    stats["last_trade_at"] = None
                # Last signal from any strategy
                last_sig = 0.0
                for s in self.strategies:
                    for ts in s._last_signal_at.values():
                        if ts > last_sig:
                            last_sig = ts
                stats["last_signal_at"] = last_sig if last_sig > 0 else None

                self._stats_cache = stats
            except Exception as e:
                logger.error("Stats refresh error: %s", e, exc_info=True)
            await asyncio.sleep(3)

    async def _checkpoint_loop(self) -> None:
        """v3.5.5 FIX (P1-03): Periodic WAL checkpoint every 30 minutes.

        Flushes WAL file to main DB to prevent unbounded WAL growth.
        PASSIVE mode is safe — does not block concurrent readers/writers.
        """
        while self._running:
            try:
                await asyncio.sleep(1800)  # 30 min
                await self.db.checkpoint()
                logger.info("WAL checkpoint completed (periodic)")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("WAL checkpoint error: %s", e)

    async def _trigger_wal_checkpoint(self) -> None:
        """v3.5.7: Manual WAL checkpoint trigger via admin API.

        Called by daemon when WAL file > 5MB. Safer than `docker exec sqlite3`
        because bot handles async concurrency properly (no race with running writes).
        """
        await self.db.checkpoint()

    async def _get_trades_paginated(self, page: int = 1, limit: int = 20) -> tuple[list, int]:
        """v3.5.11: Paginated trade history for dashboard.

        Returns (trades_serialized, total_count) for /api/trades endpoint.
        """
        trades, total = await self.trade_repo.get_trades_paginated(page, limit)
        # Serialize to dict for JSON response
        result = []
        for t in trades:
            result.append({
                "id": t.id,
                "market_condition_id": t.market_condition_id,
                "market_question": t.market_question,
                "side": t.side.value,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "shares": t.shares,
                "invested": t.invested,
                "pnl_dollar": t.pnl_dollar,
                "pnl_percent": t.pnl_percent,
                "strategy": t.strategy,
                "reason": t.reason,
                "opened_at": t.opened_at,
                "closed_at": t.closed_at,
                "is_pair": t.is_pair,
                "pair_id": t.pair_id,
            })
        return result, total

    async def _get_db_stats(self, hours: int = 1) -> dict:
        """v3.5.7: Pre-computed DB aggregates for daemon watchdog.

        Returns signal/trade counts and PnL sum for the last `hours` window.
        Used by daemon's SignalStarvationChecker via /api/admin/db_stats endpoint.
        """
        cutoff = time.time() - hours * 3600
        # Signal counts (executed + rejected)
        signals_total = await self.signal_repo.count_since(cutoff)
        signals_executed = await self.signal_repo.count_since(cutoff, executed=True)
        signals_rejected = signals_total - signals_executed
        # Trade counts + PnL
        trades_closed = await self.trade_repo.count_since(cutoff)
        pnl_period = await self.trade_repo.sum_pnl_since(cutoff)
        # Per-strategy signal breakdown
        per_strategy = await self.signal_repo.count_by_strategy_since(cutoff)
        return {
            "window_hours": hours,
            "cutoff_timestamp": cutoff,
            "signals": {
                "total": signals_total,
                "executed": signals_executed,
                "rejected": signals_rejected,
                "rejection_rate": (signals_rejected / signals_total) if signals_total > 0 else 0.0,
                "per_strategy": per_strategy,
            },
            "trades": {
                "closed": trades_closed,
                "pnl_total": round(pnl_period, 4) if pnl_period else 0.0,
            },
        }

    def stop(self) -> None:
        self._running = False


def main() -> None:
    bot = PolyClawCipherV3()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        bot.stop()
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
