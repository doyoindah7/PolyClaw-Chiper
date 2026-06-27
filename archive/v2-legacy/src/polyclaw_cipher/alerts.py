"""Telegram alerts — stub implementation (no bot token configured)."""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


class TelegramAlerter:
    """Stub alerter — logs instead of sending Telegram messages."""

    def __init__(self, *args, **kwargs):
        pass

    async def notify_startup(self, bankroll: float, strategies: list[str]):
        logger.info("Telegram stub: startup $%.2f, strategies=%s", bankroll, strategies)

    async def notify_trade(self, side: str, entry_price: float, invested: float,
                           confidence: float, question: str, strategy: str):
        logger.info("Telegram stub: %s @ %.4f $%.2f conf=%.2f | %s",
                     side, entry_price, invested, confidence, question[:50])

    async def notify_pnl(self, bankroll: float, initial: float,
                         trades: int, win_rate: float):
        pnl = bankroll - initial
        logger.info("Telegram stub: PnL $%.2f (%.1f%%) | %d trades | WR=%.1f%%",
                     pnl, (pnl / initial * 100) if initial > 0 else 0,
                     trades, win_rate)

    async def close(self):
        pass
