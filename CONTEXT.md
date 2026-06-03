# CONTEXT.md — Polymarket Bot Project State

This file exists for session continuity. Any AI assistant or collaborator picking up this project cold should read this first.

---

## Project Name & Purpose

**polymarket-bot** — An autonomous prediction market betting bot targeting crypto markets on Polymarket. The bot fetches active markets from the Polymarket Gamma API, filters for crypto-only opportunities, sends each market to a local LLM for signal generation, runs a Kelly criterion decision engine to size bets, and (in Phase 4) will place live bets via the Polymarket CLOB API using a USDC wallet on Polygon.

---

## Collaborators

| Name    | GitHub              | Role                             |
|---------|---------------------|----------------------------------|
| Cameron | `vcvikings3-debug`  | Primary developer, project owner |
| Coos    | `coosara2007`       | Collaborator                     |
| Terry   | TBD                 | Collaborator (GitHub account pending creation) |

All collaborators use **DeepSeek V3 + Cline + local LLM** for development assistance.

---

## Full Tech Stack

| Layer             | Technology                                              |
|-------------------|---------------------------------------------------------|
| Language          | Python 3.12                                             |
| Database          | SQLite (via `data/polymarket.db`)                       |
| Web framework     | Flask (planned for dashboard — not yet built)           |
| Local LLM         | Qwen2.5 14B Instruct via LM Studio                      |
| LLM endpoint      | `http://127.0.0.1:1234/v1/chat/completions`             |
| LLM API format    | OpenAI-compatible (chat completions)                    |
| Market data API   | Polymarket Gamma API (`https://gamma-api.polymarket.com`) |
| Order execution   | Polymarket CLOB API (Phase 4 — not yet wired)           |
| Blockchain        | Polygon (MATIC)                                         |
| Settlement token  | USDC on Polygon                                         |
| Logging           | `loguru`                                                |
| Config management | `python-dotenv` + `config.py`                           |

---

## Phase Roadmap

| Phase | Status      | Description                                              |
|-------|-------------|----------------------------------------------------------|
| 0     | Complete    | Project scaffold — directory structure, config, `.env`   |
| 1     | Complete    | Gamma API connected, crypto filter, SQLite storage       |
| 2     | Complete    | LM Studio wired, LLM analysis, BET_YES/BET_NO/SKIP signals saved to DB |
| 2.5   | Complete    | Deep Intelligence Layer — news, on-chain, sentiment, self-learning, base rates |
| 2.6   | **Complete** | Paper Trading Engine — simulation, resolution monitoring, portfolio management, go-live readiness |
| 3     | Complete    | Decision engine — Kelly sizing, risk manager, recommendations table |
| 4     | Next        | CLOB API auth, wallet connection, USDC balance check, live bet placement |
| 5     | Planned     | Flask dashboard — live view of markets, signals, P&L     |
| 6     | Planned     | Scheduler — run pipeline on a cron/loop automatically    |

---

## What Each Completed Phase Built

### Phase 0 — Scaffold
- Directory structure: `data/`, `execution/`, `intelligence/`, `decision/`, `dashboard/`, `logs/`, `tests/`
- `config.py` — central env var loader
- `.env` / `.env.example` — all secrets and tuning parameters
- `requirements.txt`

### Phase 1 — Market Data Pipeline
- `execution/polymarket_client.py` — Gamma API fetch with full pagination (`fetch_all_markets(max_markets=500)`), strict crypto keyword filter (`_matches_crypto()`), blocklist, and market normalizer (`parse_market()`)
- `data/database.py` — SQLite handler; `markets` table, `market_snapshots` table (price history), `bets` table (for Phase 4 tracking); WAL mode; migration helper
- `main.py` Phase 1 block — fetch → filter → save → snapshot

