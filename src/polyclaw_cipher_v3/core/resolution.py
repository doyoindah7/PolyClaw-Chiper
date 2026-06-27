"""Resolution detection — REAL resolution check using Gamma API fields.

Fixes v2 bug: v2 guessed winner from `end_date` + prices.
v3 uses `closed` + `resolvedBy` fields from Gamma API.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..core.types import Market, Side

logger = logging.getLogger(__name__)


def parse_resolution(item: dict[str, Any]) -> tuple[bool, list[str]]:
    """Extract real resolution state from Gamma API market item.

    Returns:
        (is_closed, resolved_by_token_ids)
        - is_closed: whether market is closed (no more trading)
        - resolved_by: list of winning token IDs (empty if not yet resolved)
    """
    is_closed = bool(item.get("closed", False))

    # resolvedBy may be a JSON string or a list
    resolved_by_raw = item.get("resolvedBy") or item.get("resolved_by") or []
    if isinstance(resolved_by_raw, str):
        try:
            resolved_by_raw = json.loads(resolved_by_raw)
        except (json.JSONDecodeError, ValueError):
            resolved_by_raw = []
    if not isinstance(resolved_by_raw, list):
        resolved_by_raw = []

    resolved_by = [str(t) for t in resolved_by_raw if t]
    return is_closed, resolved_by


def get_winning_side(market: Market) -> Side | None:
    """Determine winning side from resolvedBy token IDs.

    Returns None if market not yet resolved or resolution ambiguous.
    """
    if not market.is_closed or not market.resolved_by:
        return None
    if market.yes_token_id and market.yes_token_id in market.resolved_by:
        return Side.YES
    if market.no_token_id and market.no_token_id in market.resolved_by:
        return Side.NO
    # Ambiguous — log and return None
    logger.warning(
        "Ambiguous resolution for %s: resolvedBy=%s, yes_token=%s, no_token=%s",
        market.condition_id[:8], market.resolved_by,
        market.yes_token_id[:8], market.no_token_id[:8],
    )
    return None


def is_truly_resolved(market: Market) -> bool:
    """True only if market is closed AND has winning side determined."""
    return get_winning_side(market) is not None
