"""Autonomous prompt versioning, A/B testing, and weekly strategy evolution."""

import sys
import os
import json
import requests
from datetime import datetime, timezone, timedelta
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import LM_STUDIO_HOST, OLLAMA_MODEL

# The canonical base prompt — saved as version 1 on first run
BASE_SYSTEM_PROMPT = (
    "You are a prediction market analyst specializing in crypto markets on Polymarket. "
    "Your job is to identify markets where the current odds are mispriced relative to the "
    "true probability of the outcome. You are analytical, data-driven, and concise. "
    "Weigh all available intelligence signals. Never recommend betting more than the user can afford to lose."
)


def get_current_prompt_version() -> dict:
    """Return the active prompt version dict, or seed version 1 if none exists."""
    from data.database import get_active_prompt_version, save_prompt_version, set_active_prompt
    active = get_active_prompt_version()
    if active:
        return active
    # First run — seed the base prompt as version 1
    logger.info("prompt_evolution: seeding base prompt as version 1")
    new_id = save_prompt_version(
        version_number=1,
        prompt_text=BASE_SYSTEM_PROMPT,
        evolution_notes="Initial base prompt",
    )
    if new_id:
        set_active_prompt(new_id)
        return {"id": new_id, "version_number": 1, "prompt_text": BASE_SYSTEM_PROMPT, "is_active": 1}
    return {"id": 0, "version_number": 1, "prompt_text": BASE_SYSTEM_PROMPT, "is_active": 1}


def evaluate_prompt_performance(version_id: int, min_samples: int = 10) -> dict:
    """Calculate win rate, avg edge, and calibration for a specific prompt version."""
    from data.database import get_resolved_predictions
    predictions = get_resolved_predictions(limit=1000)
    version_preds = [p for p in predictions if p.get("prompt_version_id") == version_id]

    if len(version_preds) < min_samples:
        return {
            "version_id": version_id,
            "sample_size": len(version_preds),
            "win_rate": None,
            "avg_edge": None,
            "calibration_score": None,
            "sufficient_data": False,
        }

    wins = sum(1 for p in version_preds if p.get("was_correct"))
    win_rate = wins / len(version_preds)
    avg_edge = sum(float(p.get("edge") or 0) for p in version_preds) / len(version_preds)

    # Calibration: Brier score (lower = better)
    brier = sum(
        (float(p.get("confidence") or 0.5) - float(bool(p.get("was_correct")))) ** 2
        for p in version_preds
    ) / len(version_preds)
    calibration_score = round(1 - brier, 4)  # 1 = perfect, 0 = worst

    return {
        "version_id": version_id,
        "sample_size": len(version_preds),
        "win_rate": round(win_rate, 4),
        "avg_edge": round(avg_edge, 4),
        "calibration_score": calibration_score,
        "sufficient_data": True,
    }


def generate_evolved_prompt(current_prompt: str, performance_data: dict, bias_data: list) -> str:
    """Send current prompt + performance data to LLM and get an improved version."""
    win_rate = performance_data.get("win_rate") or 0.0
    sample_size = performance_data.get("sample_size") or 0
    calibration = performance_data.get("calibration_score") or 0.5
    bias_names = [b.get("bias_type", "") for b in bias_data if isinstance(b, dict)]

    meta_prompt = (
        f"You are an expert prompt engineer for a prediction market betting system.\n\n"
        f"CURRENT PROMPT:\n{current_prompt}\n\n"
        f"PERFORMANCE STATISTICS:\n"
        f"- Win rate: {win_rate:.0%} over {sample_size} resolved predictions\n"
        f"- Calibration score: {calibration:.2f} (1.0 = perfect)\n"
        f"- Active biases detected: {', '.join(bias_names) or 'none'}\n\n"
        f"Generate an improved version of this prompt that:\n"
        f"1. Addresses the detected biases explicitly\n"
        f"2. Adds calibration guidance if needed\n"
        f"3. Emphasizes analytical discipline over narrative following\n"
        f"4. Remains concise and actionable\n\n"
        f"Return ONLY the improved prompt text, nothing else."
    )

    try:
        url = f"{LM_STUDIO_HOST}/v1/chat/completions"
        resp = requests.post(
            url,
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": meta_prompt}],
                "temperature": 0.35,
                "max_tokens": 600,
            },
            timeout=60,
        )
        resp.raise_for_status()
        evolved = resp.json()["choices"][0]["message"]["content"].strip()
        if len(evolved) < 80:
            logger.warning("prompt_evolution: LLM returned short evolved prompt — keeping current")
            return current_prompt
        return evolved
    except Exception as e:
        logger.error("prompt_evolution: generate_evolved_prompt LLM call failed — {}", e)
        return current_prompt


