"""Quick RPC test — find working endpoint from Ireland VPS."""
import urllib.request, json, time

rpcs = [
    "https://polygon-rpc.com",
    "https://polygon.drpc.org",
    "https://1rpc.io/matic",
    "https://rpc.ankr.com/polygon",
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.llamarpc.com",
    "https://polygonscan.com/rpc",
    "https://rpc-mainnet.matic.network",
    "https://rpc-mainnet.maticvigil.com",
    "https://rpc-mainnet.matic.quiknode.pro",
    "https://polygon-bor.publicnode.com",
]

payload = json.dumps({"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}).encode()

for rpc in rpcs:
    try:
        t0 = time.time()
        req = urllib.request.Request(rpc, data=payload, headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            d = json.loads(resp.read().decode())
            block = int(d.get("result","0x0"), 16)
            ms = int((time.time()-t0)*1000)
            print(f"✅ {rpc} — block={block} ({ms}ms)")
    except Exception as e:
        code = getattr(e, 'code', '?')
        print(f"❌ {rpc} — {str(e)[:60]}")
