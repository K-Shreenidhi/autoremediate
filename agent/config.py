"""Loads settings from the .env file in the project root."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DEFAULT_DATABASE_URL = "postgresql://autoremediate:autoremediate_dev_pw@localhost:5433/autoremediate"


def llm_settings() -> dict:
    key = os.getenv("LLM_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip() or os.getenv("GEMINI_MODEL", "").strip() or "gemini-3-flash-preview"
    base_url = os.getenv("LLM_BASE_URL", "").strip() or None
    if not key or not model:
        raise RuntimeError("Set LLM_API_KEY (or GOOGLE_API_KEY) and LLM_MODEL in the .env file (see .env.example)")
    return {"api_key": key, "model": model, "base_url": base_url}


def database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip() or DEFAULT_DATABASE_URL
