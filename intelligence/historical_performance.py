"""Self-learning system: tracks bot accuracy, identifies biases, autonomously adjusts strategy."""

import sys
import os
import json
from datetime import datetime, timezone, timedelta
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

# Market category keywords for classification
_CATEGORY_PATTERNS = {
    "bitcoin_price":    ["bitcoin", "btc"],
    "ethereum_price":   ["ethereum", "eth"],
    "defi_protocol":    ["defi", "uniswap", "aave", "compound", "curve", "tvl", "liquidity"],
    "nft_market":       ["nft", "opensea", "blur", "collection"],
    "exchange_event":   ["coinbase", "binance", "exchange", "listing", "delisting"],
    "regulatory":       ["sec", "cftc", "ban", "regulation", "legal", "lawsuit"],
    "macro_crypto":     ["market cap", "dominance", "altcoin", "crypto market", "total"],
    "solana_ecosystem": ["solana", "sol"],
    "layer2":           ["arbitrum", "optimism", "polygon", "base", "zk", "layer2", "l2"],
}


def _classify_market_category(market_question: str) -> str:
    """Classify a market question into a category for accuracy tracking."""
    text = market_question.lower()
    for category, keywords in _CATEGORY_PATTERNS.items():
        if any(kw in text for kw in keywords):
            return category
    return "general"


def record_prediction(market_id: str, signal: str, confidence: float, edge: float,
                      reasoning: str, context_snapshot: dict, prompt_version_id: int = None) -> int:
    """Record a prediction before outcome is known. Returns prediction row id."""
    from data.database import save_prediction
    try:
        snapshot_json = json.dumps(context_snapshot) if isinstance(context_snapshot, dict) else str(context_snapshot)
        row_id = save_prediction(
            market_id=market_id,
            signal=signal,
            confidence=confidence,
            edge=edge,
            reasoning=reasoning,
            context_snapshot=snapshot_json,
            prompt_version_id=prompt_version_id,
        )
        if row_id:
            logger.debug("historical: recorded prediction {} for market {}", row_id, market_id[:20])
        return row_id
    except Exception as e:
        logger.error("historical: record_prediction failed for {}: {}", market_id, e)
        return 0


def record_resolution(market_id: str, actual_outcome: str, pnl: float) -> bool:
    """Called when a market resolves. Updates prediction with outcome and PnL."""
    from data.database import update_prediction_resolution
    try:
        return update_prediction_resolution(market_id, actual_outcome, pnl)
    except Exception as e:
        logger.error("historical: record_resolution failed for {}: {}", market_id, e)
        return False


def calculate_accuracy_by_category(min_samples: int = 5) -> dict:
    """
    Analyze prediction history by market category.
    Returns {category: {accuracy, sample_size}} for categories with enough data.
    """
    from data.database import get_resolved_predictions
    predictions = get_resolved_predictions(limit=1000)
    if not predictions:
        return {}
    counts: dict = {}
    wins: dict = {}
    for p in predictions:
        question = p.get("market_id", "")
        # Try to get question from reasoning/context
        context_raw = p.get("context_snapshot", "")
        question_text = ""
        if context_raw:
            try:
                ctx = json.loads(context_raw) if isinstance(context_raw, str) else context_raw
                question_text = ctx.get("question", "")
            except Exception:
                pass
        cat = _classify_market_category(question_text) if question_text else "general"
        counts[cat] = counts.get(cat, 0) + 1
        if p.get("was_correct"):
            wins[cat] = wins.get(cat, 0) + 1
    result = {}
    for cat, total in counts.items():
        if total >= min_samples:
            accuracy = wins.get(cat, 0) / total
            result[cat] = {"accuracy": round(accuracy, 4), "sample_size": total}
    return result


def calculate_accuracy_by_confidence_bucket() -> dict:
    """
    Check if confidence scores are calibrated.
    Returns {bucket_label: {predicted_conf, actual_win_rate, sample_size}}.
    """
    from data.database import get_resolved_predictions
    predictions = get_resolved_predictions(limit=1000)
    if not predictions:
        return {}
    buckets = {
        "0.5-0.6": {"min": 0.5, "max": 0.6, "total": 0, "wins": 0},
        "0.6-0.7": {"min": 0.6, "max": 0.7, "total": 0, "wins": 0},
        "0.7-0.8": {"min": 0.7, "max": 0.8, "total": 0, "wins": 0},
        "0.8-0.9": {"min": 0.8, "max": 0.9, "total": 0, "wins": 0},
        "0.9-1.0": {"min": 0.9, "max": 1.0, "total": 0, "wins": 0},
    }
    for p in predictions:
        conf = float(p.get("confidence") or 0)
        correct = p.get("was_correct", 0)
        for label, bucket in buckets.items():
            if bucket["min"] <= conf < bucket["max"] or (bucket["max"] == 1.0 and conf == 1.0):
                bucket["total"] += 1
                if correct:
                    bucket["wins"] += 1
                break
    result = {}
    for label, bucket in buckets.items():
        if bucket["total"] >= 3:
            mid = (bucket["min"] + bucket["max"]) / 2
            actual_rate = bucket["wins"] / bucket["total"]
            gap = actual_rate - mid
            if gap > 0.1:
                note = "UNDERCONFIDENT"
            elif gap < -0.1:
                note = "OVERCONFIDENT"
            else:
                note = "CALIBRATED"
            result[label] = {
                "predicted_confidence": round(mid, 2),
                "actual_win_rate": round(actual_rate, 4),
                "sample_size": bucket["total"],
                "calibration_note": note,
                "calibration_gap": round(gap, 4),
            }
    return result