### Phase 2 — LLM Intelligence Layer
- `intelligence/market_scorer.py` — sends each market to Qwen2.5 via LM Studio; builds structured prompt with YES/NO prices, liquidity, days-to-resolution; parses JSON response (`BET_YES` / `BET_NO` / `SKIP` + confidence + reasoning + edge); robust fallback parsing; logs all calls to `logs/llm_calls.log`
- `data/database.py` — `save_llm_analysis()` writes signal back to the `markets` row

### Phase 2.5 — Deep Intelligence Layer (NEW)
- `intelligence/news_analyzer.py` — multi-source news (RSS: CoinDesk/Decrypt/CoinTelegraph, CoinGecko trending, PRAW Reddit optional), velocity scoring, narrative bias detection, keyword extraction
- `intelligence/onchain_analyzer.py` — Binance perpetual funding rates, CoinGecko exchange flows, DeFiLlama TVL trend, mempool.space congestion, whale activity proxy, RSI/MACD/Bollinger technicals, Fear/Greed proxy. In-memory 5-minute TTL cache
- `intelligence/sentiment_engine.py` — VADER Reddit sentiment (when PRAW configured), pytrends Google Trends, crowd psychology detection (FOMO/PANIC/EUPHORIA/CAPITULATION/COMPLACENCY), smart money divergence (-1 to 1)
- `intelligence/historical_performance.py` — prediction recording, resolution tracking, accuracy by category, confidence calibration, bias detection (RECENCY/NARRATIVE/CONFIRMATION), `generate_strategy_adjustment()` (THE CORE), `auto_evolve_prompt()`
- `intelligence/prompt_evolution_engine.py` — weekly evolution cycle, prompt versioning, A/B testing with statistical significance (scipy z-test), `generate_evolved_prompt()` via LM Studio
- `intelligence/base_rate_calculator.py` — queries Gamma API for resolved markets by type, Bayesian prior formula (40% history + 60% current price), 13 question types
- `intelligence/context_builder.py` — parallel orchestration (4 modules concurrent + sentiment after onchain), composite signal score (6-component weighted sum), `format_llm_brief()` → structured intelligence brief replacing bare prompt
- `intelligence/market_scorer.py` — fully rewired to use intelligence layer; falls back to simple prompt on failure; records all predictions to DB; respects strategy adjustments (avoided categories, cooling periods)
- `data/database.py` — added 4 new tables: `predictions`, `prompt_versions`, `signal_performance`, `strategy_state`; added `record_bet()`, full prediction tracking helpers, prompt versioning helpers, strategy state KV store
- `config.py` — added `POLYGON_RPC_URL`, `DRY_RUN`, `CRYPTOPANIC_AUTH_TOKEN`, `ETHERSCAN_API_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`; `DISCORD_UPDATES_WEBHOOK` removed (GitHub native webhook handles repo notifications)
- `requirements.txt` — added feedparser, praw, pytrends, nltk, vaderSentiment, requests-cache, scipy
- `main.py` — added `_setup_weekly_evolution()` (schedule every Sunday 03:00)

