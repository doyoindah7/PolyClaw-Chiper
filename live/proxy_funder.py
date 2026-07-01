"""Use Builder address (proxy wallet) as funder instead of EOA"""
import os, sys, json
from pathlib import Path

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
WALLET = os.environ.get("BOT_ADDRESS")  # EOA 0x034F0a...
PROXY = "0xf9f38a1dc12fc665222734cf73b1a8f5daf24e9a"  # Builder address = proxy wallet

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    ApiCreds, OrderArgs, OrderType, CreateOrderOptions,
    BalanceAllowanceParams, AssetType
)
from py_clob_client_v2.order_builder.constants import BUY

creds = ApiCreds(
    api_key="024fa050-9154-f12a-c1b5-e7b31e3c1e04",
    api_secret="tKq6Vrf8Yzt0O55K0EomApZ3RdmdCxazD9rO_2slXoQ=",
    api_passphrase="9f22be7b1c29a4a61217ae4f0846ee36e775c0c3681ac25ce45115e2f71e161f",
)

print(f"=== Try PROXY as funder ===")
print(f"  Signer (EOA): {WALLET}")
print(f"  Funder (Proxy): {PROXY}")

# Try with PROXY as funder
client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=creds,
    signature_type=0, funder=PROXY,  # USE PROXY WALLET
)

# Check balance with proxy
print(f"\n[1] Balance (funder=proxy)...")
try:
    bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  {json.dumps(bal, indent=2, default=str)}")
except Exception as e:
    print(f"  {e}")

# Get market
print(f"\n[2] Market...")
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

tids = [t.get("token_id") for t in best.get("tokens", []) if isinstance(t, dict)]
neg_risk = bool(best.get("neg_risk", False))
tick = str(best.get("minimum_tick_size", "0.01"))
min_size = float(best.get("minimum_order_size", 5))

# Place order
print(f"\n[3] Order: {best.get('question','?')[:50]}")
print(f"  BUY {min_size} YES @ 0.50 (funder={PROXY[:10]}...)")

try:
    args = OrderArgs(token_id=tids[0], price=0.50, size=min_size, side=BUY)
    options = CreateOrderOptions(tick_size=tick, neg_risk=neg_risk)
    signed = client.create_order(args, options)
    result = client.post_order(signed, OrderType.GTC)
    print(f"  🎉🎉🎉 LIVE ORDER PLACED! 🎉🎉🎉")
    print(f"  {json.dumps(result, indent=2, default=str)}")
    with open("/tmp/test_order.json", "w") as f:
        json.dump({"result": result}, f, default=str)
except Exception as e:
    err = str(e)
    print(f"  ❌ {err[:200]}")
    if 'maker address not allowed' in err:
        print(f"  Proxy didn't work either...")

# Also try with EOA as funder but different signature_type
print(f"\n[4] Try signature_type=1 (POLY_PROXY) with EOA funder...")
try:
    client2 = ClobClient(
        host="https://clob.polymarket.com",
        key=PRIVATE_KEY, chain_id=137, creds=creds,
        signature_type=1, funder=WALLET,
    )
    args = OrderArgs(token_id=tids[0], price=0.50, size=min_size, side=BUY)
    signed2 = client2.create_order(args, options)
    result2 = client2.post_order(signed2, OrderType.GTC)
    print(f"  🎉 WORKED with sig_type=1!")
    print(f"  {json.dumps(result2, indent=2, default=str)}")
except Exception as e:
    print(f"  ❌ {str(e)[:150]}")

print("\n=== Done ===")
