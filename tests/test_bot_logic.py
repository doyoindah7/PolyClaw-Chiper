import pytest
import math
from unittest.mock import MagicMock
from polyclaw_cipher_v3.state.wallet import Wallet, InsufficientFundsError
from polyclaw_cipher_v3.risk.manager import RiskManager
from polyclaw_cipher_v3.core.types import Side, Signal, Position, Leg
from polyclaw_cipher_v3.strategy.latency_arb import LatencyArbStrategy

# 1. Tests for Wallet Reservation & Debit Guard
@pytest.mark.asyncio
async def test_wallet_debit_and_reservation():
    # Mock DB execute
    db_mock = MagicMock()
    async def fake_fetchone(sql, params=()):
        return None
    async def fake_execute(sql, params=()):
        return None
    db_mock.fetchone = fake_fetchone
    db_mock.execute = fake_execute

    wallet = Wallet(db_mock, initial_bankroll=25.0)
    await wallet.load()

    assert wallet.cash == 25.0
    assert wallet.available_cash == 25.0

    # Test reservation
    wallet.reserve(10.0)
    assert wallet.cash == 25.0
    assert wallet.available_cash == 15.0
    assert wallet.has_funds(15.0) is True
    assert wallet.has_funds(15.1) is False

    # Test debit from cash
    await wallet.debit(10.0)
    assert wallet.cash == 15.0
    # Release reservation
    wallet.release(10.0)
    assert wallet.cash == 15.0
    assert wallet.available_cash == 15.0

    # Test debit guard raising InsufficientFundsError
    with pytest.raises(InsufficientFundsError):
        await wallet.debit(20.0)

# 2. Tests for RiskManager Correlation Exposure Limit
def test_risk_manager_exposure_checking():
    risk = RiskManager({"max_net_exposure_per_asset_pct": 50.0})
    risk.init(100.0)  # Bankroll is $100.0, limit is $50.0

    # Mock open positions
    pos1 = Position(
        id="p1",
        market_condition_id="m1",
        market_question="Will Bitcoin be above $100k?",
        side=Side.YES,
        token_id="t1",
        entry_price=0.5,
        shares=60.0,
        invested=30.0,
        strategy="latency_arb",
        opened_at=12345.0
    )

    # Asset for pos1 is parsed as BTC, side is YES, exposure = +$30
    assert pos1.crypto_asset == "BTC"

    # Evaluate new YES trade for BTC with size $15
    # Current net BTC exposure = +$30
    # New net BTC exposure = +$45 <= $50 limit -> ALLOW
    sig1 = Signal(
        market_condition_id="m1",
        side=Side.YES,
        suggested_price=0.5,
        suggested_size_usd=15.0,
        confidence=0.8,
        reason="test",
        strategy_name="latency_arb",
        token_id="t1"
    )

    allowed, reason = risk.check_exposure(
        strategy_name="latency_arb",
        current_bankroll=100.0,
        asset="BTC",
        signal=sig1,
        open_positions=[pos1]
    )
    assert allowed is True

    # Evaluate new YES trade for BTC with size $25
    # New net BTC exposure = $30 + $25 = $55 > $50 limit -> BLOCK
    sig2 = Signal(
        market_condition_id="m1",
        side=Side.YES,
        suggested_price=0.5,
        suggested_size_usd=25.0,
        confidence=0.8,
        reason="test",
        strategy_name="latency_arb",
        token_id="t1"
    )

    allowed, reason = risk.check_exposure(
        strategy_name="latency_arb",
        current_bankroll=100.0,
        asset="BTC",
        signal=sig2,
        open_positions=[pos1]
    )
    assert allowed is False
    assert "exposure would be $55.00" in reason

    # Evaluate new NO trade for BTC with size $25 (hedging YES position)
    # Current net = +$30
    # New net = $30 - $25 = +$5 <= $50 limit -> ALLOW
    sig3 = Signal(
        market_condition_id="m1",
        side=Side.NO,
        suggested_price=0.5,
        suggested_size_usd=25.0,
        confidence=0.8,
        reason="test",
        strategy_name="latency_arb",
        token_id="t2"
    )

    allowed, reason = risk.check_exposure(
        strategy_name="latency_arb",
        current_bankroll=100.0,
        asset="BTC",
        signal=sig3,
        open_positions=[pos1]
    )
    assert allowed is True

    # Evaluate hedged pair signal (atomic YES + NO legs)
    # Net exposure change = +10 - 10 = 0 -> ALLOW
    sig_pair = Signal(
        market_condition_id="m1",
        side=Side.YES,
        suggested_price=0.5,
        suggested_size_usd=20.0,
        confidence=0.9,
        reason="test",
        strategy_name="atomic_arb",
        token_id="t1",
        is_pair=True,
        legs=[
            Leg(token_id="t1", side=Side.YES, price=0.5, size_usd=10.0),
            Leg(token_id="t2", side=Side.NO, price=0.5, size_usd=10.0)
        ]
    )

    allowed, reason = risk.check_exposure(
        strategy_name="atomic_arb",
        current_bankroll=100.0,
        asset="BTC",
        signal=sig_pair,
        open_positions=[pos1]
    )
    assert allowed is True

# 3. Tests for LatencyArbStrategy time-weighted log-normal CDF
def test_latency_arb_cdf_model():
    strategy = LatencyArbStrategy({"min_edge_pct": 2.0})

    # Test cases: current_price, threshold, seconds_to_close
    # If price is at threshold, probability is exactly 50%
    prob = strategy._implied_prob_above(
        current_price=100000.0,
        threshold=100000.0,
        asset="BTC",
        seconds_to_close=3600.0
    )
    assert abs(prob - 0.50) < 0.01

    # If price is above threshold, probability is > 50%
    prob_high = strategy._implied_prob_above(
        current_price=101000.0,
        threshold=100000.0,
        asset="BTC",
        seconds_to_close=3600.0
    )
    assert prob_high > 0.50

    # If price is below threshold, probability is < 50%
    prob_low = strategy._implied_prob_above(
        current_price=99000.0,
        threshold=100000.0,
        asset="BTC",
        seconds_to_close=3600.0
    )
    assert prob_low < 0.50

    # Decay effect: closer to close makes the probability more extreme
    prob_close = strategy._implied_prob_above(
        current_price=101000.0,
        threshold=100000.0,
        asset="BTC",
        seconds_to_close=60.0  # 1 min to close
    )
    prob_far = strategy._implied_prob_above(
        current_price=101000.0,
        threshold=100000.0,
        asset="BTC",
        seconds_to_close=86400.0  # 1 day to close
    )
    assert prob_close > prob_far
