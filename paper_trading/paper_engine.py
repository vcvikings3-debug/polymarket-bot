"""
PaperTradingEngine — production-grade simulation with realistic slippage, fees,
resolution monitoring, portfolio correlation tracking, and go-live readiness evaluation.
"""

import sys
import os
import json
from datetime import datetime, timezone
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    PAPER_STARTING_BANKROLL, PAPER_MAX_DAILY_TRADES, PAPER_MAX_OPEN_POSITIONS,
)
from data.database import (
    get_open_paper_positions, get_all_paper_positions, get_paper_position_by_market,
    get_today_paper_trade_count, save_paper_position, update_paper_position_resolution,
    record_bankroll_event, get_bankroll_history, save_go_live_readiness,
    get_last_readiness_notification_time, mark_readiness_notification_sent,
    upsert_paper_daily_stats,
)
from paper_trading.portfolio_manager import (
    calculate_correlation_groups, calculate_total_exposure,
    is_diversification_acceptable, calculate_portfolio_var,
    suggest_position_sizing_adjustment,
)
from paper_trading.performance_analyzer import generate_full_performance_report
from paper_trading.report_generator import (
    generate_trade_notification, generate_readiness_report,
)
from utils.monitoring import send_discord_update, DISCORD_COLOR_BLUE


def _slippage_rate(liquidity: float) -> float:
    """Determine slippage rate based on market liquidity."""
    if liquidity >= 50_000:
        return 0.001   # 0.1%
    elif liquidity >= 10_000:
        return 0.003   # 0.3%
    elif liquidity >= 1_000:
        return 0.008   # 0.8%
    return 0.020       # 2.0%


