"""Decides whether to place a bet based on confidence score and Kelly criterion sizing."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MIN_CONFIDENCE, MAX_BET_SIZE, MAX_BANKROLL_RISK

MIN_EDGE = 0.05
MIN_BET_DOLLARS = 0.25


def evaluate_bet(market: dict, llm_result: dict) -> dict:
    """Evaluate an LLM signal and return a sized bet recommendation.

    Returns dict with keys: should_bet, side, recommended_size, reasoning, kelly_fraction
    """
    signal = llm_result.get("signal", "SKIP")
    confidence = float(llm_result.get("confidence", 0.0))
    edge = float(llm_result.get("edge", 0.0))
    reasoning = str(llm_result.get("reasoning", ""))

    base = {
        "should_bet": False,
        "side": None,
        "recommended_size": 0.0,
        "reasoning": reasoning,
        "kelly_fraction": 0.0,
    }

    if signal == "SKIP":
        return base
    if confidence < MIN_CONFIDENCE:
        return base
    if edge <= MIN_EDGE:
        return base

    # Kelly criterion: f* = (edge × confidence) / (1 − confidence)
    # Confidence approaching 1.0 would blow up the formula; hard-cap prevents that.
    if confidence >= 1.0:
        kelly_fraction = 0.25
    else:
        kelly_fraction = (edge * confidence) / (1.0 - confidence)
    kelly_fraction = min(kelly_fraction, 0.25)

    # Size: Kelly fraction of MAX_BANKROLL_RISK, hard-capped at MAX_BET_SIZE
    recommended_size = kelly_fraction * MAX_BANKROLL_RISK
    recommended_size = min(recommended_size, MAX_BET_SIZE)

    if recommended_size < MIN_BET_DOLLARS:
        return {**base, "kelly_fraction": round(kelly_fraction, 4), "recommended_size": round(recommended_size, 4)}

    return {
        "should_bet": True,
        "side": "YES" if signal == "BET_YES" else "NO",
        "recommended_size": round(recommended_size, 4),
        "reasoning": reasoning,
        "kelly_fraction": round(kelly_fraction, 4),
    }
