"""SQLite database handler for logging all bets, outcomes, and performance metrics."""

import sqlite3
import os
from loguru import logger

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "polymarket.db")


def _get_connection():
    """Get a connection to the SQLite database."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_columns(conn):
    """Add any missing columns to the markets table."""
    cursor = conn.execute("PRAGMA table_info(markets)")
    existing = {row["name"] for row in cursor.fetchall()}
    migrations = {
        "is_crypto": "INTEGER DEFAULT 0",
        "llm_signal": "TEXT",
        "llm_confidence": "REAL",
        "llm_reasoning": "TEXT",
        "llm_edge": "REAL",
        "llm_analyzed_at": "TEXT",
    }
    for col, coltype in migrations.items():
        if col not in existing:
            logger.info("Adding missing column '{}' to markets table", col)
            conn.execute(f"ALTER TABLE markets ADD COLUMN {col} {coltype}")
    conn.commit()


def initialize_db():
    """Create both tables (markets, market_snapshots) if they don't exist."""
    conn = _get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS markets (
            id TEXT PRIMARY KEY,
            question TEXT,
            category TEXT,
            end_date TEXT,
            yes_price REAL,
            no_price REAL,
            volume REAL,
            liquidity REAL,
            is_crypto INTEGER DEFAULT 0,
            last_updated TEXT,
            raw_json TEXT,
            llm_signal TEXT,
            llm_confidence REAL,
            llm_reasoning TEXT,
            llm_edge REAL,
            llm_analyzed_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT,
            yes_price REAL,
            no_price REAL,
            volume REAL,
            timestamp TEXT,
            FOREIGN KEY (market_id) REFERENCES markets(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT,
            side TEXT,
            size REAL,
            outcome TEXT DEFAULT 'PENDING',
            pnl REAL DEFAULT 0,
            placed_at TEXT,
            resolved_at TEXT,
            order_id TEXT,
            FOREIGN KEY (market_id) REFERENCES markets(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT,
            signal TEXT,
            confidence REAL,
            edge REAL,
            reasoning TEXT,
            context_snapshot TEXT,
            prompt_version_id INTEGER,
            predicted_at TEXT,
            resolved_at TEXT,
            actual_outcome TEXT,
            was_correct INTEGER,
            pnl REAL,
            FOREIGN KEY (market_id) REFERENCES markets(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prompt_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_number INTEGER,
            prompt_text TEXT,
            created_at TEXT,
            win_rate REAL,
            avg_edge REAL,
            calibration_score REAL,
            sample_size INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 0,
            parent_version_id INTEGER,
            evolution_notes TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signal_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_name TEXT,
            was_present INTEGER,
            prediction_was_correct INTEGER,
            market_id TEXT,
            recorded_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS strategy_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE,
            value TEXT,
            updated_at TEXT
        )
    """)

    # ── Phase 2.6: Paper Trading tables ───────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT,
            market_question TEXT,
            signal TEXT,
            intended_size REAL,
            actual_size REAL,
            slippage_applied REAL,
            fee_applied REAL,
            actual_position_cost REAL,
            entry_price REAL,
            actual_entry_price REAL,
            confidence REAL,
            edge REAL,
            reasoning TEXT,
            primary_signal TEXT,
            conflicting_signals TEXT,
            intelligence_signals_snapshot TEXT,
            composite_signal_score REAL,
            prompt_version_id INTEGER,
            opened_at TEXT,
            expected_resolution_date TEXT,
            resolved_at TEXT,
            actual_outcome TEXT,
            was_correct INTEGER,
            gross_pnl REAL,
            net_pnl REAL,
            resolution_fee_applied REAL,
            hold_time_hours REAL,
            status TEXT DEFAULT 'OPEN'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_bankroll_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            bankroll_amount REAL,
            event_type TEXT,
            event_id INTEGER,
            note TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            trades_opened INTEGER DEFAULT 0,
            trades_closed INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            gross_pnl REAL DEFAULT 0,
            net_pnl REAL DEFAULT 0,
            ending_bankroll REAL,
            win_rate REAL,
            avg_confidence REAL,
            avg_edge REAL,
            sharpe_ratio REAL,
            max_drawdown REAL,
            notes TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS go_live_readiness (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evaluated_at TEXT,
            trades_completed INTEGER,
            total_pnl REAL,
            win_rate REAL,
            criterion_1_met INTEGER DEFAULT 0,
            criterion_2_met INTEGER DEFAULT 0,
            criterion_3_met INTEGER DEFAULT 0,
            all_criteria_met INTEGER DEFAULT 0,
            readiness_report_json TEXT,
            notification_sent INTEGER DEFAULT 0,
            cameron_approved INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    _ensure_columns(conn)
    _ensure_bets_columns(conn)
    conn.close()
    logger.info("Database initialized at {}", DB_PATH)


def save_market(market: dict) -> bool:
    """Insert or update a market record. Returns True on success."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO markets (id, question, category, end_date, yes_price, no_price,
                                 volume, liquidity, is_crypto, last_updated, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                question = excluded.question,
                category = excluded.category,
                end_date = excluded.end_date,
                yes_price = excluded.yes_price,
                no_price = excluded.no_price,
                volume = excluded.volume,
                liquidity = excluded.liquidity,
                is_crypto = excluded.is_crypto,
                last_updated = excluded.last_updated,
                raw_json = excluded.raw_json
        """, (
            market["id"],
            market["question"],
            market.get("category", ""),
            market.get("end_date", ""),
            market.get("yes_price", 0.0),
            market.get("no_price", 0.0),
            market.get("volume", 0.0),
            market.get("liquidity", 0.0),
            1 if market.get("is_crypto") else 0,
            market.get("last_updated", ""),
            market.get("raw_json", ""),
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to save market {}: {}", market.get("id", "unknown"), e)
        return False


def save_snapshot(market_id: str, yes_price: float, no_price: float, volume: float) -> bool:
    """Write a price/volume snapshot for a market. Returns True on success."""
    from datetime import datetime, timezone
    try:
        conn = _get_connection()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO market_snapshots (market_id, yes_price, no_price, volume, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (market_id, yes_price, no_price, volume, now))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to save snapshot for {}: {}", market_id, e)
        return False


def get_all_crypto_markets() -> list:
    """Return all markets flagged as crypto."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM markets WHERE is_crypto = 1 ORDER BY volume DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_crypto_markets_only() -> list:
    """Return only markets where is_crypto=1 (strictly flagged)."""
    return get_all_crypto_markets()


def get_top_markets(limit: int = 20) -> list:
    """Return top N crypto markets by volume, with llm_confidence exposed as 'score'."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT *, COALESCE(llm_confidence, 0.0) AS score
        FROM markets
        WHERE is_crypto = 1
        ORDER BY volume DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_market_by_id(market_id: str) -> dict | None:
    """Return a single market by its Polymarket ID."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM markets WHERE id = ?", (market_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_market_count() -> int:
    """Return total number of markets in the database."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM markets")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def get_crypto_market_count() -> int:
    """Return number of crypto markets in the database."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM markets WHERE is_crypto = 1")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def _ensure_bets_columns(conn):
    """Add order_id column to bets table if missing."""
    cursor = conn.execute("PRAGMA table_info(bets)")
    existing = {row["name"] for row in cursor.fetchall()}
    if "order_id" not in existing:
        logger.info("Adding missing column 'order_id' to bets table")
        conn.execute("ALTER TABLE bets ADD COLUMN order_id TEXT")
    conn.commit()


def save_llm_analysis(market_id: str, signal: str, confidence: float, reasoning: str, edge: float) -> bool:
    """Update a market record with LLM analysis results. Returns True on success."""
    from datetime import datetime, timezone
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute("""
            UPDATE markets SET
                llm_signal = ?,
                llm_confidence = ?,
                llm_reasoning = ?,
                llm_edge = ?,
                llm_analyzed_at = ?
            WHERE id = ?
        """, (signal, confidence, reasoning, edge, now, market_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to save LLM analysis for {}: {}", market_id, e)
        return False


# ── Phase 4: bet recording ─────────────────────────────────────────────────

def record_bet(market_id: str, side: str, size: float, outcome: str, order_id: str = None) -> bool:
    """Insert a bet record into the bets table. Returns True on success."""
    from datetime import datetime, timezone
    try:
        conn = _get_connection()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO bets (market_id, side, size, outcome, placed_at, order_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (market_id, side, size, outcome, now, order_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to record bet for {}: {}", market_id, e)
        return False


# ── Phase 2.5: prediction tracking ────────────────────────────────────────

def save_prediction(market_id: str, signal: str, confidence: float, edge: float,
                    reasoning: str, context_snapshot: str, prompt_version_id: int = None) -> int:
    """Save an LLM prediction before outcome is known. Returns new row id, 0 on failure."""
    from datetime import datetime, timezone
    try:
        conn = _get_connection()
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute("""
            INSERT INTO predictions
                (market_id, signal, confidence, edge, reasoning, context_snapshot,
                 prompt_version_id, predicted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (market_id, signal, confidence, edge, reasoning, context_snapshot,
              prompt_version_id, now))
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id
    except Exception as e:
        logger.error("Failed to save prediction for {}: {}", market_id, e)
        return 0


def update_prediction_resolution(market_id: str, actual_outcome: str, pnl: float,
                                  resolved_at: str = None) -> bool:
    """Mark a prediction as resolved with actual outcome and PnL. Returns True on success."""
    from datetime import datetime, timezone
    try:
        conn = _get_connection()
        if resolved_at is None:
            resolved_at = datetime.now(timezone.utc).isoformat()
        row = conn.execute(
            "SELECT id, signal FROM predictions WHERE market_id = ? AND resolved_at IS NULL "
            "ORDER BY predicted_at DESC LIMIT 1",
            (market_id,)
        ).fetchone()
        if not row:
            conn.close()
            return False
        signal = row["signal"]
        was_correct = 1 if (
            (signal == "BET_YES" and actual_outcome == "YES") or
            (signal == "BET_NO" and actual_outcome == "NO")
        ) else 0
        conn.execute("""
            UPDATE predictions SET
                resolved_at = ?, actual_outcome = ?, was_correct = ?, pnl = ?
            WHERE id = ?
        """, (resolved_at, actual_outcome, was_correct, pnl, row["id"]))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to update resolution for {}: {}", market_id, e)
        return False


def get_resolved_predictions(limit: int = 500) -> list:
    """Return resolved predictions ordered by most recent."""
    try:
        conn = _get_connection()
        rows = conn.execute("""
            SELECT * FROM predictions
            WHERE resolved_at IS NOT NULL AND was_correct IS NOT NULL
            ORDER BY resolved_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Failed to get resolved predictions: {}", e)
        return []


def get_unresolved_predictions(limit: int = 200) -> list:
    """Return predictions that have not yet been resolved."""
    try:
        conn = _get_connection()
        rows = conn.execute("""
            SELECT * FROM predictions
            WHERE resolved_at IS NULL
            ORDER BY predicted_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Failed to get unresolved predictions: {}", e)
        return []


# ── Phase 2.5: prompt versioning ──────────────────────────────────────────

def save_prompt_version(version_number: int, prompt_text: str,
                        evolution_notes: str = None, parent_version_id: int = None) -> int:
    """Insert a new prompt version. Returns new row id, 0 on failure."""
    from datetime import datetime, timezone
    try:
        conn = _get_connection()
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute("""
            INSERT INTO prompt_versions
                (version_number, prompt_text, created_at, is_active, evolution_notes, parent_version_id)
            VALUES (?, ?, ?, 0, ?, ?)
        """, (version_number, prompt_text, now, evolution_notes, parent_version_id))
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id
    except Exception as e:
        logger.error("Failed to save prompt version {}: {}", version_number, e)
        return 0


def get_active_prompt_version() -> dict | None:
    """Return the currently active prompt version, or None if none set."""
    try:
        conn = _get_connection()
        row = conn.execute(
            "SELECT * FROM prompt_versions WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to get active prompt version: {}", e)
        return None


def set_active_prompt(version_id: int) -> bool:
    """Deactivate all prompt versions and activate the specified one."""
    try:
        conn = _get_connection()
        conn.execute("UPDATE prompt_versions SET is_active = 0")
        conn.execute("UPDATE prompt_versions SET is_active = 1 WHERE id = ?", (version_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to set active prompt {}: {}", version_id, e)
        return False


def update_prompt_performance(version_id: int, win_rate: float, avg_edge: float,
                               calibration_score: float, sample_size: int) -> bool:
    """Update performance stats for a prompt version."""
    try:
        conn = _get_connection()
        conn.execute("""
            UPDATE prompt_versions SET
                win_rate = ?, avg_edge = ?, calibration_score = ?, sample_size = ?
            WHERE id = ?
        """, (win_rate, avg_edge, calibration_score, sample_size, version_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to update prompt performance for {}: {}", version_id, e)
        return False


# ── Phase 2.5: signal performance ─────────────────────────────────────────

def save_signal_performance(signal_name: str, was_present: bool,
                             prediction_was_correct: bool, market_id: str) -> bool:
    """Record whether a signal was present and whether the prediction was correct."""
    from datetime import datetime, timezone
    try:
        conn = _get_connection()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO signal_performance
                (signal_name, was_present, prediction_was_correct, market_id, recorded_at)
            VALUES (?, ?, ?, ?, ?)
        """, (signal_name, 1 if was_present else 0, 1 if prediction_was_correct else 0,
              market_id, now))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to save signal performance: {}", e)
        return False


def get_signal_performance_stats() -> dict:
    """Return win rate when each signal was present vs absent."""
    try:
        conn = _get_connection()
        rows = conn.execute("""
            SELECT signal_name, was_present,
                   COUNT(*) as total,
                   SUM(prediction_was_correct) as wins
            FROM signal_performance
            GROUP BY signal_name, was_present
        """).fetchall()
        conn.close()
        stats = {}
        for row in rows:
            name = row["signal_name"]
            if name not in stats:
                stats[name] = {"present_win_rate": None, "absent_win_rate": None,
                               "present_count": 0, "absent_count": 0}
            total = row["total"]
            wins = row["wins"] or 0
            rate = wins / total if total > 0 else 0.0
            if row["was_present"]:
                stats[name]["present_win_rate"] = round(rate, 4)
                stats[name]["present_count"] = total
            else:
                stats[name]["absent_win_rate"] = round(rate, 4)
                stats[name]["absent_count"] = total
        return stats
    except Exception as e:
        logger.error("Failed to get signal performance stats: {}", e)
        return {}


# ── Phase 2.5: strategy state key-value store ─────────────────────────────

def get_strategy_state(key: str, default=None):
    """Read a strategy state value by key. Returns default if not set."""
    try:
        conn = _get_connection()
        row = conn.execute(
            "SELECT value FROM strategy_state WHERE key = ?", (key,)
        ).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception as e:
        logger.error("Failed to get strategy state {}: {}", key, e)
        return default


def set_strategy_state(key: str, value: str) -> bool:
    """Upsert a strategy state key-value pair."""
    from datetime import datetime, timezone
    try:
        conn = _get_connection()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO strategy_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """, (key, value, now))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to set strategy state {}: {}", key, e)
        return False


# ── Phase 2.6: Paper Trading helpers ──────────────────────────────────────

def save_paper_position(market_id: str, market_question: str, signal: str,
                        intended_size: float, actual_size: float, slippage_applied: float,
                        fee_applied: float, actual_position_cost: float,
                        entry_price: float, actual_entry_price: float,
                        confidence: float, edge: float, reasoning: str,
                        primary_signal: str, conflicting_signals: str,
                        intelligence_signals_snapshot: str, composite_signal_score: float,
                        prompt_version_id: int, expected_resolution_date: str) -> int:
    """Insert a new paper position. Returns row id, 0 on failure."""
    from datetime import datetime, timezone
    try:
        conn = _get_connection()
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute("""
            INSERT INTO paper_positions (
                market_id, market_question, signal,
                intended_size, actual_size, slippage_applied, fee_applied, actual_position_cost,
                entry_price, actual_entry_price,
                confidence, edge, reasoning, primary_signal, conflicting_signals,
                intelligence_signals_snapshot, composite_signal_score, prompt_version_id,
                opened_at, expected_resolution_date, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')
        """, (
            market_id, market_question, signal,
            intended_size, actual_size, slippage_applied, fee_applied, actual_position_cost,
            entry_price, actual_entry_price,
            confidence, edge, reasoning, primary_signal, conflicting_signals,
            intelligence_signals_snapshot, composite_signal_score, prompt_version_id,
            now, expected_resolution_date,
        ))
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id
    except Exception as e:
        logger.error("Failed to save paper position for {}: {}", market_id, e)
        return 0


def update_paper_position_resolution(position_id: int, actual_outcome: str, was_correct: bool,
                                      gross_pnl: float, net_pnl: float, resolution_fee: float,
                                      status: str) -> bool:
    """Close a paper position with its resolution outcome. Returns True on success."""
    from datetime import datetime, timezone
    try:
        conn = _get_connection()
        now = datetime.now(timezone.utc).isoformat()
        # Calculate hold time
        row = conn.execute(
            "SELECT opened_at FROM paper_positions WHERE id = ?", (position_id,)
        ).fetchone()
        hold_hours = 0.0
        if row and row["opened_at"]:
            try:
                opened = datetime.fromisoformat(row["opened_at"])
                hold_hours = (datetime.now(timezone.utc) - opened.replace(tzinfo=timezone.utc)
                              if opened.tzinfo is None else
                              datetime.now(timezone.utc) - opened).total_seconds() / 3600
            except Exception:
                pass
        conn.execute("""
            UPDATE paper_positions SET
                resolved_at = ?, actual_outcome = ?, was_correct = ?,
                gross_pnl = ?, net_pnl = ?, resolution_fee_applied = ?,
                hold_time_hours = ?, status = ?
            WHERE id = ?
        """, (now, actual_outcome, 1 if was_correct else 0,
              gross_pnl, net_pnl, resolution_fee, round(hold_hours, 2), status, position_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to update paper position {}: {}", position_id, e)
        return False


def get_open_paper_positions() -> list:
    """Return all OPEN paper positions."""
    try:
        conn = _get_connection()
        rows = conn.execute(
            "SELECT * FROM paper_positions WHERE status = 'OPEN' ORDER BY opened_at ASC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Failed to get open paper positions: {}", e)
        return []


def get_all_paper_positions(status: str = None) -> list:
    """Return paper positions filtered by status (or all if status=None)."""
    try:
        conn = _get_connection()
        if status:
            rows = conn.execute(
                "SELECT * FROM paper_positions WHERE status = ? ORDER BY opened_at DESC",
                (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM paper_positions ORDER BY opened_at DESC"
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Failed to get paper positions: {}", e)
        return []


def get_paper_position_by_market(market_id: str) -> dict | None:
    """Return the most recent OPEN paper position for a given market_id, or None."""
    try:
        conn = _get_connection()
        row = conn.execute(
            "SELECT * FROM paper_positions WHERE market_id = ? AND status = 'OPEN' "
            "ORDER BY opened_at DESC LIMIT 1",
            (market_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to get paper position for market {}: {}", market_id, e)
        return None


def get_today_paper_trade_count() -> int:
    """Return number of paper positions opened today (UTC)."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        conn = _get_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM paper_positions WHERE date(opened_at) = ?", (today,)
        ).fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.error("Failed to get today's paper trade count: {}", e)
        return 0


def record_bankroll_event(timestamp: str, bankroll_amount: float, event_type: str,
                           event_id: int = None, note: str = None) -> bool:
    """Record a bankroll history event. Returns True on success."""
    try:
        conn = _get_connection()
        conn.execute("""
            INSERT INTO paper_bankroll_history (timestamp, bankroll_amount, event_type, event_id, note)
            VALUES (?, ?, ?, ?, ?)
        """, (timestamp, bankroll_amount, event_type, event_id, note))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to record bankroll event: {}", e)
        return False


def get_bankroll_history() -> list:
    """Return all bankroll history events ordered by time."""
    try:
        conn = _get_connection()
        rows = conn.execute(
            "SELECT * FROM paper_bankroll_history ORDER BY timestamp ASC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Failed to get bankroll history: {}", e)
        return []


def upsert_paper_daily_stats(date: str, **kwargs) -> bool:
    """Insert or update paper trading stats for a given date."""
    try:
        conn = _get_connection()
        # Build dynamic upsert
        fields = list(kwargs.keys())
        vals = list(kwargs.values())
        set_clause = ", ".join(f"{f} = excluded.{f}" for f in fields)
        placeholders = ", ".join("?" for _ in fields)
        conn.execute(f"""
            INSERT INTO paper_daily_stats (date, {', '.join(fields)})
            VALUES (?, {placeholders})
            ON CONFLICT(date) DO UPDATE SET {set_clause}
        """, [date] + vals)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to upsert paper daily stats for {}: {}", date, e)
        return False


def save_go_live_readiness(trades_completed: int, total_pnl: float, win_rate: float,
                            c1: bool, c2: bool, c3: bool, all_met: bool,
                            report_json: str) -> int:
    """Record a go-live readiness evaluation. Returns row id."""
    from datetime import datetime, timezone
    try:
        conn = _get_connection()
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute("""
            INSERT INTO go_live_readiness (
                evaluated_at, trades_completed, total_pnl, win_rate,
                criterion_1_met, criterion_2_met, criterion_3_met, all_criteria_met,
                readiness_report_json, notification_sent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (now, trades_completed, total_pnl, win_rate,
              1 if c1 else 0, 1 if c2 else 0, 1 if c3 else 0, 1 if all_met else 0,
              report_json))
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id
    except Exception as e:
        logger.error("Failed to save go-live readiness: {}", e)
        return 0


def get_last_readiness_notification_time() -> str | None:
    """Return the evaluated_at timestamp of the last notification that was sent, or None."""
    try:
        conn = _get_connection()
        row = conn.execute(
            "SELECT evaluated_at FROM go_live_readiness WHERE notification_sent = 1 "
            "ORDER BY evaluated_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return row["evaluated_at"] if row else None
    except Exception as e:
        logger.error("Failed to get last readiness notification time: {}", e)
        return None


def mark_readiness_notification_sent(row_id: int) -> bool:
    """Mark a readiness record as having sent the notification."""
    try:
        conn = _get_connection()
        conn.execute(
            "UPDATE go_live_readiness SET notification_sent = 1 WHERE id = ?", (row_id,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to mark readiness notification sent: {}", e)
        return False