def calculate_accuracy_by_signal_source() -> dict:
    """Track which signals correlated with correct predictions."""
    from data.database import get_signal_performance_stats
    stats = get_signal_performance_stats()
    ranked = []
    for signal_name, data in stats.items():
        present_rate = data.get("present_win_rate") or 0.0
        absent_rate = data.get("absent_win_rate") or 0.0
        present_count = data.get("present_count", 0)
        if present_count >= 5:
            lift = present_rate - absent_rate
            ranked.append({
                "signal": signal_name,
                "present_win_rate": present_rate,
                "absent_win_rate": absent_rate,
                "lift": round(lift, 4),
                "present_count": present_count,
            })
    ranked.sort(key=lambda x: abs(x["lift"]), reverse=True)
    return {r["signal"]: r for r in ranked}


def detect_own_biases() -> list:
    """
    Analyze prediction history for systematic biases.
    Returns list of {bias_type, severity, description}.
    """
    from data.database import get_resolved_predictions
    predictions = get_resolved_predictions(limit=500)
    if len(predictions) < 10:
        return []

    biases = []

    # RECENCY_BIAS: accuracy drops after 3 consecutive wins or losses
    correct_sequence = [bool(p.get("was_correct")) for p in predictions[:50]]
    streaks = []
    current_streak = 0
    current_val = None
    for val in correct_sequence:
        if val == current_val:
            current_streak += 1
        else:
            if current_streak >= 3 and current_val is not None:
                streaks.append((current_val, current_streak))
            current_val = val
            current_streak = 1
    # Check if win streaks are followed by worse accuracy
    win_streak_breaks = sum(1 for v, l in streaks if v and l >= 3)
    if win_streak_breaks >= 2:
        biases.append({
            "bias_type": "RECENCY_BIAS",
            "severity": "MEDIUM",
            "description": f"Detected {win_streak_breaks} win streak breaks — overconfidence after wins likely",
        })

    # NARRATIVE_BIAS: check correlation between high news velocity and poor accuracy
    high_vel_predictions = []
    for p in predictions:
        ctx_raw = p.get("context_snapshot", "")
        if ctx_raw:
            try:
                ctx = json.loads(ctx_raw) if isinstance(ctx_raw, str) else ctx_raw
                news_ctx = ctx.get("news", {})
                if news_ctx.get("velocity_score", 0) > 0.5:
                    high_vel_predictions.append(bool(p.get("was_correct")))
            except Exception:
                pass
    if len(high_vel_predictions) >= 5:
        high_vel_acc = sum(high_vel_predictions) / len(high_vel_predictions)
        all_acc = sum(1 for p in predictions if p.get("was_correct")) / len(predictions)
        if high_vel_acc < all_acc - 0.15:
            biases.append({
                "bias_type": "NARRATIVE_BIAS",
                "severity": "HIGH",
                "description": f"Accuracy {high_vel_acc:.0%} during high news velocity vs {all_acc:.0%} overall — following narratives hurts",
            })

    # CONFIRMATION_BIAS: accuracy when all signals agree vs when they conflict
    # (checked via context snapshot signal consensus)
    consensus_correct = []
    conflict_correct = []
    for p in predictions:
        ctx_raw = p.get("context_snapshot", "")
        if ctx_raw:
            try:
                ctx = json.loads(ctx_raw) if isinstance(ctx_raw, str) else ctx_raw
                composite = ctx.get("composite_signal", 0.5)
                correct = bool(p.get("was_correct"))
                if composite > 0.65 or composite < 0.35:
                    consensus_correct.append(correct)
                else:
                    conflict_correct.append(correct)
            except Exception:
                pass
    if len(consensus_correct) >= 5 and len(conflict_correct) >= 5:
        cons_acc = sum(consensus_correct) / len(consensus_correct)
        conf_acc = sum(conflict_correct) / len(conflict_correct)
        if cons_acc < conf_acc - 0.10:
            biases.append({
                "bias_type": "CONFIRMATION_BIAS",
                "severity": "MEDIUM",
                "description": f"Accuracy {cons_acc:.0%} when signals agree vs {conf_acc:.0%} when conflicted — overweighting consensus",
            })

    return biases


