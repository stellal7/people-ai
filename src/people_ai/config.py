"""Paths and settings shared by every layer."""
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
DB_PATH = DATA_DIR / "people.duckdb"
LOG_DIR = REPO_ROOT / "logs"

load_dotenv(REPO_ROOT / ".env")

# Model choice is config, not a constant: a cheap model routes and classifies, a strong one writes SQL and drafts.
ROUTER_MODEL = os.getenv("PEOPLE_AI_ROUTER_MODEL", "claude-haiku-4-5")
ANSWER_MODEL = os.getenv("PEOPLE_AI_ANSWER_MODEL", "claude-opus-5")
ANSWER_EFFORT = os.getenv("PEOPLE_AI_ANSWER_EFFORT", "high")     # low | medium | high | xhigh | max


def has_credentials():
    """True when a key is configured. The SDK also accepts an `ant auth login` profile, which this won't see."""
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