### Phase 2.6 — Paper Trading Engine (NEW)
- `paper_trading/paper_engine.py` — `PaperTradingEngine` class: `simulate_bet()` (slippage simulation + fee deduction + pre-flight portfolio checks), `resolve_paper_position()` (closes winning/losing positions, feeds self-learning system), `check_go_live_readiness()` (3-criteria evaluator, Discord notification), `get_portfolio_state()`, `scan_for_resolutions()`
- `paper_trading/portfolio_manager.py` — correlation groups (BTC/ETH/SOL/DeFi/Macro), diversification checks (max 3 per correlation group, max 10 open, 80% exposure cap), Monte Carlo VaR (1000 sims)
- `paper_trading/resolution_monitor.py` — `scan_all_open_positions()` (Gamma API resolution checking each cycle), `get_market_resolution_status()`, void handling for expired markets, DISPUTED status tracking
- `paper_trading/performance_analyzer.py` — Sharpe ratio, max drawdown, Kelly accuracy, edge capture rate, calibration score by confidence bucket, signal attribution, category edge, time patterns, streaks, `generate_full_performance_report()`
- `paper_trading/report_generator.py` — `generate_daily_report()` (embeds: bankroll, performance, calibration, go-live progress), `generate_trade_notification()` (entry/exit embeds to updates channel), `generate_readiness_report()` (full readiness report with recommended live settings), `generate_weekly_summary()`
- `data/database.py` — added 4 new tables: `paper_positions`, `paper_bankroll_history`, `paper_daily_stats`, `go_live_readiness` + 16 new helper functions
- `utils/monitoring.py` — added `DISCORD_COLOR_YELLOW`, `send_bet_alert` alias for `send_discord_alert`; both point to `DISCORD_BETS_WEBHOOK` only
- `intelligence/market_scorer.py` — extended `_call_llm()` to extract `primary_signal`, `conflicting_signals`, `bias_check` from LLM JSON; `analyze_market_with_llm()` now returns `composite_signal` and `prompt_version_id` so paper engine can store them
- `config.py` — added `DISCORD_UPDATES_WEBHOOK`, `PAPER_TRADING`, `LIVE_TRADING`, `PAPER_STARTING_BANKROLL`, `PAPER_MAX_DAILY_TRADES`, `PAPER_MAX_OPEN_POSITIONS`, `PAPER_MAX_CORRELATION_EXPOSURE`
- `main.py` — Phase 2.6 block: resolution scan → simulate paper bets for every PLACE BET verdict → portfolio state summary; daily report and weekly summary scheduled

**Discord channel strategy (post-restructure):**
- `DISCORD_BETS_WEBHOOK` → `#github-bets` — receives ONLY: paper trade opened, paper trade closed, daily performance report, go-live readiness report, future live bet placed
- `DISCORD_UPDATES_WEBHOOK` — retired from bot; GitHub's native webhook handles repo notifications
- Pipeline errors, crashes, Sentry events → Sentry + loguru logs only, no Discord
- Prompt evolution cycle results → loguru logs only, no Discord

**Go-live criteria (all three must be met simultaneously):**
1. 50+ completed paper trades (entered AND resolved)
2. Positive total PnL after all fees and slippage
3. Win rate ≥ 52%
When met: bot sends READINESS REPORT to Discord updates channel and sets `go_live_flag=READY_AWAITING_APPROVAL` in strategy_state. Cameron must manually set `LIVE_TRADING=true` in `.env`.

### Phase 3 — Decision Engine
- `decision/bet_engine.py` — `evaluate_bet(market, llm_result)`:
  - Kelly fraction = `(edge × confidence) / (1 − confidence)`, capped at 0.25
  - `recommended_size = kelly_fraction × MAX_BANKROLL_RISK`, capped at `MAX_BET_SIZE`
  - Returns `should_bet=True` only if: confidence >= `MIN_CONFIDENCE`, signal != SKIP, edge > 5%, size >= $0.25
- `decision/risk_manager.py` — `get_daily_stats(db_path)` queries `bets` table for today's totals; `is_safe_to_bet()` enforces daily wager cap (`MAX_BET_SIZE × 10`) and $5 stop-loss
- `main.py` Phase 3 block — full recommendations table + daily stats summary

---

## What Phase 4 Needs to Build

Phase 4 is **live bet placement** via the Polymarket CLOB API.

Tasks:
1. **CLOB API authentication** — sign requests using `POLYMARKET_PRIVATE_KEY` (EIP-712 or API key scheme; check current Polymarket docs)
2. **Wallet connection** — connect `WALLET_ADDRESS` on Polygon, verify it is the correct signing address
3. **USDC balance check** — query on-chain USDC balance before placing any bet; refuse to bet if balance < `recommended_size`
4. **Order construction** — build a limit or market order for the correct `conditionId` / `tokenId` from the market data
5. **Order placement** — POST to CLOB API, handle response, log order ID
6. **Bet recording** — write placed bet to `bets` table with `outcome='PENDING'`
7. **Wire into main.py** — after Phase 3 produces `PLACE BET` verdicts, call the execution layer for each

