"""Orchestrates all intelligence modules into a single enriched context for the LLM."""

import sys
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intelligence.news_analyzer import build_news_context
from intelligence.onchain_analyzer import build_onchain_context
from intelligence.sentiment_engine import build_sentiment_context
from intelligence.historical_performance import build_performance_context
from intelligence.base_rate_calculator import build_base_rate_context

_MODULE_TIMEOUT = 15.0  # seconds per intelligence module


def _safe_call(fn, *args, timeout=_MODULE_TIMEOUT, fallback=None):
    """Call fn(*args) with timeout. Returns fallback dict on failure."""
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(fn, *args)
            return future.result(timeout=timeout)
    except FuturesTimeout:
        logger.warning("context_builder: {} timed out", fn.__name__)
    except Exception as e:
        logger.warning("context_builder: {} raised — {}", fn.__name__, e)
    if fallback is not None:
        return fallback
    return {"is_available": False, "summary": f"{fn.__name__} unavailable"}


def _normalize_whale_signal(onchain: dict) -> float:
    """Convert whale direction to 0-1 bullishness score."""
    direction = onchain.get("whale_activity", {}).get("net_flow_direction", "NEUTRAL")
    return {"ACCUMULATING": 0.70, "NEUTRAL": 0.50, "DISTRIBUTING": 0.30}.get(direction, 0.50)


def _normalize_funding_signal(onchain: dict) -> float:
    """Convert funding rate label to 0-1 bullishness score."""
    label = onchain.get("funding_rate", {}).get("label", "NEUTRAL")
    # Extreme positioning → mean reversion (contrarian)
    mapping = {
        "EXTREME_BULLISH_POSITIONING": 0.30,  # overextended longs — fade
        "BULLISH_POSITIONING": 0.65,
        "NEUTRAL": 0.50,
        "BEARISH_POSITIONING": 0.35,
        "EXTREME_BEARISH_POSITIONING": 0.70,  # overextended shorts — fade
    }
    return mapping.get(label, 0.50)


def _normalize_smart_money_divergence(sentiment: dict) -> float:
    """Convert -1..1 divergence to 0-1 score."""
    div = sentiment.get("smart_money_divergence", 0.0)
    return (div + 1.0) / 2.0


def _normalize_base_rate_divergence(base_rate: dict) -> float:
    """Convert divergence_from_market to 0-1 score."""
    div = base_rate.get("divergence_from_market", 0.0)
    # div > 0 means market underpriced vs history → bullish
    # Sigmoid-like: clamp to ±0.3 range, map to 0.3-0.7
    clamped = max(-0.3, min(0.3, div))
    return round(0.5 + clamped, 3)


def _normalize_historical_accuracy(performance: dict) -> float:
    """Return 0-1 where 0.5 = baseline, higher = historically accurate in this category."""
    accuracy = performance.get("historical_accuracy")
    if accuracy is None:
        return 0.5
    # Map 0.4-0.8 accuracy range to 0.3-0.7
    clamped = max(0.4, min(0.8, accuracy))
    return round((clamped - 0.4) / 0.4 * 0.4 + 0.3, 3)


def _composite_interpretation(score: float) -> str:
    if score >= 0.65:
        return "STRONG BULLISH SIGNAL"
    elif score >= 0.55:
        return "BULLISH LEAN"
    elif score >= 0.45:
        return "NEUTRAL — no clear edge"
    elif score >= 0.35:
        return "BEARISH LEAN"
    return "STRONG BEARISH SIGNAL"