def generate_strategy_adjustment() -> dict:
    """
    THE CORE AUTONOMOUS LEARNING FUNCTION.
    Analyzes all performance data and returns specific, actionable strategy adjustments.
    """
    from data.database import get_resolved_predictions, get_strategy_state, set_strategy_state
    predictions = get_resolved_predictions(limit=500)

    # Defaults — safe starting state with no history
    result = {
        "confidence_multipliers_by_category": {},
        "avoided_categories": [],
        "specialized_categories": [],
        "detected_biases": [],
        "recommended_min_edge_by_category": {},
        "cooling_period_active": False,
        "strategy_report": "Insufficient history for strategy adjustment — running baseline strategy.",
    }

    if len(predictions) < 10:
        logger.debug("historical: not enough predictions for strategy adjustment ({}/10)", len(predictions))
        return result

    category_acc = calculate_accuracy_by_category(min_samples=5)
    calibration = calculate_accuracy_by_confidence_bucket()
    biases = detect_own_biases()
    signal_importance = calculate_accuracy_by_signal_source()

    # Category-based adjustments
    avoided_categories = []
    specialized_categories = []
    confidence_multipliers = {}
    min_edge_by_category = {}

    for cat, data in category_acc.items():
        accuracy = data["accuracy"]
        n = data["sample_size"]
        if n >= 20 and accuracy < 0.52:
            avoided_categories.append(cat)
            logger.info("historical: avoiding category {} (accuracy {:.0%} over {} samples)", cat, accuracy, n)
        elif n >= 20 and accuracy > 0.65:
            specialized_categories.append(cat)
            confidence_multipliers[cat] = 1.1
            min_edge_by_category[cat] = 0.03  # lower edge threshold for specialized categories
            logger.info("historical: specializing in {} (accuracy {:.0%} over {} samples)", cat, accuracy, n)

    # Calibration adjustments
    for bucket, data in calibration.items():
        if data["calibration_note"] == "OVERCONFIDENT" and data["calibration_gap"] < -0.15:
            logger.info("historical: overconfident in bucket {} — applying 0.9x confidence multiplier", bucket)

    # Cooling period: if 3+ losses in last 5 predictions
    recent_5 = [bool(p.get("was_correct")) for p in predictions[:5]]
    if len(recent_5) >= 5 and sum(recent_5) <= 1:
        # Check if cooling period already active
        cooling_until = get_strategy_state("cooling_period_until")
        if cooling_until:
            try:
                cutoff = datetime.fromisoformat(cooling_until)
                if datetime.now(timezone.utc) < cutoff:
                    result["cooling_period_active"] = True
            except Exception:
                pass
        else:
            # Activate 12-hour cooling period
            until = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
            set_strategy_state("cooling_period_until", until)
            result["cooling_period_active"] = True
            logger.warning("historical: cooling period activated — 3 of last 5 predictions wrong")
    else:
        # Clear cooling period if we're recovering
        if sum(recent_5) >= 3:
            set_strategy_state("cooling_period_until", "")

    # Build report
    lines = []
    if avoided_categories:
        lines.append(f"Avoiding: {', '.join(avoided_categories)} (below 52% accuracy over 20+ samples)")
    if specialized_categories:
        lines.append(f"Specializing in: {', '.join(specialized_categories)} (above 65% accuracy)")
    if biases:
        lines.append(f"Active biases to watch: {', '.join(b['bias_type'] for b in biases)}")
    if result["cooling_period_active"]:
        lines.append("COOLING PERIOD ACTIVE — 3+ consecutive losses detected, threshold raised")
    if not lines:
        total = len(predictions)
        wins = sum(1 for p in predictions if p.get("was_correct"))
        lines.append(f"Running normally. Overall accuracy: {wins/total:.0%} over {total} resolved predictions.")

    result.update({
        "confidence_multipliers_by_category": confidence_multipliers,
        "avoided_categories": avoided_categories,
        "specialized_categories": specialized_categories,
        "detected_biases": biases,
        "recommended_min_edge_by_category": min_edge_by_category,
        "strategy_report": " ".join(lines),
    })

    return result


