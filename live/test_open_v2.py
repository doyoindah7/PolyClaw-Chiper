"""
Open a small test trade — simplified, use Gamma API for market lookup
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

# Step 1: Get markets from CLOB
print("\n[1] Getting markets from CLOB...")
resp = client.get_markets()
print(f"  Type: {type(resp)}")
if isinstance(resp, dict):
    print(f"  Keys: {list(resp.keys())[:10]}")
    data = resp.get("data", resp.get("markets", []))
    if isinstance(data, str):
        data = json.loads(data)
    print(f"  Data type: {type(data)}, count: {len(data) if isinstance(data, list) else '?'}")
    if isinstance(data, list) and len(data) > 0:
        print(f"  First item keys: {list(data[0].keys()) if isinstance(data[0], dict) else type(data[0])}")
        print(f"  First item: {json.dumps(data[0], default=str)[:300]}")
elif isinstance(resp, list):
    print(f"  Count: {len(resp)}")
    if len(resp) > 0:
        print(f"  First: {json.dumps(resp[0], default=str)[:300]}")
else:
    print(f"  Raw: {str(resp)[:500]}")

# Step 2: Find a market with token IDs
print("\n[2] Finding tradeable market...")
markets_list = []
if isinstance(resp, dict):
    markets_list = resp.get("data", [])
    if isinstance(markets_list, str):
        markets_list = json.loads(markets_list)
elif isinstance(resp, list):
    markets_list = resp

best = None
for m in markets_list:
    if not isinstance(m, dict):
        continue
    tokens = m.get("clobTokenIds", m.get("clob_token_ids", []))
    if isinstance(tokens, str):
        try:
            tokens = json.loads(tokens)
        except:
            continue
    if tokens and len(tokens) >= 2:
        best = m
        break

if not best:
    print("❌ No market with token IDs found")
    print(f"Checked {len(markets_list)} markets")
    sys.exit(1)

question = best.get("question", best.get("q", "?"))[:80]
tokens = best.get("clobTokenIds", best.get("clob_token_ids", []))
if isinstance(tokens, str):
    tokens = json.loads(tokens)
condition_id = best.get("conditionId", best.get("condition_id", ""))

print(f"  Market: {question}")
print(f"  Condition ID: {condition_id}")
print(f"  Token IDs: {tokens}")

yes_token = tokens[0]
no_token = tokens[1] if len(tokens) > 1 else None

# Step 3: Check orderbook
print(f"\n[3] Checking orderbook for YES token...")
try:
    book = client.get_order_book(yes_token)
    print(f"  Orderbook type: {type(book)}")
    print(f"  Raw: {str(book)[:500]}")
except Exception as e:
    print(f"  Error: {e}")

# Step 4: Place test order — $1 BUY YES at 0.50
print(f"\n[4] Placing test order: $1 BUY YES @ 0.50...")
price = 0.50
size = 2  # 2 shares at $0.50 = $1

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
