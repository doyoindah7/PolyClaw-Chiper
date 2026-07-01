"""
Test trade — find market with orderbook enabled, try FOK order
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

print("=== Test Trade v6 — enable_order_book=True ===\n")

# Skip to page 4 where 722 markets are accepting orders
cursor = "NDAwMA=="  # page 4
resp = client.get_markets(next_cursor=cursor)
data = resp.get("data", [])

print(f"Page 4: {len(data)} markets")

# Find market with enable_order_book=True AND accepting_orders
best = None
for m in data:
    if not isinstance(m, dict):
        continue
    if not m.get("accepting_orders"):
        continue
    if not m.get("enable_order_book"):
        continue
    tokens = m.get("tokens", [])
    if not tokens or len(tokens) < 2:
        continue
    tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
    if len(tids) >= 2:
        best = m
        break

if not best:
    # Count stats
    accepting = [m for m in data if isinstance(m, dict) and m.get("accepting_orders")]
    with_book = [m for m in accepting if m.get("enable_order_book")]
    print(f"Accepting: {len(accepting)}, with orderbook: {len(with_book)}")
    for m in with_book[:3]:
        tokens = m.get("tokens", [])
        tids = [t.get("token_id","?")[:20] for t in tokens if isinstance(t,dict)]
        print(f"  {m.get('question','?')[:60]} | tokens={tids[:2]}")
    if not with_book:
        print("❌ No market with enable_order_book=True")
        sys.exit(1)

question = best.get("question", "?")[:80]
tokens = best.get("tokens", [])
tids = [t.get("token_id") for t in tokens if isinstance(t, dict)]
print(f"Market: {question}")
print(f"Tokens: {tids[:2]}")
print(f"Min size: {best.get('minimum_order_size')}")
print(f"neg_risk: {best.get('neg_risk')}")

yes_token = tids[0]

# Check orderbook
print(f"\nOrderbook check...")
try:
    book = client.get_order_book(yes_token)
    if hasattr(book, 'bids'):
        bids = book.bids or []
        asks = book.asks or []
        print(f"  Bids: {len(bids)}, Asks: {len(asks)}")
        if bids: print(f"  Best bid: {bids[-1]}")
        if asks: print(f"  Best ask: {asks[0]}")
    else:
        print(f"  Raw: {str(book)[:400]}")
except Exception as e:
    print(f"  Error: {e}")

# Try place order with FOK (Fill or Kill)
print(f"\nPlacing FOK order: BUY 5 shares @ 0.50...")
min_size = best.get("minimum_order_size", 5)
try:
    order_args = OrderArgs(
        token_id=yes_token,
        price=0.50,
        size=float(min_size),  # use minimum order size
        side=BUY,
    )
    signed = client.create_order(order_args)
    result = client.post_order(signed, OrderType.FOK)
    print(f"  ✅ {json.dumps(result, indent=2, default=str)}")
    with open("/tmp/test_order.json", "w") as f:
        json.dump({"result": result, "yes_token": yes_token, "no_token": tids[1] if len(tids)>1 else None, "question": question}, f, default=str)
except Exception as e:
    print(f"  ❌ FOK: {e}")
    
    # Try GTC
    print(f"\n  Trying GTC instead...")
    try:
        result = client.post_order(signed, OrderType.GTC)
        print(f"  ✅ GTC: {json.dumps(result, indent=2, default=str)}")
        with open("/tmp/test_order.json", "w") as f:
            json.dump({"result": result, "yes_token": yes_token, "no_token": tids[1] if len(tids)>1 else None, "question": question}, f, default=str)
    except Exception as e2:
        print(f"  ❌ GTC: {e2}")

print("\n=== Done ===")
