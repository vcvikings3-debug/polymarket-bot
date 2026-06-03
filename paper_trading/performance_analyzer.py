"""Deep statistical analysis of paper trading results."""

import sys
import os
import json
from datetime import datetime, timezone
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import numpy as np
    _NUMPY = True
except ImportError:
    _NUMPY = False

try:
    from scipy import stats as scipy_stats
    _SCIPY = True
except ImportError:
    _SCIPY = False


def _safe_div(a, b, default=0.0):
    return a / b if b else default


def calculate_sharpe_ratio(paper_trades: list, risk_free_rate: float = 0.05) -> float:
    """
    Sharpe ratio = (mean return - risk_free_rate) / std_dev_returns.
    Returns per-trade Sharpe. >1.0 acceptable, >2.0 excellent.
    """
    if len(paper_trades) < 3:
        return 0.0
    returns = []
    for t in paper_trades:
        size = t.get("actual_size") or t.get("intended_size") or 0
        net_pnl = t.get("net_pnl") or 0
        if size > 0:
            returns.append(net_pnl / size)
    if not returns:
        return 0.0
    if _NUMPY:
        arr = np.array(returns)
        mean_r = float(np.mean(arr))
        std_r = float(np.std(arr))
    else:
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        std_r = var ** 0.5
    if std_r == 0:
        return 0.0
    # Annualize: approximate trades per year = 365 (daily), but per-trade is fine for our scale
    return round((mean_r - risk_free_rate / 365) / std_r, 4)


def calculate_max_drawdown(bankroll_history: list) -> dict:
    """
    Maximum peak-to-trough decline. Returns max_drawdown_pct and max_drawdown_dollars.
    """
    if len(bankroll_history) < 2:
        return {"max_drawdown_pct": 0.0, "max_drawdown_dollars": 0.0, "current_drawdown_pct": 0.0}
    amounts = [h["bankroll_amount"] for h in bankroll_history]
    peak = amounts[0]
    max_dd = 0.0
    max_dd_dollars = 0.0
    for val in amounts:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            max_dd_dollars = peak - val

    # Current drawdown from most recent peak
    current = amounts[-1]
    current_peak = max(amounts)
    current_dd = (current_peak - current) / current_peak if current_peak > 0 else 0

    return {
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_drawdown_dollars": round(max_dd_dollars, 4),
        "current_drawdown_pct": round(current_dd * 100, 2),
    }


def calculate_kelly_accuracy(paper_trades: list) -> dict:
    """
    Compare actual Kelly fraction used vs optimal given the realized outcome.
    Optimal Kelly = (p - (1-p)/b) where p=win_rate, b=odds.
    """
    closed = [t for t in paper_trades if t.get("was_correct") is not None]
    if len(closed) < 5:
        return {"over_kelly_rate": 0.0, "under_kelly_rate": 0.0, "avg_kelly_error": 0.0}

    wins = sum(1 for t in closed if t.get("was_correct"))
    p = wins / len(closed)
    errors = []
    over_count = 0
    under_count = 0
    for t in closed:
        used_kelly = t.get("intended_size", 0) / max(t.get("actual_position_cost", t.get("intended_size", 1) * 1.02), 0.01)
        price = max(t.get("actual_entry_price") or 0.5, 0.01)
        b = (1.0 / price) - 1  # odds (profit per dollar bet)
        if b > 0:
            optimal_kelly = max(0, (p - (1 - p) / b))
            error = abs(used_kelly - optimal_kelly)
            errors.append(error)
            if used_kelly > optimal_kelly + 0.01:
                over_count += 1
            elif used_kelly < optimal_kelly - 0.01:
                under_count += 1
    n = len(errors)
    return {
        "over_kelly_rate": round(_safe_div(over_count, n), 4),
        "under_kelly_rate": round(_safe_div(under_count, n), 4),
        "avg_kelly_error": round(_safe_div(sum(errors), n), 4),
    }


