"""Sentry error monitoring and Discord bet notification utilities."""

import os
import sys
import requests
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SENTRY_DSN, DISCORD_BETS_WEBHOOK

DISCORD_COLOR_GREEN = 3066993    # BET_YES / wins
DISCORD_COLOR_RED = 15158332     # BET_NO / losses
DISCORD_COLOR_PURPLE = 10181046  # errors
DISCORD_COLOR_BLUE = 3447003     # info
DISCORD_COLOR_YELLOW = 16776960  # warnings / neutral


def init_sentry():
    """Initialize Sentry SDK. No-op if SENTRY_DSN is not set."""
    if not SENTRY_DSN:
        logger.warning("SENTRY_DSN not configured — Sentry disabled")
        return
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.1)
        logger.info("Sentry initialized")
    except Exception as e:
        logger.error("Failed to initialize Sentry: {}", e)


def capture_error(error, context=None):
    """Send an exception to Sentry with optional context dict."""
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            if context:
                for key, value in context.items():
                    scope.set_extra(key, value)
            sentry_sdk.capture_exception(error)
    except Exception as e:
        logger.error("Failed to capture error to Sentry: {}", e)


def capture_message(message, level="info"):
    """Send an informational message to Sentry."""
    try:
        import sentry_sdk
        sentry_sdk.capture_message(message, level=level)
    except Exception as e:
        logger.error("Failed to capture message to Sentry: {}", e)


def send_discord_alert(title: str, message: str, color: int = None):
    """Post a betting activity embed to #github-bets via DISCORD_BETS_WEBHOOK.

    Only call this for actual betting events:
        - Paper trade opened / closed
        - Daily performance report
        - Go-live readiness report
        - Live bet placed (Phase 4)

    All other notifications (errors, system events) go to Sentry and logs only.

    Colors:
        DISCORD_COLOR_GREEN  — wins / BET_YES
        DISCORD_COLOR_RED    — losses / BET_NO
        DISCORD_COLOR_YELLOW — neutral / paper trading
        DISCORD_COLOR_BLUE   — informational (default)
    """
    if not DISCORD_BETS_WEBHOOK:
        logger.debug("DISCORD_BETS_WEBHOOK not configured — alert skipped: {}", title)
        return
    if color is None:
        color = DISCORD_COLOR_BLUE
    payload = {
        "embeds": [{"title": title[:256], "description": message[:4096], "color": color}]
    }
    try:
        resp = requests.post(DISCORD_BETS_WEBHOOK, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Discord bet alert sent: {}", title)
    except requests.exceptions.RequestException as e:
        logger.error("Failed to send Discord alert '{}': {}", title, e)


# Alias — both names route to DISCORD_BETS_WEBHOOK
send_bet_alert = send_discord_alert