def build_full_context(market: dict, strategy_adjustment: dict = None) -> dict:
    """
    Calls all five intelligence modules. Runs in parallel where possible.
    Returns FullIntelligenceContext dict with composite_signal score.
    """
    # Phase 1: run news, onchain, base_rate, performance in parallel (4 modules)
    with ThreadPoolExecutor(max_workers=4) as executor:
        news_future = executor.submit(_safe_call, build_news_context, market)
        onchain_future = executor.submit(_safe_call, build_onchain_context, market)
        base_rate_future = executor.submit(_safe_call, build_base_rate_context, market)
        performance_future = executor.submit(_safe_call, build_performance_context, market)

        try:
            news = news_future.result(timeout=_MODULE_TIMEOUT + 5)
        except Exception as e:
            logger.warning("context_builder: news module failed — {}", e)
            news = {"is_available": False, "velocity_score": 0.0, "sentiment_direction": "NEUTRAL",
                    "summary": "News data unavailable", "top_headlines": "(unavailable)"}

        try:
            onchain = onchain_future.result(timeout=_MODULE_TIMEOUT + 5)
        except Exception as e:
            logger.warning("context_builder: onchain module failed — {}", e)
            onchain = {
                "is_available": False, "summary": "On-chain data unavailable",
                "funding_rate": {"rate": 0.0, "label": "NEUTRAL"},
                "exchange_flows": {"direction": "NEUTRAL"},
                "whale_activity": {"net_flow_direction": "NEUTRAL"},
                "technicals": {"rsi": 50.0, "macd_signal": "NEUTRAL", "bb_position": "MIDDLE", "volume_trend": "STABLE"},
                "fear_greed": {"score": 50, "label": "NEUTRAL"},
                "mempool": {"congestion": "UNKNOWN", "fee_rate_sat_vbyte": 0},
                "dex_health": {"direction": "STABLE", "change_7d_pct": 0.0},
            }

        try:
            base_rate = base_rate_future.result(timeout=_MODULE_TIMEOUT + 5)
        except Exception as e:
            logger.warning("context_builder: base_rate module failed — {}", e)
            base_rate = {"is_available": False, "summary": "Base rate data unavailable",
                         "question_type": "general", "historical_base_rate": 0.5,
                         "bayesian_prior": 0.5, "divergence_from_market": 0.0}

        try:
            performance = performance_future.result(timeout=_MODULE_TIMEOUT + 5)
        except Exception as e:
            logger.warning("context_builder: performance module failed — {}", e)
            performance = {"is_available": True, "summary": "Performance data unavailable",
                           "category": "general", "historical_accuracy": None, "active_biases": []}

    # Phase 2: sentiment (depends on onchain result)
    try:
        sentiment = _safe_call(build_sentiment_context, market, onchain)
    except Exception as e:
        logger.warning("context_builder: sentiment module failed — {}", e)
        sentiment = {
            "is_available": False, "summary": "Sentiment data unavailable",
            "reddit_sentiment": 0.0, "google_trends_score": 50,
            "crowd_psychology_pattern": "UNCERTAINTY", "smart_money_divergence": 0.0,
            "crowd_trading_implication": "NEUTRAL", "divergence_label": "unavailable",
        }

    # Composite signal
    news_velocity = float(news.get("velocity_score", 0.0)) if news.get("is_available") else 0.5
    whale_signal = _normalize_whale_signal(onchain) if onchain.get("is_available") else 0.5
    funding_signal = _normalize_funding_signal(onchain) if onchain.get("is_available") else 0.5
    sm_divergence = _normalize_smart_money_divergence(sentiment) if sentiment.get("is_available") else 0.5
    base_rate_div = _normalize_base_rate_divergence(base_rate) if base_rate.get("is_available") else 0.5
    hist_accuracy = _normalize_historical_accuracy(performance)

    # Velocity: neutral baseline at 0.5 when low, slight boost when high
    news_component = 0.5 + (news_velocity - 0.3) * 0.5 if news_velocity > 0 else 0.5
    news_component = max(0.3, min(0.7, news_component))

    composite_signal = round(
        news_component * 0.15 +
        whale_signal * 0.20 +
        funding_signal * 0.15 +
        sm_divergence * 0.20 +
        base_rate_div * 0.15 +
        hist_accuracy * 0.15,
        4,
    )

    interpretation = _composite_interpretation(composite_signal)

    return {
        "news": news,
        "onchain": onchain,
        "sentiment": sentiment,
        "performance": performance,
        "base_rate": base_rate,
        "composite_signal": composite_signal,
        "composite_interpretation": interpretation,
        "question": market.get("question", ""),
    }


