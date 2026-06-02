My name is Coos. I am a collaborator on an active Python project called polymarket-bot. The project owner is Cameron (GitHub: vcvikings3-debug). My GitHub username is coosara2007.

This is an autonomous Polymarket crypto prediction market betting bot. It fetches active crypto markets from the Polymarket Gamma API, analyzes them using a local LLM, runs a Kelly Criterion decision engine to size bets, and will place live bets via the Polymarket CLOB API in Phase 4.

My setup:
- OS: Windows PC
- IDE: VS Code
- Coding agent: Cline with DeepSeek V3 (or Claude Code)
- Local LLM: Running via LM Studio (either Qwen3 7B or Llama 3.1 8B — I will confirm which)
- Python 3.12 installed
- Git installed
- GitHub account: coosara2007

The project is already 4 phases complete. I am joining to help build Phase 4 onward. Before I do anything I need you to help me get set up and oriented.

Step 1 — Clone the repo:
git clone https://github.com/vcvikings3-debug/polymarket-bot C:\Users\Admin\polymarket-bot
cd C:\Users\Admin\polymarket-bot
pip install -r requirements.txt
python tests/health_check.py

Help me run these commands and confirm I get 35/35 health check passes.

Step 2 — Read the context file:
Once cloned, read CONTEXT.md in the project root. Tell me verbatim what it says about Phase 4.

Step 3 — Set up my .env file:
Copy .env.example to .env and help me fill in my real values:
- POLYMARKET_API_KEY — from my Polymarket account settings
- POLYMARKET_PRIVATE_KEY — from my Phantom wallet
- WALLET_ADDRESS — my Polygon wallet public address
- LM_STUDIO_HOST — http://127.0.0.1:1234
- OLLAMA_MODEL — the exact model identifier shown in LM Studio's API Usage section

Step 4 — Confirm the pipeline runs:
Run python main.py and confirm clean output. If anything fails debug it before moving on.

Step 5 — Orientation complete:
Once health check passes, CONTEXT.md is read, .env is filled in, and main.py runs cleanly — tell me I am fully oriented and ready to build Phase 4. Do not start writing any Phase 4 code until Cameron confirms he is ready.

Important rules:
- Never push directly to main — always create a branch and open a pull request
- Never touch Phase 1, 2, or 3 code unless fixing a confirmed bug
- Never commit or push .env — it contains private keys
- Always confirm with Cameron before merging anything into main
- If anything is unclear read CONTEXT.md again before asking

My AI assistant for this project is you. Cameron's AI assistant is Claude. We are building this together.
