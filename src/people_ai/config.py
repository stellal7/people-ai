"""Paths and settings shared by every layer."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
DB_PATH = DATA_DIR / "people.duckdb"
LOG_DIR = REPO_ROOT / "logs"
