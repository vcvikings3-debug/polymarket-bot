"""Scores Polymarket markets using the Deep Intelligence Layer (Phase 2.5).

Falls back to simple prompt if intelligence modules are unavailable.
"""

import json
import requests
from datetime import datetime, timezone
from loguru import logger
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LM_STUDIO_HOST, OLLAMA_MODEL

LM_STUDIO_URL = f"{LM_STUDIO_HOST}/v1/chat/completions"
LM_MODEL = OLLAMA_MODEL
LLM_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "llm_calls.log"
)

os.makedirs(os.path.dirname(LLM_LOG_PATH), exist_ok=True)
llm_logger = logger.bind(name="llm")
llm_logger.add(LLM_LOG_PATH, rotation="10 MB", format="{time} | {message}")


def _days_until(end_date_str: str) -> int:
    if not end_date_str:
        return 0
    try:
        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return max((end_date - now).days, 0)
    except (ValueError, TypeError):
        return 0


SYSTEM_PROMPT = (
    "You are a prediction market analyst specializing in crypto markets on Polymarket. "
    "Your job is to identify markets where the current odds are mispriced relative to the "
    "true probability of the outcome. You are analytical, data-driven, and concise. "
    "Weigh all available intelligence signals. Never recommend betting more than the user can afford to lose."
)

_SIMPLE_PROMPT_TEMPLATE = (
    "Analyze this Polymarket prediction market and give me a betting signal.\n\n"
    "Market: {question}\n"
    "Current YES price: {yes_price} (implies {yes_pct}% probability)\n"
    "Current NO price: {no_price} (implies {no_pct}% probability)\n"
    "Liquidity: ${liquidity:,.2f}\n"
    "Days until resolution: {days_left}\n\n"
    "Based on your knowledge of crypto markets and current conditions, is this market mispriced?\n"
    "Respond with ONLY valid JSON using double quotes, no other text:\n"
    "{{\n"
    '  "signal": "BET_YES" or "BET_NO" or "SKIP",\n'
    '  "confidence": 0.0 to 1.0,\n'
    '  "reasoning": "one sentence max",\n'
    '  "edge": "estimated edge as decimal e.g. 0.08 means 8% edge"\n'
    "}}"
)


def _parse_llm_json(content: str) -> dict | None:
    """Extract and parse JSON from LLM response. Handles markdown fences and single quotes."""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(line for line in lines if not line.startswith("```"))
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    json_str = cleaned[start:end]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            import ast
            return ast.literal_eval(json_str)
        except (ValueError, SyntaxError):
            return None


