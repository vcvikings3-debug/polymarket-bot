"""Enforces bankroll limits, max bet size, and daily loss limits."""

import sqlite3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MAX_BET_SIZE

DAILY_BET_LIMIT_MULTIPLIER = 10   # daily wager cap = MAX_BET_SIZE × 10
DAILY_LOSS_LIMIT = 5.00           # stop betting if down more than $5 today


def get_daily_stats(db_path: str) -> dict:
    """Query the bets table for today's activity. Returns totals for today (UTC)."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bets'"
        ).fetchone()
        if not exists:
            return {"total_bets_today": 0, "total_wagered_today": 0.0,
                    "total_won_today": 0.0, "net_pnl_today": 0.0}

        row = conn.execute("""
            SELECT
                COUNT(*) AS total_bets,
                COALESCE(SUM(size), 0.0) AS total_wagered,
                COALESCE(SUM(CASE WHEN outcome = 'WIN' THEN pnl ELSE 0 END), 0.0) AS total_won,
                COALESCE(SUM(pnl), 0.0) AS net_pnl
            FROM bets
            WHERE date(placed_at) = ?
        """, (today,)).fetchone()

        return {
            "total_bets_today": int(row["total_bets"]),
            "total_wagered_today": float(row["total_wagered"]),
            "total_won_today": float(row["total_won"]),
            "net_pnl_today": float(row["net_pnl"]),
        }
    finally:
        conn.close()


def is_safe_to_bet(db_path: str, recommended_size: float) -> bool:
    """Return True only if daily wager and loss limits have not been breached."""
    stats = get_daily_stats(db_path)
    daily_limit = MAX_BET_SIZE * DAILY_BET_LIMIT_MULTIPLIER
    if stats["total_wagered_today"] + recommended_size > daily_limit:
        return False
    if stats["net_pnl_today"] < -DAILY_LOSS_LIMIT:
        return False
    return True
