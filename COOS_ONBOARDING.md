# COOS_ONBOARDING.md — AI Onboarding Prompt

**Paste this entire file into your AI (Cline / DeepSeek) as your first message when starting a session on this project.**

---

## Who You Are Working With

You are assisting **Coos** (`coosara2007` on GitHub), a collaborator on the **polymarket-bot** project. The project owner is **Cameron** (`vcvikings3-debug`). Coos is joining mid-build to implement Phase 4 — live bet execution.

---

## Project Summary

**polymarket-bot** is an autonomous prediction market betting bot targeting crypto markets on Polymarket. It:

1. Fetches active markets from the Polymarket Gamma API (up to 500 per run)
2. Filters to crypto-only markets using a strict keyword list
3. Sends the top 20 by volume to a local LLM (LM Studio) for signal generation
4. Runs a Kelly criterion decision engine to size bets
5. (Phase 4 — your job) Places live bets via the Polymarket CLOB API using a USDC wallet on Polygon

Phases 0–3 are complete and confirmed working. Do not modify them.

---

## Coos's Local Setup

| Item | Value |
|------|-------|
| OS | Windows |
| Editor | VS Code |
| AI coding agent | Cline + DeepSeek V3 |
| Local LLM | LM Studio — Qwen3 7B **or** Llama 3.1 8B (Q4 version) |
| LLM endpoint | `http://127.0.0.1:1234` |
| Python | 3.12 |
| Git | Installed |
| GitHub username | `coosara2007` |

---

## Step 1 — Clone the Repo and Verify Your Environment

Run these commands in order:

```bash
git clone https://github.com/vcvikings3-debug/polymarket-bot C:\Users\Coos\polymarket-bot
cd C:\Users\Coos\polymarket-bot
pip install -r requirements.txt
python tests/health_check.py
```

**Expected result:** `35/35 checks passed` — all green. If any check fails, fix it before proceeding. Do not move to Step 2 until health_check passes completely.

---

## Step 2 — Read the Project Context

Read `CONTEXT.md` in full. After reading it, tell Coos verbatim:

- What Phases 0–3 built (one sentence each)
- Exactly what Phase 4 needs to build — list every task from the "What Phase 4 Needs to Build" section
- Which two new files need to be created
- Which existing file needs to be updated
- The rule: do not touch Phase 1, 2, or 3 code unless fixing a confirmed bug

---

## Step 3 — Set Up the .env File

Copy the example file:

```bash
copy .env.example .env
```

Then open `.env` and fill in the following values:

```
LM_STUDIO_HOST=http://127.0.0.1:1234
OLLAMA_MODEL=<exact model identifier shown in LM Studio → API Usage tab>
POLYMARKET_API_KEY=<your Polymarket API key from profile settings>
POLYMARKET_PRIVATE_KEY=<your wallet private key — never share this>
WALLET_ADDRESS=<your Phantom wallet public address on Polygon>
```

Leave these blank for now — Cameron will provide them:

```
SENTRY_DSN=
DISCORD_WEBHOOK_URL=
CODECOV_TOKEN=
```

**Security rules:**
- Never commit `.env` to Git — it is already in `.gitignore`
- Never paste your private key into any chat, Discord, or document
- Never share your seed phrase with anyone, including Cameron

---

## Step 4 — Confirm the Pipeline Runs

Make sure LM Studio is open and the server is running (Local Server tab → Start Server).

Then run:

```bash
python main.py
```

**Expected output:**
- `Sentry initialized` (or warning that SENTRY_DSN is not set — both are fine)
- `[Phase 1] Fetching active markets...` — should pull 400–500 markets
- `[KEY] Crypto-flagged...` — should find 30–60 crypto markets
- `[Phase 2] Analyzing top 20...` — LLM should return BET_YES / BET_NO / SKIP signals, not all errors
- `Done. Exiting cleanly.` at the end

If Phase 2 shows `LLM connection error` on every market, LM Studio is not running or the server is not started. Fix that first.

If you see real signals (BET_YES / BET_NO) but all verdicts are HOLD — that is expected. It means the Kelly-sized bets are below the $0.25 floor at current `MAX_BANKROLL_RISK` settings. The pipeline is working correctly.

---

## Step 5 — You Are Oriented. Read Your Build Instructions.

Once `python main.py` exits cleanly with real LLM signals appearing in Phase 2, you are fully oriented and ready to build.

**Read `PHASE4_BUILD.md` now.** That file contains your specific build instructions for Phase 4 — the execution layer.

**Do NOT start writing any Phase 4 code until you have read `PHASE4_BUILD.md` in full.**

---

## Standing Rules for This Project

1. **Never push directly to `main`** — always create a branch named after what you are building
2. **Open a pull request** when your work is ready — tag Cameron (`vcvikings3-debug`) for review
3. **Never commit `.env`** — secrets stay local only
4. **Never touch Phases 1–3 code** unless you are fixing a confirmed, reproducible bug — and tell Cameron before you do
5. **Always read `CONTEXT.md` before asking questions** — most answers are already there
6. **Branch naming:** use the name specified in your build file (e.g., `phase-4-execution`)
7. **Do not merge your own pull requests** — Cameron reviews and merges
8. **Every commit must include an update to CONTEXT.md** — what was built, phase statuses, next steps, who is doing what next. Your AI must do this automatically before every git commit. No exceptions.
