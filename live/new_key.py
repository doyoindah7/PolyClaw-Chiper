"""Force create new L2 API key for wallet account, then test order"""
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
from py_clob_client_v2.clob_types import OrderArgs, OrderType, CreateOrderOptions

print("=== Create New L2 Key for Wallet Account ===\n")

client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY,
    chain_id=137,
    signature_type=0,
    funder=WALLET,
)

# Try create_api_key directly (skip derive)
print("[1] Create new API key (skip derive)...")
creds = None
try:
    creds = client.create_api_key()
    print(f"  ✅ Created!")
    print(f"  KEY: {creds.api_key}")
    print(f"  SECRET: {creds.api_secret}")
    print(f"  PASSPHRASE: {creds.api_passphrase}")
except Exception as e:
    print(f"  create_api_key: {e}")

if not creds:
    print("\n[2] Try create_or_derive_api_key...")
    try:
        creds = client.create_or_derive_api_key()
        print(f"  KEY: {creds.api_key}")
    except Exception as e:
        print(f"  Error: {e}")

if not creds:
    print("  ❌ Could not create or derive API key")
    print("  Manual step: Go to Polymarket → Settings → API Keys → Create new L2 key")
    sys.exit(1)

# Test the new key
print(f"\n[3] Test new key...")
client2 = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=creds,
    signature_type=0, funder=WALLET,
)

try:
    keys = client2.get_api_keys()
    print(f"  ✅ Auth OK: {keys}")
except Exception as e:
    print(f"  ❌ Auth: {e}")
    sys.exit(1)

# Check balance
try:
    from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
    bal = client2.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  Balance: {json.dumps(bal, indent=2, default=str)}")
except Exception as e:
    print(f"  Balance: {e}")

# Get market
resp = client2.get_sampling_markets()
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
q = best.get("question", "?")[:60]
neg_risk = bool(best.get("neg_risk", False))
tick = str(best.get("minimum_tick_size", "0.01"))
min_size = float(best.get("minimum_order_size", 5))

# Place test order — $1
print(f"\n[4] Place order: {q}")
print(f"  BUY {min_size} YES @ 0.50")

from py_clob_client_v2.order_builder.constants import BUY

try:
    args = OrderArgs(token_id=tids[0], price=0.50, size=min_size, side=BUY)
    options = CreateOrderOptions(tick_size=tick, neg_risk=neg_risk)
    signed = client2.create_order(args, options)
    result = client2.post_order(signed, OrderType.GTC)
    print(f"  🎉🎉🎉 LIVE ORDER PLACED! 🎉🎉🎉")
    print(f"  {json.dumps(result, indent=2, default=str)}")
    with open("/tmp/test_order.json", "w") as f:
        json.dump({"result": result, "token": tids[0]}, f, default=str)
    
    # Print for .env update
    print(f"\n  === CREDENTIALS FOR .env ===")
    print(f"  POLYMARKET_API_KEY={creds.api_key}")
    print(f"  POLYMARKET_API_SECRET={creds.api_secret}")
    print(f"  POLYMARKET_API_PASSPHRASE={creds.api_passphrase}")
    
except Exception as e:
    err = str(e)
    if 'maker address not allowed' in err:
        print(f"  ❌ maker address not allowed — wallet not activated for trading")
        print(f"  Might need: Enable Trading step in UI")
    else:
        print(f"  ❌ {err[:200]}")

print("\n=== Done ===")
