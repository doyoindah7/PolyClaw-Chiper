#!/usr/bin/env python3
"""PolyClaw-Cipher TG Bot — Ireland monitor v3.5.16.

Usage: python3 scripts/tg_bot.py
Env: TG_BOT_TOKEN, TG_CHAT_ID, BOT_API_BASES (JSON list)
"""
import json, os, sys, time, urllib.request

TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# v3.5.16: Ireland-only (EC2 decommissioned)
DEFAULT_BASES = json.dumps([
    {"label": "🇮🇪 Ireland", "host": "http://18.200.234.149", "instances": [
        (8082, "Cipher $25"), (8084, "Fifteen $15"), (8086, "Scalper")
    ]},
])
BASES_RAW = os.environ.get("BOT_API_BASES", DEFAULT_BASES)
try:
    BASES = json.loads(BASES_RAW)
except:
    BASES = json.loads(DEFAULT_BASES)

if not TOKEN or not CHAT_ID:
    print("FATAL: TG_BOT_TOKEN or TG_CHAT_ID not set", file=sys.stderr)
    sys.exit(1)

# Build flat instance list for commands
ALL_INSTANCES = []  # (label_id, port, api_base, host_label)
for base in BASES:
    for port, name in base["instances"]:
        ALL_INSTANCES.append((f"[{name}]", port, base["host"], base["label"]))

def tg_api(method, params=None):
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
    tg_api("sendMessage", {
        "chat_id": CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    })

def fetch_api(base, port, path="/api/stats"):
    try:
        url = f"{base}:{port}{path}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None

def fmt_uptime(sec):
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m}m" if h > 0 else f"{m}m{s}s"

def cmd_start():
    send(
        "🔍 <b>PolyClaw-Cipher v3.5.16</b>\n\n"
        "/status — All instances overview\n"
        "/positions — Open positions\n"
        "/trades — Last 20 trades\n"
        "/top — Top 5 profits & losses\n"
        "/health — Health & uptime\n"
        "/dashboard — Dashboard links"
    )

def cmd_status():
    lines = ["🔍 <b>PolyClaw-Cipher Status</b>\n"]
    for base in BASES:
        lines.append(f"<b>{base['label']}</b>")
        for port, name in base["instances"]:
            snap = fetch_api(base["host"], port)
            if not snap:
                lines.append(f"  ⭕ <b>{name}</b>: OFFLINE")
                continue
            br = snap.get("bankroll", 0)
            init = snap.get("initial_bankroll", 25)
            pnl = br - init
            pct = (pnl / init * 100) if init > 0 else 0
            trades = snap.get("trades", 0)
            wr = snap.get("win_rate", 0)
            opens = len(snap.get("open_positions", []))
            tier = snap.get("tier", {}).get("current_tier", 1)
            ver = snap.get("version", "?")
            emoji = "🟢" if pnl >= 0 else "🔴"
            dash = f"{base['host']}:{port}/"
            lines.append(
                f"  {emoji} <b>{name}</b>: ${br:.2f} ({pct:+.1f}%) | "
                f"{trades}T {wr:.0f}%WR | {opens} open | v{ver} | "
                f"<a href='{dash}'>T{tier}</a>"
            )
        lines.append("")
    send("\n".join(lines))

def cmd_positions():
    lines = ["📊 <b>Open Positions</b>\n"]
    total = 0
    for base in BASES:
        for port, name in base["instances"]:
            snap = fetch_api(base["host"], port)
            if not snap:
                continue
            positions = snap.get("open_positions", [])
            if positions:
                lines.append(f"<b>{base['label']} {name}</b>")
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
    for base in BASES:
        for port, name in base["instances"]:
            snap = fetch_api(base["host"], port)
            if not snap:
                continue
            trades = snap.get("recent_trades", [])[:10]
            if not trades:
                continue
            lines.append(f"<b>{base['label']} {name}</b>")
            for t in trades[:10]:
                pnl = t.get("pnl_dollar", 0)
                pct = t.get("pnl_percent", 0)
                emoji = "🟢" if pnl > 0 else "🔴"
                strat = t.get("strategy", "?")
                side = t.get("side", "?")
                reason = (t.get("reason") or "?")[:20]
                lines.append(f"  {emoji} ${pnl:+.2f} ({pct:+.1f}%) | {strat} {side} | {reason}")
            lines.append("")
    send("\n".join(lines))

def cmd_top():
    lines = ["🏆 <b>Top 5 Profits & Losses</b>\n"]
    for base in BASES:
        for port, name in base["instances"]:
            snap = fetch_api(base["host"], port)
            if not snap:
                continue
            trades = snap.get("recent_trades", [])
            if not trades:
                continue
            sorted_t = sorted(trades, key=lambda t: t.get("pnl_dollar", 0))
            top5 = sorted_t[-5:][::-1]
            bot5 = sorted_t[:5]
            lines.append(f"<b>{base['label']} {name}</b>")
            lines.append("  🏆 Best:")
            for t in top5:
                lines.append(f"  🟢 ${t['pnl_dollar']:+.2f} ({t['pnl_percent']:+.1f}%) | {(t.get('reason') or '?')[:25]}")
            if bot5:
                lines.append("  💀 Worst:")
                for t in bot5:
                    lines.append(f"  🔴 ${t['pnl_dollar']:+.2f} ({t['pnl_percent']:+.1f}%) | {(t.get('reason') or '?')[:25]}")
            lines.append("")
    send("\n".join(lines))

def cmd_health():
    lines = ["🫀 <b>Bot Health</b>\n"]
    for base in BASES:
        lines.append(f"<b>{base['label']}</b>")
        for port, name in base["instances"]:
            h = fetch_api(base["host"], port, "/api/health")
            if not h:
                lines.append(f"  ❌ <b>{name}</b>: OFFLINE")
                continue
            ut = h.get("uptime_sec", 0)
            ver = h.get("version", "?")
            lines.append(f"  ✅ <b>{name}</b>: {fmt_uptime(ut)} | v{ver}")
        lines.append("")
    # Dashboard links
    lines.append("🌐 Dashboards:")
    for base in BASES:
        for port, name in base["instances"]:
            lines.append(f"  <a href='{base['host']}:{port}/'>{base['label']} {name}</a>")
    send("\n".join(lines))

def cmd_dashboard():
    links = []
    for base in BASES:
        for port, name in base["instances"]:
            links.append(f"🌐 <a href='{base['host']}:{port}/'>{base['label']} {name}</a>")
    send("\n".join(links))

COMMANDS = {
    "/start": cmd_start, "/help": cmd_start, "/menu": cmd_start,
    "/status": cmd_status, "/positions": cmd_positions,
    "/trades": cmd_trades, "/history": cmd_trades,
    "/top": cmd_top, "/pnl": cmd_top,
    "/health": cmd_health, "/dashboard": cmd_dashboard,
}

def main():
    print("💬 PolyClaw TG Bot (multi-VPS) started", flush=True)
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
