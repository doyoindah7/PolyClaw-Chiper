#!/usr/bin/env python3
"""PolyClaw Live TG Bot — dedicated command handler for @polyclawlive_bot.

Commands: /status, /positions, /trades, /health, /balance, /dashboard, /help
          /portfolio (Poly real-time), /close (force close)
"""
import json, os, sys, time, urllib.request, aiohttp, asyncio, io

TOKEN = os.environ.get("LIVE_TG_BOT_TOKEN", "8461482229:AAGgT6PZu5x7ibA7HGTB0Z1fUtr1N5QD5fE")
CHAT_ID = os.environ.get("TG_CHAT_ID", "2051570522")
API_BASE = os.environ.get("LIVE_API_BASE", "http://18.200.234.149:8090")
LABEL = "🔴LIVE"

if not TOKEN:
    print("FATAL: no token", file=sys.stderr); sys.exit(1)

def tg_send(text, reply_to=None):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "reply_to_message_id": reply_to or 0}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"send error: {e}", file=sys.stderr)
        return None

def api_get(path):
    try:
        with urllib.request.urlopen(f"{API_BASE}{path}", timeout=5) as resp:
            return json.loads(resp.read().decode())
    except:
        return None

def fmt_money(v):
    try: return f"${float(v):.2f}"
    except: return "?"

