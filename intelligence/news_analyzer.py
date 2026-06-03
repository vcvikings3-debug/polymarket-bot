"""Multi-source crypto news aggregation, velocity tracking, and narrative bias detection."""

import sys
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests

try:
    import feedparser
    _FEEDPARSER = True
except ImportError:
    _FEEDPARSER = False
    logger.warning("feedparser not installed — RSS sources unavailable")

try:
    import praw as _praw
    _PRAW = True
except ImportError:
    _PRAW = False
    logger.warning("praw not installed — Reddit source unavailable")

from config import (
    CRYPTOPANIC_AUTH_TOKEN, REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT,
)

# Source tiers by domain keyword
_TIER1_DOMAINS = {"reuters.com", "ap.org", "bloomberg.com", "wsj.com"}
_TIER2_DOMAINS = {"coindesk.com", "theblock.co", "decrypt.co", "cointelegraph.com", "blockworks.co"}
_TIER3_DOMAINS = {"cryptopanic.com", "reddit.com", "cryptoslate.com", "bitcoinmagazine.com"}

_RSS_FEEDS = [
    ("CoinDesk",  "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Decrypt",   "https://decrypt.co/feed"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
]

_CRYPTO_STOP_WORDS = {
    "will", "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "to", "of", "in", "for", "on",
    "with", "at", "by", "from", "this", "that", "which", "or", "and", "but",
    "if", "not", "what", "how", "when", "where", "who", "before", "after", "it",
    "its", "hit", "reach", "fall", "above", "below", "price", "end", "year",
    "month", "week", "day", "2024", "2025", "2026",
}


@dataclass
class NewsItem:
    title: str
    source: str
    url: str
    published_at: datetime
    sentiment_score: float = 0.0
    source_tier: int = 3


def _tier_for_source(source_name: str) -> int:
    s = source_name.lower()
    for d in _TIER1_DOMAINS:
        if d in s:
            return 1
    for d in _TIER2_DOMAINS:
        if d in s:
            return 2
    for d in _TIER3_DOMAINS:
        if d in s:
            return 3
    return 4


def _tier_weight(tier: int) -> float:
    return {1: 1.0, 2: 0.7, 3: 0.4, 4: 0.2}.get(tier, 0.2)


def _parse_dt(dt_str) -> datetime:
    if isinstance(dt_str, datetime):
        return dt_str.replace(tzinfo=timezone.utc) if dt_str.tzinfo is None else dt_str
    if isinstance(dt_str, (int, float)):
        return datetime.fromtimestamp(dt_str, tz=timezone.utc)
    if not dt_str:
        return datetime.now(timezone.utc)
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
        "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT",
    ):
        try:
            dt = datetime.strptime(str(dt_str)[:25].strip(), fmt[:len(str(dt_str)[:25].strip())])
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _fetch_cryptopanic(keywords=None) -> list:
    """Fetch hot crypto news from CryptoPanic free API."""
    try:
        params = {
            "auth_token": CRYPTOPANIC_AUTH_TOKEN,
            "filter": "hot",
            "public": "true",
        }
        resp = requests.get(
            "https://cryptopanic.com/api/v1/posts/",
            params=params, timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        items = []
        for post in data.get("results", [])[:30]:
            title = post.get("title", "")
            published = _parse_dt(post.get("published_at", ""))
            votes = post.get("votes", {})
            up = votes.get("positive", 0) or 0
            down = votes.get("negative", 0) or 0
            total_votes = up + down
            sentiment = (up - down) / total_votes if total_votes > 0 else 0.0
            tier = 3
            items.append(NewsItem(
                title=title,
                source="cryptopanic.com",
                url=post.get("url", ""),
                published_at=published,
                sentiment_score=sentiment,
                source_tier=tier,
            ))
        logger.debug("news_analyzer: CryptoPanic returned {} items", len(items))
        return items
    except Exception as e:
        logger.warning("news_analyzer: CryptoPanic fetch failed — {}", e)
        return []


def _fetch_rss() -> list:
    """Fetch items from configured RSS feeds."""
    if not _FEEDPARSER:
        return []
    items = []
    for feed_name, feed_url in _RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            tier = _tier_for_source(feed_url)
            for entry in feed.entries[:15]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                published = _parse_dt(entry.get("published", entry.get("updated", "")))
                items.append(NewsItem(
                    title=title,
                    source=feed_name,
                    url=link,
                    published_at=published,
                    sentiment_score=0.0,
                    source_tier=tier,
                ))
        except Exception as e:
            logger.warning("news_analyzer: RSS {} fetch failed — {}", feed_name, e)
    logger.debug("news_analyzer: RSS feeds returned {} items", len(items))
    return items


def _fetch_coingecko_trending() -> list:
    """Fetch trending coins from CoinGecko as a proxy for active narratives."""
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/search/trending",
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        items = []
        now = datetime.now(timezone.utc)
        for coin in data.get("coins", [])[:10]:
            item = coin.get("item", {})
            name = item.get("name", "")
            symbol = item.get("symbol", "")
            title = f"Trending: {name} ({symbol}) — top 10 on CoinGecko"
            items.append(NewsItem(
                title=title,
                source="coingecko.com",
                url="https://coingecko.com",
                published_at=now,
                sentiment_score=0.3,  # trending = mild positive
                source_tier=3,
            ))
        logger.debug("news_analyzer: CoinGecko trending returned {} items", len(items))
        return items
    except Exception as e:
        logger.warning("news_analyzer: CoinGecko trending failed — {}", e)
        return []


def _fetch_reddit(keywords=None) -> list:
    """Fetch top posts from crypto subreddits via PRAW."""
    if not _PRAW or not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        return []
    try:
        reddit = _praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
        )
        subreddits = ["CryptoCurrency", "Bitcoin", "ethereum", "solana"]
        items = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
        for sub_name in subreddits:
            try:
                sub = reddit.subreddit(sub_name)
                for post in sub.hot(limit=10):
                    published = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
                    if published < cutoff:
                        continue
                    # Keyword filter if provided
                    if keywords:
                        title_lower = post.title.lower()
                        if not any(kw.lower() in title_lower for kw in keywords):
                            continue
                    # Score ratio as sentiment proxy
                    ratio = post.upvote_ratio or 0.5
                    sentiment = (ratio - 0.5) * 2  # map 0-1 → -1 to 1
                    items.append(NewsItem(
                        title=post.title,
                        source=f"reddit.com/r/{sub_name}",
                        url=f"https://reddit.com{post.permalink}",
                        published_at=published,
                        sentiment_score=sentiment,
                        source_tier=3,
                    ))
            except Exception as e:
                logger.warning("news_analyzer: Reddit r/{} failed — {}", sub_name, e)
        logger.debug("news_analyzer: Reddit returned {} items", len(items))
        return items
    except Exception as e:
        logger.warning("news_analyzer: Reddit init failed — {}", e)
        return []


