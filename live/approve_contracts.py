"""
Approve USDC.e spending to CTF Exchange + NegRisk Exchange
Requires MATIC for gas.
"""
import os, sys, json
from pathlib import Path
from web3 import Web3

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

RPC_URL = os.environ.get("POLYGON_RPC_URL", "https://rpc-mainnet.matic.quiknode.pro")
PRIVATE_KEY = os.environ.get("PRIVATE_KEY", "")
WALLET = os.environ.get("BOT_ADDRESS", "")

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# ERC20 ABI (approve + allowance + balanceOf)
ERC20_ABI = [
    {"constant":False,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
]

w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 15}))
account = w3.eth.account.from_key(PRIVATE_KEY)

print(f"Wallet: {account.address}")
print(f"RPC: {RPC_URL}")
print(f"Block: {w3.eth.block_number}")
print()

usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
usdc_bal = usdc.functions.balanceOf(account.address).call()
decimals = usdc.functions.decimals().call()
print(f"USDC.e balance: {usdc_bal / (10**decimals):.6f}")

MAX_UINT = 2**256 - 1

spenders = [
    ("CTF Exchange", CTF_EXCHANGE),
    ("NegRisk Exchange", NEG_RISK_EXCHANGE),
]

for name, spender_addr in spenders:
    current = usdc.functions.allowance(account.address, Web3.to_checksum_address(spender_addr)).call()
    print(f"\n--- {name} ({spender_addr}) ---")
    print(f"  Current allowance: {current / (10**decimals):.6f}")
    
    if current > 0:
        print(f"  ⏭️ Already approved, skipping")
        continue
    
    # Build approve tx
    nonce = w3.eth.get_transaction_count(account.address)
    tx = usdc.functions.approve(Web3.to_checksum_address(spender_addr), MAX_UINT).build_transaction({
        "from": account.address,
        "nonce": nonce,
        "gas": 60000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 137,
    })
    
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  📤 Tx sent: {tx_hash.hex()}")
    
    # Wait for receipt
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    status = "✅ SUCCESS" if receipt.status == 1 else "❌ FAILED"
    gas_used = receipt.gasUsed
    print(f"  {status} — gas: {gas_used}, block: {receipt.blockNumber}")
    
    # Verify
    new_allow = usdc.functions.allowance(account.address, Web3.to_checksum_address(spender_addr)).call()
    print(f"  New allowance: {new_allow / (10**decimals):.6f}")

print("\n=== Approve complete ===")
