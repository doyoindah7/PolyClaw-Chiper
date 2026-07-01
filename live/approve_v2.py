"""
Approve USDC.e — fix gas price (Polygon min 30 Gwei)
"""
import os, sys, json
from pathlib import Path
from web3 import Web3

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

ERC20_ABI = [
    {"constant":False,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
]

w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 15}))
account = w3.eth.account.from_key(PRIVATE_KEY)

# Check gas price
network_gas = w3.eth.gas_price
min_gas = w3.to_wei(35, "gwei")  # 35 Gwei — above Polygon minimum
gas_price = max(network_gas, min_gas)

print(f"Wallet: {account.address}")
print(f"Network gas price: {w3.from_wei(network_gas, 'gwei'):.1f} Gwei")
print(f"Using gas price: {w3.from_wei(gas_price, 'gwei'):.1f} Gwei")
print(f"MATIC balance: {w3.from_wei(w3.eth.get_balance(account.address), 'ether'):.4f}")
print()

usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
decimals = usdc.functions.decimals().call()
usdc_bal = usdc.functions.balanceOf(account.address).call()
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
    
    nonce = w3.eth.get_transaction_count(account.address)
    
    # Estimate gas first
    try:
        gas_est = usdc.functions.approve(
            Web3.to_checksum_address(spender_addr), MAX_UINT
        ).estimate_gas({"from": account.address})
        print(f"  Estimated gas: {gas_est}")
        gas_limit = gas_est + 20000  # buffer
    except Exception as e:
        print(f"  Gas estimate failed: {e}")
        gas_limit = 100000
    
    tx = usdc.functions.approve(Web3.to_checksum_address(spender_addr), MAX_UINT).build_transaction({
        "from": account.address,
        "nonce": nonce,
        "gas": gas_limit,
        "gasPrice": gas_price,
        "chainId": 137,
    })
    
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  📤 Tx sent: {tx_hash.hex()}")
    
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    status = "✅ SUCCESS" if receipt.status == 1 else "❌ FAILED"
    gas_used = receipt.gasUsed
    cost = w3.from_wei(gas_used * gas_price, "ether")
    print(f"  {status} — gas: {gas_used}, cost: {cost:.6f} MATIC, block: {receipt.blockNumber}")
    
    if receipt.status == 1:
        new_allow = usdc.functions.allowance(account.address, Web3.to_checksum_address(spender_addr)).call()
        print(f"  New allowance: {new_allow / (10**decimals):.6f}")
    else:
        # Try to decode revert reason
        print(f"  ⚠️ Tx reverted. Trying smaller amount...")
        # Retry with exact balance instead of MAX_UINT
        nonce = w3.eth.get_transaction_count(account.address)
        tx2 = usdc.functions.approve(Web3.to_checksum_address(spender_addr), MAX_UINT).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": 200000,
            "gasPrice": gas_price,
            "chainId": 137,
        })
        signed2 = account.sign_transaction(tx2)
        tx_hash2 = w3.eth.send_raw_transaction(signed2.raw_transaction)
        print(f"  📤 Retry tx: {tx_hash2.hex()}")
        receipt2 = w3.eth.wait_for_transaction_receipt(tx_hash2, timeout=120)
        status2 = "✅ SUCCESS" if receipt2.status == 1 else "❌ FAILED"
        print(f"  {status2} — gas: {receipt2.gasUsed}")
        if receipt2.status == 1:
            new_allow = usdc.functions.allowance(account.address, Web3.to_checksum_address(spender_addr)).call()
            print(f"  New allowance: {new_allow / (10**decimals):.6f}")

print("\n=== Done ===")
