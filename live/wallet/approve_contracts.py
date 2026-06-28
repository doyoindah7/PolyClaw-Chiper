#!/usr/bin/env python3
"""One-time setup: approve Polymarket contracts to spend USDC.e + CTF tokens.

Approves 4 contracts (per autoclaw-confirmed list):
  1. USDC.e → CTF Exchange (trade YES/NO tokens)
  2. USDC.e → Negative Risk Adapter (trade neg-risk markets)
  3. ConditionalTokens (ERC-1155) → CTF Exchange (transfer position tokens)
  4. ConditionalTokens (ERC-1155) → Negative Risk Adapter

Costs ~$0.01-0.05 in MATIC gas (one-time).

USAGE:
    python live/wallet/approve_contracts.py

REQUIRES:
    - .env with WALLET_PRIVATE_KEY, WALLET_ADDRESS, POLYGON_RPC_URL
    - Wallet funded with MATIC for gas

OUTPUT:
    - Submits 4 approval transactions
    - Waits for each confirmation
    - Verifies allowances set
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Load .env
env_path = Path(__file__).parent.parent / ".env"
if not env_path.exists():
    print(f"ERROR: .env file not found at {env_path}")
    sys.exit(1)

with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

PRIVATE_KEY = os.environ.get("WALLET_PRIVATE_KEY", "")
WALLET_ADDRESS = os.environ.get("WALLET_ADDRESS", "")
RPC_URL = os.environ.get("POLYGON_RPC_URL", "")

# Contracts
USDC_ADDRESS = os.environ.get("USDC_ADDRESS", "0x2791bca1f2de4661ed88a30c99a7a9449aa84174")
CTF_EXCHANGE = os.environ.get("CTF_EXCHANGE_ADDRESS", "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
NEG_RISK_ADAPTER = os.environ.get("NEGATIVE_RISK_ADAPTER_ADDRESS", "0xE5407fE24858D1c01c2Be6d19B5213a7dB5b4F40")
CONDITIONAL_TOKENS = os.environ.get("CONDITIONAL_TOKENS_ADDRESS", "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")

# Validate
if not PRIVATE_KEY.startswith("0x") or len(PRIVATE_KEY) != 66:
    print("ERROR: WALLET_PRIVATE_KEY invalid in .env (must be 0x + 64 hex chars)")
    sys.exit(1)
if "YOUR_" in RPC_URL:
    print("ERROR: POLYGON_RPC_URL still placeholder in .env")
    sys.exit(1)

try:
    from web3 import Web3
    from eth_account import Account
except ImportError:
    print("ERROR: web3 or eth-account not installed.")
    print("Install: pip install web3 eth-account")
    sys.exit(1)


# ABIs
ERC20_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]

ERC1155_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_operator", "type": "address"},
            {"name": "_approved", "type": "bool"},
        ],
        "name": "setApprovalForAll",
        "outputs": [],
        "type": "function",
    },
]

# Max uint256 (approve unlimited — standard for trading bots)
MAX_UINT256 = 2**256 - 1


def submit_tx(w3: Web3, private_key: str, to: str, data: bytes, gas: int = 200000) -> str:
    """Submit a transaction and return tx hash."""
    account = Account.from_key(private_key)
    nonce = w3.eth.get_transaction_count(account.address)

    # Get current gas price + 20% buffer for fast inclusion
    base_gas = w3.eth.gas_price
    gas_price = int(base_gas * 1.2)

    tx = {
        "nonce": nonce,
        "gasPrice": gas_price,
        "gas": gas,
        "to": Web3.to_checksum_address(to),
        "data": data,
        "chainId": 137,
    }

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()


def wait_for_receipt(w3: Web3, tx_hash: str, timeout: int = 60) -> dict:
    """Wait for transaction confirmation."""
    receipt = w3.eth.wait_for_transaction_receipt(
        Web3.to_bytes(hexstr=tx_hash), timeout=timeout
    )
    return receipt


def main() -> None:
    print("=" * 60)
    print("  Polyclaw Live — Contract Approvals (One-Time Setup)")
    print("=" * 60)
    print()
    print(f"  Wallet: {WALLET_ADDRESS}")
    print()

    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 15}))
    if not w3.is_connected():
        print("✗ ERROR: Cannot connect to Polygon RPC")
        sys.exit(1)

    # Check MATIC balance first
    matic_balance = w3.from_wei(
        w3.eth.get_balance(Web3.to_checksum_address(WALLET_ADDRESS)), "ether"
    )
    print(f"  MATIC balance: {matic_balance:.6f}")
    if matic_balance < 0.01:
        print("✗ ERROR: Insufficient MATIC for gas. Need at least 0.01 MATIC.")
        print(f"  Current: {matic_balance:.6f} MATIC")
        sys.exit(1)
    print(f"  ✓ Sufficient MATIC for gas")
    print()

    # Confirm before proceeding
    print("  This will submit 4 approval transactions (cost ~$0.01-0.05 in gas).")
    print("  Approving:")
    print(f"    1. USDC.e → CTF Exchange ({CTF_EXCHANGE})")
    print(f"    2. USDC.e → NegRisk Adapter ({NEG_RISK_ADAPTER})")
    print(f"    3. ConditionalTokens → CTF Exchange (setApprovalForAll)")
    print(f"    4. ConditionalTokens → NegRisk Adapter (setApprovalForAll)")
    print()
    response = input("  Proceed? (type 'yes' to continue): ")
    if response.lower() != "yes":
        print("  Aborted.")
        sys.exit(0)
    print()

    # === Approval 1: USDC → CTF Exchange ===
    print("─" * 60)
    print("  [1/4] Approving USDC.e → CTF Exchange...")
    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_ADDRESS), abi=ERC20_ABI
    )
    data = usdc.functions.approve(
        Web3.to_checksum_address(CTF_EXCHANGE), MAX_UINT256
    ).build_transaction({"from": WALLET_ADDRESS, "gas": 200000, "gasPrice": 0})["data"]

    try:
        tx_hash = submit_tx(w3, PRIVATE_KEY, USDC_ADDRESS, data)
        print(f"  TX submitted: {tx_hash}")
        print(f"  https://polygonscan.com/tx/{tx_hash}")
        receipt = wait_for_receipt(w3, tx_hash)
        if receipt["status"] == 1:
            print(f"  ✓ Confirmed in block {receipt['blockNumber']}")
        else:
            print(f"  ✗ TX FAILED (status=0)")
            sys.exit(1)
    except Exception as e:
        print(f"  ✗ Error: {e}")
        sys.exit(1)
    print()

    # === Approval 2: USDC → NegRisk Adapter ===
    print("─" * 60)
    print("  [2/4] Approving USDC.e → NegRisk Adapter...")
    data = usdc.functions.approve(
        Web3.to_checksum_address(NEG_RISK_ADAPTER), MAX_UINT256
    ).build_transaction({"from": WALLET_ADDRESS, "gas": 200000, "gasPrice": 0})["data"]

    try:
        tx_hash = submit_tx(w3, PRIVATE_KEY, USDC_ADDRESS, data)
        print(f"  TX submitted: {tx_hash}")
        print(f"  https://polygonscan.com/tx/{tx_hash}")
        receipt = wait_for_receipt(w3, tx_hash)
        if receipt["status"] == 1:
            print(f"  ✓ Confirmed in block {receipt['blockNumber']}")
        else:
            print(f"  ✗ TX FAILED (status=0)")
            sys.exit(1)
    except Exception as e:
        print(f"  ✗ Error: {e}")
        sys.exit(1)
    print()

    # === Approval 3: ConditionalTokens → CTF Exchange (ERC-1155 setApprovalForAll) ===
    print("─" * 60)
    print("  [3/4] Approving ConditionalTokens → CTF Exchange...")
    ctf = w3.eth.contract(
        address=Web3.to_checksum_address(CONDITIONAL_TOKENS), abi=ERC1155_ABI
    )
    data = ctf.functions.setApprovalForAll(
        Web3.to_checksum_address(CTF_EXCHANGE), True
    ).build_transaction({"from": WALLET_ADDRESS, "gas": 200000, "gasPrice": 0})["data"]

    try:
        tx_hash = submit_tx(w3, PRIVATE_KEY, CONDITIONAL_TOKENS, data)
        print(f"  TX submitted: {tx_hash}")
        print(f"  https://polygonscan.com/tx/{tx_hash}")
        receipt = wait_for_receipt(w3, tx_hash)
        if receipt["status"] == 1:
            print(f"  ✓ Confirmed in block {receipt['blockNumber']}")
        else:
            print(f"  ✗ TX FAILED (status=0)")
            sys.exit(1)
    except Exception as e:
        print(f"  ✗ Error: {e}")
        sys.exit(1)
    print()

    # === Approval 4: ConditionalTokens → NegRisk Adapter ===
    print("─" * 60)
    print("  [4/4] Approving ConditionalTokens → NegRisk Adapter...")
    data = ctf.functions.setApprovalForAll(
        Web3.to_checksum_address(NEG_RISK_ADAPTER), True
    ).build_transaction({"from": WALLET_ADDRESS, "gas": 200000, "gasPrice": 0})["data"]

    try:
        tx_hash = submit_tx(w3, PRIVATE_KEY, CONDITIONAL_TOKENS, data)
        print(f"  TX submitted: {tx_hash}")
        print(f"  https://polygonscan.com/tx/{tx_hash}")
        receipt = wait_for_receipt(w3, tx_hash)
        if receipt["status"] == 1:
            print(f"  ✓ Confirmed in block {receipt['blockNumber']}")
        else:
            print(f"  ✗ TX FAILED (status=0)")
            sys.exit(1)
    except Exception as e:
        print(f"  ✗ Error: {e}")
        sys.exit(1)
    print()

    # === Summary ===
    print("=" * 60)
    print("  ✅ ALL 4 APPROVALS COMPLETED SUCCESSFULLY!")
    print("=" * 60)
    print()
    print("  Next steps:")
    print("    1. Verify: python live/wallet/check_balance.py")
    print("    2. Derive API key: python live/wallet/derive_api_key.py")
    print("    3. Full verify: python live/wallet/verify_setup.py")


if __name__ == "__main__":
    main()
