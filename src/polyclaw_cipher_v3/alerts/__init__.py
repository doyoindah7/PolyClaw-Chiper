"""Alerts module — STUB (Telegram alerts skipped per user request).

To activate Telegram alerts later:
1. Create bot via @BotFather, get BOT_TOKEN
2. Get CHAT_ID via @userinfobot
3. Add to .env:
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
4. Implement TelegramAlerter class here
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class Alerter:
    """Stub alerter — logs instead of sending notifications."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.enabled = False

    async def notify_startup(self, bankroll: float, strategies: list[str], version: str = "v3") -> None:
        logger.info("ALERT (stub): startup $%.2f, strategies=%s, version=%s",
                    bankroll, strategies, version)

    async def notify_trade(self, side: str, entry_price: float, invested: float,
                           confidence: float, question: str, strategy: str) -> None:
        logger.info("ALERT (stub): %s @ %.4f $%.2f conf=%.2f strat=%s | %s",
                    side, entry_price, invested, confidence, strategy, question[:50])

    async def notify_trade_close(self, strategy: str, side: str, pnl: float, reason: str) -> None:
        logger.info("ALERT (stub): CLOSE %s %s PnL=$%.4f | %s",
                    strategy, side, pnl, reason)

    async def notify_pnl(self, bankroll: float, initial: float, trades: int, win_rate: float) -> None:
        pnl = bankroll - initial
        logger.info("ALERT (stub): PnL $%.2f (%.1f%%) | %d trades | WR=%.1f%%",
                    pnl, (pnl / initial * 100) if initial > 0 else 0, trades, win_rate)

    async def notify_drawdown(self, current_dd: float, max_dd: float) -> None:
        logger.warning("ALERT (stub): Drawdown %.1f%% / max %.1f%%", current_dd, max_dd)

    async def notify_crash(self, error: str) -> None:
        logger.error("ALERT (stub): CRASH %s", error)

    async def close(self) -> None:
        pass
