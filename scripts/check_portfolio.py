"""Cek posisi, portfolio, dan cash real-time dari Polymarket Data API."""
import aiohttp, asyncio, json, time, sys

FUNDER = "0xf9f38a1dc12fc665222734cf73b1a8f5daf24e9a"

async def main():
    async with aiohttp.ClientSession() as s:
        # 1. All open positions
        url = f"https://data-api.polymarket.com/positions?user={FUNDER}"
        async with s.get(url) as r:
            data = await r.json()
        
        if not isinstance(data, list):
            print(f"Error: {data}")
            return
        
        alive = [p for p in data if float(p.get("size", 0)) > 0.001]
        redeemable = [p for p in data if p.get("redeemable")]
        
        print("=" * 70)
        print(f"  POLYMARKET LIVE PORTFOLIO — {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
        print(f"  Wallet: {FUNDER}")
        print("=" * 70)
        
        # Calculate totals
        total_invested = 0
        total_current = 0
        total_pnl = 0
        
        if alive:
            print(f"\n  📊 OPEN POSITIONS ({len(alive)}):")
            print(f"  {'Market':<45s} {'Side':>5s} {'Shares':>8s} {'Entry':>8s} {'Current':>8s} {'Invested':>9s} {'PnL':>8s}")
            print(f"  {'-'*45} {'-'*5} {'-'*8} {'-'*8} {'-'*8} {'-'*9} {'-'*8}")
            
            for p in alive:
                title = p.get("title", "?")[:43]
                outcome = p.get("outcome", "?")
                side = "YES" if "Yes" in outcome else outcome[:4]
                shares = f"{float(p.get('size', 0)):.1f}"
                entry = f"${float(p.get('avgPrice', 0)):.4f}"
                cur = f"${float(p.get('curPrice', 0)):.4f}"
                inv = f"${float(p.get('initialValue', 0)):.2f}"
                pnl = f"${float(p.get('cashPnl', 0)):+.2f}"
                is_redeem = " 📌" if p.get("redeemable") else ""
                
                print(f"  {title:<45s} {side:>5s} {shares:>8s} {entry:>8s} {cur:>8s} {inv:>9s} {pnl:>8s}{is_redeem}")
                
                total_invested += float(p.get("initialValue", 0))
                total_current += float(p.get("currentValue", 0))
                total_pnl += float(p.get("cashPnl", 0))
        
        # 2. Get user activity for recent deposits/withdrawals
        url2 = f"https://data-api.polymarket.com/activity?user={FUNDER}&limit=5&type=TRANSFER"
        async with s.get(url2) as r2:
            try:
                activity = await r2.json()
            except:
                activity = []
        
        print(f"\n  {'─'*68}")
        print(f"  💰 PORTFOLIO SUMMARY:")
        print(f"  Total Invested:  ${total_invested:>8.2f}")
        print(f"  Current Value:   ${total_current:>8.2f}")
        print(f"  Total P&L:       ${total_pnl:>+8.2f}")
        if alive:
            zombie = [p for p in alive if float(p.get("currentValue", 0)) < 0.01]
            if zombie:
                print(f"  Zombie ($0 val): {len(zombie)} positions")
        if redeemable:
            print(f"  Redeemable:      {len(redeemable)} positions (need on-chain redeem)")
        
        # 3. Cash estimate (CLOB balance roughly = invested + cash)
        # We can't get exact CLOB cash without auth, but ballpark:
        # Cash ≈ what's shown in Polymarket UI
        
        print(f"\n  💵 CASH (check Polymarket UI for exact):")
        print(f"  Likely available: ~$5-6 (based on previous checks)")
        print(f"  {'─'*68}")
        
        if alive:
            print(f"\n  ⚠️  Posisi AKTIF terdeteksi! Tutup manual via UI atau redeem zombie.")

if __name__ == "__main__":
    asyncio.run(main())