def calculate_edge_capture_rate(paper_trades: list) -> dict:
    """
    Predicted edge vs actually captured edge.
    Captured edge = net_pnl / actual_size (realized return ratio).
    """
    closed = [t for t in paper_trades if t.get("was_correct") is not None]
    if not closed:
        return {"avg_predicted_edge": 0.0, "avg_captured_edge": 0.0, "capture_rate_pct": 0.0}
    predicted = [float(t.get("edge") or 0) for t in closed]
    captured = [
        float(t.get("net_pnl") or 0) / max(float(t.get("actual_size") or 0.01), 0.01)
        for t in closed
    ]
    avg_pred = _safe_div(sum(predicted), len(predicted))
    avg_cap = _safe_div(sum(captured), len(captured))
    capture_rate = _safe_div(avg_cap, avg_pred) * 100 if avg_pred != 0 else 0
    return {
        "avg_predicted_edge": round(avg_pred, 4),
        "avg_captured_edge": round(avg_cap, 4),
        "capture_rate_pct": round(capture_rate, 1),
    }


def calculate_calibration_score(paper_trades: list) -> dict:
    """
    Compare confidence buckets to actual win rates.
    Returns calibration_error (mean absolute diff) and calibration_curve.
    """
    closed = [t for t in paper_trades if t.get("was_correct") is not None]
    buckets = {
        "0.5-0.6": {"min": 0.5, "max": 0.6, "wins": 0, "total": 0},
        "0.6-0.7": {"min": 0.6, "max": 0.7, "wins": 0, "total": 0},
        "0.7-0.8": {"min": 0.7, "max": 0.8, "wins": 0, "total": 0},
        "0.8-0.9": {"min": 0.8, "max": 0.9, "wins": 0, "total": 0},
        "0.9-1.0": {"min": 0.9, "max": 1.0, "wins": 0, "total": 0},
    }
    for t in closed:
        conf = float(t.get("confidence") or 0)
        correct = bool(t.get("was_correct"))
        for label, b in buckets.items():
            if b["min"] <= conf < b["max"] or (conf == 1.0 and b["max"] == 1.0):
                b["total"] += 1
                if correct:
                    b["wins"] += 1
                break

    errors = []
    curve = []
    for label, b in buckets.items():
        if b["total"] < 3:
            continue
        mid = (b["min"] + b["max"]) / 2
        actual_wr = b["wins"] / b["total"]
        errors.append(abs(actual_wr - mid))
        curve.append({
            "bucket": label,
            "predicted_confidence": round(mid, 2),
            "actual_win_rate": round(actual_wr, 4),
            "sample_size": b["total"],
            "calibration_gap": round(actual_wr - mid, 4),
        })

    cal_error = _safe_div(sum(errors), len(errors)) if errors else 0.0
    return {
        "calibration_error_pct": round(cal_error * 100, 2),
        "calibration_curve": curve,
        "is_overconfident": cal_error > 0.10 and (
            sum(c["calibration_gap"] for c in curve) / len(curve) < 0 if curve else False
        ),
    }


def calculate_signal_attribution(paper_trades: list) -> dict:
    """
    For correct vs incorrect predictions, which intelligence signals were present?
    Returns signal_importance_ranking.
    """
    signal_wins: dict = {}
    signal_total: dict = {}
    for t in paper_trades:
        if t.get("was_correct") is None:
            continue
        snap_raw = t.get("intelligence_signals_snapshot", "")
        if not snap_raw:
            continue
        try:
            snap = json.loads(snap_raw) if isinstance(snap_raw, str) else snap_raw
        except Exception:
            continue
        correct = bool(t.get("was_correct"))
        # Check which signals were available
        signal_checks = {
            "news_available":    snap.get("news", {}).get("is_available", False),
            "onchain_available": snap.get("onchain", {}).get("is_available", False),
            "sentiment_available": snap.get("sentiment", {}).get("is_available", False),
            "base_rate_available": snap.get("base_rate", {}).get("is_available", False),
            "high_composite":    float(snap.get("composite_signal", 0.5)) > 0.6,
            "strong_whale":      snap.get("onchain", {}).get("whale_direction") == "ACCUMULATING",
            "high_velocity_news": float(snap.get("news", {}).get("velocity_score", 0)) > 0.4,
        }
        for sig, present in signal_checks.items():
            if sig not in signal_total:
                signal_total[sig] = [0, 0]  # [total_with, wins_with]
            if present:
                signal_total[sig][0] += 1
                if correct:
                    signal_total[sig][1] += 1
    ranking = []
    for sig, (total, wins) in signal_total.items():
        if total >= 3:
            wr = wins / total
            ranking.append({"signal": sig, "win_rate_when_present": round(wr, 4), "count": total})
    ranking.sort(key=lambda x: x["win_rate_when_present"], reverse=True)
    return {r["signal"]: r for r in ranking}


