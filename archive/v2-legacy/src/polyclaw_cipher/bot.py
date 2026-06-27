"""PolyClaw-Cipher v2.0 - Improved orchestrator.

Changes:
- Telegram alerts integration
- Improved risk management with daily auto-reset
- Streak tracking for scalper
- Better stats collection
"""
from __future__ import annotations
import asyncio
import logging
import os
import sys
import time
from typing import Any
from .config import load_config
from .core.scanner import MarketScanner
from .core.price_feed import PriceFeed
from .core.clob_feed import CLOBFeed
from .core.http_server import HTTPServer
from .core.types import Market, Position, Side, Signal
from .strategy.scalper import CryptoScalper
from .strategy.universal import UniversalScalper
from .strategy.arbitrage import Arbitrage101
from .strategy.momentum import MomentumHunter
from .execution.paper import PaperExecutor
from .risk.sizer import CompoundingSizer
from .risk.limits import DrawdownLimiter
from .state.wallet import Wallet
from .alerts import TelegramAlerter

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("polyclaw-cipher")


class PolyClawCipher:
    def __init__(self):
        self.config = load_config()
        self.wallet = Wallet("data/wallet.json")
        self.scanner = MarketScanner(
            min_volume=self.config.get("market", {}).get("min_volume_24h_usd", 500),
            page_size=self.config.get("market", {}).get("api_page_size", 500),
            max_pages=self.config.get("market", {}).get("max_pages", 3),
        )
        self.price_feed = PriceFeed()
        self.clob_feed = CLOBFeed(poll_interval_sec=3.0)  # Slightly slower for t2.small
        self.executor = PaperExecutor(self.config.get("execution", {}).get("paper", {}))
        self.sizer = CompoundingSizer(self.config.get("risk", {}))
        self.dd_limiter = DrawdownLimiter(self.config.get("risk", {}))
        self.alerter = TelegramAlerter()

        # Strategies
        s_conf = self.config.get("strategies", {})
        self.strategies: list[Any] = []
        if s_conf.get("scalper", {}).get("enabled", True):
            self.strategies.append(CryptoScalper(s_conf.get("scalper", {})))
        if s_conf.get("universal", {}).get("enabled", True):
            uni = UniversalScalper(s_conf.get("universal", {}))
            uni.set_clob_feed(self.clob_feed)
            self.strategies.append(uni)
        if s_conf.get("arbitrage", {}).get("enabled", False):
            self.strategies.append(Arbitrage101(s_conf.get("arbitrage", {})))
        if s_conf.get("momentum", {}).get("enabled", False):
            self.strategies.append(MomentumHunter(s_conf.get("momentum", {})))

        self.http_server = HTTPServer(
            host=self.config.get("monitoring", {}).get("web", {}).get("host", "0.0.0.0"),
            port=self.config.get("monitoring", {}).get("web", {}).get("port", 8080),
            get_stats=self._get_stats,
        )

        self._running = False
        self._markets: list[Market] = []
        self._last_scan: float = 0.0
        self._signals_this_cycle: int = 0
        self._last_pnl_alert_check: float = 0.0

    async def run(self):
        self._running = True
        logger.info("=== PolyClaw-Cipher v2.0 starting ===")
        logger.info("Strategies: %s", [s.name for s in self.strategies])
        logger.info("Bankroll: $%.2f (initial: $%.2f)",
                     self.wallet.bankroll, self.wallet.initial_bankroll)

        self.dd_limiter.init(self.wallet.bankroll)

        # Start services
        await self.price_feed.start()
        await self.clob_feed.start()
        await self.http_server.start()

        # Startup notification
        await self.alerter.notify_startup(
            self.wallet.bankroll,
            [s.name for s in self.strategies]
        )

        scan_interval = self.config.get("bot", {}).get("scan_interval_sec", 15)
        loop_interval = self.config.get("bot", {}).get("loop_interval_sec", 2)

        try:
            while self._running:
                try:
                    await self._loop(scan_interval, loop_interval)
                except Exception as e:
                    logger.error("Loop error: %s", e, exc_info=True)
                    await asyncio.sleep(5)
        finally:
            await self.price_feed.stop()
            await self.clob_feed.stop()
            await self.http_server.stop()
            await self.alerter.close()
            await self.scanner.close()
            logger.info("=== PolyClaw-Cipher v2.0 stopped ===")

    async def _loop(self, scan_interval: float, loop_interval: float):
        now = time.time()

        # Scan markets
        if now - self._last_scan >= scan_interval or not self._markets:
            logger.info("Scanning markets...")
            self._markets = await self.scanner.scan()
            self._last_scan = now
            self.wallet.set_last_scan(now)
            crypto_markets = [m for m in self._markets if m.is_crypto_up_down]
            logger.info("Markets: %d total, %d crypto Up/Down",
                        len(self._markets), len(crypto_markets))

            # Track top markets by volume in CLOB feed (max 30 for t2.small)
            top_markets = sorted(self._markets, key=lambda m: m.volume_24h, reverse=True)[:30]
            for m in top_markets:
                self.clob_feed.track(m.yes_token_id, m.condition_id, "YES")
                self.clob_feed.track(m.no_token_id, m.condition_id, "NO")

        # Check open positions for resolution
        await self._manage_positions()

        # Run strategies
        self._signals_this_cycle = 0
        crypto_count = 0
        for market in self._markets:
            if not self._running:
                break
            if market.is_crypto_up_down:
                crypto_count += 1
            await self._try_strategies(market)

        # Periodic P&L alert check (every 5 minutes)
        if now - self._last_pnl_alert_check >= 300:
            self._last_pnl_alert_check = now
            snap = self.wallet.snapshot()
            await self.alerter.notify_pnl(
                snap["bankroll"], self.wallet.initial_bankroll,
                snap["trades"], snap.get("win_rate", 0)
            )

        # Heartbeat
        self.wallet.update_heartbeat()

        await asyncio.sleep(loop_interval)

    async def _try_strategies(self, market: Market):
        open_positions = [
            Position(**p) if isinstance(p, dict) else p
            for p in self.wallet.open_positions
        ]

        # Risk check
        can_trade, reason = self.dd_limiter.can_trade(self.wallet.bankroll)
        if not can_trade and self._signals_this_cycle == 0:
            if self._signals_this_cycle == 0:
                pass  # Silent when no signals
            return

        context = {
            "open_positions": open_positions,
            "price_feed": self.price_feed,
            "bankroll": self.wallet.bankroll,
            "cash": self.wallet.cash,
        }

        for strat in self.strategies:
            try:
                signal = await strat.evaluate(market, context)
                if signal:
                    self._signals_this_cycle += 1
                    self.wallet.update_stats("signals_emitted")
                    await self._execute_signal(signal, market)
            except Exception as e:
                logger.warning("Strategy %s error on %s: %s", strat.name, market.condition_id[:8], e)

    async def _execute_signal(self, signal: Signal, market: Market):
        can_trade, reason = self.dd_limiter.can_trade(self.wallet.bankroll)
        if not can_trade:
            logger.warning("Signal blocked: %s", reason)
            return

        notional = signal.suggested_size_usd
        max_notional = self.wallet.bankroll * 0.35  # 35% max per trade (aggressive)
        notional = min(notional, max_notional)
        notional = min(notional, self.wallet.cash * 0.90)  # Leave 10% buffer

        if notional < 2.5:
            logger.info("Skip signal: notional $%.2f < $2.50 min (cash=$%.2f)", notional, self.wallet.cash)
            return

        signal = signal.model_copy(update={"suggested_size_usd": notional})

        pos = self.executor.execute_entry(signal, market.question, self.wallet.bankroll)
        if pos:
            self.wallet.open_position(pos.model_dump())
            self.dd_limiter.record_trade(0)

            strat = self._find_strategy(pos.strategy)
            if strat and hasattr(strat, 'register_entry'):
                strat.register_entry(pos.id, pos.market_condition_id, pos.entry_price)

            # Telegram alert for trade
            await self.alerter.notify_trade(
                pos.side.value, pos.entry_price, pos.invested,
                signal.confidence, market.question, pos.strategy
            )

    async def _manage_positions(self):
        """Check open positions for resolution or exit."""
        positions = self.wallet.open_positions
        if not positions:
            return

        market_map = {m.condition_id: m for m in self._markets}

        for pos_data in positions[:]:
            pos = Position(**pos_data) if isinstance(pos_data, dict) else pos_data
            market = market_map.get(pos.market_condition_id)

            if market:
                current = market.yes_price if pos.side == Side.YES else market.no_price

                # Market resolved
                sec_to_close = market.seconds_to_close
                if sec_to_close <= 0:
                    if market.crypto_asset:
                        pct = self.price_feed.get_pct_move(market.crypto_asset)
                        winner = "YES" if pct >= 0 else "NO"
                    else:
                        winner = "YES" if market.yes_price > market.no_price else "NO"

                    trade = self.executor.resolve_position(pos, winner)
                    self.wallet.close_position(pos.id, trade.model_dump())
                    self.dd_limiter.record_trade(trade.pnl_dollar)

                    won = trade.pnl_dollar > 0
                    strat = self._find_strategy(pos.strategy)
                    if strat:
                        if won:
                            strat.trades_won += 1
                        else:
                            strat.trades_lost += 1
                        strat.total_pnl += trade.pnl_dollar
                        if hasattr(strat, 'record_result'):
                            strat.record_result(won)
                        if hasattr(strat, 'clear_position'):
                            strat.clear_position(pos.id, pos.market_condition_id)
                    continue

                # Check TP/SL
                strat = self._find_strategy(pos.strategy)
                if strat and hasattr(strat, 'check_exit'):
                    should_exit, exit_reason = strat.check_exit(
                        pos.id, pos.market_condition_id, current
                    )
                    if should_exit:
                        trade = self.executor.close_position(pos, current, exit_reason)
                        self.wallet.close_position(pos.id, trade.model_dump())
                        self.dd_limiter.record_trade(trade.pnl_dollar)

                        won = trade.pnl_dollar > 0
                        if strat:
                            if won:
                                strat.trades_won += 1
                            else:
                                strat.trades_lost += 1
                            strat.total_pnl += trade.pnl_dollar
                            if hasattr(strat, 'record_result'):
                                strat.record_result(won)
                            if hasattr(strat, 'clear_position'):
                                strat.clear_position(pos.id, pos.market_condition_id)
                        logger.info("EXIT: %s %s | %s | PnL=$%.2f",
                                    pos.strategy.upper(), pos.side.value,
                                    exit_reason, trade.pnl_dollar)

    def _find_strategy(self, name: str):
        for s in self.strategies:
            if s.name == name:
                return s
        return None

    def _get_stats(self) -> dict[str, Any]:
        snap = self.wallet.snapshot()
        snap["mode"] = self.config.get("bot", {}).get("mode", "paper")
        snap["markets"] = len(self._markets)
        snap["crypto_markets"] = len([m for m in self._markets if m.is_crypto_up_down])
        snap["strategies"] = [s.stats() for s in self.strategies]
        snap["risk"] = self.dd_limiter.stats
        snap["btc_price"] = self.price_feed.get_price("BTC")
        snap["btc_move"] = round(self.price_feed.get_pct_move("BTC"), 4)
        return snap

    def stop(self):
        self._running = False


def main():
    bot = PolyClawCipher()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        bot.stop()
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
