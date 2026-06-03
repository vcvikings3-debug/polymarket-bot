# Polymarket Bot

An autonomous Polymarket crypto prediction market betting bot that uses Python, local LLM analysis via Ollama and LM Studio, and the Polymarket CLOB API.

## Architecture

```
polymarket-bot/
├── data/                  # SQLite database storage
├── intelligence/          # News analysis, market scoring, sentiment engine
├── decision/              # Bet engine (Kelly criterion) and risk manager
├── execution/             # Polymarket API client and wallet management
├── dashboard/             # Flask web app for live P&L tracking
├── logs/                  # Log output
└── tests/                 # Health checks and test suite
```

## Setup

1. Clone the repo:
   ```bash
   git clone https://github.com/vcvikings3-debug/polymarket-bot.git
   cd polymarket-bot
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Copy and configure environment variables:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` with your Polymarket API credentials, wallet address, and LLM endpoints.

4. Run the health check:
   ```bash
   python tests/health_check.py
   ```

5. Start the bot:
   ```bash
   python main.py
   ```

## Requirements

- Python 3.12+
- Ollama (with qwen2.5:14b or compatible model) or LM Studio running locally
- USDC wallet on Polygon with API credentials from Polymarket

Webhook test — github-updates channel