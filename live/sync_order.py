"""Sync balance + place order — wallet account, balance confirmed $3.73 in UI"""
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

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    ApiCreds, OrderArgs, OrderType, CreateOrderOptions,
    BalanceAllowanceParams, AssetType
)
from py_clob_client_v2.order_builder.constants import BUY

creds = ApiCreds(api_key=L2_KEY, api_secret=L2_SECRET, api_passphrase=L2_PASS)

client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=creds,
    signature_type=0, funder=WALLET,
)

print("=== Sync & Place Order ===")

# Step 1: Update balance allowance (force sync)
print("\n[1] Force sync balance...")
try:
    result = client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  update result: '{result}'")
except Exception as e:
    print(f"  update error: {e}")

# Step 2: Check balance — try multiple methods
print("\n[2] Check balance (all methods)...")
methods = [
    # Method A: COLLATERAL
    lambda: client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)),
    # Method B: USDC (asset_type=1?)
    lambda: client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.USDC)),
    # Method C: No params
    lambda: client.get_balance_allowance(),
]

for i, method in enumerate(methods):
    try:
        bal = method()
        b = float(bal.get("balance", "0")) if isinstance(bal, dict) else 0
        print(f"  [{chr(65+i)}] balance={b}")
    except Exception as e:
        print(f"  [{chr(65+i)}] error: {str(e)[:60]}")

# Step 3: Get market
print("\n[3] Get market...")
resp = client.get_sampling_markets()
data = resp.get("data", [])
best = None
for m in data[:5]:
    tokens = m.get("tokens", [])
    if tokens and len(tokens) >= 2:
        tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
        if len(tids) >= 2:
            best = m
            break

if not best:
    print("  ❌ No market")
    sys.exit(1)

tids = [t.get("token_id") for t in best.get("tokens", []) if isinstance(t, dict)]
q = best.get("question", "?")[:70]
neg_risk = bool(best.get("neg_risk", False))
tick = str(best.get("minimum_tick_size", "0.01"))
min_size = float(best.get("minimum_order_size", 5))

print(f"  Market: {q}")
print(f"  neg_risk={neg_risk}, tick={tick}, min={min_size}")

# Step 4: Place order — $1 position
print(f"\n[4] Place order: BUY {min_size} YES @ 0.50...")
try:
    args = OrderArgs(token_id=tids[0], price=0.50, size=min_size, side=BUY)
    options = CreateOrderOptions(tick_size=tick, neg_risk=neg_risk)
    signed = client.create_order(args, options)
    result = client.post_order(signed, OrderType.GTC)
    print(f"  🎉🎉🎉 ORDER PLACED! 🎉🎉🎉")
    print(f"  {json.dumps(result, indent=2, default=str)}")
    with open("/tmp/test_order.json", "w") as f:
        json.dump({"result": result, "token": tids[0], "question": q}, f, default=str)
except Exception as e:
    err = str(e)
    if 'maker address not allowed' in err:
        print(f"  ❌ maker address not allowed — still need to investigate")
        print(f"  UI shows $3.73 balance but API says maker not allowed")
        print(f"  Might need: Enable Trading step in UI, or different funder address")
    elif 'insufficient' in err.lower():
        print(f"  ⚠️ insufficient balance: {err[:100]}")
    else:
        print(f"  ❌ {err[:150]}")

print("\n=== Done ===")
