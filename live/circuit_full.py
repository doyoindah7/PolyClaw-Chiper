"""
1. Fix third approval (nonce issue)
2. Deposit USDC.e to CTF Exchange
3. Update CLOB balance
4. Find active market with orderbook
5. Place test order
"""
import os, sys, json, time
from pathlib import Path

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
WALLET = os.environ.get("BOT_ADDRESS")
L2_KEY = os.environ.get("POLYMARKET_API_KEY")
L2_SECRET = os.environ.get("POLYMARKET_API_SECRET")
L2_PASS = os.environ.get("POLYMARKET_API_PASSPHRASE")
RPC_URL = os.environ.get("POLYGON_RPC_URL", "https://rpc-mainnet.matic.quiknode.pro")

from web3 import Web3
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType, CreateOrderOptions, BalanceAllowanceParams, AssetType, MarketOrderArgs
from py_clob_client.order_builder.constants import BUY

creds = ApiCreds(api_key=L2_KEY, api_secret=L2_SECRET, api_passphrase=L2_PASS)
client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=creds,
    signature_type=0, funder=WALLET,
)

w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 15}))
account = w3.eth.account.from_key(PRIVATE_KEY)

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
THIRD_ADDR = "0xe2222d279d744050d28e00520010520000310F59"

ERC20_ABI = [
    {"constant":False,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
]

usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
decimals = usdc.functions.decimals().call()
MAX_UINT = 2**256 - 1
gas_price = max(w3.eth.gas_price, w3.to_wei(35, "gwei"))

# === Step 1: Fix third approval ===
print("=== Step 1: Fix Third Approval ===")
addr3 = Web3.to_checksum_address(THIRD_ADDR)
current = usdc.functions.allowance(account.address, addr3).call()
print(f"  Current allowance to {THIRD_ADDR}: {current / (10**decimals):.6f}")

if current == 0:
    nonce = w3.eth.get_transaction_count(account.address)
    print(f"  Nonce: {nonce}")
    gas_est = usdc.functions.approve(addr3, MAX_UINT).estimate_gas({"from": account.address})
    tx = usdc.functions.approve(addr3, MAX_UINT).build_transaction({
        "from": account.address, "nonce": nonce,
        "gas": gas_est + 20000, "gasPrice": gas_price, "chainId": 137,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    status = "✅" if receipt.status == 1 else "❌"
    print(f"  {status} gas: {receipt.gasUsed}")

# === Step 2: Check all allowances ===
print("\n=== Step 2: Verify All Allowances ===")
addrs_to_check = [
    CTF_EXCHANGE,
    "0xC5d563A36AE78145C45a50134d48A1215220f80a",  # NegRisk old
    "0xE111180000d2663C0091e4f400237545B87B996B",
    "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
    "0xe2222d279d744050d28e00520010520000310F59",
]
for addr in addrs_to_check:
    a = usdc.functions.allowance(account.address, Web3.to_checksum_address(addr)).call()
    print(f"  {addr}: {a / (10**decimals):.2f}")

# === Step 3: Deposit USDC.e to CTF Exchange ===
print("\n=== Step 3: Deposit USDC.e to CTF Exchange ===")

# CTF Exchange ABI — deposit function
# Function: deposit(address token, uint256 amount) — but need to check exact signature
# Let me try common deposit signatures
CTF_ABI_candidates = [
    # Try: deposit(uint256 amount)
    {"constant":False,"inputs":[{"name":"amount","type":"uint256"}],"name":"deposit","outputs":[],"type":"function"},
    # Try: deposit(address token, uint256 amount)
    {"constant":False,"inputs":[{"name":"token","type":"address"},{"name":"amount","type":"uint256"}],"name":"deposit","outputs":[],"type":"function"},
    # Try: deposit()
    {"constant":False,"inputs":[],"name":"deposit","outputs":[],"type":"function"},
    # Try: depositTo(address to, uint256 amount)
    {"constant":False,"inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"depositTo","outputs":[],"type":"function"},
    # Read: balanceOf(address) on exchange
    {"constant":True,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
]

# Try to call balanceOf on CTF Exchange to check if we have any balance there
try:
    ctf_contract = w3.eth.contract(
        address=Web3.to_checksum_address(CTF_EXCHANGE),
        abi=[CTF_ABI_candidates[4]]  # balanceOf
    )
    ctf_bal = ctf_contract.functions.balanceOf(account.address).call()
    print(f"  CTF Exchange balanceOf: {ctf_bal / (10**decimals):.6f}")
except Exception as e:
    print(f"  CTF balanceOf error: {e}")

# Try deposit with different signatures
deposit_amount = int(2 * (10**decimals))  # Deposit $2 USDC.e
print(f"  Deposit amount: $2.00 USDC.e")

# Attempt 1: deposit(uint256 amount)
try:
    ctf1 = w3.eth.contract(
        address=Web3.to_checksum_address(CTF_EXCHANGE),
        abi=[CTF_ABI_candidates[0]]
    )
    nonce = w3.eth.get_transaction_count(account.address)
    gas_est = ctf1.functions.deposit(deposit_amount).estimate_gas({"from": account.address})
    tx = ctf1.functions.deposit(deposit_amount).build_transaction({
        "from": account.address, "nonce": nonce,
        "gas": gas_est + 30000, "gasPrice": gas_price, "chainId": 137,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    status = "✅" if receipt.status == 1 else "❌"
    print(f"  {status} deposit(uint256) — gas: {receipt.gasUsed}")
    if receipt.status == 1:
        print("  🎉 Deposit succeeded!")
    else:
        print("  Reverted, trying other method...")
        raise Exception("reverted")
except Exception as e:
    print(f"  deposit(uint256) failed: {str(e)[:80]}")
    
    # Attempt 2: Try deposit via the CLOB client's update_balance_allowance
    # Maybe we don't need to deposit on-chain — CLOB does it automatically?
    print("\n  Trying update_balance_allowance on CLOB...")
    try:
        result = client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        print(f"  Update result: '{result}'")
    except Exception as e2:
        print(f"  Update error: {e2}")
    
    # Re-check balance
    time.sleep(2)
    bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  Balance after update: {bal.get('balance', '0')}")
    print(f"  Allowances: {json.dumps(bal.get('allowances', {}), indent=2)}")

# === Step 4: Find active market with orderbook ===
print("\n=== Step 4: Find Active Market ===")
# Try different pages to find current markets
best = None
for page_cursor in [None, "MTAwMA==", "MjAwMA==", "MzAwMA==", "NDAwMA==", "NTAwMA=="]:
    if page_cursor:
        resp = client.get_markets(next_cursor=page_cursor)
    else:
        resp = client.get_markets()
    data = resp.get("data", [])
    
    for m in data:
        if not isinstance(m, dict): continue
        if not m.get("accepting_orders"): continue
        if not m.get("enable_order_book"): continue
        tokens = m.get("tokens", [])
        if not tokens or len(tokens) < 2: continue
        tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
        if len(tids) >= 2:
            # Check if orderbook exists
            try:
                book = client.get_order_book(tids[0])
                if hasattr(book, 'bids') and (book.bids or book.asks):
                    best = m
                    print(f"  ✅ Found market with orderbook: {m.get('question','?')[:60]}")
                    print(f"    Bids: {len(book.bids or [])}, Asks: {len(book.asks or [])}")
                    break
            except:
                pass
    if best:
        break

if not best:
    print("  ❌ No market with active orderbook found")
    # Try first accepting market anyway
    for page_cursor in [None, "NDAwMA=="]:
        resp = client.get_markets(next_cursor=page_cursor) if page_cursor else client.get_markets()
        data = resp.get("data", [])
        for m in data:
            if not isinstance(m, dict): continue
            if not m.get("accepting_orders"): continue
            tokens = m.get("tokens", [])
            if tokens and len(tokens) >= 2:
                tids = [t.get("token_id") for t in tokens if isinstance(t, dict) and t.get("token_id")]
                if len(tids) >= 2:
                    best = m
                    print(f"  Fallback: {m.get('question','?')[:60]}")
                    break
        if best: break

if not best:
    sys.exit(1)

tids = [t.get("token_id") for t in best.get("tokens", []) if isinstance(t, dict)]
neg_risk = bool(best.get("neg_risk", False))
tick_size_str = str(best.get("minimum_tick_size", "0.01"))
min_size = float(best.get("minimum_order_size", 5))

print(f"  Market: {best.get('question','?')[:70]}")
print(f"  neg_risk: {neg_risk}, tick: {tick_size_str}, min: {min_size}")

# === Step 5: Place test order ===
print(f"\n=== Step 5: Place Test Order ===")
try:
    order_args = OrderArgs(
        token_id=tids[0],
        price=0.50,
        size=min_size,
        side=BUY,
    )
    
    options = CreateOrderOptions(
        tick_size=tick_size_str,  # pass as string
        neg_risk=neg_risk,
    )
    
    signed_order = client.create_order(order_args, options)
    print(f"  ✅ Order signed")
    
    result = client.post_order(signed_order, OrderType.GTC)
    print(f"  ✅ Order placed!")
    print(f"  {json.dumps(result, indent=2, default=str)}")
    
    with open("/tmp/test_order.json", "w") as f:
        json.dump({"result": result, "yes_token": tids[0], "question": best.get("question","")}, f, default=str)
        
except Exception as e:
    print(f"  ❌ {e}")
    if hasattr(e, 'msg'): print(f"  msg: {e.msg}")

print("\n=== Done ===")
