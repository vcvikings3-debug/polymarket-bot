"""Central configuration loader — loads all environment variables from .env using python-dotenv."""

import os
from dotenv import load_dotenv

load_dotenv()

# Polymarket API
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "")

# LLM backends
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
LM_STUDIO_HOST = os.getenv("LM_STUDIO_HOST", "http://localhost:1234")

# Betting parameters
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", 0.65))
MAX_BET_SIZE = float(os.getenv("MAX_BET_SIZE", 1.00))
MAX_BANKROLL_RISK = float(os.getenv("MAX_BANKROLL_RISK", 0.20))

# Database
DB_PATH = os.getenv("DB_PATH", "data/polymarket.db")

# Monitoring & notifications
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
DISCORD_BETS_WEBHOOK = os.getenv("DISCORD_BETS_WEBHOOK", "")
CODECOV_TOKEN = os.getenv("CODECOV_TOKEN", "")

# Paper trading
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
LIVE_TRADING = os.getenv("LIVE_TRADING", "false").lower() == "true"
PAPER_STARTING_BANKROLL = float(os.getenv("PAPER_STARTING_BANKROLL", "10.00"))
PAPER_MAX_DAILY_TRADES = int(os.getenv("PAPER_MAX_DAILY_TRADES", "5"))
PAPER_MAX_OPEN_POSITIONS = int(os.getenv("PAPER_MAX_OPEN_POSITIONS", "10"))
PAPER_MAX_CORRELATION_EXPOSURE = float(os.getenv("PAPER_MAX_CORRELATION_EXPOSURE", "0.40"))

# Phase 4 execution
POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# Intelligence layer — optional API keys (modules degrade gracefully if absent)
CRYPTOPANIC_AUTH_TOKEN = os.getenv("CRYPTOPANIC_AUTH_TOKEN", "free")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "polymarket-bot/1.0")