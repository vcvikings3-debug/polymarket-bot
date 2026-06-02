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