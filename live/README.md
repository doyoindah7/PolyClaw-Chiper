# Polyclaw Live — Live Trading Module

> ⚠️ **WARNING**: This module trades REAL money on Polymarket. Test with $1 first!

## Overview

Live trading module for Polyclaw-Chiper bot. Uses `py-clob-client-v2` for order signing/submission, `web3.py` for chain reads, and implements conservative risk management for small initial capital ($15).

## Architecture

```
live/
├── .env                      # Secrets (NEVER commit — gitignored)
├── .env.example              # Template (safe to commit)
├── .gitignore                # Protects .env, *.key, wallet files
├── README.md                 # This file
│
├── wallet/                   # One-time wallet setup tools
│   ├── generate_wallet.py    # Create new EOA wallet
│   ├── check_balance.py      # Check MATIC + USDC + allowances
│   ├── approve_contracts.py  # 1-time USDC + CTF approvals
│   ├── derive_api_key.py     # Get CLOB API credentials
│   └── verify_setup.py       # All-in-one pre-trade check
│
├── src/                      # Live trading code (Phase 2 — TODO)
│   ├── live_executor.py      # Implements BaseExecutor (FAK orders)
│   ├── order_manager.py      # Submit, retry, partial fill
│   ├── chain_monitor.py      # web3.py reads (balance, allowance)
│   ├── nonce_manager.py      # Track nonce locally
│   ├── gas_oracle.py         # Dynamic gas pricing
│   ├── state_reconciler.py   # API vs on-chain sync
│   ├── scanner_cache.py      # Cache Gamma API (rate limit)
│   └── sanitize_order.py     # Decimal bug fix
│
├── tests/                    # Test suite (Phase 2)
│   ├── test_signature.py
│   ├── test_allowance.py
│   └── test_small_order.py
│
├── scripts/
│   └── test_1_dollar.py      # $1 live test order
│
└── config/
    └── live.yaml             # Conservative params ($15 modal)
```

## Setup Guide (Step-by-Step)

### Prerequisites

- Python 3.11+
- `pip install web3 eth-account py-clob-client-v2 httpx`
- Alchemy account (free tier OK) — get API key at https://alchemy.com
- $15 to fund wallet ($1 MATIC + $14 USDC.e on Polygon)

### Step 1: Generate Wallet

```bash
cd /path/to/PolyClaw-Chiper
python live/wallet/generate_wallet.py
```

Output:
```
Wallet Address : 0xABC123...
Private Key    : 0xDEF456...
```

⚠️ **SAVE PRIVATE KEY SECURELY** — anyone with this key has full access to your funds.

### Step 2: Configure .env

```bash
cp live/.env.example live/.env
nano live/.env
```

Fill in:
```env
WALLET_PRIVATE_KEY=0xDEF456...    # from Step 1
WALLET_ADDRESS=0xABC123...        # from Step 1
POLYGON_RPC_URL=https://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY
POLYGON_RPC_FALLBACK_URL=https://polygon-mainnet.infura.io/v3/YOUR_KEY
```

Leave `CLOB_API_KEY`, `CLOB_API_SECRET`, `CLOB_API_PASSPHRASE` empty for now — we'll derive them in Step 5.

### Step 3: Fund Wallet

Send to your wallet address (from Step 1):

| Asset | Amount | Network | Purpose |
|---|---|---|---|
| MATIC | $1.00 | Polygon | Gas for approvals + redeems |
| USDC.e | $14.00 | Polygon | Trading capital |

**Where to get:**
- MATIC: Buy on Binance/Coinbase, withdraw to Polygon network
- USDC.e: Bridge from Ethereum via Polygon Bridge, or buy on Polygon DEX

⚠️ **Verify network is Polygon (chain ID 137)**, not Ethereum mainnet.

### Step 4: Approve Contracts (One-Time)

```bash
python live/wallet/approve_contracts.py
```

This submits 4 on-chain transactions (~$0.01-0.05 gas total):
1. USDC.e → CTF Exchange
2. USDC.e → Negative Risk Adapter
3. ConditionalTokens → CTF Exchange (setApprovalForAll)
4. ConditionalTokens → Negative Risk Adapter (setApprovalForAll)