def fetch_all_news(keywords=None) -> list:
    """Pull from all four sources in parallel with 10s timeout each. Returns unified list."""
    sources = [
        ("CryptoPanic", lambda: _fetch_cryptopanic(keywords)),
        ("RSS", _fetch_rss),
        ("CoinGecko Trending", _fetch_coingecko_trending),
        ("Reddit", lambda: _fetch_reddit(keywords)),
    ]
    all_items = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fn): name for name, fn in sources}
        for future, name in futures.items():
            try:
                result = future.result(timeout=10)
                all_items.extend(result)
            except FuturesTimeout:
                logger.warning("news_analyzer: {} timed out", name)
            except Exception as e:
                logger.warning("news_analyzer: {} raised {}", name, e)
    return all_items


def calculate_news_velocity(news_items: list, hours: int = 6) -> float:
    """
    Measures how fast a story is spreading. Returns 0.0-1.0.
    High = broke recently and spreading. Low = old news already priced in.
    """
    if not news_items:
        return 0.0
    now = datetime.now(timezone.utc)
    cutoff_total = now - timedelta(hours=hours)
    cutoff_recent = now - timedelta(hours=1)
    total = sum(1 for n in news_items if n.published_at >= cutoff_total)
    recent = sum(1 for n in news_items if n.published_at >= cutoff_recent)
    if total == 0:
        return 0.0
    raw = recent / total
    # Boost score if there are many recent articles
    volume_bonus = min(recent / 10.0, 0.2)
    return min(round(raw + volume_bonus, 3), 1.0)


