"""Retry update_balance_allowance post-key-reset + try all approaches"""
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
    ApiCreds, BalanceAllowanceParams, AssetType,
    OrderArgs, OrderType, CreateOrderOptions
)
from py_clob_client_v2.order_builder.constants import BUY

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

print("=== Post-Key-Reset Balance Sync ===\n")

# 1. Try update_balance_allowance
print("[1] update_balance_allowance...")
try:
    result = client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  Result: '{result}'")
except Exception as e:
    print(f"  Error: {e}")

# 2. Check balance again
print("\n[2] get_balance_allowance...")
try:
    bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  {json.dumps(bal, indent=2, default=str)}")
except Exception as e:
    print(f"  Error: {e}")

# 3. Try update + check in sequence a few times
import time
for i in range(3):
    time.sleep(3)
    try:
        client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        b = bal.get("balance", "0") if isinstance(bal, dict) else "err"
        print(f"  [{i+1}] balance={b}")
    except Exception as e:
        print(f"  [{i+1}] {e}")

# 4. Check what endpoints we CAN call
print("\n[3] Verifying all accessible endpoints...")
tests = [
    ("get_server_time", lambda: client.get_server_time()),
    ("get_api_keys", lambda: client.get_api_keys()),
    ("get_orders", lambda: client.get_open_orders()),
    ("get_balance", lambda: client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))),
]

for name, fn in tests:
    try:
        result = fn()
        print(f"  ✅ {name}: OK")
    except Exception as e:
        print(f"  ❌ {name}: {str(e)[:60]}")

print("\n[4] Summary:")
print("  If balance still 0: UI deposit likely goes to custodial pool")
print("  Need to: find 'Transfer to Exchange' or explicitly deposit to CLOB contract")
print("  OR: make 1 manual trade via UI first to activate the wallet for API trading")
print("\n=== Done ===")