def run_ab_test(prompt_a_id: int, prompt_b_id: int, allocation: float = 0.5) -> dict:
    """
    Route a fraction of markets to prompt B. After 20+ samples per version,
    promote B if it's significantly better (90% confidence).
    Returns the id of the currently preferred prompt.
    """
    from data.database import (
        update_prompt_performance, set_active_prompt, get_strategy_state, set_strategy_state,
    )
    try:
        from scipy import stats as scipy_stats
        _SCIPY = True
    except ImportError:
        _SCIPY = False

    perf_a = evaluate_prompt_performance(prompt_a_id, min_samples=20)
    perf_b = evaluate_prompt_performance(prompt_b_id, min_samples=20)

    if perf_a.get("win_rate") is not None:
        update_prompt_performance(
            prompt_a_id, perf_a["win_rate"], perf_a["avg_edge"],
            perf_a["calibration_score"], perf_a["sample_size"],
        )
    if perf_b.get("win_rate") is not None:
        update_prompt_performance(
            prompt_b_id, perf_b["win_rate"], perf_b["avg_edge"],
            perf_b["calibration_score"], perf_b["sample_size"],
        )

    if not perf_a.get("sufficient_data") or not perf_b.get("sufficient_data"):
        logger.debug("prompt_evolution: A/B test — insufficient data, keeping A active")
        return {"preferred_id": prompt_a_id, "promoted": False, "reason": "insufficient data"}

    win_rate_a = perf_a["win_rate"]
    win_rate_b = perf_b["win_rate"]

    if win_rate_b > win_rate_a + 0.05:
        if _SCIPY:
            # Two-proportion z-test
            n_a = perf_a["sample_size"]
            n_b = perf_b["sample_size"]
            wins_a = int(win_rate_a * n_a)
            wins_b = int(win_rate_b * n_b)
            p_pool = (wins_a + wins_b) / (n_a + n_b)
            if p_pool > 0 and p_pool < 1:
                z = (win_rate_b - win_rate_a) / ((p_pool * (1 - p_pool) * (1/n_a + 1/n_b)) ** 0.5)
                p_val = 1 - scipy_stats.norm.cdf(z)
                if p_val < 0.10:  # 90% confidence
                    set_active_prompt(prompt_b_id)
                    logger.info(
                        "prompt_evolution: promoting prompt B (v{}) — {:.0%} vs {:.0%} (p={:.3f})",
                        prompt_b_id, win_rate_b, win_rate_a, p_val,
                    )
                    return {"preferred_id": prompt_b_id, "promoted": True,
                            "reason": f"B wins {win_rate_b:.0%} vs A {win_rate_a:.0%}, p={p_val:.3f}"}
        else:
            # No scipy — simple threshold check
            if win_rate_b > win_rate_a + 0.08 and perf_b["sample_size"] >= 30:
                set_active_prompt(prompt_b_id)
                logger.info("prompt_evolution: promoting prompt B (no scipy) — {:.0%} vs {:.0%}", win_rate_b, win_rate_a)
                return {"preferred_id": prompt_b_id, "promoted": True,
                        "reason": f"B clearly outperforms A: {win_rate_b:.0%} vs {win_rate_a:.0%}"}

    logger.debug("prompt_evolution: A/B test — A still preferred ({:.0%} vs {:.0%})", win_rate_a, win_rate_b)
    return {"preferred_id": prompt_a_id, "promoted": False,
            "reason": f"A ({win_rate_a:.0%}) not beaten by B ({win_rate_b:.0%})"}


def weekly_evolution_cycle() -> dict:
    """
    Master weekly function (runs Sunday 03:00).
    Evaluates current prompt → detects biases → generates evolved prompt → starts A/B test.
    """
    from data.database import save_prompt_version, get_strategy_state, set_strategy_state
    from intelligence.historical_performance import detect_own_biases

    logger.info("prompt_evolution: starting weekly evolution cycle")

    try:
        current = get_current_prompt_version()
        current_id = current.get("id", 0)
        current_version_num = current.get("version_number", 1)

        perf = evaluate_prompt_performance(current_id, min_samples=10)
        biases = detect_own_biases()

        if not perf.get("sufficient_data"):
            logger.info(
                "prompt_evolution: insufficient data for evolution ({} predictions) — skipping",
                perf.get("sample_size", 0),
            )
            return {"evolved": False, "reason": "insufficient_data"}

        evolved_text = generate_evolved_prompt(
            current_prompt=current.get("prompt_text", BASE_SYSTEM_PROMPT),
            performance_data=perf,
            bias_data=biases,
        )

        if evolved_text == current.get("prompt_text"):
            logger.info("prompt_evolution: evolution produced no change — current prompt retained")
            return {"evolved": False, "reason": "no_change"}

        new_version_num = current_version_num + 1
        new_id = save_prompt_version(
            version_number=new_version_num,
            prompt_text=evolved_text,
            evolution_notes=(
                f"Auto-evolved from v{current_version_num}. "
                f"Baseline win rate: {perf.get('win_rate', 0):.0%}. "
                f"Biases: {[b.get('bias_type') for b in biases]}."
            ),
            parent_version_id=current_id,
        )

        if not new_id:
            logger.error("prompt_evolution: failed to save evolved prompt")
            return {"evolved": False, "reason": "save_failed"}

        ab_result = run_ab_test(current_id, new_id)

        logger.info(
            "prompt_evolution: weekly cycle complete — v{} created, A/B test: {}",
            new_version_num,
            "PROMOTED" if ab_result.get("promoted") else "TESTING",
        )

        return {
            "evolved": True,
            "old_version": current_version_num,
            "new_version": new_version_num,
            "new_id": new_id,
            "ab_result": ab_result,
            "biases_addressed": [b.get("bias_type") for b in biases],
        }

    except Exception as e:
        logger.error("prompt_evolution: weekly_evolution_cycle failed — {}", e)
        return {"evolved": False, "reason": str(e)}