def calculate_category_edge(paper_trades: list, min_samples: int = 5) -> dict:
    """Win rate and average PnL by market category."""
    from intelligence.historical_performance import _classify_market_category
    cats: dict = {}
    for t in paper_trades:
        if t.get("was_correct") is None:
            continue
        q = t.get("market_question", "")
        cat = _classify_market_category(q)
        if cat not in cats:
            cats[cat] = {"wins": 0, "total": 0, "pnl": 0.0}
        cats[cat]["total"] += 1
        if t.get("was_correct"):
            cats[cat]["wins"] += 1
        cats[cat]["pnl"] += float(t.get("net_pnl") or 0)
    result = {}
    for cat, data in cats.items():
        if data["total"] >= min_samples:
            result[cat] = {
                "win_rate": round(data["wins"] / data["total"], 4),
                "total_pnl": round(data["pnl"], 4),
                "avg_pnl_per_trade": round(data["pnl"] / data["total"], 4),
                "sample_size": data["total"],
                "recommendation": (
                    "AVOID" if data["wins"] / data["total"] < 0.52 and data["total"] >= 10
                    else "SPECIALIZE" if data["wins"] / data["total"] > 0.65 and data["total"] >= 10
                    else "NORMAL"
                ),
            }
    return result


def calculate_time_patterns(paper_trades: list) -> dict:
    """Accuracy by day of week and days-to-resolution buckets."""
    closed = [t for t in paper_trades if t.get("was_correct") is not None]
    if len(closed) < 5:
        return {"day_of_week": {}, "resolution_days_buckets": {}}

    day_stats: dict = {}
    res_buckets: dict = {"<7d": [0, 0], "7-14d": [0, 0], "14-30d": [0, 0], ">30d": [0, 0]}

    for t in closed:
        correct = bool(t.get("was_correct"))
        # Day of week from opened_at
        opened_str = t.get("opened_at", "")
        if opened_str:
            try:
                opened = datetime.fromisoformat(opened_str.replace("Z", "+00:00"))
                day_name = opened.strftime("%A")
                if day_name not in day_stats:
                    day_stats[day_name] = [0, 0]
                day_stats[day_name][0] += 1
                if correct:
                    day_stats[day_name][1] += 1
            except Exception:
                pass
        # Days to resolution from expected_resolution_date vs opened_at
        end_str = t.get("expected_resolution_date", "")
        if end_str and opened_str:
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                open_dt = datetime.fromisoformat(opened_str.replace("Z", "+00:00"))
                days = (end_dt - open_dt).days
                if days < 7:
                    bucket = "<7d"
                elif days < 14:
                    bucket = "7-14d"
                elif days < 30:
                    bucket = "14-30d"
                else:
                    bucket = ">30d"
                res_buckets[bucket][0] += 1
                if correct:
                    res_buckets[bucket][1] += 1
            except Exception:
                pass

    return {
        "day_of_week": {
            day: {"win_rate": round(_safe_div(wins, total), 4), "count": total}
            for day, (total, wins) in day_stats.items() if total >= 2
        },
        "resolution_days_buckets": {
            bucket: {"win_rate": round(_safe_div(wins, total), 4), "count": total}
            for bucket, (total, wins) in res_buckets.items() if total >= 2
        },
    }


