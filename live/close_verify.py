"""Close position + measure latency + verify Builder tier"""
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
from py_clob_client_v2.clob_types import ApiCreds, OrderArgs, OrderType, CreateOrderOptions
from py_clob_client_v2.order_builder.constants import BUY, SELL

creds = ApiCreds(
    api_key="024fa050-9154-f12a-c1b5-e7b31e3c1e04",
    api_secret="tKq6Vrf8Yzt0O55K0EomApZ3RdmdCxazD9rO_2slXoQ=",
    api_passphrase="9f22be7b1c29a4a61217ae4f0846ee36e775c0c3681ac25ce45115e2f71e161f",
)

client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=creds,
    signature_type=3, funder=DEPOSIT_WALLET,
)

yes_token = "43187333641922996188398060383389814287787647811837308994701068387397271207198"

print("=== Circuit Test Stage 2: Close + Verify ===\n")

# Step 1: Get current orders
print("[1] Check open orders before close...")
orders = client.get_open_orders()
print(f"  Orders: {json.dumps(orders, indent=2, default=str)[:500]}")

# Step 2: Get orderbook for sell price
print("\n[2] Get orderbook...")
book = client.get_order_book(yes_token)
asks = book.asks if hasattr(book, 'asks') else []
bids = book.bids if hasattr(book, 'bids') else []
best_bid = max(bids, key=lambda x: float(x.price)) if bids else None
best_ask = min(asks, key=lambda x: float(x.price)) if asks else None
print(f"  Best bid: {best_bid.price if best_bid else 'none'}")
print(f"  Best ask: {best_ask.price if best_ask else 'none'}")

# Step 3: SELL at best bid (to close immediately)
sell_price = float(best_bid.price) if best_bid else 0.04
print(f"\n[3] SELL 5 YES @ {sell_price} (best bid)...")
print(f"  Entry cost: $0.245")
print(f"  Expected sell: ${5 * sell_price:.3f}")

t0 = time.time()
try:
    args = OrderArgs(token_id=yes_token, price=sell_price, size=5.0, side=SELL)
    opts = CreateOrderOptions(tick_size="0.001", neg_risk=False)
    signed = client.create_order(args, opts)
    t_sign = time.time() - t0
    print(f"  Sign latency: {t_sign*1000:.0f}ms")
    
    t1 = time.time()
    result = client.post_order(signed, OrderType.GTC)
    t_post = time.time() - t1
    t_total = time.time() - t0
    print(f"  Post latency: {t_post*1000:.0f}ms")
    print(f"  Total latency: {t_total*1000:.0f}ms")
    print(f"\n  ✅ SELL ORDER PLACED!")
    print(f"  Status: {result.get('status', '?')}")
    print(f"  Order ID: {result.get('orderID', '?')}")
    print(f"  TX: {result.get('transactionsHashes', ['?'])[0]}")
    if result.get('makingAmount'):
        print(f"  Received: ${float(result['makingAmount'])}")
        
    # PnL
    cost = 0.245
    revenue = float(result.get('makingAmount', 0))
    pnl = revenue - cost
    print(f"  PnL (pre-fees): ${pnl:+.4f}")
except Exception as e:
    t_total = time.time() - t0
    print(f"  ❌ {e} (latency: {t_total*1000:.0f}ms)")

# Step 4: Check final balance
print(f"\n[4] Final balance check...")
try:
    from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
    bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    b = int(bal.get("balance", "0")) / 1e6
    print(f"  Balance after roundtrip: ${b:.6f}")
except Exception as e:
    print(f"  Error: {e}")

# Step 5: Verify Builder tier
print(f"\n[5] Verify Builder status...")
BUILDER_KEY = os.environ.get("BUILDER_API_KEY", "")
print(f"  Builder name: Polyclaw-chiper")
print(f"  Builder code: {os.environ.get('BUILDER_CODE', '')[:20]}...")
print(f"  Relayer key: {os.environ.get('RELAYER_API_KEY', '')[:20]}...")
print(f"  Relayer addr: {os.environ.get('RELAYER_ADDRESS', '')}")
print(f"  Deposit wallet: {DEPOSIT_WALLET}")

# Check builder API keys via CLOB
try:
    keys = client.get_builder_api_keys()
    print(f"  Builder API keys: {json.dumps(keys, indent=2, default=str)}")
except Exception as e:
    print(f"  Builder keys error: {str(e)[:100]}")

# Check relayer transactions (last few)
try:
    trades = client.get_builder_trades()
    print(f"  Builder trades: {json.dumps(trades, indent=2, default=str)[:500]}")
except Exception as e:
    print(f"  Builder trades error: {str(e)[:100]}")

# Step 6: Verify relayer endpoint
print(f"\n[6] Test relayer endpoint...")
import urllib.request, json as j

relayer_url = "https://relayer-v2.polymarket.com/transactions"
# Try GET with relayer key
try:
    req = urllib.request.Request(
        relayer_url,
        headers={
            "RELAYER_API_KEY": os.environ.get("RELAYER_API_KEY", ""),
            "RELAYER_API_KEY_ADDRESS": os.environ.get("RELAYER_ADDRESS", ""),
            "Content-Type": "application/json",
        }
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"  ✅ Relayer GET: {resp.read().decode()[:200]}")
except Exception as e:
    print(f"  Relayer GET: {str(e)[:100]}")

print("\n=== Circuit Test Complete ===")
print(f"\nSummary:")
print(f"  Entry: BUY 5 YES @ ~$0.049 = -$0.245")
print(f"  Exit:  SELL 5 YES @ ~${sell_price:.3f} = +${5*sell_price:.3f}")
print(f"  Status: {result.get('status', 'completed')}")
print(f"  Signature type: 3 (POLY_1271)")
print(f"  Funder: {DEPOSIT_WALLET}")
print(f"  Latency: sign={t_sign*1000:.0f}ms post={t_post*1000:.0f}ms total={t_total*1000:.0f}ms")
