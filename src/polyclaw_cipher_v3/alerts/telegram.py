"""Telegram Alerter — lightweight, zero-dependency, fire-and-forget.

v3.5.12: Real-time trade notifications + PnL alerts via raw HTTPS calls.
No external libraries needed (urllib from stdlib).
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

from . import Alerter

logger = logging.getLogger(__name__)

# Color helpers for Telegram markdown
G = "🟢"   # green / profit
R = "🔴"   # red / loss
W = "⚪"   # white / breakeven
Y = "🟡"   # yellow / warning
B = "🔵"   # blue / info


class TelegramAlerter(Alerter):
    """Sends trade/pnl/crash notifications to Telegram via bot API."""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.token = config.get("bot_token", "") if config else ""
        self.chat_id = str(config.get("chat_id", "")) if config else ""
        self.enabled = bool(self.token and self.chat_id and self.chat_id not in ("", "None"))

    def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Fire-and-forget message send. Returns True on success."""
        if not self.enabled:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            body = json.dumps({
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return True
                logger.warning("TG send failed: status=%d", resp.status)
        except Exception as e:
            logger.warning("TG send error: %s", e)
        return False

    # === Notification methods ===

    async def notify_startup(self, bankroll: float, strategies: list[str], version: str = "v3") -> None:
        logger.info("ALERT (TG): startup $%.2f, strategies=%s", bankroll, strategies)
        self._send(
            f"{B} *PolyClaw-Cipher {version}* started\n"
            f"💰 Bankroll: ${bankroll:.2f}\n"
            f"📋 Strategies: {', '.join(strategies)}\n"
            f"🌐 [Dashboard](http://3.107.53.103:8082/)"
        )

    async def notify_trade(self, side: str, entry_price: float, invested: float,
                           confidence: float, question: str, strategy: str) -> None:
        emoji = G if side == "YES" else R
        short_q = question[:45] + ("..." if len(question) > 45 else "")
        self._send(
            f"{emoji} *{strategy} {side}* opened\n"
            f"Entry: {entry_price:.4f} | ${invested:.2f}\n"
            f"Conf: {confidence:.0%} | {short_q}"
        )

    async def notify_trade_close(self, strategy: str, side: str, pnl: float, reason: str) -> None:
        emoji = G if pnl > 0 else (W if abs(pnl) < 0.01 else R)
        pnl_pct = reason.split(":")[-1].strip() if ":" in reason else ""
        self._send(
            f"{emoji} *{strategy} {side}* closed\n"
            f"PnL: ${pnl:+.2f} {pnl_pct}\n"
            f"Reason: {reason[:60]}"
        )

    async def notify_pnl(self, bankroll: float, initial: float, trades: int, win_rate: float) -> None:
        pnl = bankroll - initial
        pnl_pct = (pnl / initial * 100) if initial > 0 else 0
        emoji = G if pnl > 0 else R
        self._send(
            f"{emoji} *PnL Update*\n"
            f"💰 ${bankroll:.2f} ({pnl_pct:+.1f}%)\n"
            f"📊 {trades} trades | WR: {win_rate:.1f}%"
        )

    async def notify_drawdown(self, current_dd: float, max_dd: float) -> None:
        self._send(
            f"{Y} *Drawdown Alert*\n"
            f"Current DD: {current_dd:.1f}%\n"
            f"Max allowed: {max_dd:.1f}%"
        )

    async def notify_crash(self, error: str) -> None:
        self._send(f"🚨 *Bot Crash*\n{error[:200]}")


    async def notify_tier_up(self, old_tier: int, new_tier: int, bankroll: float) -> None:
        """Alert on tier transition (important!)."""
        emoji = "🎯" if new_tier >= 2 else "📈"
        self._send(
            f"{emoji} <b>Tier UP: {old_tier} → {new_tier}</b>\n"
            f"💰 Bankroll: ${bankroll:.2f}\n"
            f"🌐 <a href='http://3.107.53.103:8082/'>Dashboard</a>"
        )

    async def close(self) -> None:
        pass
