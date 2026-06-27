"""Core type definitions — Pydantic v2 models."""
from __future__ import annotations

from datetime import datetime, UTC
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Side(str, Enum):
    YES = "YES"
    NO = "NO"


class Market(BaseModel):
    """Polymarket market (binary outcome)."""
    model_config = ConfigDict(frozen=False)

    condition_id: str
    question: str
    slug: str = ""
    end_date: datetime
    yes_token_id: str = ""
    no_token_id: str = ""
    yes_price: float = 0.5
    no_price: float = 0.5
    yes_bid: float = 0.0
    yes_ask: float = 0.0
    no_bid: float = 0.0
    no_ask: float = 0.0
    spread: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0
    is_active: bool = True
    is_closed: bool = False            # Real closed flag from Gamma API
    resolved_by: list[str] = Field(default_factory=list)  # Winning token IDs
    crypto_asset: str | None = None
    window_minutes: int | None = None

    @property
    def is_crypto_up_down(self) -> bool:
        return self.crypto_asset is not None

    @property
    def is_resolved(self) -> bool:
        """Real resolution check — uses `closed` AND `resolvedBy` fields."""
        return self.is_closed and len(self.resolved_by) > 0

    @property
    def winning_side(self) -> Side | None:
        """Determine winning side from resolvedBy token IDs."""
        if not self.is_resolved:
            return None
        if self.yes_token_id in self.resolved_by:
            return Side.YES
        if self.no_token_id in self.resolved_by:
            return Side.NO
        return None

    @property
    def seconds_to_close(self) -> float:
        return (self.end_date - datetime.now(UTC)).total_seconds()

    @property
    def combined_ask(self) -> float:
        """YES ask + NO ask — for arbitrage detection."""
        return self.yes_ask + self.no_ask

    @property
    def combined_mid(self) -> float:
        return self.yes_price + self.no_price


class Leg(BaseModel):
    """Single leg of a (potentially multi-leg) signal."""
    token_id: str
    side: Side
    price: float
    size_usd: float


class Signal(BaseModel):
    """Trading signal emitted by a strategy."""
    model_config = ConfigDict(frozen=False)

    id: str = ""
    market_condition_id: str
    side: Side                          # Primary side (for single-leg)
    suggested_price: float
    suggested_size_usd: float
    confidence: float
    reason: str
    strategy_name: str
    token_id: str = ""
    timestamp: float = 0.0
    # Multi-leg support (for atomic arbitrage)
    legs: list[Leg] = Field(default_factory=list)
    is_pair: bool = False

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if not self.id:
            import uuid
            self.id = uuid.uuid4().hex[:8]
        if not self.timestamp:
            import time
            self.timestamp = time.time()
        # If legs not specified, build default single leg
        if not self.legs and self.token_id:
            self.legs = [Leg(
                token_id=self.token_id,
                side=self.side,
                price=self.suggested_price,
                size_usd=self.suggested_size_usd,
            )]


class Position(BaseModel):
    """Open position."""
    model_config = ConfigDict(frozen=False)

    id: str
    market_condition_id: str
    market_question: str
    side: Side
    token_id: str
    entry_price: float
    shares: float
    invested: float
    strategy: str
    opened_at: float
    current_price: float = 0.0
    current_value: float = 0.0
    pnl_percent: float = 0.0
    pnl_dollar: float = 0.0
    # Pair-trade support
    is_pair: bool = False
    pair_id: str = ""
    pair_sibling_id: str = ""


class Trade(BaseModel):
    """Closed trade (historical)."""
    model_config = ConfigDict(frozen=False)

    id: str
    market_condition_id: str
    market_question: str
    side: Side
    entry_price: float
    exit_price: float
    shares: float
    invested: float
    pnl_dollar: float
    pnl_percent: float
    opened_at: float
    closed_at: float
    strategy: str
    reason: str = ""
    is_pair: bool = False
    pair_id: str = ""


class NewsEvent(BaseModel):
    """News event from scraper (for LLM agent — deferred)."""
    id: str = ""
    source: str                        # "nitter" / "rss" / "polymarket"
    headline: str
    body: str = ""
    url: str = ""
    timestamp: float = 0.0
    llm_analyzed: bool = False
    llm_summary: str = ""
    signals_emitted: int = 0

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if not self.id:
            import uuid
            self.id = uuid.uuid4().hex[:12]
        if not self.timestamp:
            import time
            self.timestamp = time.time()


class TickUpdate(BaseModel):
    """CLOB WS tick update."""
    token_id: str
    price: float
    best_bid: float = 0.0
    best_ask: float = 0.0
    timestamp: float

    @property
    def mid(self) -> float:
        if self.best_bid > 0 and self.best_ask > 0:
            return (self.best_bid + self.best_ask) / 2
        return self.price


class BinanceTick(BaseModel):
    """Binance WS tick."""
    symbol: str                        # "BTC" / "ETH" / "SOL"
    price: float
    volume: float = 0.0
    timestamp: float
