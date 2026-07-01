"""Fix cancel + re-sell"""
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
from py_clob_client_v2.clob_types import ApiCreds, OrderArgs, OrderType, CreateOrderOptions, BalanceAllowanceParams, AssetType, OrderMarketCancelParams
from py_clob_client_v2.order_builder.constants import BUY, SELL

creds = ApiCreds(api_key="024fa050-9154-f12a-c1b5-e7b31e3c1e04", api_secret="tKq6Vrf8Yzt0O55K0EomApZ3RdmdCxazD9rO_2slXoQ=", api_passphrase="9f22be7b1c29a4a61217ae4f0846ee36e775c0c3681ac25ce45115e2f71e161f")
client = ClobClient(host="https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=137, creds=creds, signature_type=3, funder=DEPOSIT_WALLET)

yes_token = "43187333641922996188398060383389814287787647811837308994701068387397271207198"

print("=== Cancel + Re-sell ===\n")

# Cancel using OrderMarketCancelParams
print("[1] Cancel live order...")
try:
    cancel_params = OrderMarketCancelParams(order_id="0x6597f8a9c3d4e23fecfebd10e62aceabdfeb7c9980f2c150a7202669fc016d6d")
    result = client.cancel_order(cancel_params)
    print(f"  Cancel: {result}")
except Exception as e:
    print(f"  Cancel error: {str(e)[:100]}")
    # Try plain string
    try:
        result = client.cancel_order("0x6597f8a9c3d4e23fecfebd10e62aceabdfeb7c9980f2c150a7202669fc016d6d")
        print(f"  Cancel str: {result}")
    except Exception as e2:
        print(f"  Cancel str error: {str(e2)[:100]}")
        # Try cancel_all
        try:
            result = client.cancel_all()
            print(f"  Cancel all: {result}")
        except Exception as e3:
            print(f"  Cancel all error: {str(e3)[:100]}")

# Check orders
time.sleep(2)
print("\n[2] Verify cancelled...")
orders = client.get_open_orders()
print(f"  Open orders: {len(orders) if isinstance(orders, list) else orders}")

# Fresh orderbook
print("\n[3] Get orderbook...")
book = client.get_order_book(yes_token)
bids = book.bids if hasattr(book, 'bids') else []
sorted_bids = sorted(bids, key=lambda x: float(x.price), reverse=True) if bids else []
best_bid = sorted_bids[0] if sorted_bids else None
print(f"  Best bid: {best_bid.price} @ {best_bid.size} (${float(best_bid.price)*float(best_bid.size):.2f})" if best_bid else "  No bids")

# SELL  
sell_price = float(best_bid.price) if best_bid else 0.04
print(f"\n[4] SELL 5 YES @ {sell_price}...")

t0 = time.time()
try:
    args = OrderArgs(token_id=yes_token, price=sell_price, size=5.0, side=SELL)
    opts = CreateOrderOptions(tick_size="0.001", neg_risk=False)
    signed = client.create_order(args, opts)
    t_sign = (time.time() - t0) * 1000
    print(f"  Sign: {t_sign:.0f}ms")
    
    t1 = time.time()
    result = client.post_order(signed, OrderType.GTC)
    t_post = (time.time() - t1) * 1000
    t_total = (time.time() - t0) * 1000
    
    status = result.get('status', '?')
    making = result.get('makingAmount', '0')
    tx = (result.get('transactionsHashes') or ['none'])[0]
    
    print(f"  Status: {status}")
    print(f"  Making: ${float(making)}" if making and making != '0' else "")
    print(f"  TX: {tx}")
    
    cost = 0.245
    revenue = float(making) if making and making != '0' and making != '' else 5.0 * sell_price
    pnl = revenue - cost
    print(f"  PnL: ${pnl:+.4f}")
    
    # Latency
    print(f"\n  === LATENCY ===")
    print(f"  Sign: {t_sign:.0f}ms | Post: {t_post:.0f}ms | Total: {t_total:.0f}ms")
    
except Exception as e:
    print(f"  ❌ {str(e)[:120]}")

# Final balance
print(f"\n[5] Final balance...")
bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
b = int(bal.get("balance", "0")) / 1e6
print(f"  ${b:.6f}")

# Final orders
orders = client.get_open_orders()
print(f"  Open orders: {len(orders) if isinstance(orders, list) else orders}")

print("\n=== DONE ===")
