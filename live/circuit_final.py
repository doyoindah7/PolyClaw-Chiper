"""Derive L2 API key + check balance + place test order on wallet account"""
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

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    ApiCreds, OrderArgs, OrderType, CreateOrderOptions,
    BalanceAllowanceParams, AssetType
)
from py_clob_client_v2.order_builder.constants import BUY

# Step 1: Derive new L2 API key
print("=== 1. Derive New L2 API Key ===")
try:
    client_no_creds = ClobClient(
        host="https://clob.polymarket.com",
        key=PRIVATE_KEY,
        chain_id=137,
        signature_type=0,
        funder=WALLET,
    )
    creds = client_no_creds.create_or_derive_api_key()
    print(f"  ✅ L2 Key: {creds.api_key}")
    print(f"  Secret: {creds.api_secret}")
    print(f"  Passphrase: {creds.api_passphrase}")
except Exception as e:
    print(f"  ❌ {e}")
    # Maybe already derived? Try reading from env
    if os.environ.get("POLYMARKET_API_KEY"):
        creds = ApiCreds(
            api_key=os.environ.get("POLYMARKET_API_KEY"),
            api_secret=os.environ.get("POLYMARKET_API_SECRET"),
            api_passphrase=os.environ.get("POLYMARKET_API_PASSPHRASE"),
        )
        print(f"  ⚠️ Using existing L2 key")
    else:
        sys.exit(1)

# Step 2: Connect with L2 creds + check balance
print("\n=== 2. Check Balance ===")
client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY,
    chain_id=137,
    creds=creds,
    signature_type=0,
    funder=WALLET,
)

# Verify auth
keys = client.get_api_keys()
print(f"  Auth: ✅ {keys}")

# Check balance
bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
print(f"  Balance: {json.dumps(bal, indent=2, default=str)}")

balance_val = float(bal.get("balance", "0"))
if balance_val <= 0:
    print("  ⚠️ Balance is 0 — deposit might not have settled yet")
else:
    print(f"  💰 Available balance: ${balance_val}")

# Step 3: Get market + place order
print("\n=== 3. Place Test Order ===")
resp = client.get_sampling_markets()
data = resp.get("data", [])

best = None
for m in data[:5]:
    tokens = m.get("tokens", [])
    if not tokens or len(tokens) < 2: continue
    tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
    if len(tids) < 2: continue
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
print(f"  Token: {tids[0][:20]}...")
print(f"  neg_risk={neg_risk}, tick={tick}, min={min_size}")

# Try place order — $1 position
try:
    args = OrderArgs(
        token_id=tids[0],
        price=0.50,
        size=min_size,
        side=BUY,
    )
    options = CreateOrderOptions(tick_size=tick, neg_risk=neg_risk)
    signed = client.create_order(args, options)
    result = client.post_order(signed, OrderType.GTC)
    print(f"  🎉 ORDER PLACED!")
    print(f"  {json.dumps(result, indent=2, default=str)}")
    
    with open("/tmp/test_order.json", "w") as f:
        json.dump({"result": result, "token": tids[0], "question": q}, f, default=str)
        
    # Print L2 creds for .env update
    print(f"\n  === UPDATE .env with these L2 creds ===")
    print(f"  POLYMARKET_API_KEY={creds.api_key}")
    print(f"  POLYMARKET_API_SECRET={creds.api_secret}")
    print(f"  POLYMARKET_API_PASSPHRASE={creds.api_passphrase}")
    
except Exception as e:
    print(f"  ❌ {e}")
    if hasattr(e, 'msg'): print(f"  msg: {e.msg}")

print("\n=== Done ===")
