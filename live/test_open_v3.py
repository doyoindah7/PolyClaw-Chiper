"""
Open test trade — find active market with tokens, place $1 order
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

print("=== PolyClaw Live Trade Test ===")

# Get markets
print("\n[1] Getting markets from CLOB...")
resp = client.get_markets()
data = resp.get("data", []) if isinstance(resp, dict) else resp
print(f"  Total markets: {len(data)}")

# Find active market with tokens
print("\n[2] Finding active market with tokens...")
best = None
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
    # Check tokens have token_id
    token_ids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
    if len(token_ids) >= 2:
        best = m
        break

if not best:
    # Fallback: any active market with tokens field
    for m in data:
        if not isinstance(m, dict):
            continue
        if not m.get("active"):
            continue
        tokens = m.get("tokens", [])
        if tokens and len(tokens) >= 2:
            token_ids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
            if len(token_ids) >= 2:
                best = m
                break

if not best:
    print("❌ No active market with tokens found")
    # Show first 5 active markets for debug
    active = [m for m in data if isinstance(m, dict) and m.get("active")]
    print(f"  Active markets: {len(active)}")
    for m in active[:5]:
        print(f"    - {m.get('question','?')[:60]} | accepting={m.get('accepting_orders')} | tokens={len(m.get('tokens',[]))}")
    sys.exit(1)

question = best.get("question", "?")[:80]
condition_id = best.get("condition_id", "")
tokens = best.get("tokens", [])
token_ids = [t.get("token_id") for t in tokens if isinstance(t, dict)]
print(f"  Market: {question}")
print(f"  Condition ID: {condition_id}")
print(f"  Token IDs: {token_ids}")
print(f"  Accepting orders: {best.get('accepting_orders')}")
print(f"  Min order size: {best.get('minimum_order_size')}")

yes_token = token_ids[0]
no_token = token_ids[1] if len(token_ids) > 1 else None

# Check orderbook
print(f"\n[3] Checking orderbook...")
try:
    book = client.get_order_book(yes_token)
    print(f"  Type: {type(book)}")
    print(f"  Raw: {str(book)[:400]}")
except Exception as e:
    print(f"  Error: {e}")

# Place order — $1 BUY YES at 0.50
print(f"\n[4] Placing test order: BUY YES @ 0.50, size=2 ($1)...")
price = 0.50
size = 2.0

try:
    order_args = OrderArgs(
        token_id=yes_token,
        price=price,
        size=size,
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
            "no_token": no_token,
            "condition_id": condition_id,
            "question": question,
            "price": price,
            "size": size,
            "timestamp": time.time(),
        }, f, indent=2, default=str)
    print("  Saved to /tmp/test_order.json")
    
except Exception as e:
    print(f"  ❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n=== Done ===")
