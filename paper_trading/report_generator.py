"""Formats paper trading performance data into Discord reports."""

import sys
import os
from datetime import datetime, timezone
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.monitoring import (
    send_discord_alert,
    DISCORD_COLOR_GREEN, DISCORD_COLOR_RED, DISCORD_COLOR_BLUE,
    DISCORD_COLOR_YELLOW, DISCORD_COLOR_PURPLE,
)


def _pct_str(val: float, decimals: int = 1) -> str:
    return f"{val:+.{decimals}f}%"


def _dollars(val: float) -> str:
    return f"${val:+.4f}" if val != 0 else "$0.0000"


def generate_daily_report(performance_data: dict, paper_engine) -> None:
    """Generate and send daily paper trading report to Discord updates channel."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        bankroll = performance_data.get("current_bankroll", 0)
        starting = performance_data.get("starting_bankroll", 10.0)
        total_pnl = performance_data.get("total_net_pnl", 0)
        pnl_pct = performance_data.get("pnl_pct", 0)
        peak = getattr(paper_engine, "peak_bankroll", starting)
        drawdown_data = performance_data.get("drawdown", {})
        max_dd = drawdown_data.get("max_drawdown_pct", 0)
        win_rate = performance_data.get("win_rate", 0) * 100
        n_total = performance_data.get("total_trades", 0)
        n_open = performance_data.get("open_trades", 0)
        n_closed = performance_data.get("closed_trades", 0)
        sharpe = performance_data.get("sharpe_ratio", 0)
        edge_cap = performance_data.get("edge_capture", {})
        cal = performance_data.get("calibration", {})
        streaks = performance_data.get("streaks", {})
        criteria = performance_data.get("criteria", {})
        cat_edge = performance_data.get("category_edge", {})

        # Top/bottom categories
        cat_sorted = sorted(cat_edge.items(), key=lambda x: x[1].get("win_rate", 0), reverse=True)
        top_cats = "\n".join(
            f"  {cat}: {d['win_rate']:.0%} wr ({d['sample_size']} trades)"
            for cat, d in cat_sorted[:3]
        ) or "  (insufficient data)"
        bot_cats = "\n".join(
            f"  {cat}: {d['win_rate']:.0%} wr ({d['sample_size']} trades)"
            for cat, d in cat_sorted[-3:]
        ) or "  (insufficient data)"

        # Calibration summary
        cal_curve = cal.get("calibration_curve", [])
        if cal_curve:
            cal_summary = "\n".join(
                f"  {c['bucket']}: predicted {c['predicted_confidence']:.0%} → actual {c['actual_win_rate']:.0%}"
                for c in cal_curve
            )
        else:
            cal_summary = "  (need 3+ trades per confidence bucket)"

        pnl_color = DISCORD_COLOR_GREEN if total_pnl > 0 else (
            DISCORD_COLOR_RED if total_pnl < 0 else DISCORD_COLOR_YELLOW
        )

        msg = (
            f"**Date:** {today}\n\n"
            f"**BANKROLL**\n"
            f"Starting: ${starting:.2f} | Current: ${bankroll:.4f}\n"
            f"Total PnL: {_dollars(total_pnl)} ({_pct_str(pnl_pct)})\n"
            f"Peak: ${peak:.4f} | Max Drawdown: {max_dd:.1f}%\n\n"
            f"**PERFORMANCE**\n"
            f"Trades: {n_total} ({n_open} open, {n_closed} closed)\n"
            f"Win rate: {win_rate:.1f}% (target: 52%)\n"
            f"Avg predicted edge: {performance_data.get('avg_edge', 0):.4f}\n"
            f"Avg edge captured: {edge_cap.get('avg_captured_edge', 0):.4f} "
            f"({edge_cap.get('capture_rate_pct', 0):.1f}% capture rate)\n"
            f"Sharpe ratio: {sharpe:.3f}\n\n"
            f"**CALIBRATION**\n{cal_summary}\n"
            f"Cal error: {cal.get('calibration_error_pct', 0):.1f}%\n\n"
            f"**TOP CATEGORIES**\n{top_cats}\n\n"
            f"**WORST CATEGORIES**\n{bot_cats}\n\n"
            f"**STREAKS**\n"
            f"Current: {streaks.get('current_streak', 0)}x {streaks.get('current_streak_type', 'NONE')} | "
            f"Best: {streaks.get('longest_win_streak', 0)} | "
            f"Worst: {streaks.get('longest_loss_streak', 0)}\n\n"
            f"**GO-LIVE PROGRESS**\n"
            f"{'✅' if criteria.get('c1_trades_met') else '❌'} Trades: {n_closed}/50\n"
            f"{'✅' if criteria.get('c2_pnl_positive') else '❌'} PnL: {'POSITIVE' if total_pnl > 0 else 'NEGATIVE'}\n"
            f"{'✅' if criteria.get('c3_win_rate_met') else '❌'} Win rate: {win_rate:.1f}% (need 52%)\n"
            f"Overall: {sum([criteria.get('c1_trades_met',0), criteria.get('c2_pnl_positive',0), criteria.get('c3_win_rate_met',0)])}/3 criteria met"
        )

        send_discord_alert("Daily Paper Trading Report", msg, color=pnl_color)
        logger.info("Daily paper trading report sent to Discord")
    except Exception as e:
        logger.error("report_generator.generate_daily_report failed: {}", e)


def generate_trade_notification(position: dict, action: str,
                                 current_bankroll: float, trades_to_goal: int) -> None:
    """Send a compact Discord embed on trade entry or exit."""
    try:
        question = (position.get("market_question") or "Unknown")[:60]
        signal = position.get("signal", "")
        conf = float(position.get("confidence") or 0) * 100
        edge = float(position.get("edge") or 0)
        size = float(position.get("actual_size") or 0)
        slippage = float(position.get("slippage_applied") or 0) * 100
        entry_price = float(position.get("actual_entry_price") or 0)
        composite = float(position.get("composite_signal_score") or 0.5)
        primary = position.get("primary_signal") or "—"

        if action == "OPEN":
            color = DISCORD_COLOR_GREEN if signal == "BET_YES" else DISCORD_COLOR_RED
            sig_emoji = "🟢" if signal == "BET_YES" else "🔴"
            msg = (
                f"**Market:** {question}\n"
                f"**Signal:** {signal} {sig_emoji}\n"
                f"**Confidence:** {conf:.0f}% | **Edge:** {edge:.4f}\n"
                f"**Size:** ${size:.4f} (after {slippage:.1f}% slippage + 2% fee)\n"
                f"**Entry price:** {entry_price:.4f}\n"
                f"**Composite signal:** {composite:.3f}\n"
                f"**Primary driver:** {primary}\n"
                f"**Bankroll remaining:** ${current_bankroll:.4f}\n"
                f"**Trades to go-live:** {trades_to_goal}"
            )
            send_discord_alert("Paper Trade Opened", msg, color=color)

        elif action in ("WIN", "LOSS"):
            gross_pnl = float(position.get("gross_pnl") or 0)
            net_pnl = float(position.get("net_pnl") or 0)
            hold_hours = float(position.get("hold_time_hours") or 0)
            outcome = position.get("actual_outcome", "?")
            color = DISCORD_COLOR_GREEN if action == "WIN" else DISCORD_COLOR_RED
            result_emoji = "✅" if action == "WIN" else "❌"
            msg = (
                f"**Market:** {question}\n"
                f"**Result:** {'CORRECT' if action == 'WIN' else 'INCORRECT'} — outcome was {outcome}\n"
                f"**Gross PnL:** {_dollars(gross_pnl)}\n"
                f"**Net PnL:** {_dollars(net_pnl)} (after fees)\n"
                f"**Hold time:** {hold_hours:.1f}h\n"
                f"**Bankroll:** ${current_bankroll:.4f}\n"
                f"**Trades to go-live:** {trades_to_goal}"
            )
            title = f"Paper Trade Closed — {'WIN' if action == 'WIN' else 'LOSS'} {result_emoji}"
            send_discord_alert(title, msg, color=color)

        elif action == "VOID":
            msg = (
                f"**Market:** {question}\n"
                f"Market expired unresolved — position refunded.\n"
                f"**Bankroll:** ${current_bankroll:.4f}"
            )
            send_discord_alert("Paper Trade Voided", msg, color=DISCORD_COLOR_YELLOW)

    except Exception as e:
        logger.error("report_generator.generate_trade_notification failed: {}", e)


def generate_readiness_report(performance_data: dict, paper_engine) -> str:
    """Generate the full go-live readiness report. Returns report text."""
    try:
        n_closed = performance_data.get("closed_trades", 0)
        total_pnl = performance_data.get("total_net_pnl", 0)
        pnl_pct = performance_data.get("pnl_pct", 0)
        win_rate = performance_data.get("win_rate", 0) * 100
        sharpe = performance_data.get("sharpe_ratio", 0)
        drawdown_data = performance_data.get("drawdown", {})
        max_dd = drawdown_data.get("max_drawdown_pct", 0)
        cal = performance_data.get("calibration", {})
        streaks = performance_data.get("streaks", {})
        cat_edge = performance_data.get("category_edge", {})
        signal_attr = performance_data.get("signal_attribution", {})
        edge_cap = performance_data.get("edge_capture", {})

        # Risk ratings
        dd_rating = "ACCEPTABLE" if max_dd < 30 else ("WARNING" if max_dd < 50 else "DANGEROUS")
        sharpe_rating = "EXCELLENT" if sharpe > 2 else ("ACCEPTABLE" if sharpe > 1 else "BELOW AVERAGE")
        cal_error = cal.get("calibration_error_pct", 0)
        cal_rating = "WELL CALIBRATED" if cal_error < 5 else ("ACCEPTABLE" if cal_error < 10 else "NEEDS WORK")

        # Recommended settings based on performance
        rec_confidence = max(0.60, min(0.80, performance_data.get("avg_confidence", 0.65) + 0.05))
        rec_bankroll_risk = 5.0 if win_rate > 58 else (3.0 if win_rate > 54 else 1.0)
        avoid_cats = [cat for cat, d in cat_edge.items() if d.get("recommendation") == "AVOID"]
        specialize_cats = [cat for cat, d in cat_edge.items() if d.get("recommendation") == "SPECIALIZE"]

        top_signal = list(signal_attr.keys())[0] if signal_attr else "unknown"
        bot_signal = list(signal_attr.keys())[-1] if len(signal_attr) > 1 else "unknown"

        report = (
            f"ALL THREE CRITERIA MET — AWAITING CAMERON APPROVAL\n\n"
            f"✅ Criterion 1: 50+ trades completed ({n_closed} trades)\n"
            f"✅ Criterion 2: Positive PnL (${total_pnl:.4f} / {pnl_pct:+.2f}%)\n"
            f"✅ Criterion 3: Win rate above 52% ({win_rate:.1f}%)\n\n"
            f"**FULL PERFORMANCE SUMMARY**\n"
            f"Win rate: {win_rate:.1f}% | Sharpe: {sharpe:.3f} | "
            f"Edge capture: {edge_cap.get('capture_rate_pct', 0):.1f}%\n"
            f"Avg edge predicted: {performance_data.get('avg_edge', 0):.4f} | "
            f"Avg confidence: {performance_data.get('avg_confidence', 0):.2f}\n\n"
            f"**RISK ASSESSMENT**\n"
            f"Max drawdown: {max_dd:.1f}% ({dd_rating})\n"
            f"Sharpe ratio: {sharpe:.3f} ({sharpe_rating})\n"
            f"Calibration error: {cal_error:.1f}% ({cal_rating})\n"
            f"Longest losing streak: {streaks.get('longest_loss_streak', 0)}\n\n"
            f"**RECOMMENDED LIVE SETTINGS**\n"
            f"Starting bankroll: $10.00\n"
            f"MAX_BANKROLL_RISK: {rec_bankroll_risk:.2f}\n"
            f"MAX_BET_SIZE: 1.00\n"
            f"MIN_CONFIDENCE: {rec_confidence:.2f}\n"
            f"Categories to avoid: {', '.join(avoid_cats) or 'none'}\n"
            f"Categories to specialize: {', '.join(specialize_cats) or 'none'}\n\n"
            f"**INTELLIGENCE LAYER ASSESSMENT**\n"
            f"Most predictive signal: {top_signal}\n"
            f"Least predictive signal: {bot_signal}\n\n"
            f"**TO GO LIVE:**\n"
            f"1. Review this report\n"
            f"2. Set LIVE_TRADING=true in your .env file\n"
            f"3. Set the recommended values above\n"
            f"4. Restart the bot\n"
            f"5. Paper trading will continue alongside live trading\n\n"
            f"Cameron — this bot has proven itself. Your call."
        )

        send_discord_alert("GO-LIVE READINESS REPORT", report, color=DISCORD_COLOR_GREEN)
        logger.info("Go-live readiness report sent to Discord")
        return report
    except Exception as e:
        logger.error("report_generator.generate_readiness_report failed: {}", e)
        return ""


def generate_weekly_summary(performance_data: dict, week_trades: list) -> None:
    """Send week-over-week summary to Discord every Sunday."""
    try:
        n = len(week_trades)
        if n == 0:
            send_discord_alert(
                "Weekly Paper Trading Summary",
                "No paper trades resolved this week.",
                color=DISCORD_COLOR_BLUE,
            )
            return

        wins = sum(1 for t in week_trades if t.get("was_correct"))
        wr = wins / n * 100
        week_pnl = sum(float(t.get("net_pnl") or 0) for t in week_trades)

        msg = (
            f"**Week ending:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
            f"**THIS WEEK**\n"
            f"Trades resolved: {n}\n"
            f"Win rate: {wr:.1f}%\n"
            f"Net PnL: {_dollars(week_pnl)}\n\n"
            f"**CUMULATIVE**\n"
            f"Win rate: {performance_data.get('win_rate', 0) * 100:.1f}% | "
            f"Total trades: {performance_data.get('closed_trades', 0)}\n"
            f"Net PnL: {_dollars(performance_data.get('total_net_pnl', 0))}\n"
            f"Go-live criteria: "
            f"{sum(1 for k in ['c1_trades_met','c2_pnl_positive','c3_win_rate_met'] if performance_data.get('criteria', {}).get(k))}/3 met"
        )

        color = DISCORD_COLOR_GREEN if week_pnl > 0 else (
            DISCORD_COLOR_RED if week_pnl < 0 else DISCORD_COLOR_YELLOW
        )
        send_discord_alert("Weekly Paper Trading Summary", msg, color=color)
        logger.info("Weekly paper trading summary sent to Discord")
    except Exception as e:
        logger.error("report_generator.generate_weekly_summary failed: {}", e)
