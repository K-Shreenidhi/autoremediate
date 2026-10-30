"""Run with:  python -m eval.harness [incident_ids...] [--output results.json]"""

# Load .env before anything else
from pathlib import Path
try:
    from dotenv import load_dotenv
    _env = Path(__file__).resolve().parent.parent / ".env"
    if _env.exists():
        load_dotenv(_env)
except ImportError:
    pass

import asyncio
from eval.harness import main

if __name__ == "__main__":
    asyncio.run(main())
