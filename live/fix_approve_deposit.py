"""
1. Get correct exchange addresses from CLOB API
2. Approve USDC.e to correct contracts
3. Deposit USDC.e to exchange
4. Find active market with orderbook
5. Place test order
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
RPC_URL = os.environ.get("POLYGON_RPC_URL", "https://rpc-mainnet.matic.quiknode.pro")

from web3 import Web3
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType, CreateOrderOptions, BalanceAllowanceParams, AssetType
from py_clob_client.order_builder.constants import BUY

creds = ApiCreds(api_key=L2_KEY, api_secret=L2_SECRET, api_passphrase=L2_PASS)
client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=creds,
    signature_type=0, funder=WALLET,
)

w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 15}))
account = w3.eth.account.from_key(PRIVATE_KEY)

print("=== Step 1: Get Exchange Addresses ===")
try:
    ctf = client.get_exchange_address()
    print(f"  CTF Exchange: {ctf}")
except Exception as e:
    print(f"  Error: {e}")
    ctf = None

try:
    coll = client.get_collateral_address()
    print(f"  Collateral (USDC): {coll}")
except Exception as e:
    print(f"  Error: {e}")
    coll = None

try:
    cond = client.get_conditional_address()
    print(f"  Conditional: {cond}")
except Exception as e:
    print(f"  Error: {e}")

# Check balance again
print("\n=== Step 2: Current Balance ===")
params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
bal = client.get_balance_allowance(params)
print(f"  Full response: {bal}")
print(f"  Balance: {bal.get('balance', '0')}")
print(f"  Allowances: {json.dumps(bal.get('allowances', {}), indent=2)}")

# The allowance addresses are the ones we need to approve
allowance_addrs = list(bal.get('allowances', {}).keys())
print(f"\n  Need to approve USDC.e to: {allowance_addrs}")

# Step 3: Approve USDC.e to these addresses
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ERC20_ABI = [
    {"constant":False,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
]

usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
decimals = usdc.functions.decimals().call()
usdc_bal = usdc.functions.balanceOf(account.address).call()
print(f"\n=== Step 3: Approve USDC.e ===")
print(f"  USDC.e balance: {usdc_bal / (10**decimals):.6f}")
print(f"  MATIC: {w3.from_wei(w3.eth.get_balance(account.address), 'ether'):.4f}")

gas_price = w3.eth.gas_price
min_gas = w3.to_wei(35, "gwei")
gas_price = max(gas_price, min_gas)
print(f"  Gas price: {w3.from_wei(gas_price, 'gwei'):.1f} Gwei")

MAX_UINT = 2**256 - 1
approved_any = False

for addr in allowance_addrs:
    addr_cs = Web3.to_checksum_address(addr)
    current = usdc.functions.allowance(account.address, addr_cs).call()
    print(f"\n  {addr}:")
    print(f"    Current allowance: {current / (10**decimals):.6f}")
    
    if current > 0:
        print(f"    ⏭️ Already approved")
        continue
    
    nonce = w3.eth.get_transaction_count(account.address)
    try:
        gas_est = usdc.functions.approve(addr_cs, MAX_UINT).estimate_gas({"from": account.address})
        tx = usdc.functions.approve(addr_cs, MAX_UINT).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": gas_est + 20000,
            "gasPrice": gas_price,
            "chainId": 137,
        })
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        status = "✅" if receipt.status == 1 else "❌"
        cost = w3.from_wei(receipt.gasUsed * gas_price, "ether")
        print(f"    {status} Tx: {tx_hash.hex()[:20]}... gas: {receipt.gasUsed}, cost: {cost:.6f} MATIC")
        approved_any = True
    except Exception as e:
        print(f"    ❌ Error: {e}")

# Step 4: Update balance allowance on CLOB
print(f"\n=== Step 4: Update Balance/Allowance on CLOB ===")
try:
    result = client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  ✅ Update result: {result}")
except Exception as e:
    print(f"  ❌ Error: {e}")

# Re-check balance
try:
    bal2 = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  Updated balance: {bal2.get('balance', '0')}")
    print(f"  Updated allowances: {json.dumps(bal2.get('allowances', {}), indent=2)}")
except Exception as e:
    print(f"  Error: {e}")

print("\n=== Done ===")
