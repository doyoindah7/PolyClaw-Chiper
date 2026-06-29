"""Telegram Alerter — sends alerts to Telegram via Bot API.

Uses same BOT_TOKEN for all instances. Each instance identifies itself
with a label (Cipher/Fifteen/Scalper) via TG_INSTANCE_LABEL env var.

Alert control env vars:
  TG_ALERT_TRADES=0  →  disable open/close alerts (default: 1)
"""
from __future__ import annotations

import logging
import os
import time
import httpx
from typing import Any

logger = logging.getLogger(__name__)


class TelegramAlerter:
    """Sends alerts to Telegram using bot API."""

    TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        token = os.environ.get("TG_BOT_TOKEN", "")
        chat_id = os.environ.get("TG_CHAT_ID", "")
        self.label = os.environ.get("TG_INSTANCE_LABEL", os.environ.get("BOT_MODE", "bot"))

        if not token or not chat_id:
            logger.warning("TelegramAlerter: missing TG_BOT_TOKEN or TG_CHAT_ID — alerts disabled")
            self.enabled = False
            return

        self.token = token
        self.chat_id = chat_id
        self.enabled = True
        
        # Alert type toggles
        self._alert_trades = os.environ.get("TG_ALERT_TRADES", "1") == "1"
        logger.info(
            "TelegramAlerter: trades=%s (set TG_ALERT_TRADES=0 to disable open/close alerts)",
            "ON" if self._alert_trades else "OFF",
        )
        
        self._client = httpx.AsyncClient(timeout=10)
        self._cooldowns: dict[str, float] = {}
        self._cooldown_sec = 300  # 5 min per alert type cooldown

    def _tag(self, msg: str) -> str:
        return f"[{self.label.upper()}] {msg}"

    def _check_cooldown(self, alert_type: str) -> bool:
        now = time.time()
        last = self._cooldowns.get(alert_type, 0)
        if now - last < self._cooldown_sec:
            return False
        self._cooldowns[alert_type] = now
        return True

    async def _send(self, text: str, alert_type: str = "info") -> None:
        if not self.enabled:
            return
        if alert_type not in ("crash",) and not self._check_cooldown(alert_type):
            return

        try:
            url = self.TELEGRAM_API.format(token=self.token)
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            resp = await self._client.post(url, json=payload)
            if resp.status_code == 429:
                logger.warning("TelegramAlerter: rate limited")
            elif resp.status_code != 200:
                logger.warning("TelegramAlerter: send failed %d", resp.status_code)
        except Exception as e:
            logger.error("TelegramAlerter: send error: %s", e)

    async def notify_startup(self, bankroll: float, strategies: list[str], version: str = "v3") -> None:
        strat_list = ", ".join(strategies) if strategies else "none"
        msg = self._tag(f"🟢 <b>START</b> ${bankroll:.2f} | {strat_list} | {version}")
        await self._send(msg, "startup")

    async def notify_trade(self, side: str, entry_price: float, invested: float,
                           confidence: float, question: str, strategy: str) -> None:
        if not self._alert_trades:
            logger.info("TG alert muted (trade): %s %s @ %.4f $%.2f conf=%.2f",
                        strategy, side, entry_price, invested, confidence)
            return
        msg = self._tag(f"📈 <b>{side.upper()}</b> ${invested:.2f} @ {entry_price:.4f} conf={confidence:.2f} strat={strategy}\n{question[:80]}")
        await self._send(msg, "trade")

    async def notify_trade_close(self, strategy: str, side: str, pnl: float, reason: str) -> None:
        if not self._alert_trades:
            logger.info("TG alert muted (close): %s %s PnL=$%.4f | %s",
                        strategy, side, pnl, reason)
            return
        emoji = "✅" if pnl > 0 else "❌"
        msg = self._tag(f"{emoji} <b>CLOSE</b> {strategy} {side} PnL=<b>${pnl:+.2f}</b> | {reason}")
        await self._send(msg, "close")

    async def notify_pnl(self, bankroll: float, initial: float, trades: int, win_rate: float) -> None:
        pnl = bankroll - initial
        pct = (pnl / initial * 100) if initial > 0 else 0
        emoji = "💰" if pnl > 0 else "📉"
        msg = self._tag(f"{emoji} <b>PnL</b> ${pnl:+.2f} ({pct:+.1f}%) | {trades} trades | WR={win_rate:.1f}%")
        await self._send(msg, "pnl")

    async def notify_drawdown(self, current_dd: float, max_dd: float) -> None:
        msg = self._tag(f"⚠️ <b>DRAWDOWN</b> {current_dd:.1f}% / max {max_dd:.1f}%")
        await self._send(msg, "drawdown")

    async def notify_crash(self, error: str) -> None:
        msg = self._tag(f"🚨 <b>CRASH</b> {error[:200]}")
        await self._send(msg, "crash")

    async def close(self) -> None:
        await self._client.aclose()
        self._client = None
