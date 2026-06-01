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
    return conn


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
            raw_json TEXT
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

    conn.commit()
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


def get_all_crypto_markets() -> list:
    """Return all markets flagged as crypto."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM markets WHERE is_crypto = 1 ORDER BY volume DESC")
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