Key files to create:
- `execution/clob_client.py` — CLOB API auth + order placement
- `execution/wallet.py` — balance check, address validation

**Do not touch Phase 1, 2, or 3 code unless fixing a confirmed bug.**

---

## Key File Map

```
polymarket-bot/
├── main.py                         # Pipeline entry point — runs all phases sequentially
├── config.py                       # Loads all env vars; exports MIN_CONFIDENCE, MAX_BET_SIZE, MAX_BANKROLL_RISK, etc.
├── .env                            # Live secrets (not committed)
├── .env.example                    # Template — all required keys with placeholder values
├── CONTEXT.md                      # This file — project state for session continuity
│
├── data/
│   ├── database.py                 # SQLite handler — initialize_db, save_market, save_snapshot, save_llm_analysis, get_daily_stats
│   └── polymarket.db               # SQLite database (generated at runtime)
│
├── execution/
│   └── polymarket_client.py        # Gamma API fetch (paginated), crypto filter, market parser
│                                   # CLOB client does NOT exist yet — Phase 4 work
│
├── intelligence/
│   └── market_scorer.py            # LM Studio integration — sends markets to Qwen2.5, parses JSON signals
│
├── decision/
│   ├── __init__.py
│   ├── bet_engine.py               # evaluate_bet() — Kelly criterion sizing, threshold checks
│   └── risk_manager.py             # get_daily_stats(), is_safe_to_bet() — daily limits + stop-loss
│
├── dashboard/                      # Empty — Flask web UI planned for Phase 5
├── logs/
│   ├── pipeline.log                # General pipeline log (loguru)
│   └── llm_calls.log               # All LLM requests and responses
└── tests/                          # Empty — tests planned
```

---

## Current .env Variables

All of these must be present in `.env` at the project root. See `.env.example` for the template.

| Variable               | Default          | Description                                         |
|------------------------|------------------|-----------------------------------------------------|
| `POLYMARKET_API_KEY`   | —                | Polymarket API key (needed for Phase 4 CLOB auth)   |
| `POLYMARKET_PRIVATE_KEY` | —              | Wallet private key for signing orders (Phase 4)     |
| `WALLET_ADDRESS`       | —                | Your Polygon wallet address                         |
| `OLLAMA_HOST`          | `http://localhost:11434` | Ollama host (not currently used — LM Studio is primary) |
| `OLLAMA_MODEL`         | `qwen2.5:14b`    | Model name sent to LM Studio                        |
| `LM_STUDIO_HOST`       | `http://localhost:1234` | LM Studio base URL                           |
| `MIN_CONFIDENCE`       | `0.65`           | Minimum LLM confidence to consider betting          |
| `MAX_BET_SIZE`         | `1.00`           | Absolute maximum per-bet size in USD                |
| `MAX_BANKROLL_RISK`    | `0.20`           | Kelly multiplier — raise this to get larger bet sizes |
| `DB_PATH`              | `data/polymarket.db` | SQLite database path                            |

**Note on bet sizing:** With default `MAX_BANKROLL_RISK=0.20`, Kelly-sized bets come out ~$0.03–$0.05, which is below the $0.25 floor — so 0 bets are recommended. This is intentional for safe paper-trading. To open real positions, raise `MAX_BANKROLL_RISK` in `.env` (e.g., `5.00` for a $5 risk pool).

---

## Known Issues / Notes for Phase 4

