#!/usr/bin/env python3
"""Check wallet balances: MATIC, USDC.e, and contract allowances.

Reads wallet address + RPC URL from .env, queries Polygon chain.

USAGE:
    python live/wallet/check_balance.py

OUTPUT:
    - MATIC balance (for gas)
    - USDC.e balance (trading capital)
    - Allowances for 4 contracts (CTF Exchange, NegRisk Adapter, etc.)
    - Status: READY to trade or NEEDS setup

NO private key needed — read-only operations.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Load .env
from pathlib import Path
env_path = Path(__file__).parent.parent / ".env"
if not env_path.exists():
    print(f"ERROR: .env file not found at {env_path}")
    print("Create it: cp .env.example .env")
    sys.exit(1)

with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# Config
WALLET_ADDRESS = os.environ.get("WALLET_ADDRESS", "")
RPC_URL = os.environ.get("POLYGON_RPC_URL", "")
USDC_ADDRESS = os.environ.get("USDC_ADDRESS", "0x2791bca1f2de4661ed88a30c99a7a9449aa84174")
CTF_EXCHANGE = os.environ.get("CTF_EXCHANGE_ADDRESS", "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
NEG_RISK_ADAPTER = os.environ.get("NEGATIVE_RISK_ADAPTER_ADDRESS", "0xE5407fE24858D1c01c2Be6d19B5213a7dB5b4F40")
CONDITIONAL_TOKENS = os.environ.get("CONDITIONAL_TOKENS_ADDRESS", "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")

if not WALLET_ADDRESS or not WALLET_ADDRESS.startswith("0x"):
    print("ERROR: WALLET_ADDRESS not set in .env")
    sys.exit(1)
if not RPC_URL or "YOUR_" in RPC_URL:
    print("ERROR: POLYGON_RPC_URL not set in .env (still placeholder)")
    sys.exit(1)

try:
    from web3 import Web3
except ImportError:
    print("ERROR: web3 not installed.")
    print("Install: pip install web3")
    sys.exit(1)


# ERC-20 ABI (minimal: balanceOf, allowance)
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "remaining", "type": "uint256"}],
        "type": "function",
    },
]

# ERC-1155 ABI (for ConditionalTokens)
ERC1155_ABI = [
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_operator", "type": "address"},
        ],
        "name": "isApprovedForAll",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]


def main() -> None:
    print("=" * 60)
    print("  Polyclaw Live — Wallet Balance & Allowance Check")
    print("=" * 60)
    print()
    print(f"  Wallet: {WALLET_ADDRESS}")
    print(f"  RPC:    {RPC_URL[:50]}...")
    print()

    # Connect to Polygon
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))
    if not w3.is_connected():
        print("✗ ERROR: Cannot connect to Polygon RPC")
        print(f"  URL: {RPC_URL}")
        sys.exit(1)

    print(f"✓ Connected to Polygon (chain_id={w3.eth.chain_id})")
    print()

    # Check MATIC balance
    matic_balance_wei = w3.eth.get_balance(Web3.to_checksum_address(WALLET_ADDRESS))
    matic_balance = w3.from_wei(matic_balance_wei, "ether")
    print(f"  MATIC Balance: {matic_balance:.6f} MATIC")

    matic_usd = float(matic_balance) * 0.50  # rough estimate
    print(f"                 (~${matic_usd:.2f} USD)")
    print()

    # Check USDC.e balance
    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_ADDRESS),
        abi=ERC20_ABI,
    )
    usdc_balance_raw = usdc.functions.balanceOf(
        Web3.to_checksum_address(WALLET_ADDRESS)
    ).call()
    usdc_balance = usdc_balance_raw / 1e6  # USDC has 6 decimals
    print(f"  USDC.e Balance: {usdc_balance:.2f} USDC")
    print()

    # Check allowances
    print("  Allowances:")
    print("  ─────────────────────────────────────────────────────")

    wallet = Web3.to_checksum_address(WALLET_ADDRESS)

    # USDC → CTF Exchange
    allowance_usdc_ctf = usdc.functions.allowance(
        wallet, Web3.to_checksum_address(CTF_EXCHANGE)
    ).call() / 1e6
    status = "✓ APPROVED" if allowance_usdc_ctf > 0 else "✗ NOT APPROVED"
    print(f"  USDC → CTF Exchange:       {allowance_usdc_ctf:>12.2f}  {status}")

    # USDC → NegRisk Adapter
    allowance_usdc_neg = usdc.functions.allowance(
        wallet, Web3.to_checksum_address(NEG_RISK_ADAPTER)
    ).call() / 1e6
    status = "✓ APPROVED" if allowance_usdc_neg > 0 else "✗ NOT APPROVED"
    print(f"  USDC → NegRisk Adapter:    {allowance_usdc_neg:>12.2f}  {status}")

    # ConditionalTokens (ERC-1155) → CTF Exchange
    ctf = w3.eth.contract(
        address=Web3.to_checksum_address(CONDITIONAL_TOKENS),
        abi=ERC1155_ABI,
    )
    ctf_approved_ctf = ctf.functions.isApprovedForAll(
        wallet, Web3.to_checksum_address(CTF_EXCHANGE)
    ).call()
    status = "✓ APPROVED" if ctf_approved_ctf else "✗ NOT APPROVED"
    print(f"  CTF tokens → CTF Exchange: {'YES' if ctf_approved_ctf else 'NO':>12}  {status}")

    # ConditionalTokens (ERC-1155) → NegRisk Adapter
    ctf_approved_neg = ctf.functions.isApprovedForAll(
        wallet, Web3.to_checksum_address(NEG_RISK_ADAPTER)
    ).call()
    status = "✓ APPROVED" if ctf_approved_neg else "✗ NOT APPROVED"
    print(f"  CTF tokens → NegRisk Adapter: {'YES' if ctf_approved_neg else 'NO':>10}  {status}")

    print()

    # Summary
    print("─" * 60)
    print("  SUMMARY:")
    print("─" * 60)

    issues = []
    if matic_balance < 0.5:
        issues.append(f"MATIC balance too low ({matic_balance:.4f} < 0.5). Need ~$1 for gas.")
    if usdc_balance < 1.0:
        issues.append(f"USDC balance too low ({usdc_balance:.2f} < 1.0). Need trading capital.")
    if allowance_usdc_ctf == 0:
        issues.append("USDC not approved for CTF Exchange. Run: approve_contracts.py")
    if allowance_usdc_neg == 0:
        issues.append("USDC not approved for NegRisk Adapter. Run: approve_contracts.py")
    if not ctf_approved_ctf:
        issues.append("ConditionalTokens not approved for CTF Exchange. Run: approve_contracts.py")
    if not ctf_approved_neg:
        issues.append("ConditionalTokens not approved for NegRisk Adapter. Run: approve_contracts.py")

    if issues:
        print("  ⚠️  SETUP INCOMPLETE — fix these issues:")
        for issue in issues:
            print(f"     • {issue}")
        print()
        print("  Run: python live/wallet/approve_contracts.py")
        sys.exit(1)
    else:
        print("  ✅ ALL CHECKS PASSED — wallet ready for trading!")
        print()
        print(f"  MATIC: {matic_balance:.4f} (~${matic_usd:.2f})")
        print(f"  USDC:  {usdc_balance:.2f}")
        print(f"  Approvals: 4/4 ✓")
        print()
        print("  Next: python live/wallet/derive_api_key.py")


if __name__ == "__main__":
    main()
