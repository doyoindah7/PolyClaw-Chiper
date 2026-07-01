"""Solution: POLY_1271 (type 3) + Deposit Wallet as funder"""
import os, sys, json
from pathlib import Path

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
WALLET = os.environ.get("BOT_ADDRESS")  # EOA

# Check what SignatureTypeV2 values are
from py_clob_client_v2 import SignatureTypeV2
print("=== SignatureTypeV2 values ===")
for attr in dir(SignatureTypeV2):
    if not attr.startswith('_'):
        val = getattr(SignatureTypeV2, attr)
        print(f"  {attr} = {val} (type={type(val).__name__})")

# Use POLY_1271 (should be type 3 per Gemini)
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds

creds = ApiCreds(
    api_key="024fa050-9154-f12a-c1b5-e7b31e3c1e04",
    api_secret="tKq6Vrf8Yzt0O55K0EomApZ3RdmdCxazD9rO_2slXoQ=",
    api_passphrase="9f22be7b1c29a4a61217ae4f0846ee36e775c0c3681ac25ce45115e2f71e161f",
)

# Try both Builder address and EOA as funder
funders_to_try = [
    ("Builder address", "0xf9f38a1dc12fc665222734cf73b1a8f5daf24e9a"),
    ("EOA wallet", WALLET),
]

sig_types_to_try = [
    ("POLY_1271", getattr(SignatureTypeV2, 'POLY_1271', None)),
    ("POLY_7702", getattr(SignatureTypeV2, 'POLY_7702', None)),
]

for sig_name, sig_type in sig_types_to_try:
    if sig_type is None:
        continue
    for f_name, funder_addr in funders_to_try:
        print(f"\n=== sig={sig_name}({sig_type}), funder={f_name} ===")
        try:
            client = ClobClient(
                host="https://clob.polymarket.com",
                key=PRIVATE_KEY,
                chain_id=137,
                creds=creds,
                signature_type=sig_type,
                funder=funder_addr,
            )
            
            # Check balance
            from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
            try:
                bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
                b = bal.get("balance", "0") if isinstance(bal, dict) else bal
                print(f"  Balance: {b}")
            except Exception as e:
                print(f"  Balance: {str(e)[:80]}")
            
            # Get market
            resp = client.get_sampling_markets()
            data = resp.get("data", [])
            for m in data[:3]:
                tokens = m.get("tokens", [])
                if tokens and len(tokens) >= 2:
                    tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
                    if len(tids) >= 2:
                        neg_risk = bool(m.get("neg_risk", False))
                        tick = str(m.get("minimum_tick_size", "0.01"))
                        min_size = float(m.get("minimum_order_size", 5))
                        
                        from py_clob_client_v2.clob_types import OrderArgs, OrderType, CreateOrderOptions
                        from py_clob_client_v2.order_builder.constants import BUY
                        
                        args = OrderArgs(token_id=tids[0], price=0.50, size=min_size, side=BUY)
                        opts = CreateOrderOptions(tick_size=tick, neg_risk=neg_risk)
                        signed = client.create_order(args, opts)
                        result = client.post_order(signed, OrderType.GTC)
                        print(f"  🎉🎉🎉 ORDER PLACED! 🎉🎉🎉")
                        print(f"  {json.dumps(result, indent=2, default=str)}")
                        
                        with open("/tmp/test_order.json", "w") as f:
                            json.dump({"result": result}, f, default=str)
                        
                        print(f"\n  === UPDATE .env ===")
                        print(f"  Use signature_type={sig_type} ({sig_name})")
                        print(f"  Funder={funder_addr}")
                        break
        except Exception as e:
            err = str(e)
            if 'maker address not allowed' in err:
                print(f"  ❌ maker address not allowed")
            elif 'Could not create api key' in err:
                print(f"  ❌ Could not create api key")
            elif 'POLY_1271' in err or 'POLY_7702' in err:
                print(f"  ❌ Unsupported sig type: {err[:80]}")
            else:
                print(f"  ❌ {err[:120]}")

print("\n=== Done ===")
