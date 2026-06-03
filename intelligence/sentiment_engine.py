"""Multi-platform sentiment aggregation, crowd psychology detection, and smart money divergence."""

import sys
import os
import re
from datetime import datetime, timezone, timedelta
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _vader = SentimentIntensityAnalyzer()
    _VADER = True
except ImportError:
    _VADER = False
    logger.warning("vaderSentiment not installed — VADER scoring unavailable")

try:
    from pytrends.request import TrendReq
    _PYTRENDS = True
except ImportError:
    _PYTRENDS = False
    logger.warning("pytrends not installed — Google Trends unavailable")

try:
    import praw as _praw
    _PRAW = True
except ImportError:
    _PRAW = False

from config import (
    REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT,
)


def _vader_score(text: str) -> float:
    """Return VADER compound sentiment score -1.0 to 1.0."""
    if not _VADER:
        return 0.0
    return _vader.polarity_scores(text)["compound"]


def get_reddit_sentiment(subreddits: list, keywords: list, hours: int = 24) -> dict:
    """
    Fetch top posts mentioning keywords from subreddits and score with VADER.
    Returns avg_sentiment, post_count, comment_count, trending_direction.
    """
    if not _PRAW or not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        return {
            "avg_sentiment": 0.0, "post_count": 0, "comment_count": 0,
            "trending_direction": "STABLE", "is_available": False,
        }
    try:
        reddit = _praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
        )
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        scores = []
        total_comments = 0
        recent_scores = []

        for sub_name in subreddits:
            try:
                sub = reddit.subreddit(sub_name)
                for post in sub.hot(limit=25):
                    published = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
                    if published < cutoff:
                        continue
                    title_lower = post.title.lower()
                    if not any(kw.lower() in title_lower for kw in keywords):
                        continue
                    score = _vader_score(post.title)
                    scores.append(score)
                    total_comments += post.num_comments or 0
                    if published >= datetime.now(timezone.utc) - timedelta(hours=6):
                        recent_scores.append(score)
            except Exception as e:
                logger.warning("sentiment: Reddit r/{} failed — {}", sub_name, e)

        if not scores:
            return {
                "avg_sentiment": 0.0, "post_count": 0, "comment_count": 0,
                "trending_direction": "STABLE", "is_available": True,
            }

        avg_sentiment = round(sum(scores) / len(scores), 3)

        # Trending direction: compare recent 6h to full window
        if len(recent_scores) >= 2 and len(scores) >= 4:
            recent_avg = sum(recent_scores) / len(recent_scores)
            older_avg = sum(scores[: len(scores) // 2]) / (len(scores) // 2)
            if recent_avg > older_avg + 0.1:
                trending = "RISING"
            elif recent_avg < older_avg - 0.1:
                trending = "FALLING"
            else:
                trending = "STABLE"
        else:
            trending = "STABLE"

        return {
            "avg_sentiment": avg_sentiment,
            "post_count": len(scores),
            "comment_count": total_comments,
            "trending_direction": trending,
            "is_available": True,
        }
    except Exception as e:
        logger.warning("sentiment: Reddit sentiment failed — {}", e)
        return {
            "avg_sentiment": 0.0, "post_count": 0, "comment_count": 0,
            "trending_direction": "STABLE", "is_available": False,
        }


def get_google_trends_signal(keywords: list, timeframe: str = "now 7-d") -> dict:
    """
    Fetch Google search interest for keywords.
    Returns interest_score 0-100, trend_direction, breakout_flag.
    """
    if not _PYTRENDS or not keywords:
        return {
            "interest_score": 50, "trend_direction": "STABLE",
            "breakout_flag": False, "is_available": False,
        }
    try:
        pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
        # Use first 5 keywords max
        search_terms = keywords[:5]
        pytrends.build_payload(search_terms, timeframe=timeframe)
        data = pytrends.interest_over_time()
        if data.empty:
            return {
                "interest_score": 50, "trend_direction": "STABLE",
                "breakout_flag": False, "is_available": True,
            }
        # Average across all keywords
        import numpy as np
        numeric_cols = [c for c in data.columns if c != "isPartial"]
        series = data[numeric_cols].mean(axis=1).values.tolist()
        if len(series) < 3:
            current_score = int(series[-1]) if series else 50
            return {
                "interest_score": current_score, "trend_direction": "STABLE",
                "breakout_flag": False, "is_available": True,
            }
        current = series[-1]
        mean_30d = sum(series) / len(series)
        std_30d = (sum((s - mean_30d) ** 2 for s in series) / len(series)) ** 0.5

        # Trend direction (last 3 vs earlier)
        recent_avg = sum(series[-3:]) / 3
        older_avg = sum(series[:-3]) / max(len(series) - 3, 1)
        if recent_avg > older_avg * 1.2:
            direction = "RISING"
        elif recent_avg < older_avg * 0.8:
            direction = "FALLING"
        else:
            direction = "STABLE"

        # Breakout: current > mean + 2 std
        breakout = bool(std_30d > 0 and current > mean_30d + 2 * std_30d)

        return {
            "interest_score": int(current),
            "trend_direction": direction,
            "breakout_flag": breakout,
            "mean_30d": round(mean_30d, 1),
            "is_available": True,
        }
    except Exception as e:
        logger.warning("sentiment: Google Trends failed — {}", e)
        return {
            "interest_score": 50, "trend_direction": "STABLE",
            "breakout_flag": False, "is_available": False,
        }


def detect_crowd_psychology(sentiment_data: dict, onchain_context: dict, market: dict) -> dict:
    """
    Identify which psychological pattern the crowd is exhibiting.
    Returns pattern, confidence, trading_implication.
    """
    avg_sentiment = sentiment_data.get("avg_sentiment", 0.0)
    trending = sentiment_data.get("trending_direction", "STABLE")
    google_score = sentiment_data.get("google_interest_score", 50)
    google_breakout = sentiment_data.get("google_breakout", False)

    yes_price = float(market.get("yes_price") or 0.5)
    fear_greed_score = onchain_context.get("fear_greed", {}).get("score", 50)
    whale_direction = onchain_context.get("whale_activity", {}).get("net_flow_direction", "NEUTRAL")
    volume_trend = onchain_context.get("technicals", {}).get("volume_trend", "STABLE")
    rsi = onchain_context.get("technicals", {}).get("rsi", 50.0)

    # FOMO: sentiment rising fast + retail search spiking + high price
    if trending == "RISING" and google_breakout and fear_greed_score > 65:
        return {
            "pattern": "FOMO",
            "confidence": 0.7,
            "description": "Retail sentiment rising fast with search spike — FOMO entering",
            "trading_implication": "BET_AGAINST_CROWD",
        }

    # PANIC: sentiment dropping fast + high volume
    if avg_sentiment < -0.3 and trending == "FALLING" and volume_trend == "INCREASING":
        return {
            "pattern": "PANIC",
            "confidence": 0.65,
            "description": "Rapid negative sentiment with high selling volume",
            "trading_implication": "BET_WITH_CROWD",
        }

    # EUPHORIA: extreme positive sentiment + declining volume (distribution)
    if avg_sentiment > 0.4 and fear_greed_score > 75 and volume_trend == "DECREASING":
        return {
            "pattern": "EUPHORIA",
            "confidence": 0.6,
            "description": "Extreme positive sentiment with declining volume — distribution phase",
            "trading_implication": "BET_AGAINST_CROWD",
        }

    # CAPITULATION: extreme negative + whale accumulation
    if avg_sentiment < -0.3 and whale_direction == "ACCUMULATING" and rsi < 35:
        return {
            "pattern": "CAPITULATION",
            "confidence": 0.65,
            "description": "Extreme negative sentiment + whale accumulation at oversold RSI",
            "trading_implication": "BET_AGAINST_CROWD",
        }

    # COMPLACENCY: flat sentiment, low volume
    if abs(avg_sentiment) < 0.1 and volume_trend == "DECREASING" and 40 < rsi < 60:
        return {
            "pattern": "COMPLACENCY",
            "confidence": 0.5,
            "description": "Flat sentiment and low volume — potential breakout setup",
            "trading_implication": "NEUTRAL",
        }

    return {
        "pattern": "UNCERTAINTY",
        "confidence": 0.4,
        "description": "Mixed signals, no dominant psychological pattern identified",
        "trading_implication": "NEUTRAL",
    }


def calculate_smart_money_divergence(market: dict, onchain_context: dict,
                                      sentiment_data: dict) -> float:
    """
    Compare retail sentiment vs smart money (on-chain) positioning.
    Returns -1.0 to 1.0 where positive = smart money bullish, negative = bearish.
    """
    avg_sentiment = sentiment_data.get("avg_sentiment", 0.0)
    whale_direction = onchain_context.get("whale_activity", {}).get("net_flow_direction", "NEUTRAL")
    funding_label = onchain_context.get("funding_rate", {}).get("label", "NEUTRAL")
    exchange_flow = onchain_context.get("exchange_flows", {}).get("direction", "NEUTRAL")

    # Smart money signal: whale + funding + exchange flows
    smart_money_score = 0.0
    if whale_direction == "ACCUMULATING":
        smart_money_score += 0.4
    elif whale_direction == "DISTRIBUTING":
        smart_money_score -= 0.4

    if "BULLISH" in funding_label:
        smart_money_score += 0.3
    elif "BEARISH" in funding_label:
        smart_money_score -= 0.3
    if "EXTREME" in funding_label:
        # Extreme positioning = mean reversion likely
        smart_money_score = -smart_money_score * 0.5

    if exchange_flow == "OUTFLOW":
        smart_money_score += 0.3
    elif exchange_flow == "INFLOW":
        smart_money_score -= 0.3

    smart_money_score = max(-1.0, min(1.0, smart_money_score))

    # Divergence: if retail positive but smart money negative → bearish divergence (return negative)
    # If retail negative but smart money positive → bullish divergence (return positive)
    divergence = smart_money_score - avg_sentiment
    return round(max(-1.0, min(1.0, divergence)), 3)


def build_sentiment_context(market: dict, onchain_context: dict) -> dict:
    """Master function — returns SentimentContext dict."""
    try:
        from intelligence.news_analyzer import extract_market_keywords
        question = market.get("question", "")
        keywords = extract_market_keywords(question)
        if not keywords:
            keywords = ["bitcoin", "crypto"]

        subreddits = ["CryptoCurrency", "Bitcoin", "ethereum"]
        reddit_data = get_reddit_sentiment(subreddits, keywords, hours=24)
        trends_data = get_google_trends_signal(keywords[:3])

        sentiment_payload = {
            "avg_sentiment": reddit_data.get("avg_sentiment", 0.0),
            "trending_direction": reddit_data.get("trending_direction", "STABLE"),
            "google_interest_score": trends_data.get("interest_score", 50),
            "google_breakout": trends_data.get("breakout_flag", False),
        }

        crowd_psych = detect_crowd_psychology(sentiment_payload, onchain_context, market)
        divergence = calculate_smart_money_divergence(market, onchain_context, sentiment_payload)

        avg_sent = reddit_data.get("avg_sentiment", 0.0)
        if avg_sent > 0.2:
            sent_label = "POSITIVE"
        elif avg_sent < -0.2:
            sent_label = "NEGATIVE"
        else:
            sent_label = "NEUTRAL"

        if divergence > 0.3:
            div_label = "BULLISH DIVERGENCE — smart money buying while crowd is negative"
        elif divergence < -0.3:
            div_label = "BEARISH DIVERGENCE — smart money distributing while crowd is positive"
        else:
            div_label = "No significant divergence"

        summary = (
            f"Reddit sentiment: {avg_sent:+.2f} ({sent_label}), {reddit_data.get('post_count', 0)} posts. "
            f"Google Trends: {trends_data.get('interest_score', 50)}/100 ({trends_data.get('trend_direction', 'STABLE')}). "
            f"Crowd psychology: {crowd_psych['pattern']} ({crowd_psych['confidence']:.0%} confidence). "
            f"Smart money divergence: {divergence:+.2f} — {div_label}."
        )

        return {
            "reddit_sentiment": avg_sent,
            "reddit_direction": reddit_data.get("trending_direction", "STABLE"),
            "reddit_post_count": reddit_data.get("post_count", 0),
            "google_trends_score": trends_data.get("interest_score", 50),
            "google_trends_direction": trends_data.get("trend_direction", "STABLE"),
            "google_breakout": trends_data.get("breakout_flag", False),
            "crowd_psychology_pattern": crowd_psych["pattern"],
            "crowd_psychology_description": crowd_psych["description"],
            "crowd_trading_implication": crowd_psych["trading_implication"],
            "smart_money_divergence": divergence,
            "divergence_label": div_label,
            "summary": summary,
            "is_available": True,
        }
    except Exception as e:
        logger.error("sentiment_engine.build_sentiment_context failed: {}", e)
        return {
            "reddit_sentiment": 0.0,
            "reddit_direction": "STABLE",
            "reddit_post_count": 0,
            "google_trends_score": 50,
            "google_trends_direction": "STABLE",
            "google_breakout": False,
            "crowd_psychology_pattern": "UNCERTAINTY",
            "crowd_psychology_description": "Sentiment data unavailable",
            "crowd_trading_implication": "NEUTRAL",
            "smart_money_divergence": 0.0,
            "divergence_label": "data unavailable",
            "summary": "Sentiment data unavailable.",
            "is_available": False,
        }
