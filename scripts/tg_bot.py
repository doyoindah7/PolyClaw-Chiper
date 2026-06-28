#!/usr/bin/env python3
"""PolyClaw-Cipher TG Bot — standalone, lightweight, zero-dependency.

Runs as a separate process. Polls Telegram for commands,
fetches data from both bot instances via HTTP API.

Usage: python3 scripts/tg_bot.py
Env: TG_BOT_TOKEN, TG_CHAT_ID
"""
import json, os, sys, time, urllib.request

TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")
API_BASE = "http://3.107.53.103"
INSTANCES = [
    (8082, "#0 $25"),
    (8083, "#1 $10"),
]
DASH_8082 = f"{API_BASE}:8082/"
DASH_8083 = f"{API_BASE}:8083/"

if not TOKEN or not CHAT_ID:
    print("FATAL: TG_BOT_TOKEN or TG_CHAT_ID not set", file=sys.stderr)
    sys.exit(1)

def tg_api(method, params=None):
    """Call Telegram API."""
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    if params:
        body = json.dumps(params).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"TG API error ({method}): {e}", file=sys.stderr)
        return {"ok": False}

def send(text):
    """Send message to chat."""
    tg_api("sendMessage", {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })

def fetch_api(port, path="/api/stats"):
    """Fetch from bot instance API."""
    try:
        url = f"{API_BASE}:{port}{path}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None

def fmt_uptime(sec):
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h{m}m"
    return f"{m}m{s}s"

# === Commands ===

def cmd_start():
    send(
        "🔍 <b>PolyClaw-Cipher v3.5.12</b>\n\n"
        "/status — Both instances overview\n"
        "/positions — Open positions detail\n"
        "/trades — Last 20 trades\n"
        "/top — Top 5 profits & losses\n"
        "/health — Bot health & uptime\n"
        "/dashboard — Dashboard links"
    )

def cmd_status():
    lines = ["🔍 <b>PolyClaw-Cipher v3.5.12</b>\n"]
    for port, label in INSTANCES:
        snap = fetch_api(port)
        if not snap:
            lines.append(f"⭕ <b>{label}</b>: OFFLINE")
            continue
        br = snap.get("bankroll", 0)
        init = snap.get("initial_bankroll", 25)
        pnl = br - init
        pct = (pnl / init * 100) if init > 0 else 0
        trades = snap.get("trades", 0)
        wr = snap.get("win_rate", 0)
        opens = len(snap.get("open_positions", []))
        tier = snap.get("tier", {}).get("current_tier", 1)
        emoji = "🟢" if pnl >= 0 else "🔴"
        dash = f"{API_BASE}:{port}/"
        lines.append(
            f"{emoji} <b>{label}</b>: ${br:.2f} ({pct:+.1f}%) | "
            f"{trades}T {wr:.0f}%WR | {opens} open | "
            f"<a href='{dash}'>T{tier}</a>"
        )
    send("\n".join(lines))

def cmd_positions():
    lines = ["📊 <b>Open Positions</b>\n"]
    total = 0
    for port, label in INSTANCES:
        snap = fetch_api(port)
        if not snap:
            continue
        positions = snap.get("open_positions", [])
        if positions:
            lines.append(f"<b>{label}</b>")
            for p in positions:
                e = p.get("entry_price", 0)
                c = p.get("current_price", 0)
                pct = (c - e) / e * 100 if e > 0 else 0
                inv = p.get("invested", 0)
                emoji = "🟢" if pct >= 0 else "🔴"
                q = (p.get("market_question") or "?")[:35]
                side = p.get("side", "?")
                lines.append(f"  {emoji} {side} {e:.4f}→{c:.4f} ({pct:+.1f}%) ${inv:.2f} | {q}")
            total += len(positions)
    if total == 0:
        lines.append("No open positions")
    send("\n".join(lines))

