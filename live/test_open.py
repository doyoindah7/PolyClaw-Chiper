"""
Open a small test trade on Polymarket live — $1 position
Uses py-clob-client SDK with L2 credentials
"""
import os, sys, json, time
from pathlib import Path

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

RPC_URL = os.environ.get("POLYGON_RPC_URL")
PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
WALLET = os.environ.get("BOT_ADDRESS")
L2_KEY = os.environ.get("POLYMARKET_API_KEY")
L2_SECRET = os.environ.get("POLYMARKET_API_SECRET")
L2_PASS = os.environ.get("POLYMARKET_API_PASSPHRASE")
BUILDER_CODE = os.environ.get("BUILDER_CODE")

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.constants import POLYGON
from py_clob_client.order_builder.constants import BUY, SELL

creds = ApiCreds(api_key=L2_KEY, api_secret=L2_SECRET, api_passphrase=L2_PASS)

client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY,
    chain_id=137,
    creds=creds,
    signature_type=0,  # EOA
    funder=WALLET,
)

print("=== PolyClaw Live Trade Test ===")
print(f"Wallet: {WALLET}")

# Step 1: Get a market with good liquidity
print("\n[1] Finding a liquid market...")
import urllib.request

# Use CLOB API to get markets (no Content-Type header = no geo-block)
req = urllib.request.Request(
    "https://clob.polymarket.com/markets?limit=20",
    headers={"User-Agent": "PolyClawCipher/3.5.17"}
)
with urllib.request.urlopen(req, timeout=10) as resp:
    markets = json.loads(resp.read().decode())

# Find a market with good liquidity
best = None
for m in markets:
    try:
        token = m.get("clob_token_ids", [])
        if not token or len(token) < 2:
            continue
        bids = m.get("bids", [])
        asks = m.get("asks", [])
        if not bids or not asks:
            continue
        best_bid = float(bids[-1].get("price", 0)) if bids else 0
        best_ask = float(asks[0].get("price", 0)) if asks else 0
        spread = best_ask - best_bid
        if spread > 0 and spread < 0.05:
            best = m
            break
    except:
        continue

if not best:
    # Just take first market with tokens
    for m in markets:
        token = m.get("clob_token_ids", [])
        if token and len(token) >= 2:
            best = m
            break

if not best:
    print("❌ No suitable market found")
    sys.exit(1)

question = best.get("question", "?")[:80]
token_ids = best.get("clob_token_ids", [])
condition_id = best.get("condition_id", "")
print(f"  Market: {question}")
print(f"  Condition ID: {condition_id}")
print(f"  Tokens: {token_ids}")

# Pick YES token (first token)
yes_token = token_ids[0]
no_token = token_ids[1] if len(token_ids) > 1 else None

# Get orderbook for this market
print(f"\n[2] Checking orderbook...")
try:
    book = client.get_order_book(yes_token)
    print(f"  Orderbook: bids={len(book.bids) if hasattr(book,'bids') else '?'} asks={len(book.asks) if hasattr(book,'asks') else '?'}")
    if hasattr(book, 'bids') and book.bids:
        print(f"  Best bid: {book.bids[-1].price if book.bids else 'none'}")
    if hasattr(book, 'asks') and book.asks:
        print(f"  Best ask: {book.asks[0].price if book.asks else 'none'}")
except Exception as e:
    print(f"  Orderbook error: {e}")

# Step 3: Place a small BUY order — $1 on YES at market price
print(f"\n[3] Placing test order: $1 BUY YES...")

# Try to place order at a reasonable price
# Use 0.50 as default if can't read orderbook
price = 0.50
size = 2  # 2 shares at 0.50 = $1

try:
    # Create order args
    order_args = OrderArgs(
        token_id=yes_token,
        price=price,
        size=size,
        side=BUY,
    )
    
    # Build and sign order
    signed_order = client.create_order(order_args)
    
    # Place order
    result = client.post_order(signed_order, OrderType.GTC)
    print(f"  ✅ Order placed!")
    print(f"  Result: {json.dumps(result, indent=2, default=str)}")
    
    order_id = result.get("orderID", result.get("id", ""))
    print(f"  Order ID: {order_id}")
    
    # Save order info
    with open("/tmp/test_order.json", "w") as f:
        json.dump({
            "order_id": order_id,
            "token_id": yes_token,
            "no_token": no_token,
            "price": price,
            "size": size,
            "side": "BUY",
            "condition_id": condition_id,
            "question": question,
            "result": result,
            "timestamp": time.time(),
        }, f, indent=2, default=str)
    print("  Saved to /tmp/test_order.json")
    
except Exception as e:
    print(f"  ❌ Order error: {e}")
    import traceback
    traceback.print_exc()

print("\n=== Test trade complete ===")
