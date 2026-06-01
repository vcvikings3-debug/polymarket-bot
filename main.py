"""One-shot market fetch + LLM analysis — pulls active markets, filters for crypto, scores with local LLM, prints results."""

import sys
import os
import json
import requests
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data.database import initialize_db, save_market, get_market_count, get_crypto_market_count, get_crypto_markets_only, save_llm_analysis
from execution.polymarket_client import _matches_crypto, parse_market
from intelligence.market_scorer import analyze_market_with_llm

GAMMA_API = "https://gamma-api.polymarket.com"

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "pipeline.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)


def main():
    print("=" * 70)
    print("  POLYMARKET BOT — Phase 1 + Phase 2 Pipeline")
    print("  Fetch → Filter → Save → LLM Analyze → Results")
    print("=" * 70)

    # Init DB
    initialize_db()

    # ── Phase 1: Fetch one page of 100 active markets ──
    print("\n  [Phase 1] Fetching https://gamma-api.polymarket.com/markets?active=true&limit=100 ...")
    try:
        resp = requests.get(f"{GAMMA_API}/markets", params={"active": "true", "limit": 100}, timeout=15)
        resp.raise_for_status()
        markets = resp.json()
    except Exception as e:
        print(f"  ❌ Fetch failed: {e}")
        sys.exit(1)

    print(f"  ✅ Received {len(markets)} active markets")

    # Filter crypto with strict keywords + blocklist
    crypto_raw = [m for m in markets if _matches_crypto(m)]
    print(f"  🔑 Crypto-flagged (strict filter): {len(crypto_raw)}")

    # Save to DB
    saved = 0
    for raw in crypto_raw:
        parsed = parse_market(raw)
        if save_market(parsed):
            saved += 1

    print(f"  💾 Saved {saved} crypto markets to data/polymarket.db")

    total_db = get_market_count()
    crypto_db = get_crypto_market_count()
    print(f"  📊 Database: {total_db} total, {crypto_db} crypto")

    # ── Phase 2: LLM Analysis ──
    print(f"\n  [Phase 2] Analyzing {crypto_db} crypto markets with Qwen2.5 14B via LM Studio...")
    print(f"  URL: http://127.0.0.1:1234/api/v1/chat/completions")
    print(f"  Model: qwen2.5-14b-instruct\n")

    crypto_markets = get_crypto_markets_only()

    if not crypto_markets:
        print("  ⚠️  No crypto markets found in database to analyze.")
        print("\n" + "=" * 70)
        print("  Done.")
        print("=" * 70)
        return

    results = []
    for i, m in enumerate(crypto_markets, 1):
        question = m.get("question", "Unknown")
        print(f"  [{i}/{len(crypto_markets)}] Analyzing: {question[:60]}...")
        analysis = analyze_market_with_llm(m)
        save_llm_analysis(
            m["id"],
            analysis["signal"],
            analysis["confidence"],
            analysis["reasoning"],
            analysis["edge"],
        )
        results.append((question, analysis))
        print(f"         → Signal: {analysis['signal']}  Confidence: {analysis['confidence']:.2f}  Edge: {analysis['edge']:.4f}")

    # ── Print Results Table ──
    print(f"\n  {'=' * 70}")
    print(f"  {'LLM ANALYSIS RESULTS':^70}")
    print(f"  {'=' * 70}")
    print(f"  {'#':<3} {'Market':<60} {'Signal':<10} {'Conf':<6} {'Edge':<8} {'Reasoning'}")
    print(f"  {'─' * 3} {'─' * 60} {'─' * 10} {'─' * 6} {'─' * 8} {'─' * 30}")

    for i, (question, analysis) in enumerate(results, 1):
        q = question[:58]
        signal = analysis["signal"]
        conf = f"{analysis['confidence']:.2f}"
        edge = f"{analysis['edge']:.4f}"
        reasoning = analysis["reasoning"][:28]

        # Color signal
        if signal == "BET_YES":
            signal_display = f"\033[92m{signal}\033[0m"  # green
        elif signal == "BET_NO":
            signal_display = f"\033[91m{signal}\033[0m"  # red
        else:
            signal_display = f"\033[93m{signal}\033[0m"  # yellow

        print(f"  {i:<3} {q:<60} {signal_display:<10} {conf:<6} {edge:<8} {reasoning}")

    print(f"  {'─' * 3} {'─' * 60} {'─' * 10} {'─' * 6} {'─' * 8} {'─' * 30}")
    print(f"\n  ✅ Phase 2 complete — {len(results)} markets analyzed by LLM")
    print(f"  📝 Full LLM call log: logs/llm_calls.log")
    print(f"  📝 Pipeline log: logs/pipeline.log")
    print("\n" + "=" * 70)
    print("  Done. Exiting cleanly.")
    print("=" * 70)


if __name__ == "__main__":
    main()