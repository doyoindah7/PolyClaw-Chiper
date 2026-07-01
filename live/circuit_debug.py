"""
1. Check py-order-utils version + order signing code
2. Try get_sampling_markets for active markets
3. Try create_and_post_order (correct API)
4. Check if neg_risk market needs different handling
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

# === 1. Check py-order-utils ===
print("=== 1. Package Versions ===")
import py_clob_client
print(f"  py-clob-client: {py_clob_client.__file__}")

try:
    import py_order_utils
    print(f"  py-order-utils: {py_order_utils.__file__}")
    # Check version
    try:
        import importlib.metadata
        v = importlib.metadata.version('py-order-utils')
        print(f"  version: {v}")
    except:
        pass
    print(f"  dir: {[x for x in dir(py_order_utils) if not x.startswith('_')]}")
except ImportError:
    print("  py-order-utils not found")

try:
    import poly_eip712_structs
    print(f"  poly-eip712-structs: {poly_eip712_structs.__file__}")
except ImportError:
    print("  poly-eip712-structs not found")

# Check order builder signing
from py_clob_client.order_builder.builder import OrderBuilder
print(f"\n  OrderBuilder init signature: {inspect.signature(OrderBuilder.__init__)}")

# Check if there's a version in the order builder
src_module = inspect.getsource(sys.modules['py_clob_client.order_builder.builder'])
# Search for version-related strings
for line in src_module.split('\n'):
    if 'version' in line.lower() or 'ORDER_TYPE' in line or 'EIP712' in line:
        print(f"  {line.strip()}")

# === 2. Check OrderBuilder create ===
print(f"\n=== 2. OrderBuilder Source ===")
src = inspect.getsource(OrderBuilder.create_order)
print(src[:2000])

# === 3. Try get_sampling_markets ===
print(f"\n=== 3. Sampling Markets ===")
try:
    sampling = client.get_sampling_markets()
    print(f"  Type: {type(sampling)}")
    if isinstance(sampling, dict):
        data = sampling.get("data", [])
        print(f"  Count: {len(data)}")
        for m in data[:5]:
            if isinstance(m, dict):
                q = m.get("question", "?")[:60]
                tokens = m.get("tokens", [])
                tids = [t.get("token_id","?")[:20] for t in tokens if isinstance(t, dict)]
                print(f"    {q} | tokens={tids[:2]} | book={m.get('enable_order_book')}")
    elif isinstance(sampling, list):
        print(f"  Count: {len(sampling)}")
        for m in sampling[:5]:
            if isinstance(m, dict):
                q = m.get("question", "?")[:60]
                print(f"    {q}")
except Exception as e:
    print(f"  Error: {e}")

# === 4. Try get_sampling_simplified_markets ===
print(f"\n=== 4. Sampling Simplified Markets ===")
try:
    simplified = client.get_sampling_simplified_markets()
    print(f"  Type: {type(simplified)}")
    if isinstance(simplified, dict):
        data = simplified.get("data", [])
        print(f"  Count: {len(data)}")
        for m in data[:3]:
            print(f"    {str(m)[:200]}")
    elif isinstance(simplified, list):
        print(f"  Count: {len(simplified)}")
        for m in simplified[:3]:
            print(f"    {str(m)[:200]}")
except Exception as e:
    print(f"  Error: {e}")

# === 5. Try to find ANY market with orderbook ===
print(f"\n=== 5. Brute Force: Find Market with Orderbook ===")
# Try getting a single market by using different approach
# Use get_markets with different params
best = None
cursor = None
checked = 0

for page in range(20):
    if cursor:
        resp = client.get_markets(next_cursor=cursor)
    else:
        resp = client.get_markets()
    
    data = resp.get("data", []) if isinstance(resp, dict) else resp
    cursor = resp.get("next_cursor") if isinstance(resp, dict) else None
    checked += len(data)
    
    for m in data:
        if not isinstance(m, dict): continue
        if not m.get("accepting_orders"): continue
        tokens = m.get("tokens", [])
        if not tokens or len(tokens) < 2: continue
        tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
        if len(tids) < 2: continue
        
        # Try orderbook
        try:
            book = client.get_order_book(tids[0])
            if hasattr(book, 'bids'):
                bids = book.bids or []
                asks = book.asks or []
                if bids or asks:
                    best = m
                    print(f"  ✅ Found! {m.get('question','?')[:60]}")
                    print(f"    Bids: {len(bids)}, Asks: {len(asks)}")
                    if bids:
                        print(f"    Best bid: {bids[-1] if bids else 'none'}")
                    if asks:
                        print(f"    Best ask: {asks[0] if asks else 'none'}")
                    break
        except:
            pass
    
    if best:
        break
    if not cursor:
        break

print(f"  Checked {checked} markets across {page+1} pages")

if best:
    tids = [t.get("token_id") for t in best.get("tokens", []) if isinstance(t, dict)]
    neg_risk = bool(best.get("neg_risk", False))
    tick = str(best.get("minimum_tick_size", "0.01"))
    min_size = float(best.get("minimum_order_size", 5))
    
    print(f"\n=== 6. Place Order ===")
    print(f"  Market: {best.get('question','?')[:70]}")
    print(f"  neg_risk: {neg_risk}, tick: {tick}, min: {min_size}")
    
    # Try create_and_post_order with correct signature
    try:
        order_args = OrderArgs(
            token_id=tids[0],
            price=0.50,
            size=min_size,
            side=BUY,
        )
        
        # Try create_and_post_order (no order_type kwarg)
        result = client.create_and_post_order(order_args)
        print(f"  ✅ {json.dumps(result, indent=2, default=str)}")
    except Exception as e:
        print(f"  ❌ create_and_post: {e}")
        if hasattr(e, 'msg'): print(f"  msg: {e.msg}")
        
        # Try create + post separately with options
        try:
            options = CreateOrderOptions(tick_size=tick, neg_risk=neg_risk)
            signed = client.create_order(order_args, options)
            result = client.post_order(signed, OrderType.GTC)
            print(f"  ✅ create+post: {json.dumps(result, indent=2, default=str)}")
        except Exception as e2:
            print(f"  ❌ create+post: {e2}")
            if hasattr(e2, 'msg'): print(f"  msg: {e2.msg}")
            
            # Try FOK
            try:
                result = client.post_order(signed, OrderType.FOK)
                print(f"  ✅ FOK: {json.dumps(result, indent=2, default=str)}")
            except Exception as e3:
                print(f"  ❌ FOK: {e3}")
else:
    print("  ❌ No market with orderbook found after checking all pages")

print("\n=== Done ===")
