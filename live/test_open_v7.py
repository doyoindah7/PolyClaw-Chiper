"""
Deep inspect SDK order builder + try create_and_post_order + check balance
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

print("=== SDK Deep Inspection ===\n")

# 1. Check balance/allowance properly
print("[1] Balance/Allowance check...")
try:
    # Try with BalanceAllowanceParams
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    bal = client.get_balance_allowance(params)
    print(f"  Balance: {bal}")
except Exception as e:
    print(f"  Error: {e}")
    # Try direct call
    try:
        bal = client.get_balance_allowance(asset_type=AssetType.COLLATERAL)
        print(f"  Balance (direct): {bal}")
    except Exception as e2:
        print(f"  Error2: {e2}")

# 2. Check exchange address
print("\n[2] Exchange addresses...")
try:
    ctf = client.get_exchange_address()
    print(f"  CTF Exchange: {ctf}")
except Exception as e:
    print(f"  Error: {e}")

try:
    coll = client.get_collateral_address()
    print(f"  Collateral (USDC): {coll}")
except Exception as e:
    print(f"  Error: {e}")

try:
    cond = client.get_conditional_address()
    print(f"  Conditional: {cond}")
except Exception as e:
    print(f"  Error: {e}")

# 3. Check order builder source
print("\n[3] Order builder inspection...")
from py_clob_client.order_builder.builder import OrderBuilder
src = inspect.getsource(OrderBuilder.create_order)
print(f"  create_order source (first 1500 chars):")
print(src[:1500])

# 4. Check if builder has neg_risk market handling
print("\n[4] Builder methods:")
for m in sorted(dir(OrderBuilder)):
    if not m.startswith('_'):
        print(f"  {m}")

# 5. Check get_neg_risk for a market
print("\n[5] Finding active market with orderbook...")
cursor = "NDAwMA=="  # page 4 where 722 accepting
resp = client.get_markets(next_cursor=cursor)
data = resp.get("data", [])

best = None
for m in data:
    if not isinstance(m, dict): continue
    if not m.get("accepting_orders"): continue
    if not m.get("enable_order_book"): continue
    tokens = m.get("tokens", [])
    if tokens and len(tokens) >= 2:
        tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
        if len(tids) >= 2:
            best = m
            break

if not best:
    # Try without enable_order_book requirement
    for m in data:
        if not isinstance(m, dict): continue
        if not m.get("accepting_orders"): continue
        tokens = m.get("tokens", [])
        if tokens and len(tokens) >= 2:
            tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
            if len(tids) >= 2:
                best = m
                break

if best:
    question = best.get("question", "?")[:70]
    tids = [t.get("token_id") for t in best.get("tokens", []) if isinstance(t, dict)]
    print(f"  Market: {question}")
    print(f"  Tokens: {tids[:2]}")
    print(f"  neg_risk: {best.get('neg_risk')}")
    print(f"  min_size: {best.get('minimum_order_size')}")
    print(f"  enable_book: {best.get('enable_order_book')}")
    
    # Check neg_risk
    try:
        nr = client.get_neg_risk(tids[0])
        print(f"  get_neg_risk: {nr}")
    except Exception as e:
        print(f"  get_neg_risk error: {e}")
    
    # Check tick size
    try:
        ts = client.get_tick_size(tids[0])
        print(f"  tick_size: {ts}")
    except Exception as e:
        print(f"  tick_size error: {e}")
    
    # Try create_and_post_order
    print(f"\n[6] Trying create_and_post_order...")
    min_size = float(best.get("minimum_order_size", 5))
    try:
        # Use CreateOrderOptions with neg_risk
        options = CreateOrderOptions(
            tick_size=best.get("minimum_tick_size", 0.01),
            neg_risk=bool(best.get("neg_risk", False)),
        )
        
        order_args = OrderArgs(
            token_id=tids[0],
            price=0.50,
            size=min_size,
            side=BUY,
        )
        
        result = client.create_and_post_order(order_args, options=options, order_type=OrderType.GTC)
        print(f"  ✅ {json.dumps(result, indent=2, default=str)}")
        
        with open("/tmp/test_order.json", "w") as f:
            json.dump({"result": result, "yes_token": tids[0], "question": question}, f, default=str)
            
    except Exception as e:
        print(f"  ❌ {e}")
        if hasattr(e, 'msg'): print(f"  msg: {e.msg}")
        
        # Try without options
        print(f"\n  Trying without options...")
        try:
            result = client.create_and_post_order(order_args, order_type=OrderType.GTC)
            print(f"  ✅ {json.dumps(result, indent=2, default=str)}")
        except Exception as e2:
            print(f"  ❌ {e2}")
            
    # Try create_market_order
    print(f"\n[7] Trying create_market_order...")
    try:
        result = client.create_market_order(tids[0], BUY, min_size * 0.50)
        print(f"  ✅ {json.dumps(result, indent=2, default=str)}")
    except Exception as e:
        print(f"  ❌ {e}")
        if hasattr(e, 'msg'): print(f"  msg: {e.msg}")

else:
    print("  ❌ No suitable market found")

print("\n=== Done ===")
