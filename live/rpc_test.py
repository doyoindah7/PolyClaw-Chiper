"""
Test Dwellir WSS RPC + retry order at market price (0.048 not 0.50)
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

DWELLIR_WS = "wss://api-polygon-mainnet-full.n.dwellir.com/4be0e187-1528-40c5-9472-49538aeb26e6"
DWELLIR_HTTP = "https://api-polygon-mainnet-full.n.dwellir.com/4be0e187-1528-40c5-9472-49538aeb26e6"

# === Test Dwellir RPC (HTTP) ===
print("=== 1. Test Dwellir RPC (HTTP) ===")
try:
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(DWELLIR_HTTP, request_kwargs={"timeout": 10}))
    if w3.is_connected():
        block = w3.eth.block_number
        print(f"  ✅ Connected! Block: {block}")
    else:
        print("  ❌ Not connected via HTTP, trying WS...")
except Exception as e:
    print(f"  ❌ HTTP: {e}")
    try:
        w3 = Web3(Web3.WebsocketProvider(DWELLIR_WS, websocket_timeout=10))
        if w3.is_connected():
            block = w3.eth.block_number
            print(f"  ✅ WebSocket connected! Block: {block}")
        else:
            print("  ❌ WebSocket not connected")
    except Exception as e2:
        print(f"  ❌ WS: {e2}")
        # Fallback to QuickNode
        quicknode = "https://rpc-mainnet.matic.quiknode.pro"
        w3 = Web3(Web3.HTTPProvider(quicknode, request_kwargs={"timeout": 10}))
        block = w3.eth.block_number
        print(f"  ⚠️ Fallback to QuickNode. Block: {block}")

# Balance check
account = w3.eth.account.from_key(PRIVATE_KEY)
matic = w3.from_wei(w3.eth.get_balance(account.address), 'ether')
print(f"  MATIC: {matic:.4f}")

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
usdc_abi = [
    {"constant":True,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
]
usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=usdc_abi)
dec = usdc.functions.decimals().call()
usdc_bal = usdc.functions.balanceOf(account.address).call()
print(f"  USDC.e: {usdc_bal / (10**dec):.6f}")

# === CLOB test — at market price ===
print("\n=== 2. CLOB: Place Order at Market Price ===")
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType, CreateOrderOptions
from py_clob_client.order_builder.constants import BUY

creds = ApiCreds(api_key=L2_KEY, api_secret=L2_SECRET, api_passphrase=L2_PASS)

# Try signature_type=1 (Poly Proxy) — maybe EOA not supported
for sig_type, sig_name in [(0, "EOA"), (1, "POLY_PROXY"), (2, "GNOSIS_SAFE")]:
    try:
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=PRIVATE_KEY, chain_id=137, creds=creds,
            signature_type=sig_type, funder=WALLET,
        )
        # Quick test: get API keys
        keys = client.get_api_keys()
        print(f"  sig_type={sig_type} ({sig_name}): ✅ auth OK")
        break
    except Exception as e:
        print(f"  sig_type={sig_type} ({sig_name}): ❌ {str(e)[:60]}")

# Find a market (reuse from previous: "Extended FDV above $3B...")
# Market: neg_risk=False, tick=0.001, min=5, best bid=0.036, best ask=0.048
TOKEN_YES = "43187333641922996188398060383389814287787647811837308994701068387397271207198"

print(f"\n  Market: Extended FDV above $3B one day after launch?")
print(f"  Token YES: {TOKEN_YES[:20]}...")
print(f"  Best bid: 0.036, Best ask: 0.048")

# Try at BEST ASK price (0.048) — this is a limit order matching the market
price = 0.048
size = 5.0
tick_str = "0.001"

for price in [0.048, 0.05, 0.10]:
    print(f"\n  Trying BUY {size} @ {price}...")
    try:
        order_args = OrderArgs(token_id=TOKEN_YES, price=price, size=size, side=BUY)
        options = CreateOrderOptions(tick_size=tick_str, neg_risk=False)
        signed = client.create_order(order_args, options)
        result = client.post_order(signed, OrderType.GTC)
        print(f"  ✅ ORDER PLACED! {json.dumps(result, indent=2, default=str)}")
        with open("/tmp/test_order.json", "w") as f:
            json.dump({"result": result, "token": TOKEN_YES, "price": price, "size": size}, f, default=str)
        break
    except Exception as e:
        err = str(e)
        if 'invalid order version' in err:
            print(f"  ❌ invalid order version (still)")
        elif 'insufficient' in err.lower() or 'balance' in err.lower():
            print(f"  ⚠️ {err[:80]}")
        else:
            print(f"  ❌ {err[:80]}")

# === Last resort: raw HTTP request to CLOB ===
print("\n=== 3. Raw CLOB API Test ===")
import urllib.request, hmac, hashlib, base64

# Check if we can at least query orders
try:
    from py_clob_client.headers.headers import create_level_2_headers
    from py_clob_client.config import get_config
    config = get_config()
    
    # Manual GET to /orders
    headers = create_level_2_headers(config, creds)
    req = urllib.request.Request("https://clob.polymarket.com/orders", headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        orders = json.loads(resp.read().decode())
        print(f"  GET /orders: {len(orders) if isinstance(orders, list) else orders}")
except Exception as e:
    print(f"  GET /orders error: {e}")

# Check balance/allowance
try:
    headers = create_level_2_headers(config, creds)
    req = urllib.request.Request(
        "https://clob.polymarket.com/balance-allowance?asset_type=COLLATERAL",
        headers=headers,
        method="GET"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        bal = json.loads(resp.read().decode())
        print(f"  Balance: {json.dumps(bal, indent=2)}")
except Exception as e:
    print(f"  Balance error: {e}")

print("\n=== Done ===")
