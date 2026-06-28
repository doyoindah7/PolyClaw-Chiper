# Polyclaw Live — Live Trading Module

> ⚠️ **WARNING**: This module trades REAL money on Polymarket. Test with $1 first!

## Overview

Live trading module for Polyclaw-Chiper bot. Uses `py-clob-client` (v0.34.x) for order signing/submission, `web3.py` for chain reads, and implements conservative risk management for small initial capital ($15). EOA wallet (sig_type=0). CLOB orders are gasless (off-chain matching).

## Architecture

```
live/
├── .env                      # Secrets (NEVER commit — gitignored)
├── .env.example              # Template (safe to commit)
├── .gitignore                # Protects .env, *.key, wallet files
├── README.md                 # This file
│
├── preflight.py              # Full integration test (2 stages)
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

## Quick Setup (2 Steps)

### Step 1: Generate Wallet + .env

```bash
python live/wallet/generate_wallet.py
```

Saves to `live/.env`. Set `POLYGON_RPC_URL` — working public endpoints:
- `https://polygon-bor-rpc.publicnode.com` (free, reliable)
- `https://1rpc.io/matic` (free, fallback)

No API key needed for public RPCs.

### Step 2: Fund Wallet

Send to your wallet address from Step 1:

| Asset | Amount | Network | Purpose |
|---|---|---|---|
| MATIC | ~$1.00 | Polygon (137) | Gas for approvals + trades |
| USDC.e | ~$1.00+ | Polygon (137) | Trading capital (start small) |

Contract addresses (Polygon Mainnet):
- USDC.e: `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`
- CTF Exchange: `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E`

---

## Pre-Flight Integration Test

**One script to validate everything BEFORE real money enters.**

```bash
# Stage 1: Dry tests (free — no funds needed)
python live/preflight.py --stage 1

# Stage 2: Wet tests (needs MATIC + USDC.e funded)
python live/preflight.py --stage 2
```

### Stage 1 Checks (8/8 required)
1. Python dependencies (web3, eth-account)
2. Wallet key validation (address matches private key)
3. RPC connectivity (Polygon chain ID 137, block height)
4. Gamma API (Polymarket market data)
5. CLOB server time (connectivity check)
6. API key derivation (EIP-712 L1 signature via py-clob-client)

### Stage 2 Checks (after funding)
1. MATIC balance (need >= 0.5 for gas)
2. USDC.e balance (need >= $1.00 for test trade)
3. Contract approvals (4x: USDC->CTF, USDC->NegRisk, CTF->CTF, CTF->NegRisk)
4. Open $1 order on real market
5. Cancel order — verify pipeline works

**If Stage 2 passes:** pipeline verified. Ready for live trading.

---

## Detailed Wallet Setup (Manual)

If you prefer step-by-step over the automated preflight:

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

| Parameter | $25 Paper | $15 Live |
|---|---|---|
| Max position | $65 (65%/trade) | **$1.50 (10%/trade)** |
| Max open positions | 10 | **3** |
| Max daily drawdown | 40% | **20%** |
| Max consecutive losses | 6 | **3** |
| Strategies enabled | momentum | **momentum only** |
| Auto-tune | at startup | at startup |
| CLOB WS tokens | 134 | 134 |

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