def detect_narrative_bias(news_items: list) -> dict:
    """
    Analyzes for coordinated narrative patterns.
    Returns {'flag': str, 'confidence': float, 'reason': str}
    """
    if len(news_items) < 3:
        return {"flag": "UNCERTAIN", "confidence": 0.3, "reason": "Insufficient data"}

    now = datetime.now(timezone.utc)
    recent_2h = [n for n in news_items if n.published_at >= now - timedelta(hours=2)]
    tier12 = [n for n in recent_2h if n.source_tier <= 2]

    if len(tier12) >= 3:
        # Check for similar title patterns (rough keyword overlap)
        titles = [n.title.lower() for n in tier12]
        words_per_title = [set(t.split()) - _CRYPTO_STOP_WORDS for t in titles]
        overlap_count = 0
        for i in range(len(words_per_title)):
            for j in range(i + 1, len(words_per_title)):
                shared = words_per_title[i] & words_per_title[j]
                if len(shared) >= 3:
                    overlap_count += 1
        if overlap_count >= 2:
            return {
                "flag": "COORDINATED",
                "confidence": 0.65,
                "reason": f"{len(tier12)} tier-1/2 sources with overlapping talking points in 2h",
            }

    # Sponsored content patterns (promotional language)
    promo_patterns = [
        r"\b(partner(ed|ship)?|sponsor(ed)?|announce[sd]?|launch(es|ed)?|present)\b",
    ]
    promo_count = 0
    for n in recent_2h[:10]:
        for pat in promo_patterns:
            if re.search(pat, n.title, re.IGNORECASE):
                promo_count += 1
                break
    if promo_count >= 3 and len(recent_2h) > 0:
        promo_ratio = promo_count / len(recent_2h)
        if promo_ratio > 0.5:
            return {
                "flag": "SPONSORED",
                "confidence": 0.55,
                "reason": f"{promo_count}/{len(recent_2h)} recent articles match promotional patterns",
            }

    if len(recent_2h) >= 3:
        return {
            "flag": "ORGANIC",
            "confidence": 0.6,
            "reason": "Natural spread across sources and time",
        }

    return {"flag": "UNCERTAIN", "confidence": 0.3, "reason": "Insufficient recent data"}


def extract_market_keywords(market_question: str) -> list:
    """Extract key entities and concepts from a market question for news searching."""
    text = market_question.lower()
    # Remove common question preamble words
    text = re.sub(r"\b(will|does|is|are|can|would|could|should|has|have|did)\b", "", text)
    words = re.findall(r"[a-z]{3,}", text)
    # Include ticker symbols (2-5 uppercase chars in original)
    tickers = re.findall(r"\b[A-Z]{2,5}\b", market_question)
    # Known crypto names to always include
    crypto_terms = [
        "bitcoin", "ethereum", "solana", "btc", "eth", "sol", "bnb", "xrp",
        "cardano", "avalanche", "polygon", "chainlink", "uniswap", "aave",
        "dogecoin", "shiba", "pepe", "defi", "nft", "web3", "layer2",
    ]
    keywords = []
    for w in words:
        if w not in _CRYPTO_STOP_WORDS and len(w) > 3:
            keywords.append(w)
    keywords.extend([t.lower() for t in tickers])
    for ct in crypto_terms:
        if ct in text and ct not in keywords:
            keywords.append(ct)
    return list(dict.fromkeys(keywords))[:10]  # deduplicate, cap at 10


