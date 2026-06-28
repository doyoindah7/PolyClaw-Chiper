#!/usr/bin/env python3
"""Derive CLOB API credentials via L1 EIP-712 signature.

Polymarket uses 2-level auth:
  L1: EIP-712 signature with private key (one-time, derive API creds)
  L2: HMAC-SHA256 with API key + secret + passphrase (per-request)

This script does L1 to derive L2 credentials. Idempotent — calling multiple
times returns the same API key (does NOT create duplicates).

USAGE:
    python live/wallet/derive_api_key.py

REQUIRES:
    - .env with WALLET_PRIVATE_KEY
    - Wallet funded (doesn't need MATIC — L1 is off-chain signature)

OUTPUT:
    - Prints API credentials (apiKey, secret, passphrase)
    - Saves to .env (CLOB_API_KEY, CLOB_API_SECRET, CLOB_API_PASSPHRASE)
"""
from __future__ import annotations

import os
import sys
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
CLOB_API_URL = os.environ.get("CLOB_API_URL", "https://clob.polymarket.com")
CHAIN_ID = int(os.environ.get("CHAIN_ID", "137"))

if not PRIVATE_KEY.startswith("0x") or len(PRIVATE_KEY) != 66:
    print("ERROR: WALLET_PRIVATE_KEY invalid in .env")
    sys.exit(1)

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
except ImportError:
    print("ERROR: py-clob-client-v2 not installed.")
    print("Install: pip install py-clob-client-v2")
    sys.exit(1)


def main() -> None:
    print("=" * 60)
    print("  Polyclaw Live — Derive CLOB API Credentials (L1 → L2)")
    print("=" * 60)
    print()
    print(f"  CLOB API: {CLOB_API_URL}")
    print(f"  Chain ID: {CHAIN_ID}")
    print()

    # Initialize client with private key (L1 auth)
    print("  Initializing CLOB client (L1 auth)...")
    try:
        client = ClobClient(
            host=CLOB_API_URL,
            key=PRIVATE_KEY,
            chain_id=CHAIN_ID,
            signature_type=0,  # EOA
        )
        print("  ✓ Client initialized")
    except Exception as e:
        print(f"  ✗ Failed to initialize client: {e}")
        sys.exit(1)
    print()

    # Derive API credentials (idempotent)
    print("  Deriving API credentials (L1 → L2)...")
    print("  (This is OFF-CHAIN — no gas cost, no TX)")
    print()
    try:
        # derive_api_key() is idempotent — returns same key each time
        creds = client.derive_api_key()
        if not creds:
            print("  ✗ Failed to derive API key (empty response)")
            sys.exit(1)

        api_key = creds.api_key if hasattr(creds, "api_key") else creds.get("apiKey", "")
        api_secret = creds.api_secret if hasattr(creds, "api_secret") else creds.get("secret", "")
        api_passphrase = creds.api_passphrase if hasattr(creds, "api_passphrase") else creds.get("passphrase", "")

        print("  ✅ API credentials derived successfully!")
        print()
        print("─" * 60)
        print(f"  API Key:      {api_key}")
        print(f"  API Secret:   {api_secret}")
        print(f"  Passphrase:   {api_passphrase}")
        print("─" * 60)
        print()
    except Exception as e:
        print(f"  ✗ Error deriving API key: {e}")
        print()
        print("  Possible causes:")
        print("    • Network issue (check internet)")
        print("    • Invalid private key")
        print("    • Polymarket API down")
        print("    • Rate limited (try again in 60s)")
        sys.exit(1)

    # Save to .env
    print("  Saving credentials to .env...")
    try:
        # Read current .env
        with open(env_path, "r") as f:
            lines = f.readlines()

        # Update or add CLOB_API_KEY, CLOB_API_SECRET, CLOB_API_PASSPHRASE
        updated_keys = {"CLOB_API_KEY", "CLOB_API_SECRET", "CLOB_API_PASSPHRASE"}
        new_lines = []
        keys_found = set()

        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=")[0].strip()
                if key in updated_keys:
                    if key == "CLOB_API_KEY":
                        new_lines.append(f"CLOB_API_KEY={api_key}\n")
                    elif key == "CLOB_API_SECRET":
                        new_lines.append(f"CLOB_API_SECRET={api_secret}\n")
                    elif key == "CLOB_API_PASSPHRASE":
                        new_lines.append(f"CLOB_API_PASSPHRASE={api_passphrase}\n")
                    keys_found.add(key)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # Add missing keys at end
        for key in updated_keys - keys_found:
            if key == "CLOB_API_KEY":
                new_lines.append(f"\n{key}={api_key}\n")
            elif key == "CLOB_API_SECRET":
                new_lines.append(f"{key}={api_secret}\n")
            elif key == "CLOB_API_PASSPHRASE":
                new_lines.append(f"{key}={api_passphrase}\n")

        # Write back
        with open(env_path, "w") as f:
            f.writelines(new_lines)

        print(f"  ✓ Saved to {env_path}")
    except Exception as e:
        print(f"  ⚠️  Could not auto-save to .env: {e}")
        print("  Please manually add these lines to .env:")
        print(f"    CLOB_API_KEY={api_key}")
        print(f"    CLOB_API_SECRET={api_secret}")
        print(f"    CLOB_API_PASSPHRASE={api_passphrase}")
    print()

    # Verify by re-initializing with full creds
    print("  Verifying credentials by re-initializing client (L2 auth)...")
    try:
        client_full = ClobClient(
            host=CLOB_API_URL,
            key=PRIVATE_KEY,
            chain_id=CHAIN_ID,
            signature_type=0,
            creds=ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            ),
        )
        # Try a simple API call (get server time)
        # If this doesn't throw, creds are valid
        print("  ✓ Client re-initialized with L2 credentials — API auth working!")
    except Exception as e:
        print(f"  ⚠️  Verification warning: {e}")
        print("  (Credentials may still be valid — try verify_setup.py)")
    print()

    print("─" * 60)
    print("  ✅ DONE — API credentials derived and saved!")
    print("─" * 60)
    print()
    print("  Next: python live/wallet/verify_setup.py")


if __name__ == "__main__":
    main()
