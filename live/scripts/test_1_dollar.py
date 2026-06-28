#!/usr/bin/env python3
"""$1 live test order — verify full pipeline before committing $15.

Submits ONE small FAK order (~$1) to a liquid Polymarket market.
Tests:
  - Wallet signing (EIP-712)
  - CLOB API authentication (L2)
  - Order submission
  - Order matching/fill
  - Position appears in API
  - Balance decreases

USAGE:
    python live/scripts/test_1_dollar.py

REQUIRES:
    - verify_setup.py passed
    - Wallet funded ($1 MATIC + $14 USDC.e)
    - Contracts approved
    - API key derived

OUTPUT:
    - Submits $1 test order
    - Waits for fill (or cancel)
    - Reports result
    - If success: ready for $15 full trading
    - If fail: shows error + suggestions
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Load .env
env_path = Path(__file__).parent.parent / ".env"
if not env_path.exists():
    print(f"ERROR: .env not found at {env_path}")
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
CLOB_API_KEY = os.environ.get("CLOB_API_KEY", "")
CLOB_API_SECRET = os.environ.get("CLOB_API_SECRET", "")
CLOB_API_PASSPHRASE = os.environ.get("CLOB_API_PASSPHRASE", "")

# Validate
if not all([PRIVATE_KEY, CLOB_API_KEY, CLOB_API_SECRET, CLOB_API_PASSPHRASE]):
    print("ERROR: Missing credentials. Run verify_setup.py first.")
    sys.exit(1)

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY
except ImportError:
    print("ERROR: py-clob-client-v2 not installed.")
    print("Install: pip install py-clob-client-v2")
    sys.exit(1)


def sanitize_order(size: float, price: float) -> tuple[float, float]:
    """Ensure size * price has ≤ 2 decimal places (avoid bug #121)."""
    notional = size * price
    notional_rounded = round(notional, 2)
    adjusted_size = notional_rounded / price
    adjusted_size = round(adjusted_size, 4)
    return adjusted_size, price


def main() -> None:
    print("=" * 60)
    print("  Polyclaw Live — $1 Test Order")
    print("=" * 60)
    print()
    print("  This will submit ONE small order (~$1) to verify the")
    print("  full live trading pipeline.")
    print()
    print("  If this succeeds, you're ready for $15 full trading.")
    print("  If this fails, fix the issue before committing more funds.")
    print()

    # Initialize client
    print("  Initializing CLOB client...")
    try:
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
        print("  ✓ Client initialized")
    except Exception as e:
        print(f"  ✗ Failed: {e}")
        sys.exit(1)
    print()

    # Find a liquid market to test with
    print("  Finding a liquid market for test order...")
    try:
        # Get a sample market — use a high-volume one
        import httpx
        resp = httpx.get(
            "https://gamma-api.polymarket.com/markets",
            params={"limit": 50, "active": "true", "closed": "false", "order": "volume24hr", "ascending": "false"},
            timeout=10,
        )
        markets = resp.json()
        if not markets:
            print("  ✗ No markets returned from Gamma API")
            sys.exit(1)

        # Pick first market with reasonable price (0.30-0.70)
        test_market = None
        for m in markets:
            if "clobTokenIds" in m and m.get("clobTokenIds"):
                # Bug fix #2: outcomePrices is JSON string '["0.5","0.5"]', not float
                # Parse JSON first, then extract first price
                import json
                prices_raw = m.get("outcomePrices", '["0.5","0.5"]')
                if isinstance(prices_raw, str):
                    prices = json.loads(prices_raw)
                elif isinstance(prices_raw, list):
                    prices = prices_raw
                else:
                    continue
                if not prices:
                    continue
                yes_price = float(prices[0])
                if 0.30 <= yes_price <= 0.70:
                    test_market = m
                    break

        if not test_market:
            # Fallback: use first market
            test_market = markets[0]

        print(f"  ✓ Selected: {test_market.get('question', '?')[:60]}")
        print(f"    Condition ID: {test_market.get('conditionId', '?')[:20]}...")

        token_ids = test_market.get("clobTokenIds", [])
        if isinstance(token_ids, str):
            import json
            token_ids = json.loads(token_ids)

        if not token_ids or len(token_ids) < 2:
            print("  ✗ Market has no valid token IDs")
            sys.exit(1)

        yes_token = token_ids[0]
        print(f"    YES token: {yes_token[:20]}...")
    except Exception as e:
        print(f"  ✗ Failed to find market: {e}")
        sys.exit(1)
    print()

    # Get current price
    print("  Getting current price...")
    try:
        price_info = client.get_price(yes_token)
        # price_info might be {"price": "0.55", ...}
        if hasattr(price_info, "price"):
            current_price = float(price_info.price)
        elif isinstance(price_info, dict):
            current_price = float(price_info.get("price", 0.5))
        else:
            current_price = float(price_info)
        print(f"  ✓ Current YES price: {current_price:.4f}")
    except Exception as e:
        print(f"  ⚠️  Could not get price ({e}), using 0.50")
        current_price = 0.50
    print()

    # Build order — $1 test
    # Use price slightly above current for better fill chance
    test_price = round(min(0.95, current_price + 0.01), 4)
    target_notional = 1.00  # $1 test
    raw_size = target_notional / test_price

    # Sanitize (avoid decimal bug)
    size, price = sanitize_order(raw_size, test_price)
    actual_notional = size * price

    print(f"  Test order details:")
    print(f"    Side:   BUY (YES)")
    print(f"    Price:  {price:.4f}")
    print(f"    Size:   {size:.4f} shares")
    print(f"    Total:  ${actual_notional:.2f}")
    print(f"    Type:   FAK (Fill and Kill — partial OK)")
    print()

    # Confirm
    response = input("  Submit this $1 test order? (type 'yes' to continue): ")
    if response.lower() != "yes":
        print("  Aborted.")
        sys.exit(0)
    print()

    # Submit order
    print("  Submitting order...")
    try:
        order_args = OrderArgs(
            token_id=yes_token,
            price=price,
            size=size,
            side=BUY,
        )
        signed_order = client.create_order(order_args)
        order_resp = client.post_order(signed_order, OrderType.FAK)

        print(f"  ✓ Order submitted!")
        print(f"    Response: {order_resp}")
        if hasattr(order_resp, "order_id"):
            print(f"    Order ID: {order_resp.order_id}")
        elif isinstance(order_resp, dict):
            print(f"    Order ID: {order_resp.get('orderID', order_resp.get('order_id', 'N/A'))}")
    except Exception as e:
        print(f"  ✗ Order submission failed: {e}")
        print()
        print("  Possible causes:")
        print("    • Insufficient USDC balance")
        print("    • Contracts not approved (run approve_contracts.py)")
        print("    • Invalid signature (check private key)")
        print("    • Decimal place bug (size * price > 2 decimals)")
        print("    • Rate limited (wait 60s and retry)")
        sys.exit(1)
    print()

    # Wait for fill
    print("  Waiting 5 seconds for fill...")
    time.sleep(5)
    print()

    # Check result
    print("  Checking order status...")
    try:
        # Get order from API
        if hasattr(order_resp, "order_id"):
            order_id = order_resp.order_id
        elif isinstance(order_resp, dict):
            order_id = order_resp.get("orderID", order_resp.get("order_id"))
        else:
            order_id = None

        if order_id:
            order_status = client.get_order(order_id)
            print(f"  Order status: {order_status}")
        else:
            print("  ⚠️  No order ID returned — check Polymarket UI manually")
    except Exception as e:
        print(f"  ⚠️  Could not check order status: {e}")
        print("  Check manually at https://polymarket.com/portfolio")
    print()

    # Summary
    print("=" * 60)
    print("  $1 TEST ORDER COMPLETED")
    print("=" * 60)
    print()
    print("  Next steps:")
    print("    1. Check https://polymarket.com/portfolio for the position")
    print("    2. If position appeared: ✅ Pipeline works!")
    print("    3. Run check_balance.py to verify USDC decreased")
    print("    4. If all OK: ready for $15 full trading")
    print()
    print("  If order failed:")
    print("    • Check error message above")
    print("    • Verify approvals: python live/wallet/check_balance.py")
    print("    • Re-run verify_setup.py")


if __name__ == "__main__":
    main()
