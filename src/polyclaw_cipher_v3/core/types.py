"""Core type definitions — Pydantic v2 models.

v3.2.0: Added market_category for strategy filtering.
"""
from __future__ import annotations

import re
from datetime import datetime, UTC
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Side(str, Enum):
    YES = "YES"
    NO = "NO"


# --- Market Category Classification ---
# v3.3.0: Split sports_derivative into sports_total (O/U, predictable Poisson)
# vs sports_spread (spread/handicap, random — 1 goal = flip outcome).
# Based on Claude's insight: point spread is statistically closer to sports_match
# (random outcome) than to O/U goals (Poisson-distributed, predictable).

CATEGORY_PATTERNS = {
    "sports_match": [
        re.compile(r"Will\s+.+\s+win\s+on\s+\d{4}", re.IGNORECASE),
        re.compile(r"Will\s+.+\s+lose\s+on\s+\d{4}", re.IGNORECASE),
        re.compile(r"end\s+in\s+a\s+draw", re.IGNORECASE),
        re.compile(r"^Will\s+[A-Z][a-z]+\s+win\b", re.IGNORECASE),
        re.compile(r"vs\.?\s+.+\s+(win|lose|draw)", re.IGNORECASE),
    ],
    # v3.3.0: O/U goals/points = Poisson-distributed, predictable (momentum edge valid)
    "sports_total": [
        re.compile(r"O/U\s+\d", re.IGNORECASE),
        re.compile(r"Over/Under\s+\d", re.IGNORECASE),
        re.compile(r"total\s+(points|goals|runs)", re.IGNORECASE),
        re.compile(r"over\s+under", re.IGNORECASE),
    ],
    # v3.3.0: spread/handicap = margin of victory, random (1 goal = flip) — exclude from momentum
    "sports_spread": [
        re.compile(r"spread", re.IGNORECASE),
        re.compile(r"handicap", re.IGNORECASE),
        re.compile(r"[\-+]\d+\.?\d*\s*(point|goal|run)", re.IGNORECASE),
    ],
    "politics": [
        re.compile(r"election", re.IGNORECASE),
        re.compile(r"president", re.IGNORECASE),
        re.compile(r"senate", re.IGNORECASE),
        re.compile(r"congress", re.IGNORECASE),
        re.compile(r"governor", re.IGNORECASE),
        re.compile(r"prime\s+minister", re.IGNORECASE),
        re.compile(r"legislation|bill|policy|veto", re.IGNORECASE),
        re.compile(r"Republican|Democrat", re.IGNORECASE),
    ],
    "economics": [
        re.compile(r"CPI|inflation", re.IGNORECASE),
        re.compile(r"GDP", re.IGNORECASE),
        re.compile(r"Fed\s+(rate|cut|hike|pause)", re.IGNORECASE),
        re.compile(r"interest\s+rate", re.IGNORECASE),
        re.compile(r"unemployment", re.IGNORECASE),
        re.compile(r"nonfarm|non-farm|NFP", re.IGNORECASE),
        re.compile(r"recession", re.IGNORECASE),
        re.compile(r"treasury\s+yield", re.IGNORECASE),
    ],
    "crypto": [
        re.compile(r"BTC|Bitcoin|ETH|Ethereum|SOL|Solana|BNB|XRP|DOGE|Dogecoin", re.IGNORECASE),
    ],
    "entertainment": [
        re.compile(r"Oscar|Grammy|Emmy|Award", re.IGNORECASE),
        re.compile(r"box\s+office", re.IGNORECASE),
        re.compile(r"Billboard|chart", re.IGNORECASE),
    ],
}

# Priority order: first match wins (more specific patterns first)
# v3.3.0: sports_total + sports_spread checked BEFORE sports_match
CATEGORY_PRIORITY = [
    "sports_total",     # O/U goals (predictable)
    "sports_spread",    # spread/handicap (random)
    "sports_match",     # match winner/draw
    "crypto",
    "politics",
    "economics",
    "entertainment",
]


def classify_market(question: str) -> str:
    """Classify market by question text. Returns category string."""
    for cat in CATEGORY_PRIORITY:
        for pat in CATEGORY_PATTERNS.get(cat, []):
            if pat.search(question):
                return cat
    return "other"


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
    is_closed: bool = False
    resolved_by: list[str] = Field(default_factory=list)
    crypto_asset: str | None = None
    window_minutes: int | None = None
    market_category: str = ""

    @property
    def is_crypto_up_down(self) -> bool:
        return self.crypto_asset is not None

    @property
    def is_resolved(self) -> bool:
        return self.is_closed and len(self.resolved_by) > 0

    @property
    def winning_side(self) -> Side | None:
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
        return self.yes_ask + self.no_ask

    @property
    def combined_mid(self) -> float:
        return self.yes_price + self.no_price

    def classify(self) -> str:
        """Compute and cache market category."""
        if not self.market_category:
            self.market_category = classify_market(self.question)
        return self.market_category

    @property
    def is_random_outcome(self) -> bool:
        """True if market is random-outcome (sports match winner, spread, election result, etc).
        v3.3.0: Added sports_spread — point spread is statistically random (1 goal = flip).
        These markets have NO momentum edge — outcome is binary random."""
        cat = self.classify()
        return cat in ("sports_match", "sports_spread", "entertainment")


class Leg(BaseModel):
    token_id: str
    side: Side
    price: float
    size_usd: float


class Signal(BaseModel):
    model_config = ConfigDict(frozen=False)

    id: str = ""
    market_condition_id: str
    side: Side
    suggested_price: float
    suggested_size_usd: float
    confidence: float
    reason: str
    strategy_name: str
    token_id: str = ""
    timestamp: float = 0.0
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
        if not self.legs and self.token_id:
            self.legs = [Leg(
                token_id=self.token_id,
                side=self.side,
                price=self.suggested_price,
                size_usd=self.suggested_size_usd,
            )]


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
    strategy: str
    opened_at: float
    current_price: float = 0.0
    current_value: float = 0.0
    pnl_percent: float = 0.0
    pnl_dollar: float = 0.0
    is_pair: bool = False
    pair_id: str = ""
    pair_sibling_id: str = ""

    @property
    def crypto_asset(self) -> str | None:
        """v3.4.0: Extract crypto asset symbol from question for correlation risk checks."""
        q = self.market_question.upper()
        if "BTC" in q or "BITCOIN" in q:
            return "BTC"
        if "ETH" in q or "ETHEREUM" in q:
            return "ETH"
        if "SOL" in q or "SOLANA" in q:
            return "SOL"
        return None


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
    is_pair: bool = False
    pair_id: str = ""


class NewsEvent(BaseModel):
    id: str = ""
    source: str
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
    symbol: str
    price: float
    volume: float = 0.0
    timestamp: float
