"""Full pipeline: Fetch -> Filter -> Save -> Snapshot -> LLM Analyze -> Decision Engine -> Results."""

import sys
import os

# Force UTF-8 output so emojis render on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data.database import (
    initialize_db, save_market, save_snapshot,
    get_market_count, get_crypto_market_count, get_crypto_markets_only,
    save_llm_analysis, DB_PATH,
)
from execution.polymarket_client import _matches_crypto, parse_market, fetch_all_markets
from intelligence.market_scorer import analyze_market_with_llm
from decision.bet_engine import evaluate_bet
from decision.risk_manager import get_daily_stats, is_safe_to_bet
from config import MAX_BET_SIZE

# Max crypto markets to send to LLM per run (keeps run time reasonable)
LLM_ANALYSIS_CAP = 20


def main():
    print("=" * 70)
    print("  POLYMARKET BOT -- Phase 1 + 2 + 3 Pipeline")
    print("  Fetch -> Filter -> Save -> LLM Analyze -> Decision Engine -> Results")
    print("=" * 70)

    initialize_db()

    # ── Phase 1: Fetch markets (paginated, capped at 500) ──────────────────
    print("\n  [Phase 1] Fetching active markets from Gamma API (up to 500)...")
    markets = fetch_all_markets(max_markets=500)
    print(f"  [OK] Received {len(markets)} active markets")

    crypto_raw = [m for m in markets if _matches_crypto(m)]
    print(f"  [KEY] Crypto-flagged (strict filter): {len(crypto_raw)}")

    saved = 0
    snapshots = 0
    for raw in crypto_raw:
        parsed = parse_market(raw)
        if save_market(parsed):
            saved += 1
        if save_snapshot(parsed["id"], parsed["yes_price"], parsed["no_price"], parsed["volume"]):
            snapshots += 1

    print(f"  [DB] Saved {saved} crypto markets  |  Snapshots written: {snapshots}")

    total_db = get_market_count()
    crypto_db = get_crypto_market_count()
    print(f"  [STATS] Database: {total_db} total, {crypto_db} crypto")

    # ── Phase 2: LLM Analysis (top N by volume from this run) ─────────────
    # Sort this run's crypto markets by volume desc, cap at LLM_ANALYSIS_CAP
    this_run_ids = {str(m.get("id", "")) for m in crypto_raw}
    all_crypto_db = get_crypto_markets_only()
    this_run_markets = [m for m in all_crypto_db if str(m["id"]) in this_run_ids]
    this_run_markets.sort(key=lambda m: float(m.get("volume") or 0), reverse=True)
    analysis_batch = this_run_markets[:LLM_ANALYSIS_CAP]

    print(f"\n  [Phase 2] Analyzing top {len(analysis_batch)} crypto markets (by volume) with LM Studio...")

    if not analysis_batch:
        print("  [WARN] No crypto markets to analyze.")
        print("\n" + "=" * 70)
        print("  Done.")
        print("=" * 70)
        return

    llm_results = []
    for i, m in enumerate(analysis_batch, 1):
        question = m.get("question", "Unknown")
        print(f"  [{i}/{len(analysis_batch)}] {question[:62]}...")
        analysis = analyze_market_with_llm(m)
        save_llm_analysis(m["id"], analysis["signal"], analysis["confidence"],
                          analysis["reasoning"], analysis["edge"])
        llm_results.append((m, analysis))
        print(f"         -> Signal: {analysis['signal']}  "
              f"Confidence: {analysis['confidence']:.2f}  "
              f"Edge: {analysis['edge']:.4f}")

    # ── Phase 3: Decision Engine ───────────────────────────────────────────
    print(f"\n  [Phase 3] Running decision engine on {len(llm_results)} markets...")

    recommendations = []
    for market, analysis in llm_results:
        bet = evaluate_bet(market, analysis)
        safe = is_safe_to_bet(DB_PATH, bet["recommended_size"]) if bet["should_bet"] else False
        verdict = "PLACE BET" if (bet["should_bet"] and safe) else "HOLD"
        recommendations.append((market, analysis, bet, safe, verdict))

    # ── Phase 2 Results Table ──────────────────────────────────────────────
    print(f"\n  {'=' * 70}")
    print(f"  {'LLM ANALYSIS RESULTS':^70}")
    print(f"  {'=' * 70}")
    print(f"  {'#':<3} {'Market':<42} {'Signal':<9} {'Conf':<6} {'Edge'}")
    print(f"  {'---':<3} {'------':<42} {'------':<9} {'----':<6} {'----'}")
    for i, (m, analysis, _, __, ___) in enumerate(recommendations, 1):
        q = m.get("question", "")[:40]
        sig = analysis["signal"]
        if sig == "BET_YES":
            sig_col = f"\033[92m{sig}\033[0m"
        elif sig == "BET_NO":
            sig_col = f"\033[91m{sig}\033[0m"
        else:
            sig_col = f"\033[93m{sig}\033[0m"
        conf = f"{analysis['confidence']:.2f}"
        edge = f"{analysis['edge']:.4f}"
        print(f"  {i:<3} {q:<42} {sig_col:<9} {conf:<6} {edge}")

    # ── Phase 3 Recommendations Table ─────────────────────────────────────
    print(f"\n  {'=' * 70}")
    print(f"  {'PHASE 3 -- BET RECOMMENDATIONS':^70}")
    print(f"  {'=' * 70}")
    print(f"  {'#':<3} {'Market':<34} {'Signal':<9} {'Conf':<5} {'Edge':<7} {'Size $':<8} {'Safe':<5} {'Verdict'}")
    print(f"  {'---':<3} {'------':<34} {'------':<9} {'----':<5} {'----':<7} {'------':<8} {'----':<5} {'-------'}")

    place_count = 0
    total_exposure = 0.0
    for i, (market, analysis, bet, safe, verdict) in enumerate(recommendations, 1):
        q = market.get("question", "")[:32]
        sig = analysis["signal"]
        conf = f"{analysis['confidence']:.2f}"
        edge = f"{analysis['edge']:.4f}"
        size = f"${bet['recommended_size']:.4f}"
        safe_str = "YES" if safe else "NO"

        if verdict == "PLACE BET":
            verdict_col = f"\033[92m{verdict}\033[0m"
            place_count += 1
            total_exposure += bet["recommended_size"]
        else:
            verdict_col = f"\033[93mHOLD\033[0m"

        print(f"  {i:<3} {q:<34} {sig:<9} {conf:<5} {edge:<7} {size:<8} {safe_str:<5} {verdict_col}")

    print(f"  {'─' * 70}")

    # ── Daily Stats Summary ────────────────────────────────────────────────
    stats = get_daily_stats(DB_PATH)
    daily_limit = MAX_BET_SIZE * 10
    remaining_capacity = max(0.0, daily_limit - stats["total_wagered_today"] - total_exposure)
    limit_open = remaining_capacity > 0 and stats["net_pnl_today"] >= -5.00

    print(f"\n  DAILY STATS SUMMARY")
    print(f"  {'─' * 50}")
    print(f"  Bets placed today (prior runs)  : {stats['total_bets_today']}")
    print(f"  Wagered today (prior runs)      : ${stats['total_wagered_today']:.4f}")
    print(f"  Net P&L today                   : ${stats['net_pnl_today']:.4f}")
    print(f"  Daily wager limit               : ${daily_limit:.2f}")
    print(f"  {'─' * 50}")
    print(f"  Bets recommended this run       : {place_count}")
    print(f"  Total exposure this run         : ${total_exposure:.4f}")
    print(f"  Remaining daily capacity        : ${remaining_capacity:.4f}")
    print(f"  Daily limits open               : {'YES' if limit_open else 'NO -- daily limit or stop-loss hit'}")
    print(f"  {'─' * 50}")

    print(f"\n  [DONE] Phase 3 complete -- {len(recommendations)} markets evaluated, {place_count} bets recommended")
    print(f"  LLM call log : logs/llm_calls.log")
    print(f"  Pipeline log : logs/pipeline.log")
    print("\n" + "=" * 70)
    print("  Done. Exiting cleanly.")
    print("=" * 70)


if __name__ == "__main__":
    main()
