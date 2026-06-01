"""Main orchestrator that runs the full bot pipeline on a schedule."""

import sys
import os
import time
from datetime import datetime
from loguru import logger

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.database import initialize_db, save_market, get_market_count, get_crypto_market_count
from execution.polymarket_client import fetch_crypto_markets, parse_market, GAMMA_API_BASE
from intelligence.market_scorer import score_market, get_top_markets
from dashboard.terminal_display import run_dashboard, build_table, build_header

# Configure logging
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "pipeline.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logger.remove()  # Remove default handler
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{message}</cyan>")
logger.add(LOG_PATH, level="DEBUG", rotation="10 MB", format="{time} | {level:<8} | {message}")


def show_startup_banner():
    """Print the startup banner."""
    banner = f"""
╔══════════════════════════════════════════════════════════╗
║              POLYMARKET BOT — MARKET SCANNER              ║
╠══════════════════════════════════════════════════════════╣
║  Collaborators:  Cameron + coos                          ║
║  Model:          Qwen2.5 14B Instruct via LM Studio      ║
║  Phase:          Phase 1 — Market Scanner                ║
║  API:            {GAMMA_API_BASE:<43}║
╚══════════════════════════════════════════════════════════╝
"""
    print(banner)
    logger.info("Startup complete — Phase 1 Market Scanner initialized")


def run_fetch_pipeline() -> str:
    """Run one full fetch pipeline: fetch, parse, save, score. Returns timestamp string."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("=== Starting market fetch pipeline ===")

    try:
        # Fetch
        raw_crypto = fetch_crypto_markets()
        logger.info("Fetched {} raw crypto markets", len(raw_crypto))

        # Parse and save
        saved_count = 0
        for raw in raw_crypto:
            parsed = parse_market(raw)
            if save_market(parsed):
                saved_count += 1

        # Log results
        total = get_market_count()
        crypto = get_crypto_market_count()
        logger.info("Pipeline complete: {} total markets, {} crypto, {} saved this run",
                     total, crypto, saved_count)

        # Log top 3 markets
        top = get_top_markets(3)
        if top:
            logger.info("Top 3 markets by score:")
            for i, m in enumerate(top, 1):
                logger.info("  {}. {} — YES={:.2f} Score={:.4f} Vol=${:,.0f}",
                           i, m.get("question", "?")[:60],
                           float(m.get("yes_price", 0)),
                           float(m.get("score", 0)),
                           float(m.get("volume", 0)))

    except Exception as e:
        logger.error("Pipeline fetch error: {}", e)
        import traceback
        logger.error(traceback.format_exc())

    return now_str


def run_test_fetch():
    """Run a one-time test fetch to confirm the pipeline works before starting the loop."""
    print("\n" + "=" * 60)
    print("  TEST FETCH — Verifying Gamma API connection...")
    print("=" * 60 + "\n")

    from execution.polymarket_client import fetch_all_markets

    all_raw = fetch_all_markets()
    print(f"  Total active markets returned: {len(all_raw)}")

    # Count crypto
    from execution.polymarket_client import _matches_crypto
    crypto_raw = [m for m in all_raw if _matches_crypto(m)]
    print(f"  Crypto-flagged markets: {len(crypto_raw)}")

    # Parse and save a sample
    print(f"\n  Saving {len(crypto_raw)} crypto markets to database...\n")
    for raw in crypto_raw:
        parsed = parse_market(raw)
        save_market(parsed)

    # Score and show top 5
    top = get_top_markets(5)
    print("  TOP 5 CRYPTO MARKETS BY SCORE:")
    print(f"  {'#':<3} {'Market':<50} {'YES':<8} {'NO':<8} {'Volume':<12} {'Score':<8}")
    print("  " + "-" * 92)
    for i, m in enumerate(top, 1):
        print(f"  {i:<3} {m.get('question', '?')[:48]:<50} "
              f"{float(m.get('yes_price',0)):<8.2f} {float(m.get('no_price',0)):<8.2f} "
              f"${float(m.get('volume',0)):<9,.0f} {float(m.get('score',0)):<8.4f}")

    print("\n" + "=" * 60)
    print("  TEST FETCH COMPLETE — Pipeline is operational")
    print("=" * 60 + "\n")
    return len(all_raw), len(crypto_raw)


def main():
    """Main entry point — orchestrates the full pipeline."""
    # Show banner
    show_startup_banner()

    # Initialize database
    initialize_db()

    # Run test fetch first
    total_markets, crypto_markets = run_test_fetch()

    # If test fetch returned 0, try a raw debug request
    if total_markets == 0:
        print("\n  ⚠️  Test fetch returned 0 markets. Running debug check...\n")
        import requests
        try:
            debug_url = f"{GAMMA_API_BASE}/markets?active=true&limit=10"
            resp = requests.get(debug_url, timeout=15)
            print(f"  Debug URL: {debug_url}")
            print(f"  Status code: {resp.status_code}")
            print(f"  Response text (first 500 chars): {resp.text[:500]}")
            if resp.status_code == 200:
                data = resp.json()
                print(f"  Markets in response: {len(data)}")
        except Exception as e:
            print(f"  Debug request failed: {e}")

        print("\n  Attempting to re-run fetch after debug...\n")
        total_markets, crypto_markets = run_test_fetch()

    if total_markets == 0:
        print("\n  ❌ Pipeline still returning 0 markets after debug. Check API connectivity.")
        print("     Verify you have internet access and the Gamma API is reachable.")
        sys.exit(1)

    # Start main loop — run fetch every 5 minutes, dashboard handles display refresh separately
    print("  🟢 Pipeline verified. Starting live dashboard with 5-minute fetch loop...\n")
    time.sleep(2)

    last_fetch = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fetch_interval = 300  # 5 minutes

    import threading
    import time as time_module

    def fetch_loop():
        nonlocal last_fetch
        while True:
            time_module.sleep(fetch_interval)
            last_fetch = run_fetch_pipeline()

    # Start fetch loop in background thread
    fetch_thread = threading.Thread(target=fetch_loop, daemon=True)
    fetch_thread.start()

    # Run dashboard in main thread
    run_dashboard(last_fetch)


if __name__ == "__main__":
    main()