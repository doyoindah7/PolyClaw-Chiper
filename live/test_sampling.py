"""
Use sampling markets (which have orderbook=True) to place test order
"""
import os, sys, json, time
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
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType, CreateOrderOptions
from py_clob_client.order_builder.constants import BUY

creds = ApiCreds(api_key=L2_KEY, api_secret=L2_SECRET, api_passphrase=L2_PASS)
client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=creds,
    signature_type=0, funder=WALLET,
)

print("=== Test Order via Sampling Markets ===\n")

# Get sampling markets (these have enable_order_book=True)
resp = client.get_sampling_markets()
data = resp.get("data", [])
print(f"Sampling markets: {len(data)}")

# Try first 10 markets — check orderbook
best = None
for m in data[:20]:
    if not isinstance(m, dict): continue
    tokens = m.get("tokens", [])
    if not tokens or len(tokens) < 2: continue
    tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
    if len(tids) < 2: continue
    
    q = m.get("question", "?")[:60]
    neg_risk = m.get("neg_risk", False)
    tick = str(m.get("minimum_tick_size", "0.01"))
    min_size = float(m.get("minimum_order_size", 5))
    
    # Check orderbook
    try:
        book = client.get_order_book(tids[0])
        if hasattr(book, 'bids'):
            bids = book.bids or []
            asks = book.asks or []
            if bids or asks:
                best = m
                print(f"✅ {q}")
                print(f"   Bids: {len(bids)}, Asks: {len(asks)}")
                if bids:
                    best_bid = max(bids, key=lambda x: float(x.price) if hasattr(x, 'price') else 0)
                    print(f"   Best bid: {best_bid.price}")
                if asks:
                    best_ask = min(asks, key=lambda x: float(x.price) if hasattr(x, 'price') else 1)
                    print(f"   Best ask: {best_ask.price}")
                break
            else:
                print(f"   ❌ {q} — empty book")
        else:
            print(f"   ❌ {q} — no book attr: {str(book)[:100]}")
    except Exception as e:
        print(f"   ❌ {q} — {str(e)[:60]}")

if not best:
    print("\n❌ No market with active orderbook found in sampling markets")
    sys.exit(1)

tids = [t.get("token_id") for t in best.get("tokens", []) if isinstance(t, dict)]
question = best.get("question", "?")[:70]
neg_risk = bool(best.get("neg_risk", False))
tick = str(best.get("minimum_tick_size", "0.01"))
min_size = float(best.get("minimum_order_size", 5))

print(f"\n=== Market Selected ===")
print(f"  Question: {question}")
print(f"  Token IDs: {tids[:2]}")
print(f"  neg_risk: {neg_risk}")
print(f"  tick_size: {tick}")
print(f"  min_size: {min_size}")

# Try place order
print(f"\n=== Place Test Order ===")
print(f"  Side: BUY YES")
print(f"  Price: 0.50")
print(f"  Size: {min_size} (minimum)")

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
    
    # Create signed order
    signed = client.create_order(order_args, options)
    print(f"  ✅ Order signed successfully")
    
    # Try post with GTC
    result = client.post_order(signed, OrderType.GTC)
    print(f"  ✅ GTC Order placed!")
    print(f"  {json.dumps(result, indent=2, default=str)}")
    
    with open("/tmp/test_order.json", "w") as f:
        json.dump({"result": result, "yes_token": tids[0], "no_token": tids[1] if len(tids)>1 else None, "question": question}, f, default=str)
    
except Exception as e:
    err_msg = str(e)
    print(f"  ❌ Error: {err_msg}")
    if hasattr(e, 'msg'):
        print(f"  msg: {e.msg}")
    
    # If "invalid order version", try different approaches
    if 'invalid order version' in err_msg:
        print(f"\n  === Trying Alternative Approaches ===")
        
        # Approach 1: create_and_post_order (no options)
        print(f"\n  [A] create_and_post_order (no options)...")
        try:
            result = client.create_and_post_order(order_args)
            print(f"  ✅ {json.dumps(result, indent=2, default=str)}")
        except Exception as e2:
            print(f"  ❌ {e2}")
        
        # Approach 2: create_and_post_order (with options)
        print(f"\n  [B] create_and_post_order (with options)...")
        try:
            result = client.create_and_post_order(order_args, options)
            print(f"  ✅ {json.dumps(result, indent=2, default=str)}")
        except Exception as e2:
            print(f"  ❌ {e2}")
        
        # Approach 3: Try with neg_risk=False even if market says True
        print(f"\n  [C] Try with neg_risk=False...")
        try:
            options2 = CreateOrderOptions(tick_size=tick, neg_risk=False)
            signed2 = client.create_order(order_args, options2)
            result2 = client.post_order(signed2, OrderType.GTC)
            print(f"  ✅ {json.dumps(result2, indent=2, default=str)}")
        except Exception as e2:
            print(f"  ❌ {e2}")
        
        # Approach 4: Try with neg_risk=True
        print(f"\n  [D] Try with neg_risk=True...")
        try:
            options3 = CreateOrderOptions(tick_size=tick, neg_risk=True)
            signed3 = client.create_order(order_args, options3)
            result3 = client.post_order(signed3, OrderType.GTC)
            print(f"  ✅ {json.dumps(result3, indent=2, default=str)}")
        except Exception as e2:
            print(f"  ❌ {e2}")
        
        # Approach 5: Try FOK instead of GTC
        print(f"\n  [E] Try FOK order type...")
        try:
            result5 = client.post_order(signed, OrderType.FOK)
            print(f"  ✅ {json.dumps(result5, indent=2, default=str)}")
        except Exception as e2:
            print(f"  ❌ {e2}")
        
        # Approach 6: Try FAK
        print(f"\n  [F] Try FAK order type...")
        try:
            result6 = client.post_order(signed, OrderType.FAK)
            print(f"  ✅ {json.dumps(result6, indent=2, default=str)}")
        except Exception as e2:
            print(f"  ❌ {e2}")

print("\n=== Done ===")
