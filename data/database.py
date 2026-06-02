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
            FOREIGN KEY (market_id) REFERENCES markets(id)
        )
    """)

    conn.commit()
    _ensure_columns(conn)
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