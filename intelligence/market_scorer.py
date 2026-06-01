"""Scores Polymarket markets by identifying mispriced odds vs real probability."""

from datetime import datetime, timezone
from loguru import logger
from data.database import get_all_crypto_markets


def score_market(market: dict) -> float:
    """Score a market from 0.0 to 1.0 based on volume, liquidity, odds interest, and recency.

    Weightings:
        Volume:      30%
        Liquidity:   25%
        Odds:        30%
        Recency:     15%
    """
    # --- Volume score (30% weight) ---
    # Normalize against $10,000 baseline; cap at 1.0
    volume = float(market.get("volume", 0) or 0)
    volume_score = min(volume / 10000.0, 1.0)

    # --- Liquidity score (25% weight) ---
    # Normalize against $5,000 baseline; cap at 1.0
    liquidity = float(market.get("liquidity", 0) or 0)
    liquidity_score = min(liquidity / 5000.0, 1.0)

    # --- Odds interest score (30% weight) ---
    # Markets where yes_price is between 0.15 and 0.85 are more interesting
    yes_price = float(market.get("yes_price", 0.5) or 0.5)
    # Distance from extremes (0.0 or 1.0) — peak interest at 0.5
    odds_interest = 1.0 - abs(yes_price - 0.5) * 2.0  # 0.0 at edges, 1.0 at center
    # But also prefer prices between 0.15-0.85
    if yes_price < 0.15 or yes_price > 0.85:
        odds_interest *= 0.5  # penalize near-certain outcomes
    odds_score = odds_interest

    # --- Recency score (15% weight) ---
    # Markets closing sooner score higher
    end_date_str = market.get("end_date", "")
    recency_score = 0.5  # default mid value
    if end_date_str:
        try:
            # Try parsing ISO format
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            days_remaining = (end_date - now).days
            if days_remaining <= 0:
                recency_score = 1.0  # closing today or already passed
            elif days_remaining >= 180:
                recency_score = 0.0  # 6+ months out
            else:
                recency_score = 1.0 - (days_remaining / 180.0)
        except (ValueError, TypeError):
            recency_score = 0.5

    # --- Weighted combination ---
    final_score = (
        volume_score * 0.30 +
        liquidity_score * 0.25 +
        odds_score * 0.30 +
        recency_score * 0.15
    )

    return round(min(max(final_score, 0.0), 1.0), 4)


def get_top_markets(n: int = 20) -> list:
    """Return the top N crypto markets by score from the database."""
    markets = get_all_crypto_markets()
    scored = []
    for m in markets:
        m["score"] = score_market(m)
        scored.append(m)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:n]