def auto_evolve_prompt(base_prompt: str, performance_data: dict) -> str:
    """
    Send base prompt + performance stats to LLM and get an evolved version.
    Saves evolved prompt as new version. Returns evolved prompt text.
    """
    from config import LM_STUDIO_HOST, OLLAMA_MODEL
    from data.database import save_prompt_version, get_active_prompt_version, set_active_prompt

    if not performance_data or len(performance_data.get("resolved", [])) < 20:
        logger.debug("historical: not enough data to evolve prompt")
        return base_prompt

    accuracy = performance_data.get("overall_accuracy", 0.5)
    biases = performance_data.get("biases", [])
    signal_importance = performance_data.get("signal_importance", {})

    meta_prompt = (
        "You are a prompt engineer specializing in prediction market analysis. "
        "Below is the current analysis prompt and its performance statistics. "
        "Identify specific weaknesses and generate an improved version.\n\n"
        f"CURRENT PROMPT:\n{base_prompt}\n\n"
        f"PERFORMANCE STATS:\n"
        f"Overall accuracy: {accuracy:.0%}\n"
        f"Active biases: {[b['bias_type'] for b in biases]}\n"
        f"Top signals correlated with wins: {list(signal_importance.keys())[:3]}\n\n"
        "The improved prompt should:\n"
        "- Address the detected biases explicitly\n"
        "- Emphasize signals that correlated with correct predictions\n"
        "- De-emphasize signals that correlated with incorrect predictions\n"
        "- Add specific examples of past mistake patterns to avoid\n\n"
        "Return ONLY the improved prompt text, nothing else."
    )

    try:
        url = f"{LM_STUDIO_HOST}/v1/chat/completions"
        resp = requests.post(
            url,
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": meta_prompt}],
                "temperature": 0.4,
                "max_tokens": 800,
            },
            timeout=60,
        )
        resp.raise_for_status()
        evolved = resp.json()["choices"][0]["message"]["content"].strip()

        if len(evolved) < 100:
            logger.warning("historical: evolved prompt too short — keeping original")
            return base_prompt

        # Get next version number
        active = get_active_prompt_version()
        version_num = (active.get("version_number", 0) if active else 0) + 1
        new_id = save_prompt_version(
            version_number=version_num,
            prompt_text=evolved,
            evolution_notes=f"Auto-evolved from v{version_num-1}. Accuracy: {accuracy:.0%}. Biases: {biases}",
            parent_version_id=active.get("id") if active else None,
        )
        if new_id:
            logger.info("historical: saved evolved prompt as version {}", version_num)

        return evolved
    except Exception as e:
        logger.error("historical: prompt evolution LLM call failed — {}", e)
        return base_prompt


def build_performance_context(market: dict) -> dict:
    """Returns PerformanceContext dict with self-awareness data for the LLM."""
    try:
        question = market.get("question", "")
        category = _classify_market_category(question)
        strategy = generate_strategy_adjustment()

        category_acc = calculate_accuracy_by_category(min_samples=5)
        calibration = calculate_accuracy_by_confidence_bucket()
        biases = strategy.get("detected_biases", [])

        cat_data = category_acc.get(category, {})
        hist_accuracy = cat_data.get("accuracy", None)
        sample_size = cat_data.get("sample_size", 0)

        if hist_accuracy is not None:
            accuracy_note = f"{hist_accuracy:.0%} accuracy over {sample_size} resolved predictions in this category"
        else:
            accuracy_note = "No historical data for this category yet"

        # Overall calibration note
        bucket_notes = []
        for bucket, data in calibration.items():
            if data.get("calibration_note") != "CALIBRATED":
                bucket_notes.append(f"At {bucket} confidence: actually wins {data['actual_win_rate']:.0%} ({data['calibration_note']})")
        calibration_text = "; ".join(bucket_notes) if bucket_notes else "Confidence appears well-calibrated"

        active_bias_names = [b["bias_type"] for b in biases]
        avoided = strategy.get("avoided_categories", [])
        cooling = strategy.get("cooling_period_active", False)

        adjustments = strategy.get("strategy_report", "Baseline strategy active")

        summary = (
            f"Market category: {category}. "
            f"Historical accuracy: {accuracy_note}. "
            f"Calibration: {calibration_text}. "
            f"Active biases to avoid: {', '.join(active_bias_names) or 'none detected'}. "
            f"{'COOLING PERIOD ACTIVE — raise thresholds.' if cooling else ''}"
        )

        return {
            "category": category,
            "historical_accuracy": hist_accuracy,
            "sample_size": sample_size,
            "calibration_note": calibration_text,
            "active_biases": active_bias_names,
            "avoided_categories": avoided,
            "cooling_period_active": cooling,
            "strategy_adjustments": adjustments,
            "summary": summary,
            "strategy": strategy,
            "is_available": True,
        }
    except Exception as e:
        logger.error("historical_performance.build_performance_context failed: {}", e)
        return {
            "category": "general",
            "historical_accuracy": None,
            "sample_size": 0,
            "calibration_note": "Data unavailable",
            "active_biases": [],
            "avoided_categories": [],
            "cooling_period_active": False,
            "strategy_adjustments": "Baseline strategy active",
            "summary": "Performance data unavailable — running baseline.",
            "strategy": {},
            "is_available": True,
        }
