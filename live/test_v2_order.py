"""
V2: Use OrderArgsV2 + try order placement on any market
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

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    ApiCreds, OrderArgs, OrderArgsV1, OrderArgsV2,
    CreateOrderOptions, OrderType, PartialCreateOrderOptions,
    BalanceAllowanceParams, AssetType
)
from py_clob_client_v2.order_builder.constants import BUY

creds = ApiCreds(api_key=L2_KEY, api_secret=L2_SECRET, api_passphrase=L2_PASS)

# Try with different signature types
print("=== V2 Order Placement Test ===\n")

# Get sampling markets
client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=creds,
    signature_type=0, funder=WALLET,
)

resp = client.get_sampling_markets()
data = resp.get("data", [])

# Take first market with tokens
best = None
for m in data:
    tokens = m.get("tokens", [])
    if tokens and len(tokens) >= 2:
        tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
        if len(tids) >= 2:
            best = m
            break

if not best:
    print("❌ No market found")
    sys.exit(1)

tids = [t.get("token_id") for t in best.get("tokens", []) if isinstance(t, dict)]
q = best.get("question", "?")[:70]
neg_risk = bool(best.get("neg_risk", False))
tick = str(best.get("minimum_tick_size", "0.01"))
min_size = float(best.get("minimum_order_size", 5))

print(f"Market: {q}")
print(f"Token: {tids[0][:20]}..., neg_risk={neg_risk}, tick={tick}, min={min_size}")
print(f"enable_order_book: {best.get('enable_order_book')}")

# Try V2 order args
print(f"\n--- Attempt 1: OrderArgsV2 ---")
try:
    args = OrderArgsV2(
        token_id=tids[0],
        price=0.048,
        size=min_size,
        side=BUY,
    )
    print(f"  OrderArgsV2 created: {args}")
    
    options = PartialCreateOrderOptions(tick_size=tick, neg_risk=neg_risk)
    signed = client.create_order(args, options)
    print(f"  ✅ Signed")
    result = client.post_order(signed, OrderType.GTC)
    print(f"  🎉 PLACED! {json.dumps(result, indent=2, default=str)}")
    with open("/tmp/test_order.json", "w") as f:
        json.dump({"result": result}, f, default=str)
except Exception as e:
    print(f"  ❌ {e}")

# Try create_and_post_order
print(f"\n--- Attempt 2: create_and_post_order (V2) ---")
try:
    args = OrderArgsV2(token_id=tids[0], price=0.048, size=min_size, side=BUY)
    result = client.create_and_post_order(args)
    print(f"  🎉 {json.dumps(result, indent=2, default=str)}")
except Exception as e:
    print(f"  ❌ {e}")

# Try with OrderArgsV1
print(f"\n--- Attempt 3: OrderArgsV1 ---")
try:
    args = OrderArgsV1(token_id=tids[0], price=0.048, size=min_size, side=BUY)
    options = PartialCreateOrderOptions(tick_size=tick, neg_risk=neg_risk)
    signed = client.create_order(args, options)
    result = client.post_order(signed, OrderType.GTC)
    print(f"  🎉 {json.dumps(result, indent=2, default=str)}")
except Exception as e:
    print(f"  ❌ {e}")

# Check what OrderArgsV2 vs OrderArgsV1 vs OrderArgs look like
import inspect
print(f"\n--- OrderArgs signatures ---")
print(f"  OrderArgs: {inspect.signature(OrderArgs.__init__)}")
try:
    print(f"  OrderArgsV1: {inspect.signature(OrderArgsV1.__init__)}")
except:
    print(f"  OrderArgsV1: no __init__")
try:
    print(f"  OrderArgsV2: {inspect.signature(OrderArgsV2.__init__)}")
except:
    print(f"  OrderArgsV2: no __init__")

# Check order builder V2
from py_clob_client_v2.order_builder.builder import OrderBuilder
src = inspect.getsource(OrderBuilder.create_order)
print(f"\n  OrderBuilder.create_order (first 300 chars):")
print(src[:300])

print("\n=== Done ===")
