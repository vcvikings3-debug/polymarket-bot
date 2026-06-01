"""Handles all Polymarket API calls including fetching markets and placing bets."""

import requests
import json
import time
from datetime import datetime, timezone
from loguru import logger

GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# Strict crypto keywords — must match exactly in the question or category
CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "defi",
    "blockchain", "altcoin", "nft", "web3", "usdc", "usdt", "stablecoin",
    "binance", "coinbase", "halving", "memecoin", "airdrop",
    "token price", "market cap",
]

# Blocklist — if any of these appear in the question, reject regardless
BLOCKLIST = [
    "football", "soccer", "fifa", "election", "president", "sports",
    "nfl", "nba", "movie", "music", "album", "tv show", "oscar",
]


def _matches_crypto(market: dict) -> bool:
    """Strict crypto check — market must match a crypto keyword AND pass blocklist."""
    question = (market.get("question") or "").lower()
    category = (market.get("category") or "").lower()

    # Check blocklist first — if question is blocked, reject immediately
    for blocked_word in BLOCKLIST:
        if blocked_word in question:
            return False

    # Combine question + category for keyword matching
    text_to_check = question + " " + category

    # Also check tags
    tags = market.get("tags", [])
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str):
                text_to_check += " " + tag.lower()

    # Check for any crypto keyword
    for keyword in CRYPTO_KEYWORDS:
        if keyword in text_to_check:
            return True

    return False


def fetch_all_markets() -> list:
    """Fetch all active markets from Polymarket Gamma API with pagination."""
    all_markets = []
    offset = 0
    limit = 100

    while True:
        try:
            url = f"{GAMMA_API_BASE}/markets"
            params = {
                "active": "true",
                "limit": limit,
                "offset": offset,
            }
            logger.debug("Fetching markets offset={} limit={}", offset, limit)
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            batch = resp.json()

            if not batch or len(batch) == 0:
                break

            all_markets.extend(batch)
            offset += limit

            if len(batch) < limit:
                break

            # Be respectful to the API
            time.sleep(0.5)

        except requests.exceptions.Timeout:
            logger.warning("Timeout fetching markets at offset={}, retrying...", offset)
            time.sleep(2)
            continue
        except requests.exceptions.RequestException as e:
            logger.error("Network error fetching markets at offset={}: {}", offset, e)
            break
        except json.JSONDecodeError as e:
            logger.error("JSON decode error at offset={}: {}", offset, e)
            break
        except Exception as e:
            logger.error("Unexpected error fetching markets: {}", e)
            break

    logger.info("Fetched {} total markets from Gamma API", len(all_markets))
    return all_markets


def fetch_crypto_markets() -> list:
    """Fetch all markets and filter to crypto-related only."""
    all_markets = fetch_all_markets()
    crypto_markets = [m for m in all_markets if _matches_crypto(m)]
    logger.info("Filtered to {} crypto markets out of {}", len(crypto_markets), len(all_markets))
    return crypto_markets


def parse_market(raw: dict) -> dict:
    """Take a raw API response dict and return a clean, normalized dict."""
    now = datetime.now(timezone.utc).isoformat()

    # Extract end date from various possible fields
    end_date = raw.get("endDate") or raw.get("end_date") or raw.get("closeTime") or ""

    # Calculate yes/no prices from outcomePrices if available
    yes_price = 0.5
    no_price = 0.5
    outcome_prices = raw.get("outcomePrices")
    if outcome_prices and isinstance(outcome_prices, str):
        try:
            prices = json.loads(outcome_prices)
            if isinstance(prices, list) and len(prices) >= 2:
                yes_price = float(prices[0])
                no_price = float(prices[1])
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # Volume
    volume = 0.0
    volume_raw = raw.get("volume")
    if volume_raw:
        try:
            volume = float(volume_raw)
        except (ValueError, TypeError):
            pass

    # Liquidity
    liquidity = 0.0
    liquidity_raw = raw.get("liquidity")
    if liquidity_raw:
        try:
            liquidity = float(liquidity_raw)
        except (ValueError, TypeError):
            pass

    # Category
    category = raw.get("category", "")
    tags = raw.get("tags", [])
    if isinstance(tags, list) and not category:
        category = " ".join(str(t) for t in tags[:3])

    is_crypto = 1 if _matches_crypto(raw) else 0

    return {
        "id": str(raw.get("id", "")),
        "question": raw.get("question", "Unknown"),
        "category": category,
        "end_date": end_date,
        "yes_price": round(yes_price, 4),
        "no_price": round(no_price, 4),
        "volume": round(volume, 2),
        "liquidity": round(liquidity, 2),
        "is_crypto": is_crypto,
        "last_updated": now,
        "raw_json": json.dumps(raw, default=str),
    }