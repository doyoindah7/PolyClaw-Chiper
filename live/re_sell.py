"""Cancel live order + re-sell at market price"""
import os, sys, json, time
from pathlib import Path

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
DEPOSIT_WALLET = "0xf9f38a1dc12fc665222734cf73b1a8f5daf24e9a"

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds, OrderArgs, OrderType, CreateOrderOptions, BalanceAllowanceParams, AssetType
from py_clob_client_v2.order_builder.constants import BUY, SELL

creds = ApiCreds(api_key="024fa050-9154-f12a-c1b5-e7b31e3c1e04", api_secret="tKq6Vrf8Yzt0O55K0EomApZ3RdmdCxazD9rO_2slXoQ=", api_passphrase="9f22be7b1c29a4a61217ae4f0846ee36e775c0c3681ac25ce45115e2f71e161f")
client = ClobClient(host="https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=137, creds=creds, signature_type=3, funder=DEPOSIT_WALLET)

yes_token = "43187333641922996188398060383389814287787647811837308994701068387397271207198"
sell_order_id = "0x6597f8a9c3d4e23fecfebd10e62aceabdfeb7c9980f2c150a7202669fc016d6d"

print("=== Re-sell at market ===\n")

# Cancel old order
print("[1] Cancel old limit order...")
try:
    result = client.cancel_order(sell_order_id)
    print(f"  ✅ Cancelled: {result}")
except Exception as e:
    print(f"  Cancel: {str(e)[:80]}")

# Check orderbook fresh
print("\n[2] Fresh orderbook...")
try:
    book = client.get_order_book(yes_token)
    bids = book.bids if hasattr(book, 'bids') else []
    asks = book.asks if hasattr(book, 'asks') else []
    
    sorted_bids = sorted(bids, key=lambda x: float(x.price), reverse=True) if bids else []
    sorted_asks = sorted(asks, key=lambda x: float(x.price)) if asks else []
    
    best_bid = sorted_bids[0] if sorted_bids else None
    best_ask = sorted_asks[0] if sorted_asks else None
    
    print(f"  Best bid: {best_bid.price} x {best_bid.size} (${float(best_bid.price)*float(best_bid.size):.2f})" if best_bid else "  No bids")
    print(f"  Best ask: {best_ask.price} x {best_ask.size} (${float(best_ask.price)*float(best_ask.size):.2f})" if best_ask else "  No asks")
except Exception as e:
    print(f"  Error: {e}")
    best_bid = None

# SELL at best bid or slightly below for quick fill
sell_price = float(best_bid.price) if best_bid else 0.04
print(f"\n[3] SELL 5 YES @ {sell_price}...")

t0 = time.time()
try:
    args = OrderArgs(token_id=yes_token, price=sell_price, size=5.0, side=SELL)
    opts = CreateOrderOptions(tick_size="0.001", neg_risk=False)
    signed = client.create_order(args, opts)
    t_sign = (time.time() - t0) * 1000
    
    t1 = time.time()
    result = client.post_order(signed, OrderType.GTC)
    t_post = (time.time() - t1) * 1000
    t_total = (time.time() - t0) * 1000
    
    status = result.get('status', '?')
    tx = result.get('transactionsHashes', ['?'])[0] if result.get('transactionsHashes') else 'none'
    
    print(f"  Status: {status}")
    print(f"  TX: {tx}")
    print(f"  Making: ${float(result.get('makingAmount', 0))}" if result.get('makingAmount') else "")
    
    if status == 'matched':
        print(f"  🎉 FILLED!")
    elif status == 'live':
        # Try FOK instead
        print(f"  ⚠️ Live (limit). Trying FOK...")
        result2 = client.post_order(signed, OrderType.FOK)
        print(f"  FOK: {result2.get('status', '?')}")
    
    # PnL
    cost = 0.245
    revenue = float(result.get('makingAmount', 0)) if result.get('makingAmount') else (5.0 * sell_price)
    print(f"  PnL: ${revenue - cost:+.4f}")
    
except Exception as e:
    t_total = (time.time() - t0) * 1000
    print(f"  ❌ {str(e)[:100]}")

# Final balance
print(f"\n[4] Final balance...")
try:
    bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    b = int(bal.get("balance", "0")) / 1e6
    print(f"  Balance: ${b:.6f}")
except Exception as e:
    print(f"  Error: {e}")

# Check open orders
print(f"\n[5] Open orders...")
orders = client.get_open_orders()
print(f"  {json.dumps(orders, indent=2, default=str)[:300]}")

latency_report = f"""
=== LATENCY REPORT ===
  Order sign:     {t_sign:.0f}ms
  Order post:     {t_post:.0f}ms
  Total:          {t_total:.0f}ms
  VPS location:   Ireland (18.200.234.149)
  API endpoint:   clob.polymarket.com
  Signature type: 3 (POLY_1271 EIP-7702 / ERC-1271)
"""
print(latency_report)
print("=== Done ===")
