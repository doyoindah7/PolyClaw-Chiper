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
import logging
import os
import time
from typing import Any

from .alerts import Alerter
from .config import load_config
from .core.binance_ws import BinanceFeed
from .core.clob_ws import CLOBFeed
from .core.event_bus import EventBus
from .core.http_server import HTTPServer
from .core.resolution import get_winning_side, is_truly_resolved
from .core.scanner import MarketScanner
from .core.types import Market, Position, Side, Signal
from .execution.paper import PaperExecutor
from .observability.logs import setup_logging
from .risk.manager import RiskManager
from .risk.sizer import CompoundingSizer
from .state.db import Database
from .state.repository import PositionRepository, SignalRepository, TradeRepository
from .state.wallet import Wallet
from .strategy.atomic_arb import AtomicArbStrategy
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
        self.event_bus = EventBus(queue_size=2000)
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
        self.binance_feed = BinanceFeed(self.event_bus)
        self.clob_feed = CLOBFeed(self.event_bus)

        # Risk + Sizer
        self.risk = RiskManager(self.config.get("risk", {}))
        self.sizer = CompoundingSizer(self.config.get("risk", {}).get("sizer", {}))

        # Executor
        self.executor = PaperExecutor(self.config.get("execution", {}).get("paper", {}))

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
            config={},
        )

        # State
        self._running = False
        self._markets: list[Market] = []
        self._last_scan: float = 0.0
        self._signals_this_cycle: int = 0
        self._start_time: float = 0.0
        self._stats_cache: dict[str, Any] = {}
        self._stats_task: asyncio.Task | None = None

    async def run(self) -> None:
        self._running = True
        self._start_time = time.time()
        logger.info("=== PolyClaw-Cipher v3 starting ===", extra={"event": "startup", "component": "bot"})
        logger.info("Strategies: %s", [s.name for s in self.strategies])

        # Connect DB + load wallet
        await self.db.connect()
        await self.wallet.load()
        self.risk.init(self.wallet.bankroll)

        # Start services
        await self.binance_feed.start()
        await self.clob_feed.start()
        await self.http_server.start()
        # Background stats cache refresher (every 2s)
        self._stats_task = asyncio.create_task(self._refresh_stats_loop(), name="stats_cache")

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

        # Check open positions for resolution / TP/SL
        await self._manage_positions()

        # Update position current values
        await self._update_position_values()

        # Run strategies
        self._signals_this_cycle = 0
        for market in self._markets:
            if not self._running:
                break
            await self._try_strategies(market)

        await asyncio.sleep(loop_interval)

    async def _try_strategies(self, market: Market) -> None:
        # Get fresh open positions
        open_positions = await self.position_repo.get_open_positions()

        # Risk check (global)
        can_trade, reason = self.risk.can_trade("global", self.wallet.bankroll)
        if not can_trade and self._signals_this_cycle == 0:
            return

        context = {
            "open_positions": open_positions,
            "binance_feed": self.binance_feed,
            "clob_feed": self.clob_feed,
            "bankroll": self.wallet.bankroll,
            "cash": self.wallet.cash,
            "sizer": self.sizer,
            "strategy_cap_pct": 0.25,  # Default, overridden per-strategy below
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
                    await self._execute_signal(signal, market, strat)
            except Exception as e:
                logger.warning("Strategy %s error on %s: %s", strat.name, market.condition_id[:8], e)

    async def _execute_signal(self, signal: Signal, market: Market, strat: Any) -> None:
        # Final risk check
        can_trade, reason = self.risk.can_trade(strat.name, self.wallet.bankroll)
        if not can_trade:
            await self.signal_repo.log_signal(signal, executed=False, rejected_reason=reason)
            logger.warning("Signal blocked: %s", reason)
            return

        # Execute (async, non-blocking)
        pos = await self.executor.execute_entry(signal, market.question, self.wallet.bankroll)
        if pos is None:
            await self.signal_repo.log_signal(signal, executed=False, rejected_reason="fill_rejected")
            return

        # Persist
        await self.position_repo.open_position(pos)
        await self.wallet.debit(pos.invested)

        # FIX: Handle pair sibling (atomic_arb creates 2 legs)
        sibling = self.executor.take_pair_sibling()
        if sibling:
            await self.position_repo.open_position(sibling)
            await self.wallet.debit(sibling.invested)
            # Register sibling entry in strategy
            if hasattr(strat, "register_entry"):
                strat.register_entry(sibling.id, sibling.market_condition_id, sibling.entry_price)
            logger.info("PAIR SIBLING: %s @ %.4f | $%.2f",
                        sibling.side.value, sibling.entry_price, sibling.invested)

        await self.signal_repo.log_signal(signal, executed=True)
        # v3.3.0: Use record_entry() for rate limit (was record_trade(strategy, 0)
        # which double-counted rate limit on entry + close)
        self.risk.record_entry(strat.name)

        # Strategy hook
        if hasattr(strat, "register_entry"):
            strat.register_entry(pos.id, pos.market_condition_id, pos.entry_price)

        # Update bankroll
        invested = await self.position_repo.total_invested()
        await self.wallet.set_bankroll(self.wallet.cash + invested)

        await self.alerter.notify_trade(
            pos.side.value, pos.entry_price, pos.invested,
            signal.confidence, market.question, pos.strategy,
        )

    async def _manage_positions(self) -> None:
        """Check open positions for resolution, TP/SL."""
        positions = await self.position_repo.get_open_positions()
        if not positions:
            return

        market_map = {m.condition_id: m for m in self._markets}

        for pos in positions[:]:
            market = market_map.get(pos.market_condition_id)

            # Refresh market from API if we have it but it's potentially stale
            # (Resolution check — real, not fake)
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
                        trade = await self.executor.close_position(pos, current, exit_reason)
                        await self._close_position(pos, trade, strat_name=pos.strategy)

    async def _close_position(self, pos: Position, trade, strat_name: str) -> None:
        """Close position: persist trade, update wallet, update risk."""
        # Remove from open positions
        await self.position_repo.close_position(pos.id)
        # Add to trades
        await self.trade_repo.add_trade(trade)
        # Credit cash (invested + pnl)
        await self.wallet.credit(pos.invested + trade.pnl_dollar)
        # Record trade in risk manager
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
        # Update bankroll
        invested = await self.position_repo.total_invested()
        await self.wallet.set_bankroll(self.wallet.cash + invested)
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
        """Build minimal stats without DB access (fallback)."""
        snap = self.wallet.snapshot()
        snap["mode"] = self.config.get("bot", {}).get("mode", "paper")
        snap["markets"] = len(self._markets)
        snap["crypto_markets"] = len([m for m in self._markets if m.is_crypto_up_down])
        snap["strategies"] = [s.stats() for s in self.strategies]
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
                trade_stats = await self.trade_repo.stats()
                stats["trades"] = trade_stats["total_trades"]
                stats["wins"] = trade_stats["wins"]
                stats["losses"] = trade_stats["losses"]
                stats["win_rate"] = trade_stats["win_rate"]
                stats["signals"] = await self.trade_repo.recent_signals_count(time.time() - 3600)
                stats["arbs"] = 0

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
                self._stats_cache = stats
            except Exception as e:
                logger.error("Stats refresh error: %s", e, exc_info=True)
            await asyncio.sleep(3)

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
