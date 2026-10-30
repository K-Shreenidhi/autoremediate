"""Run with:  python -m agent.diagnose_and_patch [incident_id]"""
import sys

# Load .env before anything else
from pathlib import Path
try:
    from dotenv import load_dotenv
    _env = Path(__file__).resolve().parent.parent / ".env"
    if _env.exists():
        load_dotenv(_env)
except ImportError:
    pass

from agent.diagnose_and_patch import run_agent_sync

if __name__ == "__main__":
    iid = sys.argv[1] if len(sys.argv) > 1 else "inc_001"
    r = run_agent_sync(iid)
    print(f"\n{'='*60}")
    print(f"Incident: {r.incident_id}")
    if r.diagnosis:
        print(f"Root cause: {r.diagnosis.root_cause}")
        print(f"File: {r.diagnosis.affected_file}:{r.diagnosis.affected_line}")
        print(f"Function: {r.diagnosis.affected_function}")
        print(f"Reasoning: {r.diagnosis.reasoning[:200]}")
    if r.patch:
        print(f"\nPatch (confidence={r.patch.confidence:.0%}):")
        print(r.patch.unified_diff)
        print(f"\nExplanation: {r.patch.explanation}")
    print(f"\nLLM calls: {r.total_llm_calls}")
    print(f"Tokens: {r.total_prompt_tokens} prompt + {r.total_completion_tokens} completion")
    print(f"Wall time: {r.wall_time_seconds}s")
    if r.error:
        print(f"Error: {r.error}")