def format_llm_brief(market: dict, full_context: dict) -> str:
    """Format a FullIntelligenceContext as a structured intelligence brief for the LLM."""
    news = full_context.get("news", {})
    onchain = full_context.get("onchain", {})
    sentiment = full_context.get("sentiment", {})
    performance = full_context.get("performance", {})
    base_rate = full_context.get("base_rate", {})
    composite_signal = full_context.get("composite_signal", 0.5)
    interpretation = full_context.get("composite_interpretation", "NEUTRAL")

    from datetime import datetime, timezone
    question = market.get("question", "Unknown")
    yes_price = float(market.get("yes_price") or 0.5)
    no_price = float(market.get("no_price") or 0.5)
    yes_pct = round(yes_price * 100, 1)
    no_pct = round(no_price * 100, 1)
    try:
        end_date = market.get("end_date", "")
        if end_date:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            days_left = max((end_dt - datetime.now(timezone.utc)).days, 0)
        else:
            days_left = 0
    except Exception:
        days_left = 0

    liquidity = float(market.get("liquidity") or 0)

    # On-chain section
    technicals = onchain.get("technicals", {})
    fear_greed = onchain.get("fear_greed", {})
    mempool = onchain.get("mempool", {})
    funding = onchain.get("funding_rate", {})
    flows = onchain.get("exchange_flows", {})
    whale = onchain.get("whale_activity", {})

    # Active biases
    active_biases = performance.get("active_biases", [])
    biases_text = ", ".join(active_biases) if active_biases else "none detected"

    avoided = performance.get("avoided_categories", [])
    hist_acc = performance.get("historical_accuracy")
    hist_acc_text = f"{hist_acc:.0%}" if hist_acc is not None else "no data yet"
    sample_n = performance.get("sample_size", 0)

    brief = f"""MARKET INTELLIGENCE BRIEF
=========================
Market: {question}
Current odds: YES {yes_pct}% / NO {no_pct}%
Days to resolve: {days_left}
Liquidity: ${liquidity:,.0f}
Composite signal score: {composite_signal:.3f} ({interpretation})

NEWS INTELLIGENCE [{news.get('velocity_label', 'unknown')} velocity, bias: {news.get('bias_flag', 'UNCERTAIN')}]
{news.get('summary', 'No news data available.')}
Top sources:
{news.get('top_headlines', '  (none found)')}

ON-CHAIN INTELLIGENCE
Funding rate: {funding.get('rate', 0):.5f} ({funding.get('label', 'NEUTRAL')})
Whale activity: {whale.get('net_flow_direction', 'NEUTRAL')} — {whale.get('note', '')}
Exchange flows: {flows.get('direction', 'NEUTRAL')} (24h price change: {flows.get('price_change_24h', 0):+.1f}%)
Fear/Greed proxy: {fear_greed.get('score', 50)}/100 ({fear_greed.get('label', 'NEUTRAL')})
Mempool signal: {mempool.get('congestion', 'UNKNOWN')} @ {mempool.get('fee_rate_sat_vbyte', 0)} sat/vbyte
Technical: RSI {technicals.get('rsi', 50.0)}, MACD {technicals.get('macd_signal', 'NEUTRAL')}, BB {technicals.get('bb_position', 'MIDDLE')}, Volume {technicals.get('volume_trend', 'STABLE')}

SENTIMENT INTELLIGENCE
Reddit sentiment: {sentiment.get('reddit_sentiment', 0.0):+.2f} ({sentiment.get('reddit_direction', 'STABLE')}, {sentiment.get('reddit_post_count', 0)} posts)
Google Trends: {sentiment.get('google_trends_score', 50)}/100 ({sentiment.get('google_trends_direction', 'STABLE')}){' — BREAKOUT SPIKE' if sentiment.get('google_breakout') else ''}
Crowd psychology: {sentiment.get('crowd_psychology_pattern', 'UNCERTAINTY')} — {sentiment.get('crowd_psychology_description', '')}
Smart money vs crowd: {sentiment.get('smart_money_divergence', 0.0):+.2f} — {sentiment.get('divergence_label', 'no data')}

HISTORICAL PERFORMANCE
Your accuracy on {performance.get('category', 'this')} markets: {hist_acc_text} ({sample_n} samples)
Calibration note: {performance.get('calibration_note', 'No data yet')}
Active biases to avoid: {biases_text}
Strategy adjustment: {performance.get('strategy_adjustments', 'Baseline active')}

BASE RATE ANALYSIS
Question type: {base_rate.get('question_type', 'general')}
Historical YES rate: {base_rate.get('historical_base_rate', 0.5):.0%}
Bayesian prior: {base_rate.get('bayesian_prior', 0.5):.0%}
Market vs history: {base_rate.get('divergence_label', 'no data')}

SELF-AWARENESS NOTE
{performance.get('summary', 'No performance data yet.')}

INSTRUCTION: Analyze this market using ALL of the above intelligence. Do not rely on a single signal. Identify which signals agree and which conflict. Weight your confidence based on signal consensus. Avoid the active biases listed above. Your response must be JSON only:
{{
  "signal": "BET_YES" or "BET_NO" or "SKIP",
  "confidence": 0.0-1.0,
  "reasoning": "2-3 sentences citing specific signals that drove this decision",
  "edge": estimated edge as decimal,
  "primary_signal": "which signal was most decisive",
  "conflicting_signals": "any signals that disagreed and why you weighted them lower",
  "bias_check": "confirmation you checked for and avoided the listed biases"
}}"""

    return brief
