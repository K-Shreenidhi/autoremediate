"""Load .env file at import time so GOOGLE_API_KEY etc. are available."""
from pathlib import Path

try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass
