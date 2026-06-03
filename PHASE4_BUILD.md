# PHASE4_BUILD.md — Phase 4 Execution Layer Build Instructions

**Paste this entire file into your AI (Cline / DeepSeek) after completing COOS_ONBOARDING.md steps 1–4.**

---

## Before You Start

Read CONTEXT.md completely. At the end of every commit you make, update CONTEXT.md with what you built, the current phase status, and what needs to happen next. This is not optional — it is required on every single commit.

---

## Context

Phases 0–3 are complete and confirmed working on Cameron's machine. The pipeline fetches markets, scores them with a local LLM, runs the decision engine, and prints a recommendations table. No bets are placed yet — the execution layer does not exist.

**Your job is Phase 4: build the execution layer.**

---

## Important Note About Local Models

Cameron runs Qwen2.5 14B Instruct via LM Studio for high-quality signals. Your machine runs a smaller model (Qwen3 7B or Llama 3.1 8B Q4). **This is fine for Phase 4.** The LLM signals in production come from Cameron's machine. You are building the execution plumbing — the code that takes a confirmed `PLACE BET` verdict and executes it on Polymarket. Your local model is more than capable of writing and reasoning about this code.

---

## What You Are Building

Two new files and one update to an existing file:

| File | Status | Your job |
|------|--------|----------|
| `execution/wallet.py` | Does not exist | Create it |
| `execution/clob_client.py` | Does not exist | Create it |
| `main.py` | Exists — do not break it | Add Phase 4 block only |

---

## File 1: `execution/wallet.py`

Build a wallet connector for Polygon. Full spec:

- Load `WALLET_ADDRESS` from `config.py`
- Connect to Polygon mainnet via `web3.py` using a public RPC endpoint (use `https://polygon-rpc.com` as default — make it configurable via `POLYGON_RPC_URL` in `.env`)
- **USDC contract address on Polygon:** `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`
- Implement `check_balance() -> float` — queries on-chain USDC balance for `WALLET_ADDRESS`, returns balance as a human-readable float (USDC has 6 decimals on Polygon)
- Implement `is_sufficient(required_amount: float) -> bool` — returns True only if `check_balance()` >= `required_amount`
- Refuse to proceed and log an error if balance is below `required_amount` — never throw an unhandled exception
- Log every balance check to `logs/execution.log` with the address, required amount, and actual balance
- Handle `web3` connection errors gracefully — return `0.0` and log the error if the RPC is unreachable

Add to `.env.example`:
```
POLYGON_RPC_URL=https://polygon-rpc.com
```

Add to `config.py`:
```python
POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
```

---

## File 2: `execution/clob_client.py`

Build the Polymarket CLOB API client. Full spec:

### Authentication
- Load `POLYMARKET_API_KEY` and `POLYMARKET_PRIVATE_KEY` from `config.py`
- Authenticate using the Polymarket CLOB API key scheme
- Base URL: `https://clob.polymarket.com`

### Functions to implement

**`get_market_orderbook(condition_id: str) -> dict | None`**
- Fetch the current orderbook for a market by its condition ID
- Returns the raw orderbook dict or `None` on failure
- Log the request and response status

**`build_order(market: dict, side: str, size_usdc: float) -> dict | None`**
- Construct a market order for the given market
- `side` is `"YES"` or `"NO"` — map to the correct token ID from the market data
- `size_usdc` is the dollar amount to spend
- Returns a dict representing the order payload, or `None` on failure
- Never call this in DRY_RUN mode — just log what the order would have been

**`place_order(order: dict) -> str | None`**
- POST the order to the CLOB API
- Returns the order ID string on success, `None` on failure
- Must never be called when `DRY_RUN=true` — guard with an assertion at the top of the function
- Log every attempt with full order details and the API response

**`get_order_status(order_id: str) -> str | None`**
- Query the CLOB API for the status of a placed order
- Returns a status string (e.g., `"FILLED"`, `"PENDING"`, `"CANCELLED"`) or `None` on failure

### Logging
- All execution events log to `logs/execution.log` (create this log file via loguru, same pattern as `logs/llm_calls.log`)
- Log format: timestamp | function | market_question | side | size | result

### Error handling
- Every function returns `None` on failure — never raise unhandled exceptions
- Log the full error message on every failure
- The pipeline must not crash if the CLOB API is unreachable

---

## Update: `main.py` — Phase 4 Block

After Phase 3 produces recommendations (the `PLACE BET` / `HOLD` table), add a Phase 4 block. **Do not modify any existing Phase 1, 2, or 3 code.**

Logic:

```
For each recommendation where verdict == "PLACE BET":
    1. If DRY_RUN=true:
           log "DRY RUN: would place {side} bet of ${size} on {question}"
           send Discord alert with DRY RUN label (blue embed)
           skip to next recommendation

    2. Call wallet.is_sufficient(recommended_size)
       If insufficient:
           log error + send Discord alert "Insufficient USDC balance"
           record bet in bets table with outcome="FAILED"
           skip to next recommendation

    3. Call clob_client.build_order(market, side, size)
       If None: log error, record FAILED, skip

    4. Call clob_client.place_order(order)
       If None: log error, record FAILED, skip

    5. On success:
           record bet in bets table with outcome="PENDING", order_id stored
           send Discord alert "Order Placed" with order ID (green embed)
           log success
```

### Writing to the bets table

`data/database.py` already has a `bets` table with schema:
```
id, market_id, side, size, outcome, pnl, placed_at, resolved_at
```

Add a function `record_bet(market_id, side, size, outcome, order_id=None)` to `database.py` to insert a row. The `bets` table needs an `order_id` column — add it via the `_ensure_columns()` migration helper that already exists in `database.py`. Do not drop or recreate the table.

---

## DRY_RUN Mode

- Add `DRY_RUN=true` to `.env.example`
- Default is `true` — safe by default, never places real bets unless explicitly disabled
- When `DRY_RUN=true`: log every action, send Discord alerts tagged `[DRY RUN]`, skip all actual API calls
- When `DRY_RUN=false`: live execution — wallet checks and order placement happen for real

**Coos: always test with `DRY_RUN=true`. Do not set it to false. Cameron will run the first live test once your code is reviewed and merged.**

---

## Testing

1. Set `DRY_RUN=true` in `.env` (it should be the default)
2. Temporarily lower `MIN_CONFIDENCE=0.10` and `MAX_BANKROLL_RISK=5.00` in `.env` to force `PLACE BET` signals through (same technique Cameron used to test Discord alerts)
3. Run `python main.py`
4. Confirm Phase 4 output appears after the recommendations table
5. Confirm `logs/execution.log` is written
6. Confirm Discord alert fires with `[DRY RUN]` label
7. Confirm no real orders were placed (DRY_RUN mode)
8. Restore `MIN_CONFIDENCE=0.65` and `MAX_BANKROLL_RISK` to your preferred value after testing

Do not test with `DRY_RUN=false`. Do not test with real USDC. Cameron handles the first live test.

---

## Dependencies

Add to `requirements.txt` if not already present:

```
web3
```

`web3` is already listed in `requirements.txt` — verify before adding a duplicate.

---

## Branch and Git Workflow

```bash
# Create your branch
git checkout -b phase-4-execution

# Work on your code...

# Stage and commit when ready
git add execution/wallet.py execution/clob_client.py main.py data/database.py requirements.txt config.py .env.example
git commit -m "Phase 4 execution layer — CLOB client, wallet connector, DRY_RUN mode"
git push origin phase-4-execution
```

Then open a pull request on GitHub:
- Base branch: `main`
- Compare branch: `phase-4-execution`
- Title: `Phase 4 — CLOB execution layer, wallet connector, DRY_RUN mode`
- Tag Cameron (`vcvikings3-debug`) as reviewer
- **Do not merge without Cameron's approval**

---

## Definition of Done

Your Phase 4 is complete when all of the following are true:

- [ ] `execution/wallet.py` exists with `check_balance()` and `is_sufficient()` fully implemented
- [ ] `execution/clob_client.py` exists with all four functions fully implemented
- [ ] `main.py` has a Phase 4 block that runs after Phase 3 and respects DRY_RUN mode
- [ ] `database.py` has `record_bet()` and the `order_id` column migration
- [ ] `logs/execution.log` is written on every run
- [ ] Discord alerts fire for every PLACE BET verdict (DRY RUN labeled when DRY_RUN=true)
- [ ] `DRY_RUN=true` is the default in `.env.example`
- [ ] `python main.py` with `DRY_RUN=true` runs cleanly end to end — no crashes, no unhandled exceptions
- [ ] Pull request is open and tagged for Cameron's review
- [ ] `.env` is not committed
- [ ] Phase 1, 2, and 3 code is unchanged