def calculate_streaks(paper_trades: list) -> dict:
    """Longest winning streak, losing streak, and current streak."""
    closed = [t for t in paper_trades if t.get("was_correct") is not None]
    if not closed:
        return {
            "longest_win_streak": 0, "longest_loss_streak": 0,
            "current_streak": 0, "current_streak_type": "NONE",
        }
    sorted_trades = sorted(closed, key=lambda t: t.get("resolved_at") or t.get("opened_at") or "")
    results = [bool(t.get("was_correct")) for t in sorted_trades]

    max_win = max_loss = 0
    cur = 1
    for i in range(1, len(results)):
        if results[i] == results[i - 1]:
            cur += 1
        else:
            if results[i - 1]:
                max_win = max(max_win, cur)
            else:
                max_loss = max(max_loss, cur)
            cur = 1
    if results:
        if results[-1]:
            max_win = max(max_win, cur)
        else:
            max_loss = max(max_loss, cur)

    # Current streak
    current_val = results[-1] if results else None
    current_len = 0
    for r in reversed(results):
        if r == current_val:
            current_len += 1
        else:
            break

    return {
        "longest_win_streak": max_win,
        "longest_loss_streak": max_loss,
        "current_streak": current_len,
        "current_streak_type": "WIN" if current_val else "LOSS",
    }


def generate_full_performance_report(paper_trades: list, bankroll_history: list) -> dict:
    """Master function — assembles all metrics into one report dict."""
    closed = [t for t in paper_trades if t.get("was_correct") is not None]
    open_trades = [t for t in paper_trades if t.get("status") == "OPEN"]
    n_closed = len(closed)
    n_wins = sum(1 for t in closed if t.get("was_correct"))
    win_rate = _safe_div(n_wins, n_closed)
    total_pnl = sum(float(t.get("net_pnl") or 0) for t in closed)

    starting_bankroll = bankroll_history[0]["bankroll_amount"] if bankroll_history else 10.0
    current_bankroll = bankroll_history[-1]["bankroll_amount"] if bankroll_history else starting_bankroll
    pnl_pct = _safe_div(current_bankroll - starting_bankroll, starting_bankroll) * 100

    return {
        "total_trades": len(paper_trades),
        "open_trades": len(open_trades),
        "closed_trades": n_closed,
        "wins": n_wins,
        "losses": n_closed - n_wins,
        "win_rate": round(win_rate, 4),
        "total_net_pnl": round(total_pnl, 4),
        "starting_bankroll": starting_bankroll,
        "current_bankroll": round(current_bankroll, 4),
        "pnl_pct": round(pnl_pct, 2),
        "avg_edge": round(_safe_div(sum(float(t.get("edge") or 0) for t in closed), n_closed), 4),
        "avg_confidence": round(_safe_div(sum(float(t.get("confidence") or 0) for t in closed), n_closed), 4),
        "sharpe_ratio": calculate_sharpe_ratio(closed),
        "drawdown": calculate_max_drawdown(bankroll_history),
        "kelly_accuracy": calculate_kelly_accuracy(closed),
        "edge_capture": calculate_edge_capture_rate(closed),
        "calibration": calculate_calibration_score(closed),
        "signal_attribution": calculate_signal_attribution(closed),
        "category_edge": calculate_category_edge(closed),
        "time_patterns": calculate_time_patterns(closed),
        "streaks": calculate_streaks(closed),
        # Go-live criteria
        "criteria": {
            "c1_trades_met": n_closed >= 50,
            "c2_pnl_positive": total_pnl > 0,
            "c3_win_rate_met": win_rate >= 0.52,
            "all_met": n_closed >= 50 and total_pnl > 0 and win_rate >= 0.52,
            "trades_remaining": max(0, 50 - n_closed),
        },
    }
