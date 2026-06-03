"""Portfolio management: correlation groups, exposure limits, VaR, position sizing."""

import sys
import os
import numpy as np
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PAPER_MAX_OPEN_POSITIONS, PAPER_MAX_CORRELATION_EXPOSURE

_CORRELATION_KEYWORDS = {
    "BTC_CORRELATED":   ["bitcoin", "btc"],
    "ETH_CORRELATED":   ["ethereum", "eth"],
    "SOL_CORRELATED":   ["solana", "sol"],
    "DEFI_CORRELATED":  ["defi", "uniswap", "aave", "compound", "curve", "tvl", "liquidity", "dex"],
    "MACRO_CORRELATED": ["crypto market", "altcoin", "dominance", "total market", "market cap"],
    "REGULATORY":       ["sec", "cftc", "ban", "regulation", "etf", "legal"],
}


def classify_correlation_group(market_question: str) -> str:
    """Map a market question to a correlation group."""
    text = market_question.lower()
    for group, keywords in _CORRELATION_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return group
    return "UNCORRELATED"


def calculate_correlation_groups(open_positions: list) -> dict:
    """Group open positions by their underlying asset exposure."""
    groups: dict = {}
    for pos in open_positions:
        question = pos.get("market_question", "")
        group = classify_correlation_group(question)
        if group not in groups:
            groups[group] = []
        groups[group].append(pos)
    return groups


def calculate_total_exposure(open_positions: list, current_bankroll: float) -> dict:
    """Return total dollars at risk and exposure breakdown."""
    total = sum(pos.get("actual_size", 0) for pos in open_positions)
    pct = (total / current_bankroll * 100) if current_bankroll > 0 else 0.0
    groups = calculate_correlation_groups(open_positions)
    by_group = {
        g: sum(p.get("actual_size", 0) for p in positions)
        for g, positions in groups.items()
    }
    return {
        "total_exposure": round(total, 4),
        "exposure_pct_of_bankroll": round(pct, 2),
        "exposure_by_group": by_group,
    }


def is_diversification_acceptable(open_positions: list, new_position: dict,
                                    current_bankroll: float) -> tuple:
    """
    Check if adding new_position would violate portfolio constraints.
    Returns (True, reason) or (False, reason).
    """
    new_size = new_position.get("intended_size", 0)
    new_question = new_position.get("market_question", "")
    new_group = classify_correlation_group(new_question)

    # 1. Position count limit
    if len(open_positions) >= PAPER_MAX_OPEN_POSITIONS:
        return False, f"Max open positions reached ({PAPER_MAX_OPEN_POSITIONS})"

    # 2. Total exposure cap (80% of bankroll)
    current_exposure = sum(pos.get("actual_size", 0) for pos in open_positions)
    if current_bankroll > 0 and (current_exposure + new_size) / current_bankroll > 0.80:
        return False, f"Total exposure would exceed 80% of bankroll"

    # 3. Correlation group cap — max 3 positions in the same correlated group
    if new_group != "UNCORRELATED" and len(open_positions) >= 3:
        groups = calculate_correlation_groups(open_positions)
        group_count = len(groups.get(new_group, []))
        if group_count >= 3:
            return False, (
                f"{new_group} already has {group_count} open positions — max 3 per correlation group"
            )

    return True, "OK"


def calculate_portfolio_var(open_positions: list, win_rate: float = 0.52,
                             confidence: float = 0.95) -> dict:
    """Monte Carlo Value at Risk (95% confidence, 1000 simulations)."""
    if not open_positions:
        return {"var_dollars": 0.0, "var_percentage": 0.0}
    n_sims = 1000
    n_pos = len(open_positions)
    outcomes = np.random.random((n_sims, n_pos)) < win_rate

    sizes = np.array([pos.get("actual_size", 0) for pos in open_positions])
    prices = np.array([max(pos.get("actual_entry_price", 0.5), 0.01) for pos in open_positions])
    fees = np.array([pos.get("fee_applied", 0) for pos in open_positions])
    costs = sizes + fees

    win_pnls = sizes * (0.99 / prices) - costs  # net after 1% resolution fee
    loss_pnls = -costs

    sim_pnls = np.where(outcomes, win_pnls[np.newaxis, :], loss_pnls[np.newaxis, :]).sum(axis=1)
    losses = -sim_pnls  # convert to positive loss values

    var_dollars = float(np.percentile(losses, confidence * 100))
    total_exp = float(costs.sum())
    var_pct = (var_dollars / total_exp * 100) if total_exp > 0 else 0.0

    return {
        "var_dollars": round(max(0, var_dollars), 4),
        "var_percentage": round(max(0, var_pct), 2),
    }


def suggest_position_sizing_adjustment(open_positions: list, recommended_size: float,
                                        current_bankroll: float,
                                        new_question: str = "") -> dict:
    """
    Check if recommended_size should be reduced for diversification.
    Returns adjusted_size and reason.
    """
    new_group = classify_correlation_group(new_question)
    acceptable, reason = is_diversification_acceptable(
        open_positions,
        {"intended_size": recommended_size, "actual_size": recommended_size,
         "market_question": new_question},
        current_bankroll,
    )
    if acceptable:
        return {"adjusted_size": recommended_size, "adjustment_reason": "no adjustment needed"}

    # Reduce size proportionally to bring within limits
    adjusted = recommended_size * 0.5
    if adjusted < 0.10:
        return {"adjusted_size": 0.0, "adjustment_reason": reason}
    return {
        "adjusted_size": round(adjusted, 4),
        "adjustment_reason": f"reduced 50% — {reason}",
    }
