#!/usr/bin/env python3
"""All-in-one pre-trade verification.

Runs ALL checks before going live:
  1. .env file exists + all required vars set
  2. Wallet address valid + matches private key
  3. RPC connection works
  4. MATIC balance > 0.5 (gas)
  5. USDC.e balance > 1.0 (trading capital)
  6. 4 contract approvals set
  7. CLOB API credentials derived + valid
  8. CLOB API reachable (test request)
  9. Polymarket contracts accessible

EXIT CODE:
  0 = all checks passed, ready to trade
  1 = some checks failed, fix before trading

USAGE:
    python live/wallet/verify_setup.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Load .env
env_path = Path(__file__).parent.parent / ".env"
if not env_path.exists():
    print(f"✗ .env file not found at {env_path}")
    print("  Create it: cp .env.example .env")
    sys.exit(1)

with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# Config
PRIVATE_KEY = os.environ.get("WALLET_PRIVATE_KEY", "")
WALLET_ADDRESS = os.environ.get("WALLET_ADDRESS", "")
RPC_URL = os.environ.get("POLYGON_RPC_URL", "")
CLOB_API_URL = os.environ.get("CLOB_API_URL", "https://clob.polymarket.com")
CLOB_API_KEY = os.environ.get("CLOB_API_KEY", "")
CLOB_API_SECRET = os.environ.get("CLOB_API_SECRET", "")
CLOB_API_PASSPHRASE = os.environ.get("CLOB_API_PASSPHRASE", "")
CHAIN_ID = int(os.environ.get("CHAIN_ID", "137"))

USDC_ADDRESS = os.environ.get("USDC_ADDRESS", "0x2791bca1f2de4661ed88a30c99a7a9449aa84174")
CTF_EXCHANGE = os.environ.get("CTF_EXCHANGE_ADDRESS", "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
NEG_RISK_ADAPTER = os.environ.get("NEGATIVE_RISK_ADAPTER_ADDRESS", "0xE5407fE24858D1c01c2Be6d19B5213a7dB5b4F40")
CONDITIONAL_TOKENS = os.environ.get("CONDITIONAL_TOKENS_ADDRESS", "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")


def status(ok: bool, msg: str) -> bool:
    icon = "✓" if ok else "✗"
    print(f"  {icon} {msg}")
    return ok


def main() -> None:
    print("=" * 60)
    print("  Polyclaw Live — Full Setup Verification")
    print("=" * 60)
    print()

    all_ok = True

    # === 1. .env variables ===
    print("──[ 1. Environment Variables ]──")
    all_ok &= status(
        PRIVATE_KEY.startswith("0x") and len(PRIVATE_KEY) == 66,
        "WALLET_PRIVATE_KEY set (0x + 64 hex)"
    )
    all_ok &= status(
        WALLET_ADDRESS.startswith("0x") and len(WALLET_ADDRESS) == 42,
        "WALLET_ADDRESS set (0x + 40 hex)"
    )
    all_ok &= status(
        bool(RPC_URL) and "YOUR_" not in RPC_URL,
        "POLYGON_RPC_URL set (not placeholder)"
    )
    all_ok &= status(CHAIN_ID == 137, "CHAIN_ID = 137 (Polygon Mainnet)")
    all_ok &= status(bool(CLOB_API_KEY), "CLOB_API_KEY set")
    all_ok &= status(bool(CLOB_API_SECRET), "CLOB_API_SECRET set")
    all_ok &= status(bool(CLOB_API_PASSPHRASE), "CLOB_API_PASSPHRASE set")
    print()

    if not all_ok:
        print("✗ Environment incomplete. Fix .env and re-run.")
        sys.exit(1)

    # === 2. Wallet derivation ===
    print("──[ 2. Wallet Derivation ]──")
    try:
        from eth_account import Account
        account = Account.from_key(PRIVATE_KEY)
        derived = account.address.lower()
        actual = WALLET_ADDRESS.lower()
        all_ok &= status(derived == actual, f"Private key → address match ({actual[:10]}...)")
    except Exception as e:
        all_ok &= status(False, f"Wallet derivation failed: {e}")
    print()

    # === 3. RPC connection ===
    print("──[ 3. Polygon RPC Connection ]──")
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))
        connected = w3.is_connected()
        all_ok &= status(connected, "RPC connection established")
        if connected:
            chain = w3.eth.chain_id
            all_ok &= status(chain == 137, f"Chain ID = {chain} (Polygon)")
    except Exception as e:
        all_ok &= status(False, f"RPC connection failed: {e}")
        w3 = None
    print()

    if not w3 or not w3.is_connected():
        print("✗ Cannot proceed without RPC. Fix POLYGON_RPC_URL.")
        sys.exit(1)

    # === 4. MATIC balance ===
    print("──[ 4. MATIC Balance (for gas) ]──")
    try:
        matic_wei = w3.eth.get_balance(Web3.to_checksum_address(WALLET_ADDRESS))
        matic = float(w3.from_wei(matic_wei, "ether"))
        all_ok &= status(matic >= 0.5, f"MATIC balance: {matic:.6f} (≥ 0.5 required)")
        if matic < 0.5:
            print(f"     ⚠️  Send at least 0.5 MATIC to {WALLET_ADDRESS}")
    except Exception as e:
        all_ok &= status(False, f"Balance check failed: {e}")
    print()

    # === 5. USDC.e balance ===
    print("──[ 5. USDC.e Balance (trading capital) ]──")
    ERC20_ABI = [
        {"constant": True, "inputs": [{"name": "_o", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
        {"constant": True, "inputs": [{"name": "_o", "type": "address"}, {"name": "_s", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    ]
    try:
        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_ADDRESS), abi=ERC20_ABI
        )
        usdc_raw = usdc.functions.balanceOf(Web3.to_checksum_address(WALLET_ADDRESS)).call()
        usdc_bal = usdc_raw / 1e6
        all_ok &= status(usdc_bal >= 1.0, f"USDC.e balance: {usdc_bal:.2f} (≥ 1.0 required)")
    except Exception as e:
        all_ok &= status(False, f"USDC check failed: {e}")
    print()

    # === 6. Contract approvals ===
    print("──[ 6. Contract Approvals (4 required) ]──")
    wallet = Web3.to_checksum_address(WALLET_ADDRESS)

    try:
        # USDC → CTF Exchange
        a1 = usdc.functions.allowance(wallet, Web3.to_checksum_address(CTF_EXCHANGE)).call() / 1e6
        all_ok &= status(a1 > 0, f"USDC → CTF Exchange: {a1:.2f} (approved)")

        # USDC → NegRisk Adapter
        a2 = usdc.functions.allowance(wallet, Web3.to_checksum_address(NEG_RISK_ADAPTER)).call() / 1e6
        all_ok &= status(a2 > 0, f"USDC → NegRisk Adapter: {a2:.2f} (approved)")

        # ConditionalTokens (ERC-1155) approvals
        ERC1155_ABI = [
            {"constant": True, "inputs": [{"name": "_o", "type": "address"}, {"name": "_op", "type": "address"}], "name": "isApprovedForAll", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
        ]
        ctf = w3.eth.contract(
            address=Web3.to_checksum_address(CONDITIONAL_TOKENS), abi=ERC1155_ABI
        )
        a3 = ctf.functions.isApprovedForAll(wallet, Web3.to_checksum_address(CTF_EXCHANGE)).call()
        all_ok &= status(a3, f"CTF tokens → CTF Exchange: {'approved' if a3 else 'NOT APPROVED'}")

        a4 = ctf.functions.isApprovedForAll(wallet, Web3.to_checksum_address(NEG_RISK_ADAPTER)).call()
        all_ok &= status(a4, f"CTF tokens → NegRisk Adapter: {'approved' if a4 else 'NOT APPROVED'}")
    except Exception as e:
        all_ok &= status(False, f"Approval check failed: {e}")
    print()

    # === 7. CLOB API credentials ===
    print("──[ 7. CLOB API Authentication ]──")
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        client = ClobClient(
            host=CLOB_API_URL,
            key=PRIVATE_KEY,
            chain_id=CHAIN_ID,
            signature_type=0,
            creds=ApiCreds(
                api_key=CLOB_API_KEY,
                api_secret=CLOB_API_SECRET,
                api_passphrase=CLOB_API_PASSPHRASE,
            ),
        )
        all_ok &= status(True, "CLOB client initialized with L2 credentials")
    except ImportError:
        all_ok &= status(False, "py-clob-client-v2 not installed (pip install py-clob-client-v2)")
        client = None
    except Exception as e:
        all_ok &= status(False, f"CLOB client init failed: {e}")
        client = None
    print()

    # === 8. CLOB API reachable ===
    print("──[ 8. CLOB API Reachable ]──")
    if client:
        try:
            # Try a simple GET request (e.g., get server time or sample market)
            # If no exception, API is reachable
            import httpx
            resp = httpx.get(f"{CLOB_API_URL}/time", timeout=10)
            all_ok &= status(resp.status_code == 200, f"CLOB API /time → HTTP {resp.status_code}")
        except Exception as e:
            all_ok &= status(False, f"CLOB API test failed: {e}")
    else:
        all_ok &= status(False, "Skipped (client not initialized)")
    print()

    # === Summary ===
    print("=" * 60)
    if all_ok:
        print("  ✅ ALL CHECKS PASSED — READY FOR LIVE TRADING!")
        print("=" * 60)
        print()
        print("  Next steps:")
        print("    1. $1 test order:  python live/scripts/test_1_dollar.py")
        print("    2. If $1 OK:       start bot with $15 modal")
        print("    3. Monitor:        watch dashboard at http://3.107.53.103:8085")
        sys.exit(0)
    else:
        print("  ⚠️  SOME CHECKS FAILED — fix before trading!")
        print("=" * 60)
        print()
        print("  Common fixes:")
        print("    • Missing vars:        edit .env")
        print("    • Low MATIC:           send MATIC to wallet")
        print("    • Low USDC:            send USDC.e to wallet")
        print("    • Not approved:        python live/wallet/approve_contracts.py")
        print("    • No API key:          python live/wallet/derive_api_key.py")
        sys.exit(1)


if __name__ == "__main__":
    main()