Verify on Polygonscan: `https://polygonscan.com/address/YOUR_WALLET`

### Step 5: Derive CLOB API Key

```bash
python live/wallet/derive_api_key.py
```

This signs an L1 EIP-712 message (off-chain, no gas cost) to derive L2 API credentials. The script auto-saves them to `.env`.

- `CLOB_API_KEY` — public identifier
- `CLOB_API_SECRET` — HMAC secret
- `CLOB_API_PASSPHRASE` — additional auth

### Step 6: Verify Full Setup

```bash
python live/wallet/verify_setup.py
```

Runs 8 checks:
1. ✅ Environment variables set
2. ✅ Private key → address derivation
3. ✅ Polygon RPC connection
4. ✅ MATIC balance ≥ 0.5
5. ✅ USDC.e balance ≥ 1.0
6. ✅ 4 contract approvals
7. ✅ CLOB API client initialized
8. ✅ CLOB API reachable

If all pass → ready for test order.

### Step 7: $1 Test Order

```bash
python live/scripts/test_1_dollar.py
```

Submits ONE small FAK order (~$1) to a liquid market. Verifies:
- Order signing works
- API authentication works
- Order fills (or partial fills)
- Position appears in Polymarket

**If $1 order succeeds** → pipeline verified, proceed to Step 8.

**If $1 order fails** → check error, fix, retry. Do NOT deposit more until this works.

### Step 8: Start Live Trading

```bash
# Deploy live container
docker compose -f docker-compose.live.yaml up -d

# Monitor
docker logs -f polyclaw-live

# Dashboard
open http://3.107.53.103:8085
```

## Safety Features

### Kill Switch

Touch a file to immediately stop trading (no new orders):

```bash
# On VPS:
touch /home/ubuntu/polyclaw-cipher-v3/data_live/KILL_SWITCH

# Bot will log: "KILL SWITCH ACTIVE — refusing to trade"
# Existing positions continue to be managed (TP/SL still work)

# To resume:
rm /home/ubuntu/polyclaw-cipher-v3/data_live/KILL_SWITCH
```

### Emergency Stop Loss

Auto-stop if daily loss exceeds threshold:

```yaml
# config/live.yaml
safety:
  emergency_stop_loss_usd: 5.0  # Stop if total loss > $5
```

### Conservative Defaults

| Parameter | Paper | Live ($15) |
|---|---|---|
| Max position | $500 | **$3** |
| Max open positions | 10 | **2** |
| Max daily drawdown | 50% | **20%** |
| Max consecutive losses | 8 | **3** |
| Strategies enabled | 4 | **1 (momentum only)** |

## Common Issues

### "Invalid signature"
- Check `WALLET_PRIVATE_KEY` has `0x` prefix
- Verify chain ID = 137

### "Insufficient allowance"
- Run `python live/wallet/approve_contracts.py`

### "Order failed silently"
- Check USDC balance ≥ order size
- Verify decimal places: `size × price` must be ≤ 2 decimals
- Check API rate limit (don't spam orders)

### "TX pending forever"
- Gas price too low → use `gas_strategy: fast`
- Nonce conflict → check pending TXs on Polygonscan

## Security Checklist

- [ ] `.env` is in `.gitignore` (never commit)
- [ ] Private key generated locally (not shared with anyone)
- [ ] Wallet funded with minimal capital ($15)
- [ ] Backup private key offline (paper/hardware wallet)
- [ ] `verify_setup.py` passes all 8 checks
- [ ] $1 test order succeeded
- [ ] Kill switch tested (touch file, verify bot stops)

## Branch Info

This module is on branch `feature/live-trading` (separate from main).
Review by autoclaw before merge to main.

## Next Phase (Phase 2)

After wallet setup verified + $1 test passes:
1. Implement `live_executor.py` (LiveExecutor class)
2. Implement `order_manager.py` (FAK/FOK + retry)
3. Implement `chain_monitor.py` (web3.py reads)
4. Implement `state_reconciler.py` (API vs chain sync)
5. Test with full $15 trading for 24-48h
6. If profitable → scale up gradually
