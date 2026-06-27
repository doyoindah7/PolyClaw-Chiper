"""LLM agent module — STUB interface for autoclaw to fill in.

This module provides the interface for the LLM news agent (Phase 4 of v3 roadmap).
Currently STUBBED because:
1. z-ai-web-dev-sdk API key not yet configured
2. Autoclaw (parallel bot) will implement & inject LLM client

When autoclaw activates this module:
1. Install z-ai-web-dev-sdk: pip install z-ai-web-dev-sdk
2. Set ZAI_API_KEY in .env
3. Set news_llm.enabled = true in config/default.yaml
4. Implement llm_client.py with z-ai-web-dev-sdk
5. Implement news_scraper.py with Nitter + RSS
6. Activate NewsLLMStrategy in bot.py

See HANDOFF_AUTOCRAW.md for detailed instructions.
"""
from __future__ import annotations

from typing import Any

from ..core.types import Market


class LLMClient:
    """STUB — to be implemented by autoclaw.

    Required interface:
        async def analyze_news_impact(news: NewsEvent, markets: list[Market]) -> list[NewsSignal]:
            '''Returns list of (condition_id, side, implied_prob, confidence, reasoning).'''

        async def assess_near_certainty(market: Market, context: dict) -> NearCertaintyAssessment:
            '''For resolution_snipe strategy.'''
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.enabled = False
        self.api_key = None  # Will be set from env when available

    async def analyze_news_impact(self, news, markets: list[Market]) -> list:
        """STUB — returns empty list. Autoclaw: implement with z-ai-web-dev-sdk."""
        return []

    async def assess_near_certainty(self, market: Market, context: dict):
        """STUB — returns None. Autoclaw: implement with z-ai-web-dev-sdk."""
        from dataclasses import dataclass

        @dataclass
        class NearCertaintyAssessment:
            confidence: float
            reasoning: str

        return NearCertaintyAssessment(
            confidence=0.0,
            reasoning="LLM not configured — see HANDOFF_AUTOCRAW.md",
        )