def _call_llm(system_prompt: str, user_prompt: str, market_question: str) -> dict:
    """Send prompts to LM Studio and return parsed result dict."""
    payload = {
        "model": LM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 512,
    }

    logger.info("LLM analyzing market: {}", market_question[:60])
    llm_logger.info("REQUEST market={}", market_question[:60])

    try:
        resp = requests.post(LM_STUDIO_URL, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        llm_logger.info("RESPONSE market={} raw={}", market_question[:60], content[:300])

        parsed = _parse_llm_json(content)
        if parsed and isinstance(parsed, dict):
            signal = parsed.get("signal", "SKIP")
            confidence = float(parsed.get("confidence") or 0)
            reasoning = str(parsed.get("reasoning") or "")[:300]

            # Parse edge
            edge_raw = parsed.get("edge", 0)
            if isinstance(edge_raw, str):
                edge_raw = edge_raw.strip()
                if "%" in edge_raw:
                    try:
                        edge = float(edge_raw.replace("%", "").strip()) / 100.0
                    except ValueError:
                        edge = 0.0
                else:
                    try:
                        edge = float(edge_raw)
                    except ValueError:
                        edge = 0.0
            else:
                edge = float(edge_raw) if edge_raw else 0.0

            if signal not in ("BET_YES", "BET_NO", "SKIP"):
                signal = "SKIP"

            result = {
                "signal": signal,
                "confidence": min(max(confidence, 0.0), 1.0),
                "reasoning": reasoning,
                "edge": edge,
            }
            logger.info("LLM result: {} confidence={:.2f} edge={:.4f}", signal, confidence, edge)
            llm_logger.info("RESULT market={} signal={} conf={} edge={}", market_question[:60], signal, confidence, edge)
            return result

        logger.warning("LLM returned unparseable JSON for: {}", market_question[:60])
        llm_logger.warning("PARSE_FAIL market={} raw={}", market_question[:60], content[:300])

    except requests.exceptions.ConnectionError:
        logger.error("LLM connection error — LM Studio unreachable at {}", LM_STUDIO_URL)
        llm_logger.error("CONNECTION_ERROR url={}", LM_STUDIO_URL)
    except requests.exceptions.Timeout:
        logger.warning("LLM timeout for market: {}", market_question[:60])
        llm_logger.warning("TIMEOUT market={}", market_question[:60])
    except requests.exceptions.RequestException as e:
        logger.error("LLM request error: {}", e)
    except Exception as e:
        logger.error("Unexpected LLM error for {}: {}", market_question[:60], e)

    return {"signal": "SKIP", "confidence": 0.0, "reasoning": "LLM unavailable", "edge": 0.0}


def analyze_market_with_llm(market: dict) -> dict:
    """
    Full intelligence pipeline:
    1. Check strategy adjustments (avoided categories, cooling period)
    2. Build intelligence brief from all 5 modules
    3. Send brief to LLM
    4. Record prediction in DB
    5. Fall back to simple prompt if any step fails
    """
    question = market.get("question", "Unknown")
    yes_price = float(market.get("yes_price") or 0.5)
    no_price = float(market.get("no_price") or 0.5)
    liquidity = float(market.get("liquidity") or 0)
    days_left = _days_until(market.get("end_date", ""))
    yes_pct = round(yes_price * 100, 1)
    no_pct = round(no_price * 100, 1)

    full_context = None
    prompt_version_id = None
    using_intelligence = False

    # Try Phase 2.5 intelligence layer
    try:
        from intelligence.historical_performance import generate_strategy_adjustment
        from intelligence.context_builder import build_full_context, format_llm_brief
        from intelligence.prompt_evolution_engine import get_current_prompt_version

        strategy = generate_strategy_adjustment()

        # Check if this market category is avoided
        from intelligence.historical_performance import _classify_market_category
        category = _classify_market_category(question)
        if category in strategy.get("avoided_categories", []):
            logger.info("LLM skip — category '{}' avoided by strategy (low historical accuracy)", category)
            return {
                "signal": "SKIP",
                "confidence": 0.0,
                "reasoning": f"Category '{category}' avoided — historical accuracy below threshold",
                "edge": 0.0,
            }

        # Check cooling period
        if strategy.get("cooling_period_active"):
            logger.warning("LLM skip — cooling period active (recent loss streak)")
            return {
                "signal": "SKIP",
                "confidence": 0.0,
                "reasoning": "Cooling period active — recent loss streak detected",
                "edge": 0.0,
            }

        # Get current prompt version
        version = get_current_prompt_version()
        system_prompt = version.get("prompt_text", SYSTEM_PROMPT)
        prompt_version_id = version.get("id")

        # Build full intelligence context
        full_context = build_full_context(market, strategy)
        user_prompt = format_llm_brief(market, full_context)
        using_intelligence = True
        logger.debug("LLM using intelligence brief for: {}", question[:50])

    except Exception as e:
        logger.warning("Phase 2.5 intelligence layer failed ({}), falling back to simple prompt", e)
        system_prompt = SYSTEM_PROMPT
        user_prompt = _SIMPLE_PROMPT_TEMPLATE.format(
            question=question, yes_price=yes_price, yes_pct=yes_pct,
            no_price=no_price, no_pct=no_pct, liquidity=liquidity, days_left=days_left,
        )

    # If intelligence failed, use simple prompt
    if not using_intelligence:
        user_prompt = _SIMPLE_PROMPT_TEMPLATE.format(
            question=question, yes_price=yes_price, yes_pct=yes_pct,
            no_price=no_price, no_pct=no_pct, liquidity=liquidity, days_left=days_left,
        )
        system_prompt = SYSTEM_PROMPT

    result = _call_llm(system_prompt, user_prompt, question)

    # Record prediction in DB
    try:
        from intelligence.historical_performance import record_prediction
        context_snapshot = {}
        if full_context:
            context_snapshot = {
                "question": question,
                "composite_signal": full_context.get("composite_signal", 0.5),
                "news": {
                    "velocity_score": full_context.get("news", {}).get("velocity_score", 0),
                    "bias_flag": full_context.get("news", {}).get("bias_flag", "UNCERTAIN"),
                    "is_available": full_context.get("news", {}).get("is_available", False),
                },
                "onchain": {
                    "whale_direction": full_context.get("onchain", {}).get("whale_activity", {}).get("net_flow_direction", "NEUTRAL"),
                    "funding_label": full_context.get("onchain", {}).get("funding_rate", {}).get("label", "NEUTRAL"),
                    "rsi": full_context.get("onchain", {}).get("technicals", {}).get("rsi", 50),
                    "is_available": full_context.get("onchain", {}).get("is_available", False),
                },
                "sentiment": {
                    "reddit_sentiment": full_context.get("sentiment", {}).get("reddit_sentiment", 0.0),
                    "crowd_pattern": full_context.get("sentiment", {}).get("crowd_psychology_pattern", "UNCERTAINTY"),
                    "smart_money_divergence": full_context.get("sentiment", {}).get("smart_money_divergence", 0.0),
                    "is_available": full_context.get("sentiment", {}).get("is_available", False),
                },
                "base_rate": {
                    "question_type": full_context.get("base_rate", {}).get("question_type", "general"),
                    "divergence": full_context.get("base_rate", {}).get("divergence_from_market", 0.0),
                    "is_available": full_context.get("base_rate", {}).get("is_available", False),
                },
            }
        record_prediction(
            market_id=str(market.get("id", "")),
            signal=result["signal"],
            confidence=result["confidence"],
            edge=result["edge"],
            reasoning=result["reasoning"],
            context_snapshot=context_snapshot,
            prompt_version_id=prompt_version_id,
        )
    except Exception as e:
        logger.warning("Failed to record prediction for {}: {}", question[:50], e)

    return result
