"""Delete old L2 key + create new one for wallet account"""
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
from py_clob_client_v2.clob_types import ApiCreds

# Connect with old creds (still auth OK)
creds = ApiCreds(
    api_key="024fa050-9154-f12a-c1b5-e7b31e3c1e04",
    api_secret="tKq6Vrf8Yzt0O55K0EomApZ3RdmdCxazD9rO_2slXoQ=",
    api_passphrase="9f22be7b1c29a4a61217ae4f0846ee36e775c0c3681ac25ce45115e2f71e161f",
)

client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=creds,
    signature_type=0, funder=WALLET,
)

# Step 1: Delete old key
print("=== Delete old L2 key ===")
try:
    result = client.delete_api_key()
    print(f"  ✅ Deleted: {result}")
except Exception as e:
    print(f"  ❌ delete: {e}")
    # Try alternative: delete_readonly_api_key
    try:
        result = client.delete_readonly_api_key()
        print(f"  ✅ readonly key deleted: {result}")
    except Exception as e2:
        print(f"  ❌ delete readonly: {e2}")

# Step 2: Create new key
print("\n=== Create new L2 key ===")
client2 = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137,
    signature_type=0, funder=WALLET,
)

new_creds = None
try:
    new_creds = client2.create_api_key()
    print(f"  ✅ New key created!")
    print(f"  KEY: {new_creds.api_key}")
    print(f"  SECRET: {new_creds.api_secret}")
    print(f"  PASSPHRASE: {new_creds.api_passphrase}")
except Exception as e:
    print(f"  ❌ create: {e}")

if not new_creds:
    # Try derive again (maybe delete worked)
    try:
        new_creds = client2.create_or_derive_api_key()
        print(f"  Derive: KEY={new_creds.api_key}")
        if new_creds.api_key == "024fa050-9154-f12a-c1b5-e7b31e3c1e04":
            print("  ⚠️ Same key — delete didn't work or not needed")
        else:
            print("  ✅ NEW key!")
    except Exception as e:
        print(f"  ❌ derive: {e}")

# Step 3: Test new key
if new_creds:
    print(f"\n=== Test new key ===")
    client3 = ClobClient(
        host="https://clob.polymarket.com",
        key=PRIVATE_KEY, chain_id=137, creds=new_creds,
        signature_type=0, funder=WALLET,
    )
    
    # Check balance
    from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
    try:
        bal = client3.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        print(f"  Balance: {json.dumps(bal, indent=2, default=str)}")
    except Exception as e:
        print(f"  Balance error: {e}")
    
    # Try order
    from py_clob_client_v2.clob_types import OrderArgs, OrderType, CreateOrderOptions
    from py_clob_client_v2.order_builder.constants import BUY
    
    resp = client3.get_sampling_markets()
    data = resp.get("data", [])
    for m in data[:5]:
        tokens = m.get("tokens", [])
        if tokens and len(tokens) >= 2:
            tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
            if len(tids) >= 2:
                neg_risk = bool(m.get("neg_risk", False))
                tick = str(m.get("minimum_tick_size", "0.01"))
                min_size = float(m.get("minimum_order_size", 5))
                try:
                    args = OrderArgs(token_id=tids[0], price=0.50, size=min_size, side=BUY)
                    options = CreateOrderOptions(tick_size=tick, neg_risk=neg_risk)
                    signed = client3.create_order(args, options)
                    result = client3.post_order(signed, OrderType.GTC)
                    print(f"  🎉🎉🎉 LIVE ORDER PLACED! 🎉🎉🎉")
                    print(f"  {json.dumps(result, indent=2, default=str)}")
                    # Print for .env
                    print(f"\n  === CREDS FOR .env ===")
                    print(f"  POLYMARKET_API_KEY={new_creds.api_key}")
                    print(f"  POLYMARKET_API_SECRET={new_creds.api_secret}")
                    print(f"  POLYMARKET_API_PASSPHRASE={new_creds.api_passphrase}")
                    break
                except Exception as e:
                    print(f"  Order error: {e}")
                    break

print("\n=== Done ===")
