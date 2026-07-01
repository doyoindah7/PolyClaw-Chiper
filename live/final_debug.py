"""Final debug: get all addresses + try relayer endpoint"""
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

creds = ApiCreds(
    api_key="024fa050-9154-f12a-c1b5-e7b31e3c1e04",
    api_secret="tKq6Vrf8Yzt0O55K0EomApZ3RdmdCxazD9rO_2slXoQ=",
    api_passphrase="9f22be7b1c29a4a61217ae4f0846ee36e775c0c3681ac25ce45115e2f71e161f",
)

client = ClobClient(host="https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=137, creds=creds, signature_type=0, funder=WALLET)

print("=== Final Address Debug ===\n")

tests = {
    "get_address": lambda: client.get_address(),
    "get_exchange_address": lambda: client.get_exchange_address(),
    "get_collateral_address": lambda: client.get_collateral_address(),
    "get_conditional_address": lambda: client.get_conditional_address(),
}

for name, fn in tests.items():
    try:
        result = fn()
        print(f"  {name}: {result}")
    except Exception as e:
        print(f"  {name}: {str(e)[:80]}")

# Try relayer endpoint with builder auth
print("\n--- Relayer test ---")
BUILDER_KEY = os.environ.get("BUILDER_API_KEY")
BUILDER_SECRET = os.environ.get("BUILDER_API_SECRET")
BUILDER_PASS = os.environ.get("BUILDER_API_PASSPHRASE")
print(f"  Builder key: {BUILDER_KEY}")
print(f"  Builder code: {os.environ.get('BUILDER_CODE')}")
print(f"  Relayer key: {os.environ.get('RELAYER_API_KEY')}")
print(f"  Relayer addr: {os.environ.get('RELAYER_ADDRESS')}")

# Try different funder values from the SDK
try:
    from py_clob_client_v2.order_builder.constants import BUY
    from py_clob_client_v2.clob_types import OrderArgs, OrderType, CreateOrderOptions, BalanceAllowanceParams, AssetType
    
    # Test 1: empty funder
    print("\n--- Order test (no funder) ---")
    client2 = ClobClient(host="https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=137, creds=creds, signature_type=0)
    try:
        bal = client2.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        print(f"  Balance: {json.dumps(bal, indent=2)}")
    except Exception as e:
        print(f"  Balance error: {e}")
    
    # Test 2: empty string funder
    print("\n--- Order test (funder='') ---")
    client3 = ClobClient(host="https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=137, creds=creds, signature_type=0, funder="")
    resp = client3.get_sampling_markets()
    data = resp.get("data", [])
    for m in data[:3]:
        tokens = m.get("tokens", [])
        if tokens and len(tokens) >= 2:
            tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
            if len(tids) >= 2:
                args = OrderArgs(token_id=tids[0], price=0.50, size=5.0, side=BUY)
                opts = CreateOrderOptions(tick_size="0.01", neg_risk=False)
                try:
                    signed = client3.create_order(args, opts)
                    result = client3.post_order(signed, OrderType.GTC)
                    print(f"  🎉 ORDER WORKED with empty funder!")
                    print(f"  {json.dumps(result, indent=2, default=str)}")
                except Exception as e:
                    print(f"  ❌ empty funder: {str(e)[:120]}")
                break

except Exception as e:
    print(f"  Error: {e}")

# Test: what does the error response details look like?
print("\n--- Raw order test with detailed error ---")
import urllib.request
try:
    # Use the SDK's internal headers
    from py_clob_client_v2.headers.headers import create_level_2_headers
    from py_clob_client_v2.config import get_config
    config = get_config()
    headers = create_level_2_headers(config, creds)
    
    # POST to order endpoint
    order_data = json.dumps({"token_id": "test", "price": 0.5, "size": 5, "side": "BUY"}).encode()
    req = urllib.request.Request("https://clob.polymarket.com/order", data=order_data, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"  Response: {resp.read().decode()}")
except Exception as e:
    body = e.read().decode() if hasattr(e, 'read') else str(e)
    print(f"  Error full body: {body}")

print("\n=== Done ===")
