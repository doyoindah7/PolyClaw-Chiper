"""
Fix API calls — check method signatures, retry with correct params
"""
import os, sys, json, time, inspect
from pathlib import Path

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
WALLET = os.environ.get("BOT_ADDRESS")
L2_KEY = os.environ.get("POLYMARKET_API_KEY")
L2_SECRET = os.environ.get("POLYMARKET_API_SECRET")
L2_PASS = os.environ.get("POLYMARKET_API_PASSPHRASE")

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType, CreateOrderOptions, BalanceAllowanceParams, AssetType
from py_clob_client.order_builder.constants import BUY

creds = ApiCreds(api_key=L2_KEY, api_secret=L2_SECRET, api_passphrase=L2_PASS)
client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=creds,
    signature_type=0, funder=WALLET,
)

# Check method signatures
print("=== Method Signatures ===")
for method_name in ['create_and_post_order', 'create_market_order', 'post_order', 'create_order']:
    method = getattr(client, method_name)
    sig = inspect.signature(method)
    print(f"  {method_name}{sig}")

# Check balance
print("\n=== Balance Check ===")
try:
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    bal = client.get_balance_allowance(params)
    print(f"  Collateral balance: {bal}")
except Exception as e:
    print(f"  Error: {e}")

# Find a market — try to find one with enable_order_book=True
print("\n=== Finding Market ===")
cursor = "NDAwMA=="
resp = client.get_markets(next_cursor=cursor)
data = resp.get("data", [])

# Find market with orderbook enabled
markets_with_book = []
for m in data:
    if not isinstance(m, dict): continue
    if not m.get("accepting_orders"): continue
    if not m.get("enable_order_book"): continue
    tokens = m.get("tokens", [])
    if tokens and len(tokens) >= 2:
        tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
        if len(tids) >= 2:
            markets_with_book.append(m)

print(f"  Markets with orderbook: {len(markets_with_book)}")
if not markets_with_book:
    # Fallback: any accepting market
    for m in data:
        if not isinstance(m, dict): continue
        if not m.get("accepting_orders"): continue
        tokens = m.get("tokens", [])
        if tokens and len(tokens) >= 2:
            tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
            if len(tids) >= 2:
                markets_with_book.append(m)
    print(f"  Fallback markets: {len(markets_with_book)}")

best = markets_with_book[0] if markets_with_book else None
if not best:
    print("  ❌ No market found")
    sys.exit(1)

question = best.get("question", "?")[:70]
tids = [t.get("token_id") for t in best.get("tokens", []) if isinstance(t, dict)]
neg_risk = bool(best.get("neg_risk", False))
tick = best.get("minimum_tick_size", 0.01)
min_size = float(best.get("minimum_order_size", 5))

print(f"  Market: {question}")
print(f"  Tokens: {tids[:2]}")
print(f"  neg_risk: {neg_risk}")
print(f"  tick: {tick}")
print(f"  min_size: {min_size}")

# Check orderbook
print(f"\n=== Orderbook ===")
try:
    book = client.get_order_book(tids[0])
    if hasattr(book, 'bids'):
        bids = book.bids or []
        asks = book.asks or []
        print(f"  Bids: {len(bids)}, Asks: {len(asks)}")
        if bids:
            sorted_bids = sorted(bids, key=lambda x: float(x.price) if hasattr(x, 'price') else 0, reverse=True)
            print(f"  Best bid: {sorted_bids[0]}")
        if asks:
            sorted_asks = sorted(asks, key=lambda x: float(x.price) if hasattr(x, 'price') else 0)
            print(f"  Best ask: {sorted_asks[0]}")
    else:
        print(f"  Raw: {str(book)[:300]}")
except Exception as e:
    print(f"  Error: {e}")

# Try create_order with options, then post_order separately
print(f"\n=== Create Order (signed) ===")
try:
    order_args = OrderArgs(
        token_id=tids[0],
        price=0.50,
        size=min_size,
        side=BUY,
    )
    
    options = CreateOrderOptions(
        tick_size=tick,
        neg_risk=neg_risk,
    )
    
    signed_order = client.create_order(order_args, options)
    print(f"  ✅ Order signed")
    print(f"  Type: {type(signed_order)}")
    print(f"  Order: {str(signed_order)[:500]}")
    
    # Post order
    print(f"\n=== Post Order ===")
    try:
        result = client.post_order(signed_order, OrderType.GTC)
        print(f"  ✅ {json.dumps(result, indent=2, default=str)}")
        with open("/tmp/test_order.json", "w") as f:
            json.dump({"result": result, "yes_token": tids[0], "question": question}, f, default=str)
    except Exception as e:
        print(f"  ❌ post_order: {e}")
        if hasattr(e, 'msg'): print(f"  msg: {e.msg}")
        
        # Try FOK
        print(f"\n  Trying FOK...")
        try:
            result = client.post_order(signed_order, OrderType.FOK)
            print(f"  ✅ FOK: {json.dumps(result, indent=2, default=str)}")
        except Exception as e2:
            print(f"  ❌ FOK: {e2}")
            if hasattr(e2, 'msg'): print(f"  msg: {e2.msg}")

except Exception as e:
    print(f"  ❌ create_order: {e}")
    import traceback
    traceback.print_exc()

print("\n=== Done ===")
