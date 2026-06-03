"""Calculates historical base rates from resolved Polymarket markets and Bayesian priors."""

import re
import sys
import os
import requests
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# Question type keyword patterns — checked in order, first match wins
_QUESTION_PATTERNS = [
    ("price_target",  r"\b(hit|reach|exceed|above|surpass|break)\b.*\$[\d,]+"),
    ("price_below",   r"\b(fall|drop|below|under|crash)\b.*\$[\d,]+"),
    ("listing",       r"\b(list(ed|ing)?|available)\b.*(exchange|coinbase|binance|kraken|bybit)"),
    ("airdrop",       r"\bairdrop\b"),
    ("launch",        r"\b(launch|deploy|release|go live|mainnet|testnet)\b"),
    ("regulation",    r"\b(ban(ned)?|regulat|sec|cftc|sanction|illegal)\b"),
    ("partnership",   r"\b(partner|integrat|acqui|merge|deal)\b"),
    ("hack",          r"\b(hack|exploit|breach|vulnerab|stolen)\b"),
    ("tvl_milestone", r"\b(tvl|total value locked)\b"),
    ("etf",           r"\betf\b"),
    ("halving",       r"\bhalving\b"),
    ("dominance",     r"\b(dominance|market share)\b"),
]

_BASE_RATE_CACHE: dict = {}
_CACHE_TS: float = 0.0
_CACHE_TTL = 1800.0  # 30 minutes — base rates don't change fast


def get_polymarket_base_rates() -> dict:
    """Query Gamma API for resolved markets and compute historical YES rates by question type."""
    import time
    global _BASE_RATE_CACHE, _CACHE_TS
    if _BASE_RATE_CACHE and (time.time() - _CACHE_TS) < _CACHE_TTL:
        return _BASE_RATE_CACHE

    counts: dict = {}
    yes_counts: dict = {}

    try:
        # Fetch resolved markets (closed=true)
        offset = 0
        limit = 100
        max_pages = 5  # cap at 500 resolved markets to avoid long startup
        while offset < limit * max_pages:
            resp = requests.get(
                f"{GAMMA_API_BASE}/markets",
                params={"closed": "true", "limit": limit, "offset": offset},
                timeout=10,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for market in batch:
                question = (market.get("question") or "").lower()
                outcome = (market.get("resolution") or "").upper()
                if outcome not in ("YES", "NO"):
                    continue
                qtype = classify_question_type(question)
                counts[qtype] = counts.get(qtype, 0) + 1
                if outcome == "YES":
                    yes_counts[qtype] = yes_counts.get(qtype, 0) + 1
            if len(batch) < limit:
                break
            offset += limit
    except Exception as e:
        logger.warning("base_rate_calculator: API fetch failed — {}", e)

    base_rates = {}
    for qtype, total in counts.items():
        if total >= 5:
            base_rates[qtype] = round(yes_counts.get(qtype, 0) / total, 4)

    # Hard-coded priors for types with insufficient data (from broad market research)
    defaults = {
        "price_target":   0.35,
        "price_below":    0.30,
        "listing":        0.55,
        "airdrop":        0.45,
        "launch":         0.50,
        "regulation":     0.25,
        "partnership":    0.50,
        "hack":           0.15,
        "tvl_milestone":  0.40,
        "etf":            0.40,
        "halving":        1.00,  # certain by definition once scheduled
        "dominance":      0.45,
        "general":        0.50,
    }
    for qtype, default in defaults.items():
        if qtype not in base_rates:
            base_rates[qtype] = default

    _BASE_RATE_CACHE = base_rates
    _CACHE_TS = __import__("time").time()
    logger.debug("base_rate_calculator: computed base rates for {} question types", len(base_rates))
    return base_rates


def classify_question_type(market_question: str) -> str:
    """Identify which base rate category a market question belongs to."""
    text = market_question.lower()
    for qtype, pattern in _QUESTION_PATTERNS:
        if re.search(pattern, text):
            return qtype
    return "general"


def calculate_bayesian_prior(market: dict, base_rates: dict) -> dict:
    """
    Combine current market price with historical base rate.
    Returns prior_probability and divergence_from_market.
    Positive divergence = market underpriced vs history.
    """
    question = (market.get("question") or "").lower()
    yes_price = float(market.get("yes_price") or 0.5)
    qtype = classify_question_type(question)
    base_rate = base_rates.get(qtype, 0.5)

    # Weighted blend: 40% history, 60% current market price
    prior = round((base_rate * 0.4) + (yes_price * 0.6), 4)
    divergence = round(prior - yes_price, 4)

    return {
        "question_type": qtype,
        "base_rate": base_rate,
        "prior_probability": prior,
        "divergence_from_market": divergence,
    }


def build_base_rate_context(market: dict) -> dict:
    """Master function — returns BaseRateContext dict for the given market."""
    try:
        base_rates = get_polymarket_base_rates()
        calc = calculate_bayesian_prior(market, base_rates)
        qtype = calc["question_type"]
        base_rate = calc["base_rate"]
        prior = calc["prior_probability"]
        divergence = calc["divergence_from_market"]
        yes_price = float(market.get("yes_price") or 0.5)

        if abs(divergence) < 0.03:
            div_label = "fairly priced vs history"
        elif divergence > 0.10:
            div_label = f"SIGNIFICANTLY UNDERPRICED vs history (+{divergence:.0%})"
        elif divergence > 0:
            div_label = f"slightly underpriced vs history (+{divergence:.0%})"
        elif divergence < -0.10:
            div_label = f"SIGNIFICANTLY OVERPRICED vs history ({divergence:.0%})"
        else:
            div_label = f"slightly overpriced vs history ({divergence:.0%})"

        summary = (
            f"Question type: {qtype}. "
            f"Historical YES rate for this type: {base_rate:.0%}. "
            f"Current market: {yes_price:.0%} YES. "
            f"Bayesian prior: {prior:.0%}. "
            f"Assessment: {div_label}."
        )

        return {
            "question_type": qtype,
            "historical_base_rate": base_rate,
            "bayesian_prior": prior,
            "divergence_from_market": divergence,
            "divergence_label": div_label,
            "summary": summary,
            "is_available": True,
        }
    except Exception as e:
        logger.error("base_rate_calculator.build_base_rate_context failed: {}", e)
        return {
            "question_type": "general",
            "historical_base_rate": 0.5,
            "bayesian_prior": 0.5,
            "divergence_from_market": 0.0,
            "divergence_label": "data unavailable",
            "summary": "Base rate data unavailable.",
            "is_available": False,
        }
