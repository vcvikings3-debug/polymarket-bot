"""Scores Polymarket markets by identifying mispriced odds vs real probability using local LLM analysis."""

import json
import requests
from datetime import datetime, timezone
from loguru import logger

# LM Studio endpoint (OpenAI-compatible) — config-driven
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LM_STUDIO_HOST, OLLAMA_MODEL
LM_STUDIO_URL = f"{LM_STUDIO_HOST}/v1/chat/completions"
LM_MODEL = OLLAMA_MODEL
LLM_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "llm_calls.log")

# Configure LLM logger
os.makedirs(os.path.dirname(LLM_LOG_PATH), exist_ok=True)
llm_logger = logger.bind(name="llm")
llm_logger.add(LLM_LOG_PATH, rotation="10 MB", format="{time} | {message}")


def _days_until(end_date_str: str) -> int:
    """Calculate days until end date."""
    if not end_date_str:
        return 0
    try:
        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days = (end_date - now).days
        return max(days, 0)
    except (ValueError, TypeError):
        return 0


SYSTEM_PROMPT = (
    "You are a prediction market analyst specializing in crypto markets on Polymarket. "
    "Your job is to identify markets where the current odds are mispriced relative to the "
    "true probability of the outcome. You are analytical, data-driven, and concise. "
    "Never recommend betting more than the user can afford to lose."
)


def analyze_market_with_llm(market: dict) -> dict:
    """Send a market to LM Studio and return the LLM's analysis.

    Returns dict with keys: signal, confidence, reasoning, edge
    """
    question = market.get("question", "Unknown")
    yes_price = float(market.get("yes_price", 0.5))
    no_price = float(market.get("no_price", 0.5))
    liquidity = float(market.get("liquidity", 0))
    days_left = _days_until(market.get("end_date", ""))
    yes_pct = round(yes_price * 100, 1)
    no_pct = round(no_price * 100, 1)

    user_prompt = (
        f"Analyze this Polymarket prediction market and give me a betting signal.\n\n"
        f"Market: {question}\n"
        f"Current YES price: {yes_price} (implies {yes_pct}% probability)\n"
        f"Current NO price: {no_price} (implies {no_pct}% probability)\n"
        f"Liquidity: ${liquidity:,.2f}\n"
        f"Days until resolution: {days_left}\n\n"
        f"Based on your knowledge of crypto markets and current conditions, is this market mispriced?\n"
        f"Respond with ONLY valid JSON using double quotes, no other text:\n"
        f"{{\n"
        f'  "signal": "BET_YES" or "BET_NO" or "SKIP",\n'
        f'  "confidence": 0.0 to 1.0,\n'
        f'  "reasoning": "one sentence max",\n'
        f'  "edge": "estimated edge as decimal e.g. 0.08 means 8% edge"\n'
        f"}}"
    )

    payload = {
        "model": LM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 256,
    }

    logger.info("LLM analyzing market: {} (YES={}, NO={})", question[:50], yes_price, no_price)
    llm_logger.info("REQUEST market={} yes_price={} no_price={}", question[:50], yes_price, no_price)

    try:
        resp = requests.post(LM_STUDIO_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # Extract response text
        content = data["choices"][0]["message"]["content"].strip()
        llm_logger.info("RESPONSE market={} raw={}", question[:50], content)

        # Parse JSON from the response
        # Try to find JSON block in the response
        parsed = None
        try:
            # Extract JSON block from the content
            cleaned = content.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                cleaned = "\n".join(line for line in lines if not line.startswith("```"))

            # Find the JSON object boundaries
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = cleaned[start:end]
                # Try json.loads (works with double-quoted JSON)
                try:
                    parsed = json.loads(json_str)
                except json.JSONDecodeError:
                    # Fallback: replace single quotes with double quotes for Python-style dicts
                    try:
                        import ast
                        parsed = ast.literal_eval(json_str)
                    except (ValueError, SyntaxError):
                        pass
        except Exception:
            pass

        if parsed and isinstance(parsed, dict):
            signal = parsed.get("signal", "SKIP")
            confidence = float(parsed.get("confidence", 0))
            reasoning = str(parsed.get("reasoning", ""))[:200]
            edge_raw = parsed.get("edge", 0)
            # Parse edge — LLM may return a decimal (0.08) or a percentage ("8%")
            if isinstance(edge_raw, str):
                try:
                    stripped = edge_raw.strip()
                    if "%" in stripped:
                        edge = float(stripped.replace("%", "").strip()) / 100.0
                    else:
                        edge = float(stripped)
                except (ValueError, TypeError):
                    edge = 0.0
            else:
                edge = float(edge_raw) if edge_raw else 0.0

            # Validate signal
            if signal not in ("BET_YES", "BET_NO", "SKIP"):
                signal = "SKIP"

            result = {
                "signal": signal,
                "confidence": min(max(confidence, 0.0), 1.0),
                "reasoning": reasoning,
                "edge": edge,
            }
            logger.info("LLM result: {} confidence={:.2f} reasoning={}", signal, confidence, reasoning)
            llm_logger.info("RESULT market={} signal={} conf={} edge={} reasoning={}",
                          question[:50], signal, confidence, edge, reasoning)
            return result

        logger.warning("LLM returned unparseable JSON for market: {}", question[:50])
        llm_logger.warning("PARSE_FAIL market={} raw_response={}", question[:50], content[:300])

    except requests.exceptions.ConnectionError as e:
        logger.error("LLM connection error — LM Studio unreachable at {}: {}", LM_STUDIO_URL, e)
        llm_logger.error("CONNECTION_ERROR url={} error={}", LM_STUDIO_URL, e)
    except requests.exceptions.Timeout:
        logger.warning("LLM timeout after 30s for market: {}", question[:50])
        llm_logger.warning("TIMEOUT market={}", question[:50])
    except requests.exceptions.RequestException as e:
        logger.error("LLM request error: {}", e)
        llm_logger.error("REQUEST_ERROR market={} error={}", question[:50], e)
    except Exception as e:
        logger.error("Unexpected LLM error for market {}: {}", question[:50], e)
        llm_logger.error("UNEXPECTED_ERROR market={} error={}", question[:50], e)

    return {"signal": "SKIP", "confidence": 0.0, "reasoning": "LLM unavailable", "edge": 0.0}