- `execution/polymarket_client.py` docstring says "placing bets" but contains zero CLOB code — this is Phase 4 work
- The `bets` table exists in the schema and is queried by `risk_manager.py`, but nothing writes to it yet — Phase 4 will populate it
- `market_snapshots` is now being written on every run — price history is accumulating correctly
- LM Studio must be running locally with Qwen2.5 14B loaded before `main.py` is invoked
- The Gamma API returns markets sorted by some internal relevance order; the top 500 by offset are used, then filtered to crypto, then top 20 by volume are sent to LLM

---

## Collaboration Rules

These rules apply to ALL AI assistants (Claude, Cline, DeepSeek) working on this project.

### On Every Commit
Before running git commit, the AI must:
1. Update CONTEXT.md with:
   - What was built or changed in this session
   - Current status of every phase (Complete / In Progress / Pending)
   - Last confirmed working commit hash
   - Any new files created and their purpose
   - Any known issues or bugs discovered
2. Update the "Next Steps" section with:
   - What needs to be built next
   - Who is assigned to it (Cameron or Coos)
   - Any blockers or dependencies
3. Never commit without updating CONTEXT.md first — no exceptions

### Branch Rules
- Never push directly to main
- Always create a branch named after what you are building
- Always open a pull request and tag Cameron for review
- Never merge without Cameron's approval

### .env Rules
- Never commit .env under any circumstances
- Never log or print real API keys, private keys, or webhook URLs
- Always use .env.example for documenting new variables

### Code Rules
- Never touch Phase 1, 2, or 3 code unless fixing a confirmed bug
- Always add error handling and loguru logging to new functions
- Always test before committing
- DRY_RUN=true must be the default for any betting code

---

## Next Steps

| Task | Assigned To | Status | Blockers |
|------|-------------|--------|----------|
| Monitor paper trading — accumulate 50 resolved trades | Cameron | In Progress | Markets need to close (days/weeks) |
| Terry — create GitHub account and send username to Cameron | Terry | Pending | Terry to action |
| Phase 4 — execution/wallet.py | Coos | Pending | Coos environment setup |
| Phase 4 — execution/clob_client.py | Coos | Pending | Coos environment setup |
| Phase 4 — wire into main.py | Coos | Pending | wallet.py and clob_client.py complete |
| Phase 4 — live test | Cameron | Pending | Phase 2.6 go-live criteria met + Polymarket credentials + USDC |
| Phase 5 — Flask dashboard | Cameron + Coos | Pending | Phase 4 complete |
| Get CryptoPanic API token (free registration) | Cameron | Optional | Token needed for CryptoPanic news source |
| Configure REDDIT_CLIENT_ID/SECRET for PRAW | Cameron | Optional | Enables Reddit sentiment scoring |
| Switch to Ollama from LM Studio | Cameron | Pending | When ready for 24/7 headless |

---

## Last Confirmed Working Commit

```
a48d194 — test: verify github-updates Discord webhook
```

Last full pipeline run (2026-06-02):
- All phases confirmed working: Phase 1 → 2 → 2.5 → 2.6 → 3
- 500 markets fetched, 45 crypto-flagged, intelligence briefs built for all 20 LLM markets
- Confidence scores varied (0.35–0.95) — LLM discriminating, not anchoring
- Paper trading: daily limit and duplicate-position checks working correctly
- Discord daily report manually tested — embed fired to #github-bets confirmed
- GitHub-updates Discord webhook verified via README.md test commit
- Portfolio state: $7.31 bankroll, 3 open positions, 2 closed, 50% win rate, +$0.37 net PnL

## Known Permanent Limitations (graceful degradation)
- Binance funding rate API: 451 geo-blocked — returns NEUTRAL, no fix needed
- CryptoPanic: `auth_token=free` returns 404 — register at cryptopanic.com for real token
- CoinGecko: 429 rate limits on rapid multi-coin runs — 5-min TTL cache mitigates
- Google Trends pytrends: 429 on rapid calls — gracefully skipped per market
- Reddit/PRAW: requires REDDIT_CLIENT_ID/SECRET in .env to activate
