#!/usr/bin/env python3
"""Generate new EOA wallet for Polymarket live trading.

SECURITY:
- This script generates a NEW wallet locally.
- Private key is printed to terminal ONCE.
- Save it to .env file manually.
- NEVER commit .env to git.
- NEVER share private key with anyone.

USAGE:
    python live/wallet/generate_wallet.py

OUTPUT:
    - Prints wallet address + private key
    - Verifies address derivation
    - Shows funding instructions

NO external calls — pure local generation.
"""
from __future__ import annotations

import sys

try:
    from eth_account import Account
except ImportError:
    print("ERROR: eth-account not installed.")
    print("Install: pip install eth-account")
    sys.exit(1)

import secrets


def generate_wallet() -> dict:
    """Generate a new EOA wallet using cryptographically secure random.

    Returns:
        dict with 'address' and 'private_key'
    """
    # Generate 32 bytes of secure random data
    private_key_bytes = secrets.token_bytes(32)
    private_key = "0x" + private_key_bytes.hex()

    # Derive address from private key
    account = Account.from_key(private_key)

    return {
        "address": account.address,
        "private_key": private_key,
    }


def verify_wallet(private_key: str, expected_address: str) -> bool:
    """Verify that private key derives to expected address."""
    account = Account.from_key(private_key)
    return account.address.lower() == expected_address.lower()


def main() -> None:
    print("=" * 60)
    print("  Polyclaw Live — EOA Wallet Generator")
    print("=" * 60)
    print()
    print("Generating new wallet with secure random...")
    print()

    wallet = generate_wallet()

    print("╔══════════════════════════════════════════════════════════╗")
    print("║  ⚠️  CRITICAL SECURITY WARNING  ⚠️                       ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print("║  Anyone with this private key has FULL ACCESS to your   ║")
    print("║  funds. Save it securely. NEVER commit to git.          ║")
    print("║  NEVER share with anyone (including AI assistants).     ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()
    print(f"  Wallet Address : {wallet['address']}")
    print(f"  Private Key    : {wallet['private_key']}")
    print()
    print("─" * 60)
    print()

    # Verify
    if verify_wallet(wallet["private_key"], wallet["address"]):
        print("✓ Verification: private key correctly derives to address")
    else:
        print("✗ Verification FAILED — do not use this wallet!")
        sys.exit(1)

    print()
    print("─" * 60)
    print("  NEXT STEPS:")
    print("─" * 60)
    print()
    print("  1. Copy private key + address to live/.env file:")
    print()
    print(f"     WALLET_PRIVATE_KEY={wallet['private_key']}")
    print(f"     WALLET_ADDRESS={wallet['address']}")
    print()
    print("  2. Fund wallet (send to address above):")
    print("     • $1.00 MATIC (Polygon)  — for gas")
    print("     • $14.00 USDC.e (Polygon) — trading capital")
    print()
    print("  3. Verify funding:")
    print("     python live/wallet/check_balance.py")
    print()
    print("  4. Approve contracts (one-time):")
    print("     python live/wallet/approve_contracts.py")
    print()
    print("  5. Derive CLOB API key:")
    print("     python live/wallet/derive_api_key.py")
    print()
    print("  6. Run full setup verification:")
    print("     python live/wallet/verify_setup.py")
    print()
    print("─" * 60)
    print()
    print("⚠️  SAVE THIS OUTPUT NOW — private key will not be shown again.")
    print("    (This script does NOT save the key to any file.)")
    print()


if __name__ == "__main__":
    main()
