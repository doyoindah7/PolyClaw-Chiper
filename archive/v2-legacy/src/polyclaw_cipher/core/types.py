"""Core type definitions."""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict


class Side(str, Enum):
    YES = "YES"
    NO = "NO"


class Market(BaseModel):
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
    spread: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0
    is_active: bool = True
    crypto_asset: str | None = None
    window_minutes: int | None = None

    @property
    def is_crypto_up_down(self) -> bool:
        return self.crypto_asset is not None

    @property
    def seconds_to_close(self) -> float:
        from datetime import UTC
        return (self.end_date - datetime.now(UTC)).total_seconds()

    @property
    def combined_cost(self) -> float:
        return self.yes_price + self.no_price


class Signal(BaseModel):
    model_config = ConfigDict(frozen=False)
    market_condition_id: str
    side: Side
    suggested_price: float
    suggested_size_usd: float
    confidence: float
    reason: str
    strategy_name: str
    token_id: str = ""
    timestamp: float = 0.0


class Position(BaseModel):
    model_config = ConfigDict(frozen=False)
    id: str
    market_condition_id: str
    market_question: str
    side: Side
    token_id: str
    entry_price: float
    shares: float
    invested: float
    opened_at: float
    strategy: str
    current_price: float = 0.0
    current_value: float = 0.0
    pnl_percent: float = 0.0
    pnl_dollar: float = 0.0


class Trade(BaseModel):
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
