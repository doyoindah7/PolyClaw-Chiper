"""
1. Test Dwellir RPC latency
2. Inspect py-clob-client-v2 API
3. Test auth with V2 SDK
"""
import os, sys, json, time
from pathlib import Path

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

RPC_URL = os.environ.get("POLYGON_RPC_URL")
PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
WALLET = os.environ.get("BOT_ADDRESS")
L2_KEY = os.environ.get("POLYMARKET_API_KEY")
L2_SECRET = os.environ.get("POLYMARKET_API_SECRET")
L2_PASS = os.environ.get("POLYMARKET_API_PASSPHRASE")

# === RPC Latency Test ===
print("=== 1. RPC Latency Test ===")
from web3 import Web3

# Test 5 times
for i in range(5):
    t0 = time.time()
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))
    block = w3.eth.block_number
    ms = (time.time() - t0) * 1000
    print(f"  [{i+1}] Dwellir: block={block}, latency={ms:.0f}ms")

# Balance
account = w3.eth.account.from_key(PRIVATE_KEY)
matic = w3.from_wei(w3.eth.get_balance(account.address), 'ether')
print(f"  MATIC: {matic:.4f}")

# === V2 SDK Inspection ===
print("\n=== 2. py-clob-client-v2 API ===")
import py_clob_client_v2 as v2
print(f"  Version: {v2.__version__ if hasattr(v2, '__version__') else '1.0.1'}")

# Check what's available
client_items = [x for x in dir(v2) if not x.startswith('_')]
print(f"  Exports: {client_items}")

# Try to import V2 client
try:
    from py_clob_client_v2.client import ClobClient as ClobClientV2
    print(f"  ClobClientV2 methods:")
    for m in sorted(dir(ClobClientV2)):
        if not m.startswith('_'):
            print(f"    {m}")
except ImportError as e:
    print(f"  ClobClientV2 import failed: {e}")
    # Try alternate import paths
    import importlib
    for mod_name in ['py_clob_client_v2.clob_client', 'py_clob_client_v2.client_v2', 'py_clob_client_v2']:
        try:
            mod = importlib.import_module(mod_name)
            print(f"  {mod_name} available, dir: {[x for x in dir(mod) if 'Client' in x or 'clob' in x.lower()]}")
        except:
            pass

# === V2 Auth Test ===
print("\n=== 3. V2 Auth + Connection Test ===")
try:
    from py_clob_client_v2.client import ClobClient as ClobClientV2
    from py_clob_client_v2.clob_types import ApiCreds as ApiCredsV2
    
    creds_v2 = ApiCredsV2(api_key=L2_KEY, api_secret=L2_SECRET, api_passphrase=L2_PASS)
    
    client_v2 = ClobClientV2(
        host="https://clob.polymarket.com",
        key=PRIVATE_KEY,
        chain_id=137,
        creds=creds_v2,
        signature_type=0,
        funder=WALLET,
    )
    
    # Test get_server_time
    server_time = client_v2.get_server_time()
    print(f"  ✅ get_server_time: {server_time}")
    
    # Test get_api_keys
    keys = client_v2.get_api_keys()
    print(f"  ✅ get_api_keys: {keys}")
    
    # Test get_balance_allowance
    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        bal = client_v2.get_balance_allowance(params)
        print(f"  Balance: {json.dumps(bal, indent=2, default=str)}")
    except Exception as e:
        print(f"  Balance (trying): {e}")
        # Try without params
        try:
            bal = client_v2.get_balance_allowance()
            print(f"  Balance (no params): {json.dumps(bal, indent=2, default=str)}")
        except Exception as e2:
            print(f"  Balance (no params): {e2}")
    
    # Get sampling markets
    print(f"\n  Getting sampling markets...")
    markets = client_v2.get_sampling_markets()
    data = markets.get("data", []) if isinstance(markets, dict) else markets
    print(f"  Found {len(data)} markets")
    
    # Find one with orderbook
    best = None
    for m in data[:5]:
        if not isinstance(m, dict): continue
        tokens = m.get("tokens", [])
        if not tokens: continue
        tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
        if len(tids) < 2: continue
        try:
            book = client_v2.get_order_book(tids[0])
            if hasattr(book, 'bids') and (book.bids or book.asks):
                best = m
                print(f"  ✅ {m.get('question','?')[:60]}")
                break
        except Exception as e:
            print(f"  ❌ {m.get('question','?')[:40]} — {str(e)[:40]}")
    
    if best:
        tids = [t.get("token_id") for t in best.get("tokens", []) if isinstance(t, dict)]
        neg_risk = bool(best.get("neg_risk", False))
        tick = str(best.get("minimum_tick_size", "0.01"))
        min_size = float(best.get("minimum_order_size", 5))
        q = best.get("question", "?")[:60]
        
        print(f"\n=== 4. V2 Place Test Order ===")
        print(f"  Market: {q}")
        print(f"  Tokens: {tids[0][:20]}...")
        print(f"  neg_risk: {neg_risk}, tick: {tick}, min: {min_size}")
        
        from py_clob_client_v2.clob_types import OrderArgs as OrderArgsV2, OrderType as OrderTypeV2
        from py_clob_client_v2.order_builder.constants import BUY
        
        try:
            # Try V2 create_and_post_order
            order_args = OrderArgsV2(
                token_id=tids[0],
                price=0.048,
                size=min_size,
                side=BUY,
            )
            
            result = client_v2.create_and_post_order(order_args)
            print(f"  🎉 ORDER PLACED!")
            print(f"  {json.dumps(result, indent=2, default=str)}")
            with open("/tmp/test_order_v2.json", "w") as f:
                json.dump({"result": result, "token": tids[0], "question": q}, f, default=str)
        except Exception as e:
            print(f"  ❌ {e}")
            if hasattr(e, 'msg'): print(f"  msg: {e.msg}")
            
            # Try create + post
            try:
                from py_clob_client_v2.clob_types import CreateOrderOptions as CreateOrderOptionsV2
                options = CreateOrderOptionsV2(tick_size=tick, neg_risk=neg_risk)
                signed = client_v2.create_order(order_args, options)
                result = client_v2.post_order(signed, OrderTypeV2.GTC)
                print(f"  🎉 GTC ORDER PLACED!")
                print(f"  {json.dumps(result, indent=2, default=str)}")
            except Exception as e2:
                print(f"  ❌ create+post: {e2}")
    else:
        print(f"  ❌ No market found")
        
except ImportError as e:
    print(f"  ❌ Import error: {e}")
    import traceback
    traceback.print_exc()
except Exception as e:
    print(f"  ❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n=== Done ===")
