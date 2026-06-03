"""Sentry error monitoring and Discord webhook notification utilities."""

import os
import sys
import requests
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SENTRY_DSN, DISCORD_BETS_WEBHOOK, DISCORD_UPDATES_WEBHOOK

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


def send_discord_alert(title, message, color=None):
    """Post a formatted embed to the configured Discord webhook.

    Colors:
        DISCORD_COLOR_GREEN  — BET_YES signals
        DISCORD_COLOR_RED    — BET_NO signals
        DISCORD_COLOR_PURPLE — errors / crashes
        DISCORD_COLOR_BLUE   — info (default)
    """
    if not DISCORD_BETS_WEBHOOK:
        logger.warning("DISCORD_BETS_WEBHOOK not configured — Discord alert skipped: {}", title)
        return
    if color is None:
        color = DISCORD_COLOR_BLUE
    payload = {
        "embeds": [
            {
                "title": title,
                "description": message,
                "color": color,
            }
        ]
    }
    try:
        resp = requests.post(DISCORD_BETS_WEBHOOK, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Discord alert sent: {}", title)
    except requests.exceptions.RequestException as e:
        logger.error("Failed to send Discord alert '{}': {}", title, e)


def send_discord_update(title: str, message: str, color: int = None):
    """Post to the UPDATES webhook (paper trading reports, daily summaries, readiness)."""
    if not DISCORD_UPDATES_WEBHOOK:
        logger.debug("DISCORD_UPDATES_WEBHOOK not configured — update skipped: {}", title)
        return
    if color is None:
        color = DISCORD_COLOR_BLUE
    payload = {
        "embeds": [{"title": title[:256], "description": message[:4096], "color": color}]
    }
    try:
        resp = requests.post(DISCORD_UPDATES_WEBHOOK, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Discord update sent: {}", title)
    except requests.exceptions.RequestException as e:
        logger.error("Failed to send Discord update '{}': {}", title, e)
