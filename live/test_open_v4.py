"""
Find active markets accepting orders + place test trade
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
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

creds = ApiCreds(api_key=L2_KEY, api_secret=L2_SECRET, api_passphrase=L2_PASS)
client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=creds,
    signature_type=0, funder=WALLET,
)

print("=== Find Active Market + Test Trade ===")

# Paginate through markets to find one that's accepting orders
best = None
cursor = None
pages = 0

while pages < 10 and not best:
    if cursor:
        resp = client.get_markets(next_cursor=cursor)
    else:
        resp = client.get_markets()
    
    data = resp.get("data", []) if isinstance(resp, dict) else resp
    cursor = resp.get("next_cursor") if isinstance(resp, dict) else None
    pages += 1
    
    for m in data:
        if not isinstance(m, dict):
            continue
        if not m.get("active") or m.get("closed"):
            continue
        if not m.get("accepting_orders"):
            continue
        tokens = m.get("tokens", [])
        if not tokens or len(tokens) < 2:
            continue
        token_ids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
        if len(token_ids) >= 2:
            best = m
            break
    
    if not cursor:
        break

if not best:
    print(f"❌ No accepting_orders market found after {pages} pages")
    # Show stats
    active_count = 0
    accepting_count = 0
    for m in data:
        if isinstance(m, dict):
            if m.get("active"): active_count += 1
            if m.get("accepting_orders"): accepting_count += 1
    print(f"  Last page: {len(data)} markets, active={active_count}, accepting={accepting_count}")
    sys.exit(1)

question = best.get("question", "?")[:80]
condition_id = best.get("condition_id", "")
tokens = best.get("tokens", [])
token_ids = [t.get("token_id") for t in tokens if isinstance(t, dict)]

print(f"Market: {question}")
print(f"Condition: {condition_id}")
print(f"Tokens: {token_ids[:2]}")
print(f"Accepting: {best.get('accepting_orders')}")
print(f"Min size: {best.get('minimum_order_size')}")
print(f"Neg risk: {best.get('neg_risk')}")

yes_token = token_ids[0]

# Check orderbook
print(f"\n[3] Orderbook check...")
try:
    book = client.get_order_book(yes_token)
    if hasattr(book, 'bids'):
        bids = book.bids or []
        asks = book.asks or []
        print(f"  Bids: {len(bids)}, Asks: {len(asks)}")
        if bids: print(f"  Best bid: {bids[-1]}")
        if asks: print(f"  Best ask: {asks[0]}")
    else:
        print(f"  Raw: {str(book)[:300]}")
except Exception as e:
    print(f"  Error: {e}")

# Place order — $1 BUY at 0.50
print(f"\n[4] Placing order: BUY 2 shares @ 0.50 = $1...")
try:
    order_args = OrderArgs(
        token_id=yes_token,
        price=0.50,
        size=2.0,
        side=BUY,
    )
    signed_order = client.create_order(order_args)
    result = client.post_order(signed_order, OrderType.GTC)
    print(f"  ✅ Order placed!")
    print(f"  Result: {json.dumps(result, indent=2, default=str)}")
    
    with open("/tmp/test_order.json", "w") as f:
        json.dump({
            "result": result,
            "yes_token": yes_token,
            "no_token": token_ids[1] if len(token_ids)>1 else None,
            "condition_id": condition_id,
            "question": question,
        }, f, indent=2, default=str)
    
except Exception as e:
    print(f"  ❌ Error: {e}")
    # Try to get more details
    if hasattr(e, 'msg'):
        print(f"  Details: {e.msg}")

print("\n=== Done ===")
