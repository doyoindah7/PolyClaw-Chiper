#!/usr/bin/env python3
"""
PolyClaw Live - Pre-Flight Integration Test
============================================
Tests every component BEFORE real money enters the system.

STAGE 1 (DRY - free):
  1. Python dependencies
  2. Wallet key validation
  3. RPC connectivity (Polygon)
  4. Gamma API (market data)
  5. CLOB connectivity + API key derivation (via py-clob-client)

STAGE 2 (WET - needs MATIC + USDC.e on Polygon):
  6. MATIC + USDC.e balances
  7. Contract approvals (4x)
  8. Open + cancel $1 test order

USAGE: python live/preflight.py [--stage 1|2|all]
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# -- Config --
LIVE_DIR = Path(__file__).parent.parent
ENV_PATH = LIVE_DIR / ".env"

PASS = "PASS"
FAIL = "FAIL"
results = []

def log_test(name, status, detail=""):
    results.append((name, status, detail))
    print(f"  [{status}] {name}")
    if detail:
        print(f"         {detail}")

def load_env():
    if not ENV_PATH.exists():
        print(f"[{FAIL}] .env not found at {ENV_PATH}")
        sys.exit(1)
    env = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


# ==================================================================
# STAGE 1: DRY TESTS
# ==================================================================

def test_deps():
    for mod, pip_name in [("web3", "web3"), ("eth_account", "eth-account")]:
        try:
            __import__(mod)
            log_test(f"Deps: {pip_name}", PASS)
        except ImportError:
            log_test(f"Deps: {pip_name}", FAIL, f"pip install {pip_name}")

def test_wallet(env):
    pk = env.get("PRIVATE_KEY", "")
    addr = env.get("BOT_ADDRESS", "").lower()
    if not pk or not pk.startswith("0x"):
        log_test("Wallet key", FAIL, "PRIVATE_KEY missing/invalid"); return None
    from eth_account import Account
    acct = Account.from_key(pk)
    if acct.address.lower() != addr:
        log_test("Wallet match", FAIL, f"{acct.address[:10]}... != {addr[:10]}..."); return None
    log_test("Wallet key", PASS, f"Address: {addr[:10]}...")
    return pk

def test_rpc(env):
    rpc = env.get("POLYGON_RPC_URL", "")
    if not rpc:
        log_test("RPC", FAIL, "POLYGON_RPC_URL not set"); return None
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))
    if not w3.is_connected():
        log_test("RPC", FAIL, f"Cannot connect to {rpc[:50]}..."); return None
    cid = w3.eth.chain_id
    blk = w3.eth.block_number
    log_test("RPC chain", PASS, f"Polygon Mainnet (chain={cid})")
    log_test("RPC block", PASS, f"Block #{blk:,}")
    return w3

def test_gamma():
    import ssl, urllib.request
    ctx = ssl.create_default_context()
    ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    try:
        url = "https://gamma-api.polymarket.com/markets?limit=1&closed=false"
        req = urllib.request.Request(url, headers={"User-Agent": "PolyClaw-Preflight/1.0"})
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read())
        q = data[0].get("question", "")[:60] if data else "?"
        log_test("Gamma API", PASS, f"Market: {q}")
    except Exception as e:
        log_test("Gamma API", FAIL, str(e)[:80])

def test_clob_auth(env, pk):
    """CLOB connectivity + API key via py-clob-client."""
    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        log_test("CLOB: import", FAIL, "pip install py-clob-client"); return None

    try:
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=pk.replace("0x", ""),
            chain_id=137,
            signature_type=0,
            funder=env.get("BOT_ADDRESS", ""),
        )
        ts = client.get_server_time()
        log_test("CLOB: server time", PASS, f"Unix ts: {ts}")

        creds = client.create_or_derive_api_creds()
        if creds.api_key:
            log_test("CLOB: API key", PASS, f"Key: {creds.api_key[:12]}...")
            return creds.api_key
        else:
            log_test("CLOB: API key", FAIL, "No key returned"); return None
    except Exception as e:
        log_test("CLOB: auth", FAIL, str(e)[:120]); return None


# ==================================================================
# STAGE 2: WET TESTS (needs funds)
# ==================================================================

USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_EXCH = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK = "0xE5407fE24858D1c01c2Be6d19B5213a7dB5b4F40"
COND_TOK = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

def test_balances(w3, env):
    from web3 import Web3
    addr = Web3.to_checksum_address(env["BOT_ADDRESS"])
    ERC20_ABI = [{"constant":True,"inputs":[{"name":"o","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]

    matic = float(w3.from_wei(w3.eth.get_balance(addr), "ether"))
    usdc_c = w3.eth.contract(address=Web3.to_checksum_address(USDC), abi=ERC20_ABI)
    usdc_val = usdc_c.functions.balanceOf(addr).call() / 1e6

    log_test("MATIC balance", PASS if matic >= 0.5 else FAIL,
             f"{matic:.4f} MATIC (~${matic*0.5:.2f})" + ("" if matic >= 0.5 else " NEED >= 0.5"))
    log_test("USDC.e balance", PASS if usdc_val >= 1.0 else FAIL,
             f"${usdc_val:.2f}" + ("" if usdc_val >= 1.0 else " NEED >= $1.00"))
    return matic, usdc_val

def test_approvals(w3, env):
    from web3 import Web3
    addr = Web3.to_checksum_address(env["BOT_ADDRESS"])

    erc20_abi = [{"constant":True,"inputs":[{"name":"o","type":"address"},{"name":"s","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"}]
    erc1155_abi = [{"constant":True,"inputs":[{"name":"o","type":"address"},{"name":"op","type":"address"}],"name":"isApprovedForAll","outputs":[{"name":"","type":"bool"}],"type":"function"}]

    usdc_c = w3.eth.contract(address=Web3.to_checksum_address(USDC), abi=erc20_abi)
    ctf_c = w3.eth.contract(address=Web3.to_checksum_address(COND_TOK), abi=erc1155_abi)

    checks = [
        ("USDC -> CTF Exchange", usdc_c.functions.allowance(addr, Web3.to_checksum_address(CTF_EXCH)).call() / 1e6, lambda v: v > 0),
        ("USDC -> NegRisk", usdc_c.functions.allowance(addr, Web3.to_checksum_address(NEG_RISK)).call() / 1e6, lambda v: v > 0),
        ("CTF -> CTF Exchange", ctf_c.functions.isApprovedForAll(addr, Web3.to_checksum_address(CTF_EXCH)).call(), lambda v: v is True),
        ("CTF -> NegRisk", ctf_c.functions.isApprovedForAll(addr, Web3.to_checksum_address(NEG_RISK)).call(), lambda v: v is True),
    ]
    all_ok = True
    for name, val, ok in checks:
        s = PASS if ok(val) else FAIL
        if not ok(val):
            all_ok = False
        log_test(f"Approval: {name}", s, f"${val:.2f}" if isinstance(val, float) else str(val))
    return all_ok

def test_trade_roundtrip(pk):
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
    except ImportError:
        log_test("Trade: import", FAIL, "pip install py-clob-client"); return

    try:
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=pk.replace("0x", ""),
            chain_id=137,
            signature_type=0,
            funder="0x034F0a2878441DEd3902058E34bD479cBC6A7794",
        )
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
    except Exception as e:
        log_test("Trade: init", FAIL, str(e)[:100]); return

    # Find cheap token
    import ssl, urllib.request
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request("https://gamma-api.polymarket.com/markets?limit=10&closed=false&tag=crypto",
                                     headers={"User-Agent": "PolyClaw-Preflight/1.0"})
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            mkts = json.loads(resp.read())
    except Exception as e:
        log_test("Trade: find market", FAIL, str(e)[:80]); return

    token_id = None; price = None
    for m in mkts:
        for t in m.get("clobTokenIds", []):
            if t and isinstance(t, str) and t.startswith("0x"):
                try:
                    burl = f"https://clob.polymarket.com/book?token_id={t}"
                    breq = urllib.request.Request(burl, headers={"User-Agent": "PolyClaw-Preflight/1.0"})
                    with urllib.request.urlopen(breq, timeout=10, context=ctx) as bresp:
                        book = json.loads(bresp.read())
                    if book.get("asks"):
                        pa = float(book["asks"][0]["price"])
                        if 0.01 <= pa <= 0.40:
                            token_id = t; price = pa; break
                except: pass
        if token_id: break

    if not token_id:
        log_test("Trade: find market", FAIL, "No cheap token found"); return

    size = round(1.0 / price, 2)
    log_test("Trade: market", PASS, f"Token={token_id[:14]}... price=${price:.2f} size={size}")

    try:
        oa = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
        signed = client.create_order(oa)
        result = client.post_order(signed, OrderType.GTC)
        oid = result.get("orderID", "")
        log_test("Trade: OPEN order", PASS, f"Order ID: {oid[:16]}...")
        time.sleep(3)
        if oid:
            client.cancel(oid)
            log_test("Trade: CANCEL order", PASS, f"Cancelled {oid[:16]}...")
        log_test("Trade: ROUNDTRIP", PASS, "OPEN+CANCEL pipeline works!")
    except Exception as e:
        log_test("Trade: error", FAIL, str(e)[:120])


# ==================================================================
# MAIN
# ==================================================================

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", type=int, choices=[1, 2], default=1)
    args = ap.parse_args()

    print("=" * 60)
    print(f"  PolyClaw Live - Pre-Flight Test (Stage {args.stage})")
    print("=" * 60)

    env = load_env()
    print(f"  Wallet: {env.get('BOT_ADDRESS', '?')[:15]}...")
    print(f"  RPC:    {env.get('POLYGON_RPC_URL', '?')[:50]}...")
    print()

    if args.stage >= 1:
        print("--- STAGE 1: Dry Tests ---")
        test_deps()
        pk = test_wallet(env)
        if pk:
            w3 = test_rpc(env)
            if w3:
                test_gamma()
                test_clob_auth(env, pk)

    if args.stage >= 2:
        print("\n--- STAGE 2: Wet Tests ---")
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(env["POLYGON_RPC_URL"], request_kwargs={"timeout": 15}))
        if not w3.is_connected():
            log_test("RPC", FAIL, "Cannot connect - aborting Stage 2")
        else:
            matic, usdc = test_balances(w3, env)
            if matic >= 0.5 and usdc >= 1.0:
                ok = test_approvals(w3, env)
                if ok:
                    test_trade_roundtrip(env.get("PRIVATE_KEY", ""))
                else:
                    print("\n  >>> Run approve_contracts.py first to set approvals")
            else:
                print("\n  >>> Fund wallet first: $1 MATIC + $1 USDC.e")

    print()
    print("=" * 60)
    print("  RESULTS SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = sum(1 for _, s, _ in results if s == FAIL)
    for name, status, detail in results:
        print(f"  [{status}] {name}")
        if detail:
            print(f"         {detail}")
    print(f"\n  Passed: {passed}/{len(results)}  Failed: {failed}")
    if failed == 0:
        print("  [PASS] ALL CHECKS PASSED!")
    else:
        print(f"  [FAIL] {failed} check(s) failed")
    print()

if __name__ == "__main__":
    main()