class PaperTradingEngine:
    """
    Manages the full paper trading lifecycle: simulate_bet, resolve_paper_position,
    go-live readiness evaluation, and portfolio state tracking.
    """

    ENTRY_FEE_RATE = 0.02     # 2% Polymarket entry fee
    RESOLUTION_FEE_RATE = 0.01  # 1% on winning payouts

    def __init__(self, starting_bankroll: float = None):
        if starting_bankroll is None:
            starting_bankroll = PAPER_STARTING_BANKROLL

        history = get_bankroll_history()
        if not history:
            # First run — seed the bankroll
            now = datetime.now(timezone.utc).isoformat()
            record_bankroll_event(now, starting_bankroll, "INIT", note="Paper engine first init")
            self.starting_bankroll = starting_bankroll
            self.current_bankroll = starting_bankroll
            self.peak_bankroll = starting_bankroll
            logger.info("Paper trading engine initialized — starting bankroll: ${:.2f}", starting_bankroll)
            send_discord_update(
                "Paper Trading Engine Started",
                f"Starting bankroll: ${starting_bankroll:.2f}\nTarget: 50 completed trades before go-live evaluation.",
                color=DISCORD_COLOR_BLUE,
            )
        else:
            self.starting_bankroll = history[0]["bankroll_amount"]
            self.current_bankroll = history[-1]["bankroll_amount"]
            self.peak_bankroll = max(h["bankroll_amount"] for h in history)
            logger.info(
                "Paper trading engine loaded — bankroll: ${:.4f} (started at ${:.2f})",
                self.current_bankroll, self.starting_bankroll,
            )

    # ── Core simulation ────────────────────────────────────────────────────

    def simulate_bet(self, market: dict, llm_result: dict, bet_recommendation: dict) -> dict | None:
        """
        Simulate placing a bet. Returns position dict if accepted, None if skipped.
        Called for every PLACE BET verdict when PAPER_TRADING=true.
        """
        if bet_recommendation.get("signal") == "SKIP" or not bet_recommendation.get("should_bet"):
            return None

        market_id = str(market.get("id", ""))
        question = market.get("question", "Unknown")
        signal = llm_result.get("signal", "")
        intended_size = float(bet_recommendation.get("recommended_size") or 0)

        if intended_size < 0.01:
            return None

        # ── Step 1: Pre-flight checks ──────────────────────────────────────
        # Already have an open position?
        existing = get_paper_position_by_market(market_id)
        if existing:
            logger.debug("paper_engine: already have open position in {} — skipping", market_id[:20])
            return None

        # Daily trade limit?
        today_count = get_today_paper_trade_count()
        if today_count >= PAPER_MAX_DAILY_TRADES:
            logger.debug("paper_engine: daily trade limit ({}) reached", PAPER_MAX_DAILY_TRADES)
            return None

        # Bankroll check
        if self.current_bankroll < intended_size * (1 + self.ENTRY_FEE_RATE):
            logger.warning("paper_engine: insufficient bankroll ${:.4f} for ${:.4f} bet",
                           self.current_bankroll, intended_size)
            return None

        # Portfolio diversification check
        open_positions = get_open_paper_positions()
        new_pos_preview = {"intended_size": intended_size, "actual_size": intended_size,
                           "market_question": question}
        ok, reason = is_diversification_acceptable(open_positions, new_pos_preview, self.current_bankroll)
        if not ok:
            logger.info("paper_engine: diversification check failed — {}", reason)
            return None

        # ── Step 2: Entry price simulation ────────────────────────────────
        liquidity = float(market.get("liquidity") or 0)
        slip = _slippage_rate(liquidity)
        fee_amount = intended_size * self.ENTRY_FEE_RATE
        actual_position_cost = intended_size + fee_amount

        if signal == "BET_YES":
            raw_entry = float(market.get("yes_price") or 0.5)
        else:
            raw_entry = float(market.get("no_price") or 0.5)

        actual_entry_price = max(raw_entry * (1 + slip), 0.001)

        # Deduct from bankroll
        self.current_bankroll -= actual_position_cost
        now = datetime.now(timezone.utc).isoformat()
        record_bankroll_event(now, self.current_bankroll, "TRADE_OPEN",
                              note=f"Opened: {question[:40]}")

        # Update peak
        self.peak_bankroll = max(self.peak_bankroll, self.current_bankroll)

        # ── Step 3: Record position ────────────────────────────────────────
        intelligence_snapshot = json.dumps({
            "question": question,
            "composite_signal": llm_result.get("composite_signal", 0.5),
            "news": {},
            "onchain": {},
            "sentiment": {},
            "base_rate": {},
        })

        pos_id = save_paper_position(
            market_id=market_id,
            market_question=question,
            signal=signal,
            intended_size=intended_size,
            actual_size=intended_size,
            slippage_applied=slip,
            fee_applied=fee_amount,
            actual_position_cost=actual_position_cost,
            entry_price=raw_entry,
            actual_entry_price=actual_entry_price,
            confidence=float(llm_result.get("confidence") or 0),
            edge=float(llm_result.get("edge") or 0),
            reasoning=str(llm_result.get("reasoning") or "")[:300],
            primary_signal=str(llm_result.get("primary_signal") or "")[:150],
            conflicting_signals=str(llm_result.get("conflicting_signals") or "")[:200],
            intelligence_signals_snapshot=intelligence_snapshot,
            composite_signal_score=float(llm_result.get("composite_signal") or 0.5),
            prompt_version_id=llm_result.get("prompt_version_id") or 0,
            expected_resolution_date=market.get("end_date") or "",
        )

        # ── Step 4: Notify ─────────────────────────────────────────────────
        position = {
            "id": pos_id,
            "market_id": market_id,
            "market_question": question,
            "signal": signal,
            "intended_size": intended_size,
            "actual_size": intended_size,
            "slippage_applied": slip,
            "fee_applied": fee_amount,
            "actual_position_cost": actual_position_cost,
            "entry_price": raw_entry,
            "actual_entry_price": actual_entry_price,
            "confidence": float(llm_result.get("confidence") or 0),
            "edge": float(llm_result.get("edge") or 0),
            "composite_signal_score": float(llm_result.get("composite_signal") or 0.5),
            "primary_signal": str(llm_result.get("primary_signal") or ""),
        }

        closed_count = len(get_all_paper_positions(status="CLOSED_WIN")) + \
                       len(get_all_paper_positions(status="CLOSED_LOSS"))
        trades_to_goal = max(0, 50 - closed_count)

        generate_trade_notification(position, "OPEN", self.current_bankroll, trades_to_goal)

        logger.info(
            "paper_engine: OPENED {} {} ${:.4f} at {:.4f} (slip {:.1%}, fee ${:.4f}) — bankroll ${:.4f}",
            signal, question[:40], intended_size, actual_entry_price,
            slip, fee_amount, self.current_bankroll,
        )

        return position

    # ── Resolution ────────────────────────────────────────────────────────

    def resolve_paper_position(self, market_id: str, actual_outcome: str) -> None:
        """Close all OPEN paper positions for this market with the given outcome."""
        open_for_market = [
            p for p in get_open_paper_positions() if p["market_id"] == market_id
        ]
        if not open_for_market:
            return

        for pos in open_for_market:
            signal = pos.get("signal", "")
            intended_size = float(pos.get("intended_size") or 0)
            actual_entry_price = float(pos.get("actual_entry_price") or 0.5)
            fee_paid = float(pos.get("fee_applied") or 0)
            actual_position_cost = float(pos.get("actual_position_cost") or (intended_size + fee_paid))

            # Determine win/loss
            was_correct = (
                (signal == "BET_YES" and actual_outcome == "YES") or
                (signal == "BET_NO" and actual_outcome == "NO")
            )

            if was_correct:
                # Gross payout = shares × $1.00
                shares = intended_size / max(actual_entry_price, 0.001)
                gross_payout = shares
                resolution_fee = gross_payout * self.RESOLUTION_FEE_RATE
                net_payout = gross_payout - resolution_fee
                gross_pnl = gross_payout - actual_position_cost
                net_pnl = net_payout - actual_position_cost
                self.current_bankroll += net_payout
                status = "CLOSED_WIN"
                action = "WIN"
            else:
                gross_payout = 0.0
                resolution_fee = 0.0
                gross_pnl = -actual_position_cost
                net_pnl = -actual_position_cost
                # Bankroll already reduced at entry — no change
                status = "CLOSED_LOSS"
                action = "LOSS"

            # Update peak
            self.peak_bankroll = max(self.peak_bankroll, self.current_bankroll)

            # Record bankroll event
            now = datetime.now(timezone.utc).isoformat()
            record_bankroll_event(
                now, self.current_bankroll, "TRADE_CLOSE",
                event_id=pos["id"],
                note=f"{'WIN' if was_correct else 'LOSS'}: {pos.get('market_question', '')[:40]}",
            )

            # Update position in DB
            update_paper_position_resolution(
                position_id=pos["id"],
                actual_outcome=actual_outcome,
                was_correct=was_correct,
                gross_pnl=round(gross_pnl, 6),
                net_pnl=round(net_pnl, 6),
                resolution_fee=round(resolution_fee, 6),
                status=status,
            )

            # Feed self-learning system
            try:
                from intelligence.historical_performance import record_resolution
                record_resolution(market_id, actual_outcome, round(net_pnl, 6))
            except Exception as e:
                logger.warning("paper_engine: record_resolution failed — {}", e)

            # Notify
            closed_position = dict(pos)
            closed_position.update({
                "actual_outcome": actual_outcome,
                "gross_pnl": round(gross_pnl, 6),
                "net_pnl": round(net_pnl, 6),
                "resolution_fee_applied": round(resolution_fee, 6),
            })
            all_closed = get_all_paper_positions(status="CLOSED_WIN") + \
                         get_all_paper_positions(status="CLOSED_LOSS")
            trades_to_goal = max(0, 50 - len(all_closed))
            generate_trade_notification(closed_position, action, self.current_bankroll, trades_to_goal)

            logger.info(
                "paper_engine: CLOSED {} {} {} net_pnl={:.4f} bankroll={:.4f}",
                "WIN" if was_correct else "LOSS", signal,
                pos.get("market_question", "")[:40], net_pnl, self.current_bankroll,
            )

        # Check go-live after every resolution
        self.check_go_live_readiness()

    def void_paper_position(self, position_id: int) -> None:
        """Void an expired-unresolved position and return the position cost to bankroll."""
        try:
            conn_positions = get_all_paper_positions()
            pos = next((p for p in conn_positions if p["id"] == position_id), None)
            if not pos:
                return
            cost = float(pos.get("actual_position_cost") or 0)
            self.current_bankroll += cost  # refund
            now = datetime.now(timezone.utc).isoformat()
            record_bankroll_event(now, self.current_bankroll, "VOID", event_id=position_id,
                                  note="Market expired unresolved — refunded")
            update_paper_position_resolution(
                position_id=position_id,
                actual_outcome="VOID",
                was_correct=False,
                gross_pnl=0.0,
                net_pnl=0.0,
                resolution_fee=0.0,
                status="VOID",
            )
            question = pos.get("market_question", "")[:60]
            void_pos = dict(pos)
            generate_trade_notification(void_pos, "VOID", self.current_bankroll, 0)
            logger.info("paper_engine: VOIDED position {} — refunded ${:.4f}", position_id, cost)
        except Exception as e:
            logger.error("paper_engine: void_paper_position failed — {}", e)

    def mark_position_disputed(self, position_id: int) -> None:
        """Mark a position as DISPUTED while awaiting final resolution."""
        try:
            from data.database import _get_connection
            conn = _get_connection()
            conn.execute(
                "UPDATE paper_positions SET status = 'DISPUTED' WHERE id = ?", (position_id,)
            )
            conn.commit()
            conn.close()
            logger.warning("paper_engine: position {} marked DISPUTED", position_id)
        except Exception as e:
            logger.error("paper_engine: mark_position_disputed failed — {}", e)

    # ── Go-live readiness ──────────────────────────────────────────────────

    def check_go_live_readiness(self) -> dict:
        """
        Check all three go-live criteria. Sends Discord notification if all met.
        Does NOT auto-enable live trading — waits for Cameron's explicit approval.
        """
        all_positions = get_all_paper_positions()
        closed = [p for p in all_positions if p["status"] in ("CLOSED_WIN", "CLOSED_LOSS")]
        n_closed = len(closed)
        wins = sum(1 for p in closed if p.get("was_correct"))
        win_rate = wins / n_closed if n_closed > 0 else 0.0
        total_pnl = sum(float(p.get("net_pnl") or 0) for p in closed)

        c1 = n_closed >= 50
        c2 = total_pnl > 0
        c3 = win_rate >= 0.52
        all_met = c1 and c2 and c3

        status = {
            "c1_trades": {"met": c1, "value": n_closed, "target": 50},
            "c2_pnl": {"met": c2, "value": round(total_pnl, 4), "target": ">0"},
            "c3_win_rate": {"met": c3, "value": round(win_rate, 4), "target": ">=0.52"},
            "all_criteria_met": all_met,
            "trades_remaining": max(0, 50 - n_closed),
        }

        criteria_met_count = sum([c1, c2, c3])
        logger.debug(
            "paper_engine: go-live criteria {}/3 — trades={} pnl={:.4f} wr={:.2%}",
            criteria_met_count, n_closed, total_pnl, win_rate,
        )

        if all_met:
            # Check if we notified within the last 24h to avoid spamming
            last_notif = get_last_readiness_notification_time()
            should_notify = True
            if last_notif:
                try:
                    last_dt = datetime.fromisoformat(last_notif)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    hours_since = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
                    if hours_since < 24:
                        should_notify = False
                except Exception:
                    pass

            if should_notify:
                history = get_bankroll_history()
                perf = generate_full_performance_report(all_positions, history)
                report_text = generate_readiness_report(perf, self)
                row_id = save_go_live_readiness(
                    trades_completed=n_closed, total_pnl=total_pnl, win_rate=win_rate,
                    c1=c1, c2=c2, c3=c3, all_met=True,
                    report_json=json.dumps(perf),
                )
                if row_id:
                    mark_readiness_notification_sent(row_id)

                # Set flag in strategy_state
                from data.database import set_strategy_state
                set_strategy_state("go_live_flag", "READY_AWAITING_APPROVAL")

                logger.warning(
                    "paper_engine: GO-LIVE CRITERIA MET ({} trades, PnL={:.4f}, WR={:.1%}) "
                    "— set LIVE_TRADING=true in .env to go live",
                    n_closed, total_pnl, win_rate,
                )

        return status

    # ── Portfolio state ────────────────────────────────────────────────────

    def get_portfolio_state(self) -> dict:
        """Return current portfolio snapshot."""
        open_positions = get_open_paper_positions()
        all_positions = get_all_paper_positions()
        closed = [p for p in all_positions if p["status"] in ("CLOSED_WIN", "CLOSED_LOSS")]
        wins = sum(1 for p in closed if p.get("was_correct"))
        n_closed = len(closed)
        win_rate = wins / n_closed if n_closed > 0 else 0.0
        total_pnl = sum(float(p.get("net_pnl") or 0) for p in closed)

        history = get_bankroll_history()
        from paper_trading.performance_analyzer import calculate_max_drawdown
        dd = calculate_max_drawdown(history)

        exposure = calculate_total_exposure(open_positions, self.current_bankroll)
        corr_groups = {k: len(v) for k, v in calculate_correlation_groups(open_positions).items()}
        var = calculate_portfolio_var(open_positions)

        return {
            "current_bankroll": round(self.current_bankroll, 4),
            "starting_bankroll": round(self.starting_bankroll, 2),
            "peak_bankroll": round(self.peak_bankroll, 4),
            "total_pnl": round(total_pnl, 4),
            "pnl_percentage": round((self.current_bankroll - self.starting_bankroll) / self.starting_bankroll * 100, 2),
            "open_positions": len(open_positions),
            "total_exposure": exposure["total_exposure"],
            "exposure_pct": exposure["exposure_pct_of_bankroll"],
            "closed_positions": n_closed,
            "wins": wins,
            "win_rate": round(win_rate, 4),
            "max_drawdown_pct": dd["max_drawdown_pct"],
            "current_drawdown_pct": dd["current_drawdown_pct"],
            "correlation_groups": corr_groups,
            "portfolio_var_95": var,
        }

    def scan_for_resolutions(self) -> int:
        """Run resolution scan. Delegates to resolution_monitor."""
        from paper_trading.resolution_monitor import scan_all_open_positions
        return scan_all_open_positions(self)