def score_news_relevance(news_item: NewsItem, market_keywords: list) -> float:
    """Score how relevant a news item is to a market (0.0-1.0)."""
    title_lower = news_item.title.lower()
    if not market_keywords:
        return 0.0
    matches = sum(1 for kw in market_keywords if kw.lower() in title_lower)
    keyword_score = min(matches / max(len(market_keywords), 1), 1.0)
    tier_bonus = _tier_weight(news_item.source_tier)
    # Recency bonus: max 0.2 for items < 1 hour old
    age_hours = (datetime.now(timezone.utc) - news_item.published_at).total_seconds() / 3600
    recency = max(0.0, 0.2 * (1 - age_hours / 24))
    score = (keyword_score * 0.6) + (tier_bonus * 0.2) + recency
    return round(min(score, 1.0), 3)


def build_news_context(market: dict) -> dict:
    """
    Master function — fetches all news, scores relevance to market, returns NewsContext dict.
    """
    try:
        question = market.get("question", "")
        keywords = extract_market_keywords(question)
        all_news = fetch_all_news(keywords=keywords)

        # Score and sort by relevance
        scored = sorted(
            [(n, score_news_relevance(n, keywords)) for n in all_news],
            key=lambda x: x[1], reverse=True,
        )
        top_items = [n for n, s in scored[:5] if s > 0]
        if not top_items:
            top_items = [n for n, _ in scored[:3]]

        velocity = calculate_news_velocity(
            [n for n, _ in scored if scored.index((n, score_news_relevance(n, keywords))) < 20],
            hours=6,
        )
        bias = detect_narrative_bias(all_news)

        # Sentiment direction from top scored items
        if scored:
            relevant = [n for n, s in scored[:10] if s > 0.1]
            if relevant:
                avg_sentiment = sum(n.sentiment_score for n in relevant) / len(relevant)
            else:
                avg_sentiment = 0.0
        else:
            avg_sentiment = 0.0

        if avg_sentiment > 0.2:
            direction = "BULLISH"
        elif avg_sentiment < -0.2:
            direction = "BEARISH"
        elif len(top_items) > 0:
            direction = "NEUTRAL"
        else:
            direction = "MIXED"

        top_headlines = "\n".join(
            f"  - [{n.source}] {n.title[:80]}" for n in top_items[:3]
        ) or "  (no relevant headlines found)"

        summary = (
            f"Found {len(all_news)} news items across sources. "
            f"Velocity: {velocity:.2f} ({velocity_label(velocity)}). "
            f"Bias: {bias['flag']}. "
            f"Sentiment direction: {direction}."
        )

        return {
            "top_news": [
                {"title": n.title, "source": n.source, "published_at": n.published_at.isoformat(),
                 "sentiment_score": n.sentiment_score, "source_tier": n.source_tier}
                for n in top_items
            ],
            "velocity_score": velocity,
            "velocity_label": velocity_label(velocity),
            "bias_flag": bias["flag"],
            "bias_confidence": bias["confidence"],
            "sentiment_direction": direction,
            "avg_sentiment": round(avg_sentiment, 3),
            "top_headlines": top_headlines,
            "summary": summary,
            "total_articles": len(all_news),
            "is_available": True,
        }
    except Exception as e:
        logger.error("news_analyzer.build_news_context failed: {}", e)
        return {
            "top_news": [],
            "velocity_score": 0.0,
            "velocity_label": "unknown",
            "bias_flag": "UNCERTAIN",
            "bias_confidence": 0.0,
            "sentiment_direction": "NEUTRAL",
            "avg_sentiment": 0.0,
            "top_headlines": "(news data unavailable)",
            "summary": "News intelligence unavailable.",
            "total_articles": 0,
            "is_available": False,
        }


def velocity_label(score: float) -> str:
    if score > 0.6:
        return "HIGH — story breaking now"
    elif score > 0.3:
        return "MEDIUM — building momentum"
    elif score > 0.1:
        return "LOW — slow-moving story"
    return "MINIMAL — old/no coverage"