def cmd_trades():
    lines = ["📜 <b>Last 20 Trades</b>\n"]
    for port, label in INSTANCES:
        snap = fetch_api(port)
        if not snap:
            continue
        trades = snap.get("recent_trades", [])[:20]
        if not trades:
            continue
        lines.append(f"<b>{label}</b>")
        wins = 0
        for t in trades:
            pnl = t.get("pnl_dollar", 0)
            pct = t.get("pnl_percent", 0)
            emoji = "🟢" if pnl > 0 else "🔴"
            if pnl > 0:
                wins += 1
            strat = t.get("strategy", "?")
            side = t.get("side", "?")
            reason = (t.get("reason") or "?")[:30]
            lines.append(f"  {emoji} ${pnl:+.2f} ({pct:+.1f}%) | {strat} {side} | {reason}")
        losses = len(trades) - wins
        lines.append(f"  📊 {wins}W / {losses}L\n")
    send("\n".join(lines))

def cmd_top():
    lines = ["🏆 <b>Top 5 Profits & Losses</b>\n"]
    for port, label in INSTANCES:
        snap = fetch_api(port)
        if not snap:
            continue
        trades = snap.get("recent_trades", [])
        if not trades:
            continue
        sorted_t = sorted(trades, key=lambda t: t.get("pnl_dollar", 0))
        top5 = sorted_t[-5:][::-1]
        bot5 = sorted_t[:5]
        lines.append(f"<b>{label}</b>")
        lines.append("  🏆 Profits:")
        for t in top5:
            pnl = t.get("pnl_dollar", 0)
            pct = t.get("pnl_percent", 0)
            reason = (t.get("reason") or "?")[:30]
            lines.append(f"  🟢 ${pnl:+.2f} ({pct:+.1f}%) | {reason}")
        lines.append("  💀 Losses:")
        for t in bot5:
            pnl = t.get("pnl_dollar", 0)
            pct = t.get("pnl_percent", 0)
            reason = (t.get("reason") or "?")[:30]
            lines.append(f"  🔴 ${pnl:+.2f} ({pct:+.1f}%) | {reason}")
        lines.append("")
    send("\n".join(lines))

def cmd_health():
    lines = ["🫀 <b>Bot Health</b>\n"]
    for port, label in INSTANCES:
        h = fetch_api(port, "/api/health")
        if not h:
            lines.append(f"❌ <b>{label}</b>: OFFLINE")
            continue
        ut = h.get("uptime_sec", 0)
        ver = h.get("version", "?")
        lines.append(f"✅ <b>{label}</b>: online {fmt_uptime(ut)} | v{ver}")
    lines.append(f"\n🌐 <a href='{DASH_8082}'>Dashboard 8082</a> | <a href='{DASH_8083}'>8083</a>")
    send("\n".join(lines))

def cmd_dashboard():
    send(f"🌐 <a href='{DASH_8082}'>Dashboard #0 ($25)</a>\n🌐 <a href='{DASH_8083}'>Dashboard #1 ($10)</a>")

COMMANDS = {
    "/start": cmd_start,
    "/help": cmd_start,
    "/status": cmd_status,
    "/positions": cmd_positions,
    "/trades": cmd_trades,
    "/history": cmd_trades,
    "/top": cmd_top,
    "/pnl": cmd_top,
    "/health": cmd_health,
    "/dashboard": cmd_dashboard,
}

# === Main loop ===

def main():
    print("💬 PolyClaw TG Bot started", flush=True)
    offset = 0
    while True:
        result = tg_api("getUpdates", {"offset": offset + 1, "timeout": 30})
        if not result.get("ok"):
            time.sleep(10)
            continue
        for upd in result.get("result", []):
            offset = upd["update_id"]
            msg = upd.get("message", {})
            if str(msg.get("chat", {}).get("id")) != str(CHAT_ID):
                continue
            text = (msg.get("text") or "").strip()
            cmd = text.split()[0].lower() if text else ""
            handler = COMMANDS.get(cmd)
            if handler:
                try:
                    handler()
                except Exception as e:
                    send(f"⚠️ Error: {e}")

if __name__ == "__main__":
    main()
