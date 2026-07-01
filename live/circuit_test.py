"""
PolyClaw Live Circuit Test — End-to-end verification
Run on VPS (Ireland) where CLOB API is accessible.

Usage: python3 circuit_test.py
Requires: py-clob-client, web3, eth-account
"""
import os, sys, json, time

# Load .env
from pathlib import Path
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

RPC_URL = os.environ.get("POLYGON_RPC_URL", "https://polygon-rpc.com")
PRIVATE_KEY = os.environ.get("PRIVATE_KEY", "")
WALLET = os.environ.get("BOT_ADDRESS", "")
L2_KEY = os.environ.get("POLYMARKET_API_KEY", "")
L2_SECRET = os.environ.get("POLYMARKET_API_SECRET", "")
L2_PASS = os.environ.get("POLYMARKET_API_PASSPHRASE", "")
BUILDER_KEY = os.environ.get("BUILDER_API_KEY", "")
BUILDER_CODE = os.environ.get("BUILDER_CODE", "")

# Contract addresses (Polygon)
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
USDC_E_HOLDER = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

print("=" * 60)
print("  PolyClaw Live Circuit Test")
print("=" * 60)
print(f"  Wallet: {WALLET}")
print(f"  RPC: {RPC_URL}")
print(f"  L2 Key: {L2_KEY[:8]}...")
print(f"  Builder: {BUILDER_KEY[:8]}...")
print("=" * 60)

# === STEP 1: RPC Connection + On-chain Balance ===
print("\n[1/5] Testing RPC connection...")
try:
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))
    if not w3.is_connected():
        print("  ❌ RPC connection failed")
        sys.exit(1)
    block = w3.eth.block_number
    print(f"  ✅ Connected. Block: {block}")
    
    matic_bal = w3.eth.get_balance(WALLET)
    matic_eth = w3.from_wei(matic_bal, "ether")
    print(f"  MATIC: {matic_eth:.4f}")
    
    # USDC.e balance (6 decimals)
    usdc_abi = [{"constant":True,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
                {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
                {"constant":True,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"}]
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=usdc_abi)
    usdc_bal = usdc.functions.balanceOf(Web3.to_checksum_address(WALLET)).call()
    usdc_dec = usdc.functions.decimals().call()
    print(f"  USDC.e: {usdc_bal / (10**usdc_dec):.6f}")
    
    # Check allowance to CTF Exchange
    allow = usdc.functions.allowance(Web3.to_checksum_address(WALLET), Web3.to_checksum_address(CTF_EXCHANGE)).call()
    print(f"  USDC.e allowance to CTF: {allow / (10**usdc_dec):.6f}")
    
except Exception as e:
    print(f"  ❌ RPC error: {e}")
    sys.exit(1)

# === STEP 2: CLOB API Auth (L2 credentials) ===
print("\n[2/5] Testing CLOB API auth...")
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
    
    creds = ApiCreds(
        api_key=L2_KEY,
        api_secret=L2_SECRET,
        api_passphrase=L2_PASS,
    )
    
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=PRIVATE_KEY,
        chain_id=137,
        creds=creds,
        signature_type=0,  # EOA
        funder=WALLET,
    )
    
    # Test: get balance allowance
    bal = client.get_balance_allowance(asset_type=0)  # USDC.e
    print(f"  ✅ CLOB auth OK")
    print(f"  CLOB USDC balance: {bal}")
    
except Exception as e:
    print(f"  ❌ CLOB auth error: {e}")
    import traceback
    traceback.print_exc()

# === STEP 3: List API keys (verify L2 key active) ===
print("\n[3/5] Verifying L2 API key...")
try:
    keys = client.get_api_keys()
    print(f"  ✅ API keys: {keys}")
except Exception as e:
    print(f"  ⚠️ get_api_keys error: {e}")

# === STEP 4: Get open orders ===
print("\n[4/5] Checking open orders...")
try:
    orders = client.get_orders()
    print(f"  ✅ Orders: {orders}")
except Exception as e:
    print(f"  ⚠️ get_orders error: {e}")

# === STEP 5: Get markets (sanity check) ===
print("\n[5/5] Checking market data...")
try:
    import urllib.request
    req = urllib.request.Request("https://gamma-api.polymarket.com/markets?limit=3&active=true")
    with urllib.request.urlopen(req, timeout=10) as resp:
        markets = json.loads(resp.read().decode())
        print(f"  ✅ Gamma API: {len(markets)} markets")
        for m in markets[:3]:
            q = m.get("question", "?")[:60]
            print(f"    - {q}")
except Exception as e:
    print(f"  ⚠️ Gamma API error: {e}")

print("\n" + "=" * 60)
print("  Circuit test complete!")
print("=" * 60)
