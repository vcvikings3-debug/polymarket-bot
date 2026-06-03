"""Monitors Polymarket market resolutions and automatically closes paper positions."""

import sys
import os
import requests
from datetime import datetime, timezone, timedelta
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
_REQUEST_TIMEOUT = 10


def get_market_resolution_status(market_id: str) -> str:
    """
    Query Gamma API for current market status.
    Returns: OPEN | CLOSED_YES | CLOSED_NO | DISPUTED | EXPIRED_UNRESOLVED
    """
    try:
        resp = requests.get(
            f"{GAMMA_API_BASE}/markets/{market_id}",
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code == 404:
            return "EXPIRED_UNRESOLVED"
        resp.raise_for_status()
        data = resp.json()

        # Handle both single market and list responses
        if isinstance(data, list):
            data = data[0] if data else {}

        active = data.get("active", True)
        closed = data.get("closed", False)

        if not closed and active:
            return "OPEN"

        # Detect outcome from prices
        outcome = get_market_outcome_from_data(data)
        if outcome == "YES":
            return "CLOSED_YES"
        elif outcome == "NO":
            return "CLOSED_NO"

        # Closed but unclear outcome
        if closed:
            return "EXPIRED_UNRESOLVED"

        return "OPEN"

    except requests.exceptions.Timeout:
        logger.warning("resolution_monitor: timeout fetching market {}", market_id)
        return "OPEN"
    except Exception as e:
        logger.warning("resolution_monitor: failed to check market {} — {}", market_id, e)
        return "OPEN"


def get_market_outcome_from_data(data: dict) -> str | None:
    """
    Parse raw market data to determine YES or NO outcome.
    Returns 'YES', 'NO', or None if undetermined.
    """
    # Primary: outcomePrices array — index 0 = YES, index 1 = NO
    outcome_prices = data.get("outcomePrices")
    if outcome_prices and isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
        try:
            yes_val = float(outcome_prices[0])
            no_val = float(outcome_prices[1])
            if yes_val >= 0.99:
                return "YES"
            if no_val >= 0.99:
                return "NO"
        except (ValueError, TypeError):
            pass

    # Fallback: current yes_price / no_price fields
    yes_price = data.get("yes_price") or data.get("bestBid")
    no_price = data.get("no_price")
    if yes_price is not None:
        try:
            if float(yes_price) >= 0.99:
                return "YES"
        except (ValueError, TypeError):
            pass
    if no_price is not None:
        try:
            if float(no_price) >= 0.99:
                return "NO"
        except (ValueError, TypeError):
            pass

    # Fallback: tokens data structure
    tokens = data.get("tokens") or []
    for token in tokens:
        outcome_name = (token.get("outcome") or "").upper()
        price = token.get("price") or 0
        try:
            if float(price) >= 0.99:
                return outcome_name if outcome_name in ("YES", "NO") else None
        except (ValueError, TypeError):
            pass

    return None


def get_market_outcome(market_id: str) -> str | None:
    """Determine the actual YES/NO outcome of a resolved market."""
    try:
        resp = requests.get(
            f"{GAMMA_API_BASE}/markets/{market_id}",
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            data = data[0] if data else {}
        return get_market_outcome_from_data(data)
    except Exception as e:
        logger.warning("resolution_monitor: failed to get outcome for {} — {}", market_id, e)
        return None


def scan_all_open_positions(paper_engine) -> int:
    """
    Run every pipeline cycle. Check all OPEN paper positions for resolution.
    Calls paper_engine.resolve_paper_position() for each resolved market.
    Returns count of positions resolved this scan.
    """
    from data.database import get_open_paper_positions
    positions = get_open_paper_positions()
    if not positions:
        return 0

    # Deduplicate by market_id — only check each market once
    checked_markets: dict = {}
    resolved_count = 0

    for pos in positions:
        market_id = pos["market_id"]
        if market_id in checked_markets:
            status = checked_markets[market_id]
        else:
            status = get_market_resolution_status(market_id)
            checked_markets[market_id] = status

        if status in ("CLOSED_YES", "CLOSED_NO"):
            outcome = "YES" if status == "CLOSED_YES" else "NO"
            logger.info("resolution_monitor: market {} resolved → {}", market_id[:20], outcome)
            paper_engine.resolve_paper_position(market_id, outcome)
            resolved_count += 1

        elif status == "EXPIRED_UNRESOLVED":
            # Check if past expected resolution + 7 day grace period
            expected_str = pos.get("expected_resolution_date", "")
            if expected_str:
                try:
                    expected = datetime.fromisoformat(expected_str.replace("Z", "+00:00"))
                    if expected.tzinfo is None:
                        expected = expected.replace(tzinfo=timezone.utc)
                    grace_cutoff = expected + timedelta(days=7)
                    if datetime.now(timezone.utc) > grace_cutoff:
                        logger.warning(
                            "resolution_monitor: market {} expired unresolved after grace period — voiding",
                            market_id[:20],
                        )
                        paper_engine.void_paper_position(pos["id"])
                        resolved_count += 1
                except Exception:
                    pass

        elif status == "DISPUTED":
            logger.warning("resolution_monitor: market {} is DISPUTED — holding", market_id[:20])
            paper_engine.mark_position_disputed(pos["id"])

    if resolved_count > 0:
        logger.info("resolution_monitor: resolved {} positions this scan", resolved_count)
    return resolved_count


def monitor_disputed_markets(paper_engine) -> int:
    """Re-check all DISPUTED positions. Called hourly."""
    from data.database import get_all_paper_positions
    disputed = get_all_paper_positions(status="DISPUTED")
    resolved = 0
    for pos in disputed:
        market_id = pos["market_id"]
        status = get_market_resolution_status(market_id)
        if status in ("CLOSED_YES", "CLOSED_NO"):
            outcome = "YES" if status == "CLOSED_YES" else "NO"
            logger.info("resolution_monitor: disputed market {} resolved → {}", market_id[:20], outcome)
            paper_engine.resolve_paper_position(market_id, outcome)
            resolved += 1
    return resolved
