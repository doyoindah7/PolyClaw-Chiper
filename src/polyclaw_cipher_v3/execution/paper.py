"""Async paper executor — fill-probability simulation, NO blocking calls.

v3.5.13 LIVE-REALISM TIER 1 (autoclaw review):
- Liquidity-based dynamic slippage (function of notional / volume_24h)
- Volatility-aware fill probability (sport markets = lower fill rate)
- On-chain settlement delay (3s ± 2s, blocks exit until confirmed)
- Gas fee model ($0.01 avg + 5% spike $0.10)
- API rate limit simulation (429 Too Many Requests)
- Position state sync (PENDING → CONFIRMED via callback)

Backward compatible: if market_volume_24h not provided, falls back to
fixed slippage_bps. If on_chain_delay_sec = 0, no delay (legacy behavior).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from typing import Any, Callable

from ..core.types import Position, Signal, Side, Trade
from .base import BaseExecutor

logger = logging.getLogger(__name__)


class PaperExecutor(BaseExecutor):
    """Async paper executor with pair-trade support + live-realism simulation."""

    def __init__(self, config: dict[str, Any] | None = None):
        c = config or {}
        # Legacy slippage (fallback when market_volume_24h not provided)
        self.slippage_bps = c.get("slippage_bps", 70)
        # v3.5.13: Liquidity-based slippage config
        self.slippage_model = c.get("slippage_model", "liquidity")  # "fixed" | "liquidity"
        self.slippage_min_bps = c.get("slippage_min_bps", 30)  # very liquid markets
        self.slippage_max_bps = c.get("slippage_max_bps", 800)  # very illiquid

        # v3.5.13: Volatility-aware fill probability
        # Default base 72% (autoclaw recommendation, was 85%)
        self.fill_prob_base = c.get("fill_probability_base", 0.72)
        self.fill_prob_volatile = c.get("fill_probability_volatile", 0.65)  # sport/crypto
        self.fill_prob_stable = c.get("fill_probability_stable", 0.80)  # economics/politics
        self.fill_prob_at_bid_low = c.get("fill_probability_at_bid_low", 0.90)
        self.fill_prob_at_bid_high = c.get("fill_probability_at_bid_high", 0.55)
        self.queue_factor = c.get("queue_position_factor", 0.6)

        self.latency_sec = c.get("simulated_latency_sec", 0.15)

        # v3.5.13: On-chain settlement delay
        self.on_chain_delay_sec = c.get("on_chain_delay_sec", 3.0)
        self.on_chain_delay_jitter = c.get("on_chain_delay_jitter", 2.0)

        # v3.5.13: Gas fee model
        self.gas_fee_avg_usd = c.get("gas_fee_avg_usd", 0.01)
        self.gas_fee_spike_probability = c.get("gas_fee_spike_probability", 0.05)
        self.gas_fee_spike_usd = c.get("gas_fee_spike_usd", 0.10)
        self.gas_fee_callback: Callable[[float], None] | None = None  # set by bot.py

        # v3.5.13: API rate limit simulation
        self.api_rate_limit_per_sec = c.get("api_rate_limit_per_sec", 10)
        self.api_throttle_probability = c.get("api_throttle_probability", 0.02)
        self._api_request_times: list[float] = []
        self._api_throttle_until: float = 0.0

        # v3.5.13: Position state callback (bot.py registers)
        # Called when position transitions PENDING → CONFIRMED after on-chain delay
        self.on_position_confirmed: Callable[[str], Any] | None = None

        # Pair sibling: for atomic arb, executor creates 2 positions
        self._pair_sibling: Position | None = None

    # ---------- v3.5.13: New helper methods ----------

    def _calc_slippage_bps(self, notional: float, market_volume_24h: float) -> int:
        """Liquidity-based slippage — function of orderbook impact.

        Slippage scales with: notional / market_volume_24h (market impact ratio)
        - <0.1% of daily volume → very liquid → 30-50 bps
        - 0.1-0.5% → liquid → 50-100 bps
        - 0.5-2% → moderate → 100-200 bps
        - 2-5% → illiquid → 200-400 bps
        - >5% → very illiquid → 400-800 bps (eats deep into book)

        Falls back to fixed slippage_bps if volume is 0 or slippage_model="fixed".
        """
        if self.slippage_model == "fixed" or market_volume_24h <= 0:
            return self.slippage_bps

        impact_ratio = notional / max(market_volume_24h, 1.0)
        if impact_ratio < 0.001:
            base = 40
        elif impact_ratio < 0.005:
            base = 70
        elif impact_ratio < 0.02:
            base = 150
        elif impact_ratio < 0.05:
            base = 300
        else:
            base = 600

        # Add randomness ±20% to model orderbook shape variance
        jitter = random.uniform(-0.2, 0.2)
        slip_bps = int(base * (1 + jitter))
        return max(self.slippage_min_bps, min(self.slippage_max_bps, slip_bps))

    def _get_fill_probability(self, market_volume_24h: float, signal: Signal) -> float:
        """Volatility-aware fill probability.

        - Sport markets (high volume, volatile odds) → 65% (lower)
        - Crypto/economics (medium volume, stable odds) → 80% (higher)
        - Default → 72% (autoclaw recommendation)
        """
        # Determine market type by volume heuristic
        # Sport matches typically have $50K-$1M volume, crypto $5K-$50K, politics $1K-$20K
        if market_volume_24h > 100_000:
            base = self.fill_prob_volatile  # 65% — sport
        elif market_volume_24h < 10_000:
            base = self.fill_prob_stable * 0.9  # 72% — illiquid politics (less likely to fill)
        else:
            base = self.fill_prob_base  # 72% — normal

        # Adjust by signal confidence (high confidence = strong momentum = price moving fast = lower fill)
        # If confidence > 0.85, momentum is strong → price already moved → fill harder
        if signal.confidence > 0.85:
            base *= 0.85  # 15% reduction for very high confidence signals

        # Adjust by price level (price near 0 or 1 = less liquid)
        price = signal.suggested_price
        if price < 0.10 or price > 0.90:
            base *= 0.80  # 20% reduction for extreme odds

        return max(0.10, min(0.95, base))

    def _simulate_gas_fee(self, num_legs: int = 1) -> float:
        """Simulate Polygon gas fee per leg.

        - Normal: $0.005-0.015 (95% of time)
        - Spike (congestion): $0.05-0.15 (5% of time)
        """
        if random.random() < self.gas_fee_spike_probability:
            gas = random.uniform(self.gas_fee_spike_usd * 0.5, self.gas_fee_spike_usd * 1.5)
        else:
            gas = random.uniform(self.gas_fee_avg_usd * 0.5, self.gas_fee_avg_usd * 1.5)
        return gas * num_legs

    async def _check_api_rate_limit(self) -> bool:
        """Simulate API rate limit check.

        Returns True if request allowed, False if throttled (429).
        Tracks last 1 second of requests; if exceeded, throttle for 2s.
        Also has 2% random throttle probability (network jitter).
        """
        now = time.time()

        # If we're in throttle period, reject
        if now < self._api_throttle_until:
            return False

        # Clean old requests (older than 1 second)
        self._api_request_times = [t for t in self._api_request_times if now - t < 1.0]

        # Check rate limit
        if len(self._api_request_times) >= self.api_rate_limit_per_sec:
            # Throttle for 2 seconds
            self._api_throttle_until = now + 2.0
            logger.warning("API THROTTLE: rate limit hit (%d req/s), throttling 2s",
                           len(self._api_request_times))
            return False

        # Random throttle (network jitter)
        if random.random() < self.api_throttle_probability:
            self._api_throttle_until = now + 0.5
            logger.debug("API THROTTLE: random network jitter, throttling 0.5s")
            return False

        # Record this request
        self._api_request_times.append(now)
        return True

    async def _simulate_on_chain_delay(self) -> float:
        """Simulate Polygon block confirmation delay.

        Returns actual delay in seconds (3s ± 2s jitter).
        During this time, position is PENDING and cannot be exited.
        """
        delay = self.on_chain_delay_sec + random.uniform(
            -self.on_chain_delay_jitter, self.on_chain_delay_jitter
        )
        delay = max(0.5, delay)  # minimum 0.5s
        await asyncio.sleep(delay)
        return delay

    def _deduct_gas_fee(self, amount: float) -> None:
        """Deduct gas fee from wallet via callback."""
        if self.gas_fee_callback and amount > 0:
            try:
                self.gas_fee_callback(amount)
            except Exception as e:
                logger.debug("Gas fee callback failed: %s", e)

    # ---------- Main execution methods ----------

    async def execute_entry(
        self, signal: Signal, market_question: str, bankroll: float,
        market_volume_24h: float = 0.0,
    ) -> Position | None:
        """Simulate order fill. NON-blocking. For pair signals, creates both legs.

        v3.5.13: Added market_volume_24h param for liquidity-based slippage.
        Falls back to fixed slippage if not provided.
        """
        # v3.5.13: API rate limit check
        if not await self._check_api_rate_limit():
            logger.info("ENTRY REJECTED (API throttled): %s | %s",
                        signal.strategy_name, market_question[:40])
            return None

        # Async latency simulation (no time.sleep!)
        await asyncio.sleep(self.latency_sec)

        # v3.5.13: Volatility-aware fill probability
        fill_prob = self._get_fill_probability(market_volume_24h, signal)

        # v3.5.13: Gas fee deduction (entry = 1 tx per leg)
        gas_entry = self._simulate_gas_fee(num_legs=len(signal.legs))
        self._deduct_gas_fee(gas_entry)

        # Fill probability per leg
        # For pair signals, ALL legs must fill (atomic)
        filled_legs = []
        for i, leg in enumerate(signal.legs):
            # For pair signals, simulate delay between legs
            if signal.is_pair and i > 0:
                leg_delay = 0.2 + (random.random() * 0.3)
                await asyncio.sleep(leg_delay)
                price_drift_bps = random.uniform(-3, 3)
                adjusted_price = leg.price * (1 + price_drift_bps / 10000.0)
                adjusted_price = round(min(0.99, max(0.01, adjusted_price)), 4)
                logger.debug(
                    "Pair leg %d: delay=%.3fs, price drift=%+.1fbps (%.4f→%.4f)",
                    i + 1, leg_delay, price_drift_bps, leg.price, adjusted_price,
                )
                fill_price_input = adjusted_price
            else:
                fill_price_input = leg.price

            # v3.5.13: Use volatility-aware fill probability
            if await self._simulate_fill(fill_price_input, fill_prob):
                # v3.5.13: Liquidity-based slippage
                slip_bps = self._calc_slippage_bps(signal.suggested_size_usd, market_volume_24h)
                slip = slip_bps / 10000.0
                fill_price = round(min(0.99, max(0.01, fill_price_input * (1 + slip))), 4)
                filled_legs.append((leg, fill_price))
                logger.debug(
                    "FILL: %s @ %.4f (slip=%dbps, vol=$%.0f) | %s",
                    leg.side.value, fill_price, slip_bps, market_volume_24h,
                    market_question[:30],
                )
            else:
                logger.info(
                    "FILL REJECTED (prob=%.0f%%): %s @ %.4f | %s",
                    fill_prob * 100, leg.side.value, fill_price_input,
                    market_question[:40],
                )
                self._pair_sibling = None
                return None

        if not filled_legs:
            return None

        # Use first leg as primary position
        primary_leg, primary_price = filled_legs[0]
        primary_shares = signal.suggested_size_usd / primary_price if primary_price > 0 else 0
        primary_invested = primary_shares * primary_price
        pos_id = uuid.uuid4().hex[:8]
        pair_id = signal.id if signal.is_pair else ""

        if signal.is_pair and len(filled_legs) >= 2:
            combined_ask = primary_price + filled_legs[1][1]
            if combined_ask > 0:
                pair_shares = signal.suggested_size_usd / combined_ask
            else:
                pair_shares = primary_shares
            primary_shares = pair_shares
            primary_invested = pair_shares * primary_price

        pos = Position(
            id=pos_id,
            market_condition_id=signal.market_condition_id,
            market_question=market_question,
            side=primary_leg.side,
            token_id=primary_leg.token_id,
            entry_price=primary_price,
            shares=primary_shares,
            invested=primary_invested,
            strategy=signal.strategy_name,
            opened_at=time.time(),
            current_price=primary_price,
            current_value=primary_invested,
            is_pair=signal.is_pair,
            pair_id=pair_id,
        )

        # For pair signals: create sibling position
        self._pair_sibling = None
        if signal.is_pair and len(filled_legs) >= 2:
            second_leg, second_price = filled_legs[1]
            second_invested = primary_shares * second_price
            sibling_id = uuid.uuid4().hex[:8]

            self._pair_sibling = Position(
                id=sibling_id,
                market_condition_id=signal.market_condition_id,
                market_question=market_question,
                side=second_leg.side,
                token_id=second_leg.token_id,
                entry_price=second_price,
                shares=primary_shares,
                invested=second_invested,
                strategy=signal.strategy_name,
                opened_at=time.time(),
                current_price=second_price,
                current_value=second_invested,
                is_pair=True,
                pair_id=pair_id,
                pair_sibling_id=pos_id,
            )
            pos.pair_sibling_id = sibling_id

        # v3.5.13: On-chain settlement delay (position is PENDING until confirmed)
        # Schedule async confirmation after delay
        if self.on_chain_delay_sec > 0:
            asyncio.create_task(self._confirm_position_after_delay(pos.id))
            logger.debug("Position %s PENDING — confirmation in %.1fs",
                         pos_id, self.on_chain_delay_sec)
        else:
            # Legacy mode: position immediately confirmed
            if self.on_position_confirmed:
                self.on_position_confirmed(pos.id)

        pair_tag = " +PAIR (leg-risk simulated)" if self._pair_sibling else ""
        gas_tag = f" gas=${gas_entry:.4f}" if gas_entry > 0 else ""
        logger.info(
            "PAPER FILL: %s %s @ %.4f | %d shares | $%.2f%s%s | %s",
            signal.strategy_name.upper(), primary_leg.side.value, primary_price,
            int(primary_shares), primary_invested,
            pair_tag, gas_tag,
            market_question[:50],
        )
        return pos

    async def _confirm_position_after_delay(self, pos_id: str) -> None:
        """v3.5.13: Simulate on-chain confirmation after delay.

        Position transitions from PENDING → CONFIRMED.
        Bot.py registers callback to update in-memory state.
        """
        await self._simulate_on_chain_delay()
        if self.on_position_confirmed:
            try:
                self.on_position_confirmed(pos_id)
                logger.debug("Position %s CONFIRMED on-chain", pos_id)
            except Exception as e:
                logger.error("Position confirmation callback failed: %s", e)

    def take_pair_sibling(self) -> Position | None:
        """Get and clear the pending pair sibling position."""
        sibling = self._pair_sibling
        self._pair_sibling = None
        return sibling

    async def _simulate_fill(self, price: float, fill_prob: float | None = None) -> bool:
        """Simulate fill probability based on bid level.

        v3.5.13: If fill_prob provided (volatility-aware), use it as base.
        Otherwise fall back to legacy calculation.
        """
        if fill_prob is not None:
            # v3.5.13: Use volatility-aware base, adjust by price level
            norm = max(0.0, min(1.0, (price - 0.05) / 0.90))
            # Higher price (near 1.0) = harder to fill (less liquidity at top)
            adjusted = fill_prob - norm * (fill_prob * 0.20)
            return random.random() <= max(0.10, min(0.95, adjusted))
        else:
            # Legacy calculation
            norm = max(0.0, min(1.0, (price - 0.05) / 0.90))
            base = self.fill_prob_at_bid_low - norm * (
                self.fill_prob_at_bid_low - self.fill_prob_at_bid_high
            )
            base = max(0.10, min(0.99, base * self.queue_factor + self.fill_prob_base * 0.3))
            return random.random() <= base

    async def resolve_position(self, pos: Position, winning_side: str) -> Trade:
        """Resolve position at market close."""
        # v3.5.13: Gas fee for exit
        gas_exit = self._simulate_gas_fee(num_legs=1)
        self._deduct_gas_fee(gas_exit)

        won = pos.side.value == winning_side
        exit_price = 1.0 if won else 0.0
        exit_value = pos.shares * exit_price
        pnl = exit_value - pos.invested
        pnl_pct = (pnl / pos.invested) * 100 if pos.invested > 0 else 0.0

        trade = Trade(
            id=uuid.uuid4().hex[:8],
            market_condition_id=pos.market_condition_id,
            market_question=pos.market_question,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            shares=pos.shares,
            invested=pos.invested,
            pnl_dollar=round(pnl, 4),
            pnl_percent=round(pnl_pct, 2),
            opened_at=pos.opened_at,
            closed_at=time.time(),
            strategy=pos.strategy,
            reason=f"Resolved: {'WON' if won else 'LOST'} ({winning_side})",
            is_pair=pos.is_pair,
            pair_id=pos.pair_id,
        )

        logger.info(
            "RESOLVE: %s %s | entry=%.4f exit=%.4f | PnL=$%.4f (%.1f%%) gas=$%.4f | %s",
            pos.strategy.upper(), pos.side.value, pos.entry_price, exit_price,
            pnl, pnl_pct, gas_exit, pos.market_question[:50],
        )
        return trade

    async def close_position(self, pos: Position, exit_price: float, reason: str,
                             market_volume_24h: float = 0.0) -> Trade:
        """Close position at given price (TP/SL/max hold).

        v3.5.13 FIX: Exit slippage now calculated using liquidity-based model.
        If market_volume_24h not available, uses moderate 100 bps default
        (exit during reversal = worse slippage than entry).
        """
        # v3.5.13: Gas fee for exit
        gas_exit = self._simulate_gas_fee(num_legs=1)
        self._deduct_gas_fee(gas_exit)

        # v3.5.13 FIX: Exit slippage — liquidity-based (was 0, unrealistic)
        # Exit slippage moves price AGAINST you (exit during reversal)
        if market_volume_24h > 0:
            slip_bps = self._calc_slippage_bps(pos.invested, market_volume_24h)
        else:
            slip_bps = 100  # moderate default for exit without volume data
        slip = slip_bps / 10000.0
        # Exit: price moves AGAINST position (sell lower for YES, buy higher for NO)
        if pos.side == Side.YES:
            exit_price = exit_price * (1 - slip)
        else:
            exit_price = exit_price * (1 + slip)
        exit_price = round(max(0.01, min(0.99, exit_price)), 4)
        exit_value = pos.shares * exit_price
        pnl = exit_value - pos.invested
        pnl_pct = (pnl / pos.invested) * 100 if pos.invested > 0 else 0.0

        trade = Trade(
            id=uuid.uuid4().hex[:8],
            market_condition_id=pos.market_condition_id,
            market_question=pos.market_question,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            shares=pos.shares,
            invested=pos.invested,
            pnl_dollar=round(pnl, 4),
            pnl_percent=round(pnl_pct, 2),
            opened_at=pos.opened_at,
            closed_at=time.time(),
            strategy=pos.strategy,
            reason=reason,
            is_pair=pos.is_pair,
            pair_id=pos.pair_id,
        )

        logger.info(
            "CLOSE: %s %s | entry=%.4f exit=%.4f | PnL=$%.4f (%.1f%%) gas=$%.4f | %s | %s",
            pos.strategy.upper(), pos.side.value, pos.entry_price, exit_price,
            pnl, pnl_pct, gas_exit, reason, pos.market_question[:40],
        )
        return trade
