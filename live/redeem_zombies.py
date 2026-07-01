"""Redeem all zombie positions from Polymarket CTF contract."""
import os, sys, json, asyncio, aiohttp
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Load .env
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k not in os.environ:
                    os.environ[k] = v.strip("'\"")
else:
    # Try live .env
    env_path = "/home/ubuntu/polyclaw-cipher-v3/live/.env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k not in os.environ:
                        os.environ[k] = v.strip("'\"")

FUNDER = os.environ.get("LIVE_FUNDER", "")
L2_API_KEY = os.environ.get("L2_API_KEY", "")
L2_API_SECRET = os.environ.get("L2_API_SECRET", "")
L2_API_PASSPHRASE = os.environ.get("L2_API_PASSPHRASE", "")
WALLET_KEY = os.environ.get("LIVE_WALLET_KEY", "")

print(f"Funder: {FUNDER}")
print(f"L2 key present: {bool(L2_API_KEY)}")

async def main():
    # 1. Get redeemable positions from Data API
    url = f"https://data-api.polymarket.com/positions?user={FUNDER}&redeemable=true"
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r:
            pos_list = await r.json()
    
    print(f"Found {len(pos_list)} redeemable positions")
    
    if not pos_list:
        print("No redeemable positions. Done!")
        return
    
    # Gather condition IDs + index sets
    to_redeem = []
    for p in pos_list:
        cid = p.get("conditionId", "")
        tid = p.get("asset", "")
        title = p.get("title", "")
        outcome = p.get("outcome", "")
        sz = float(p.get("size", 0))
        if sz > 0.001 and cid:
            to_redeem.append({
                "conditionId": cid,
                "tokenId": tid,
                "title": title,
                "outcome": outcome,
                "size": sz,
            })
    
    print(f"To redeem: {len(to_redeem)} positions")
    for tr_item in to_redeem:
        print(f"  {tr_item['title'][:50]} | {tr_item['outcome']} | {tr_item['size']:.2f} shares")
    
    # 2. Use py-clob-client to redeem
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderType
        from py_clob_client.order_builder.constants import BUY, SELL
        
        # Create client with L2 creds
        host = "https://clob.polymarket.com"
        chain_id = 137
        client = ClobClient(
            host=host,
            key=L2_API_KEY,
            chain_id=chain_id,
            creds={
                "key": L2_API_KEY,
                "secret": L2_API_SECRET,
                "passphrase": L2_API_PASSPHRASE,
            },
            signature_type=3,  # POLY_1271
            funder=FUNDER,
        )
        
        # Call redeem_positions with list of condition IDs
        cids = [tr["conditionId"] for tr in to_redeem]
        print(f"\nRedeeming {len(cids)} positions...")
        
        result = client.redeem_positions(cids)
        print(f"Redeem result: {result}")
        
        print("\n✅ Redemption submitted!")
        
    except ImportError:
        print("\n⚠️ py_clob_client not available, trying direct contract call...")
        await redeem_via_contract(to_redeem)
    except Exception as e:
        print(f"\n❌ Redeem error: {e}")
        print("Trying direct contract call...")
        await redeem_via_contract(to_redeem)

async def redeem_via_contract(positions):
    """Fallback: Direct CTF contract call."""
    from web3 import Web3
    
    rpc = "https://polygon.drpc.org"
    w3 = Web3(Web3.HTTPProvider(rpc))
    
    if not w3.is_connected():
        raise Exception("RPC connection failed")
    
    ctf_address = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    ctf_abi = json.loads('[{"inputs":[{"internalType":"contract IERC20","name":"collateralToken","type":"address"},{"internalType":"bytes32","name":"parentCollectionId","type":"bytes32"},{"internalType":"bytes32","name":"conditionId","type":"bytes32"},{"internalType":"uint256[]","name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"stateMutability":"nonpayable","type":"function"}]')
    
    ctf = w3.eth.contract(address=ctf_address, abi=ctf_abi)
    collateral = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")  # USDC.e
    parent_id = "0x0000000000000000000000000000000000000000000000000000000000000000"
    
    account = w3.eth.account.from_key(WALLET_KEY)
    
    for pos in positions[:1]:  # Test with first one
        try:
            tx = ctf.functions.redeemPositions(
                collateral,
                parent_id,
                pos["conditionId"],
                [1, 2]  # Both outcomes
            ).build_transaction({
                "from": account.address,
                "nonce": w3.eth.get_transaction_count(account.address),
                "gas": 500000,
                "gasPrice": w3.eth.gas_price,
            })
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"  Redeem tx: {tx_hash.hex()}")
            w3.eth.wait_for_transaction_receipt(tx_hash)
            print(f"  ✅ Confirmed: {pos['title'][:40]}")
        except Exception as e:
            print(f"  ❌ {pos['title'][:40]}: {e}")

asyncio.run(main())
