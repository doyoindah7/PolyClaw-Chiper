"""Telegram Alert System - Real-time P&L notifications."""
from __future__ import annotations
import asyncio
import logging
import os
import time
from typing import Any
import httpx

logger = logging.getLogger(__name__)


class TelegramAlerter:
    """Sends Telegram alerts for trading events."""

    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id)
        self.alert_on_trade = os.environ.get("TELEGRAM_ALERT_TRADE", "true").lower() == "true"
        self.pnl_threshold = float(os.environ.get("TELEGRAM_PNL_THRESHOLD", "5.0"))
        self.min_interval_sec = float(os.environ.get("TELEGRAM_INTERVAL_MIN", "30")) * 60
        self._last_alert_time: float = 0.0
        self._last_pnl_alert_value: float = 0.0
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()

    async def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """Send a Telegram message."""
        if not self.enabled:
            return False
        try:
            client = await self._ensure_client()
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            else:
                logger.warning("Telegram API error: %s - %s", resp.status_code, resp.text[:200])
                return False
        except Exception as e:
            logger.warning("Telegram send failed: %s", e)
            return False

    def _should_alert(self) -> bool:
        """Rate limiting for alerts."""
        now = time.time()
        if now - self._last_alert_time < self.min_interval_sec:
            return False
        self._last_alert_time = now
        return True

    async def notify_startup(self, bankroll: float, strategies: list[str]):
        """Send startup notification."""
        if not self.enabled:
            return
        msg = (
            f"🚀 <b>PolyClaw-Cipher v2.0 Started</b>\n"
            f"💰 Initial Bankroll: <b>${bankroll:.2f}</b>\n"
            f"🎯 Strategies: {', '.join(strategies)}\n"
            f"📊 Mode: <code>PAPER TRADING</code>\n"
            f"⏰ {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await self.send(msg)

    async def notify_trade(self, side: str, price: float, size: float, confidence: float,
                          market: str, strategy: str):
        """Send trade execution alert."""
        if not self.enabled or not self.alert_on_trade or not self._should_alert():
            return
        msg = (
            f"⚡ <b>New Trade</b>\n"
            f"Strategy: <code>{strategy}</code>\n"
            f"Side: <b>{side}</b> @ {price:.4f}\n"
            f"Size: <b>${size:.2f}</b> | Conf: {confidence:.2f}\n"
            f"Market: {market[:80]}"
        )
        await self.send(msg)

    async def notify_pnl(self, bankroll: float, initial: float, trades: int, win_rate: float):
        """Send P&L summary alert (rate limited)."""
        if not self.enabled:
            return
        pnl = bankroll - initial
        # Only alert on significant P&L changes
        if abs(pnl - self._last_pnl_alert_value) < self.pnl_threshold:
            return
        if not self._should_alert():
            return
        self._last_pnl_alert_value = pnl
        pnl_pct = (pnl / initial) * 100 if initial > 0 else 0
        emoji = "🟢" if pnl >= 0 else "🔴"
        msg = (
            f"{emoji} <b>P&L Update</b>\n"
            f"Bankroll: <b>${bankroll:.2f}</b> (${pnl:+.2f} / {pnl_pct:+.1f}%)\n"
            f"Trades: {trades} | Win Rate: {win_rate:.1f}%\n"
            f"⏰ {time.strftime('%H:%M:%S')}"
        )
        await self.send(msg)

    async def notify_drawdown(self, current_dd: float, max_dd: float):
        """Send drawdown warning."""
        if not self.enabled:
            return
        msg = (
            f"⚠️ <b>Drawdown Warning</b>\n"
            f"Current DD: <b>{current_dd:.1f}%</b> / Max: {max_dd:.1f}%\n"
            f"Trading paused until risk resets."
        )
        await self.send(msg)

    async def notify_daily_summary(self, bankroll: float, initial: float, trades: int,
                                    wins: int, losses: int, pnl: float):
        """Send end-of-day summary."""
        if not self.enabled:
            return
        win_rate = (wins / max(1, trades)) * 100
        pnl_pct = (pnl / initial) * 100 if initial > 0 else 0
        emoji = "🟢" if pnl >= 0 else "🔴"
        msg = (
            f"{emoji} <b>Daily Summary</b>\n"
            f"Bankroll: <b>${bankroll:.2f}</b> (${pnl:+.2f} / {pnl_pct:+.1f}%)\n"
            f"Trades: {trades} (W{wins}/L{losses}) | WR: {win_rate:.1f}%\n"
            f"⏰ {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await self.send(msg)
