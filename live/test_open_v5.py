"""
Find accepting_orders markets — inspect their token structure
"""
import os, sys, json
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
from py_clob_client.clob_types import ApiCreds

creds = ApiCreds(api_key=L2_KEY, api_secret=L2_SECRET, api_passphrase=L2_PASS)
client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=creds,
    signature_type=0, funder=WALLET,
)

print("=== Inspecting accepting_orders markets ===\n")

cursor = None
all_accepting = []
pages = 0

while pages < 20:
    if cursor:
        resp = client.get_markets(next_cursor=cursor)
    else:
        resp = client.get_markets()
    
    data = resp.get("data", []) if isinstance(resp, dict) else resp
    new_cursor = resp.get("next_cursor") if isinstance(resp, dict) else None
    
    for m in data:
        if not isinstance(m, dict):
            continue
        if m.get("accepting_orders"):
            all_accepting.append(m)
    
    pages += 1
    print(f"Page {pages}: {len(data)} markets, accepting={sum(1 for m in data if isinstance(m,dict) and m.get('accepting_orders'))}, cursor={new_cursor}")
    
    if not new_cursor or new_cursor == cursor:
        break
    cursor = new_cursor

print(f"\nTotal accepting_orders markets: {len(all_accepting)}")

# Show first 10 accepting markets with token info
for i, m in enumerate(all_accepting[:10]):
    tokens = m.get("tokens", [])
    token_ids = [t.get("token_id", "?")[:20] if isinstance(t, dict) else str(t)[:20] for t in tokens]
    print(f"\n  [{i}] {m.get('question','?')[:70]}")
    print(f"      condition_id: {m.get('condition_id','')[:20]}...")
    print(f"      tokens: {len(tokens)} | ids: {token_ids[:2]}")
    print(f"      min_size: {m.get('minimum_order_size')}")
    print(f"      neg_risk: {m.get('neg_risk')}")
    print(f"      enable_order_book: {m.get('enable_order_book')}")

# Try to get orderbook for the first one with tokens
best = None
for m in all_accepting:
    tokens = m.get("tokens", [])
    if tokens and len(tokens) >= 2:
        tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
        if len(tids) >= 2:
            best = m
            break

if best:
    print(f"\n=== Best market for test trade ===")
    print(f"  {best.get('question','?')[:80]}")
    tids = [t.get("token_id") for t in best.get("tokens", []) if isinstance(t, dict)]
    print(f"  Token IDs: {tids}")
    
    # Try orderbook
    try:
        book = client.get_order_book(tids[0])
        print(f"  Orderbook: {str(book)[:300]}")
    except Exception as e:
        print(f"  Orderbook error: {e}")
    
    # Try place order
    print(f"\n  Placing $1 test order...")
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY
    
    try:
        order_args = OrderArgs(token_id=tids[0], price=0.50, size=2.0, side=BUY)
        signed = client.create_order(order_args)
        result = client.post_order(signed, OrderType.GTC)
        print(f"  ✅ {json.dumps(result, indent=2, default=str)}")
        with open("/tmp/test_order.json", "w") as f:
            json.dump({"result": result, "token_id": tids[0], "no_token": tids[1]}, f, default=str)
    except Exception as e:
        print(f"  ❌ {e}")
        if hasattr(e, 'msg'): print(f"  msg: {e.msg}")
else:
    print("\n❌ No accepting market with token IDs found")

print("\n=== Done ===")
