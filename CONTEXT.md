# CONTEXT.md — Polymarket Bot Project State

This file exists for session continuity. Any AI assistant or collaborator picking up this project cold should read this first.

---

## Project Name & Purpose

**polymarket-bot** — An autonomous prediction market betting bot targeting crypto markets on Polymarket. The bot fetches active markets from the Polymarket Gamma API, filters for crypto-only opportunities, sends each market to a local LLM for signal generation, runs a Kelly criterion decision engine to size bets, and (in Phase 4) will place live bets via the Polymarket CLOB API using a USDC wallet on Polygon.

---

## Collaborators

| Name   | GitHub                | Role                        |
|--------|-----------------------|-----------------------------|
| Cameron | `vcvikings3-debug`   | Primary developer, project owner |
| Coos   | `coosara2007`         | Collaborator                |

Both collaborators use **DeepSeek V3 + Cline + local LLM** for development assistance.

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
| 3     | Complete    | Decision engine — Kelly sizing, risk manager, recommendations table |
| 4     | **Next**    | CLOB API auth, wallet connection, USDC balance check, live bet placement |
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

## Last Confirmed Working Commit

```
4c90f01 — Phase 3 complete -- decision engine, Kelly criterion sizing, risk manager
```

Pipeline confirmed working output (2026-06-01):
- 500 markets fetched, 45 crypto-flagged, 45 saved, 45 snapshots written
- 20 markets analyzed by LLM
- All signals valid (BET_YES / BET_NO)
- Decision engine running cleanly, daily stats table printing
- Clean exit confirmed