def cmd_status():
    h = api_get("/api/health") or {}
    s = api_get("/api/stats") or {}
    br = s.get("bankroll", h.get("bankroll", "?"))
    cash = s.get("cash", "?")
    trades = s.get("total_trades", s.get("trades", 0))
    wr = s.get("win_rate", 0)
    uptime = h.get("uptime_sec", 0)
    
    # Positions are inside stats
    positions = s.get("open_positions", [])
    open_count = len(positions) if isinstance(positions, list) else 0
    total_invested = sum(float(p.get("invested", 0)) for p in positions) if isinstance(positions, list) else 0
    total_value = sum(float(p.get("current_value", 0)) for p in positions) if isinstance(positions, list) else 0
    unrealized_pnl = total_value - total_invested
    
    hrs = int(uptime // 3600)
    mins = int((uptime % 3600) // 60)
    
    pnl_emoji = "🟢" if unrealized_pnl >= 0 else "🔴"
    
    text = f"""🔴 <b>LIVE Bot Status</b>
━━━━━━━━━━━━━━━
💰 Bankroll: {fmt_money(br)}
💵 Cash: {fmt_money(cash)}
📦 Invested: {fmt_money(total_invested)}
{pnl_emoji} Unrealized PnL: ${unrealized_pnl:+.4f}
📊 Trades: {trades} | WR: {wr}%
🔄 Open: {open_count} positions
⏱️ Uptime: {hrs}h {mins}m
🔧 Version: {h.get('version', '?')}
🔗 <a href="http://18.200.234.149:8090">Dashboard</a>"""
    return text

def cmd_positions():
    s = api_get("/api/stats") or {}
    positions = s.get("open_positions", [])
    if not positions:
        return "🔴 LIVE: No open positions"
    
    lines = [f"🔴 <b>LIVE Positions ({len(positions)})</b>", "━━━━━━━━━━━━━━━"]
    for p in positions[:10]:
        q = p.get("market_question", "?")[:40]
        side = p.get("side", "?")
        entry = p.get("entry_price", 0)
        cur = p.get("current_price", 0)
        pnl = p.get("pnl_percent", 0)
        inv = p.get("invested", 0)
        emoji = "🟢" if pnl >= 0 else "🔴"
        lines.append(f"{emoji} <b>{side}</b> {q}\n   Entry: {entry:.3f} | Now: {cur:.3f} | PnL: {pnl:+.1f}% | ${inv:.2f}")
    return "\n".join(lines)

def cmd_trades():
    s = api_get("/api/stats") or {}
    trades = s.get("recent_trades", [])
    if not trades:
        return "🔴 LIVE: No trades yet"
    
    lines = [f"🔴 <b>LIVE Recent Trades ({len(trades)})</b>", "━━━━━━━━━━━━━━━"]
    total_pnl = 0
    for t in trades[:10]:
        q = t.get("market_question", "?")[:35]
        side = t.get("side", "?")
        pnl = t.get("pnl_dollar", 0)
        reason = t.get("reason", "?")[:20]
        total_pnl += pnl
        emoji = "✅" if pnl >= 0 else "❌"
        lines.append(f"{emoji} <b>{side}</b> {q}\n   PnL: ${pnl:+.4f} ({reason})")
    lines.append(f"\n📊 Total PnL: ${total_pnl:+.4f}")
    return "\n".join(lines)

def cmd_health():
    h = api_get("/api/health") or {}
    return f"""🔴 <b>LIVE Health Check</b>
━━━━━━━━━━━━━━━
Status: {h.get('status', '?')}
Version: {h.get('version', '?')}
Mode: {h.get('mode', '?')}
Label: {h.get('instance_label', '?')}
Uptime: {h.get('uptime_sec', 0)}s"""

def cmd_balance():
    h = api_get("/api/health") or {}
    s = api_get("/api/stats") or {}
    br = s.get("bankroll", h.get("bankroll", "?"))
    cash = s.get("cash", "?")
    deployed = s.get("deployed", 0)
    positions = s.get("open_positions", [])
    total_value = sum(float(p.get("current_value", 0)) for p in positions) if isinstance(positions, list) else 0
    return f"""🔴 <b>LIVE Balance</b>
━━━━━━━━━━━━━━━
💰 Bankroll: {fmt_money(br)}
💵 Cash: {fmt_money(cash)}
📦 Deployed: {fmt_money(deployed)}
📈 Position Value: {fmt_money(total_value)}
🔗 <a href="https://polymarket.com">Polymarket UI</a>"""

def cmd_dashboard():
    return f"""🔴 <b>LIVE Dashboard</b>
━━━━━━━━━━━━━━━
🌐 <a href="http://18.200.234.149:8090">http://18.200.234.149:8090</a>
📊 <a href="http://18.200.234.149:8090/api/stats">Stats API</a>
📋 <a href="http://18.200.234.149:8090/api/positions">Positions</a>
💹 <a href="http://18.200.234.149:8090/api/trades">Trades</a>"""

def cmd_help():
    return """🔴 <b>LIVE Bot Commands</b>
━━━━━━━━━━━━━━━
/status — Bot status & bankroll
/positions — Open positions
/trades — Recent trades + PnL
/health — Health check
/balance — Balance info
/dashboard — Dashboard links
/portfolio — Poly real-time portfolio
/close — Close position (reply with name)
/help — This message"""

# ── /portfolio: real-time Polymarket portfolio ──

FUNDER = os.environ.get("LIVE_FUNDER", "0xf9f38a1dc12fc665222734cf73b1a8f5daf24e9a")

def cmd_portfolio():
    """Fetch real-time portfolio + CLOB balance."""
    async def _fetch():
        # Get CLOB balance
        bal_text = "N/A"
        try:
            PK = os.environ["PRIVATE_KEY"]
            L2K = os.environ["POLYMARKET_API_KEY"]
            L2S = os.environ["POLYMARKET_API_SECRET"]
            L2P = os.environ["POLYMARKET_API_PASSPHRASE"]
            from py_clob_client_v2.client import ClobClient
            from py_clob_client_v2.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
            creds = ApiCreds(api_key=L2K, api_secret=L2S, api_passphrase=L2P)
            client = ClobClient(host="https://clob.polymarket.com", key=PK, chain_id=137,
                               creds=creds, signature_type=3, funder=FUNDER)
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            bal = client.get_balance_allowance(params)
            bal_usd = float(bal.get("balance", 0)) / 1_000_000  # USDC.e has 6 decimals
            bal_text = f"${bal_usd:.2f}"
        except Exception as e:
            bal_text = "err"
        
        # Get positions from Data API
        url = f"https://data-api.polymarket.com/positions?user={FUNDER}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                data = await r.json()
        
        if not isinstance(data, list):
            return f"❌ API error: {data}"
        
        alive = [p for p in data if float(p.get("size", 0)) > 0.001]
        zombie = [p for p in alive if float(p.get("currentValue", 0)) < 0.01]
        active = [p for p in alive if float(p.get("currentValue", 0)) >= 0.01]
        
        lines = [f"📊 <b>Poly Live Portfolio</b>", f"━━━━━━━━━━━━━━━"]
        
        # Summary cards (clean view like Poly UI)
        total_cur = sum(float(p.get("currentValue", 0)) for p in alive)
        total_pnl = sum(float(p.get("cashPnl", 0)) for p in alive)
        total_inv = sum(float(p.get("initialValue", 0)) for p in alive)
        
        # Portfolio = balance + current position value
        try:
            porto_val = float(bal_text.replace("$","")) + total_cur
            lines.append(f"💰 Portfolio: ${porto_val:.2f}")
        except:
            lines.append(f"💰 Portfolio: ~${total_cur:.2f} + cash")
        lines.append(f"💵 Available Cash: {bal_text}")
        lines.append(f"📦 Positions: {len(active)} active, {len(zombie)} zombie")
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        lines.append(f"{pnl_emoji} Total P&L: ${total_pnl:+.2f}")
        
        if active:
            lines.append(f"\n━━━━━━━━━━━━━━━")
            lines.append(f"📋 <b>Active ({len(active)}):</b>")
            for p in active[:6]:
                title = p.get("title", "?")[:35]
                outcome = p.get("outcome", "?")[:4]
                sz = float(p.get("size", 0))
                cur = float(p.get("curPrice", 0))
                pnl = float(p.get("cashPnl", 0))
                inv = float(p.get("initialValue", 0))
                emoji = "🟢" if pnl >= 0 else "🔴"
                lines.append(f"{emoji} {outcome} {title}\n   {sz:.1f}sh | Inv: ${inv:.2f} | PnL: ${pnl:+.2f}")
        
        if zombie:
            lines.append(f"\n⚫ <b>Zombie ({len(zombie)}):</b> need redeem")
            for p in zombie[:2]:
                lines.append(f"   · {p.get('title','?')[:40]}")
            if len(zombie) > 2:
                lines.append(f"   ... +{len(zombie)-2} more")
        
        lines.append(f"\n🔗 <a href='https://polymarket.com/portfolio'>Polymarket UI</a>")
        
        return "\n".join(lines)
    
    try:
        return asyncio.run(_fetch())
    except Exception as e:
        return f"❌ Portfolio error: {e}"


# ── /close: force close position via CLOB ──

def cmd_close(args: str = "") -> str:
    """Close a position by name (partial match)."""
    if not args:
        return "Usage: /close <market name>\nExample: /close Pittsburgh"
    
    async def _close():
        # 1. Find position matching args
        url = f"https://data-api.polymarket.com/positions?user={FUNDER}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                data = await r.json()
        
        if not isinstance(data, list):
            return f"❌ API error"
        
        # Match by partial name (case-insensitive)
        search = args.lower()
        matches = [p for p in data 
                   if float(p.get("size", 0)) > 0.001 
                   and float(p.get("currentValue", 0)) >= 0.01
                   and search in p.get("title", "").lower()]
        
        if not matches:
            return f"❌ No active position matching '{args}'"
        if len(matches) > 1:
            names = "\n".join(f"  · {p['title'][:50]}" for p in matches[:5])
            return f"⚠️ Multiple matches:\n{names}\n\nBe more specific."
        
        p = matches[0]
        title = p["title"][:50]
        token_id = p["asset"]
        shares = float(p["size"])
        cur_price = float(p.get("curPrice", 0))
        
        # 2. Close via CLOB SDK
        try:
            PK = os.environ["PRIVATE_KEY"]
            L2K = os.environ["POLYMARKET_API_KEY"]
            L2S = os.environ["POLYMARKET_API_SECRET"]
            L2P = os.environ["POLYMARKET_API_PASSPHRASE"]
            
            from py_clob_client_v2.client import ClobClient
            from py_clob_client_v2.clob_types import ApiCreds, OrderArgs, OrderType, CreateOrderOptions, BalanceAllowanceParams, AssetType
            from py_clob_client_v2.order_builder.constants import SELL
            
            creds = ApiCreds(api_key=L2K, api_secret=L2S, api_passphrase=L2P)
            client = ClobClient(
                host="https://clob.polymarket.com", key=PK, chain_id=137,
                creds=creds, signature_type=3, funder=FUNDER,
            )
            
            tick_size = "0.01"
            neg_risk = False
            try:
                tick_size = str(client.get_tick_size(token_id))
                neg_risk = client.get_neg_risk(token_id)
            except:
                pass
            
            sell_price = max(cur_price * 0.95, 0.01)
            order_args = OrderArgs(token_id=token_id, price=sell_price, size=shares, side=SELL)
            options = CreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)
            signed = client.create_order(order_args, options)
            result = client.post_order(signed, OrderType.GTC)
            
            status = result.get("status", "?")
            if status in ("matched", "filled"):
                return f"✅ <b>Closed:</b> {title}\nShares: {shares:.1f} @ ${sell_price:.4f}\nStatus: FILLED 🎯"
            else:
                return f"⏳ <b>Order placed:</b> {title}\nShares: {shares:.1f} @ ${sell_price:.4f}\nStatus: {status}"
        except Exception as e:
            return f"❌ Close error: {e}"
    
    try:
        return asyncio.run(_close())
    except Exception as e:
        return f"❌ Error: {e}"

COMMANDS = {
    "/status": cmd_status,
    "/positions": cmd_positions,
    "/trades": cmd_trades,
    "/health": cmd_health,
    "/balance": cmd_balance,
    "/dashboard": cmd_dashboard,
    "/portfolio": cmd_portfolio,
    "/pf": cmd_portfolio,
    "/help": cmd_help,
    "/start": cmd_help,
    "/menu": cmd_help,
}

# Allowed users
ALLOWED = {int(CHAT_ID)}

print("🔴 LIVE TG Bot started — waiting for commands...")
tg_send(f"🔴 <b>LIVE TG Bot online</b>\n/status /positions /trades /portfolio /close /help")

offset = 0
while True:
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset}&timeout=30"
        with urllib.request.urlopen(url, timeout=35) as resp:
            data = json.loads(resp.read().decode())
        
        for update in data.get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            chat_id = msg.get("chat", {}).get("id", 0)
            text = msg.get("text", "").strip()
            
            if chat_id not in ALLOWED:
                continue
            
            parts = text.split(maxsplit=1)
            cmd = parts[0] if parts else ""
            args = parts[1] if len(parts) > 1 else ""
            
            if cmd == "/close":
                reply = cmd_close(args)
                tg_send(reply, reply_to=msg.get("message_id"))
            elif cmd in COMMANDS:
                reply = COMMANDS[cmd]()
                tg_send(reply, reply_to=msg.get("message_id"))
            elif text:
                tg_send(f"Unknown command: {text}\nType /help for commands", reply_to=msg.get("message_id"))
    
    except Exception as e:
        print(f"poll error: {e}", file=sys.stderr)
        time.sleep(5)
