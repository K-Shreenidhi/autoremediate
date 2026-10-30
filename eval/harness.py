"""Eval harness: replay all incidents, run the agent, score, produce a results table.

Usage:
    python -m eval.harness                      # run all 5 incidents
    python -m eval.harness inc_001 inc_003       # run specific incidents
    python -m eval.harness --output results.json # save full results to file

The harness:
  1. Iterates over each incident
  2. Runs the single-pass agent
  3. Scores the result against ground truth (gold patch + test suite)
  4. Prints a rich results table
  5. Saves full structured results to JSON
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent.diagnose_and_patch import run_agent
from agent.llm import GeminiClient
from agent.models import AgentResult, EvalRun, IncidentScore
from eval.scorer import score_incident

ROOT = Path(__file__).resolve().parent.parent
INCIDENTS_DIR = ROOT / "incidents"
RESULTS_DIR = ROOT / "eval" / "results"


def discover_incidents() -> list[str]:
    """Find all incident IDs in the incidents directory."""
    ids = []
    for d in sorted(INCIDENTS_DIR.iterdir()):
        if d.is_dir() and (d / "incident.json").exists():
            ids.append(d.name)
    return ids


async def run_eval(
    incident_ids: list[str] | None = None,
    *,
    api_key: str = "",
    model: str = "",
) -> EvalRun:
    """Run the full eval: agent + scorer on each incident."""
    if not incident_ids:
        incident_ids = discover_incidents()

    model_name = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    run_id = f"eval_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    eval_run = EvalRun(
        run_id=run_id,
        model=model_name,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    print(f"\n{'='*72}")
    print(f"  AutoRemediate Phase 2 Eval — {model_name}")
    print(f"  Run ID: {run_id}")
    print(f"  Incidents: {', '.join(incident_ids)}")
    print(f"{'='*72}\n")

    agent_results: list[tuple[AgentResult, IncidentScore]] = []

    for iid in incident_ids:
        print(f"+-- {iid} {'='*(58 - len(iid))}")

        # Run agent
        print(f"|  Running agent...")
        agent_result = await run_agent(iid, api_key=api_key, model=model)

        if agent_result.diagnosis:
            print(f"|  Diagnosis: {agent_result.diagnosis.root_cause[:80]}")
        if agent_result.patch:
            print(f"|  Patch confidence: {agent_result.patch.confidence:.0%}")
        if agent_result.error:
            print(f"|  [!] Error: {agent_result.error[:80]}")

        # Score
        print(f"|  Scoring...")
        score = await score_incident(agent_result)

        # Display result
        status = "[PASS]" if score.full_suite_green else "[FAIL]"
        print(f"|  Result: {status}")
        print(f"|  Target test fixed: {'[Y]' if score.target_test_fixed else '[N]'}")
        print(f"|  Full suite green:  {'[Y]' if score.full_suite_green else '[N]'}")
        print(f"|  Patch correct:     {'[Y]' if score.patch_correct else '[N]'}")
        print(f"|  Injection safe:    {'[Y]' if score.injection_safe else '[N]'}")
        print(f"|  LLM calls: {score.llm_calls}  |  "
              f"Tokens: {score.prompt_tokens}+{score.completion_tokens}  |  "
              f"Time: {score.wall_time_seconds:.1f}s")
        print(f"+{'='*64}\n")

        eval_run.incidents.append(score)
        agent_results.append((agent_result, score))

    # ── Summary ──────────────────────────────────────────────────────────
    total = len(eval_run.incidents)
    passed = sum(1 for s in eval_run.incidents if s.full_suite_green)
    eval_run.pass_at_1 = passed / total if total > 0 else 0.0
    eval_run.total_llm_calls = sum(s.llm_calls for s in eval_run.incidents)
    eval_run.total_prompt_tokens = sum(s.prompt_tokens for s in eval_run.incidents)
    eval_run.total_completion_tokens = sum(s.completion_tokens for s in eval_run.incidents)
    eval_run.total_wall_time_seconds = sum(s.wall_time_seconds for s in eval_run.incidents)

    # Print summary table
    print(f"\n{'='*72}")
    print(f"  RESULTS SUMMARY -- pass@1 = {passed}/{total} ({eval_run.pass_at_1:.0%})")
    print(f"{'='*72}")
    print(f"{'Incident':<10} {'Category':<20} {'Tests':<7} {'Patch':<7} {'Safe':<6} {'Time':>8} {'Tokens':>10}")
    print(f"{'-'*10} {'-'*20} {'-'*7} {'-'*7} {'-'*6} {'-':->8} {'-':->10}")
    for s in eval_run.incidents:
        tests = "[Y]" if s.full_suite_green else "[N]"
        patch = "[Y]" if s.patch_correct else "[N]"
        safe = "[Y]" if s.injection_safe else "[N]"
        tokens = s.prompt_tokens + s.completion_tokens
        print(f"{s.incident_id:<10} {s.category:<20} {tests:<7} {patch:<7} {safe:<6} "
              f"{s.wall_time_seconds:>7.1f}s {tokens:>10,}")
    print(f"{'-'*72}")

    # Cost estimate
    total_tokens = eval_run.total_prompt_tokens + eval_run.total_completion_tokens
    est_cost = GeminiClient.__new__(GeminiClient)
    est_cost.metrics = type("M", (), {
        "prompt_tokens": eval_run.total_prompt_tokens,
        "completion_tokens": eval_run.total_completion_tokens,
    })()
    from agent.llm import PRICING
    prices = PRICING.get(model_name, {"input": 0.50, "output": 1.50})
    cost = (
        eval_run.total_prompt_tokens * prices["input"] / 1_000_000
        + eval_run.total_completion_tokens * prices["output"] / 1_000_000
    )
    print(f"  Total: {eval_run.total_llm_calls} LLM calls  |  "
          f"{total_tokens:,} tokens  |  "
          f"${cost:.4f} est  |  "
          f"{eval_run.total_wall_time_seconds:.1f}s wall time")
    print(f"{'='*72}\n")

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return eval_run, agent_results


def save_results(eval_run: EvalRun, agent_results, output_path: str | None = None):
    """Save the full eval run + per-incident agent results to JSON."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if output_path:
        out = Path(output_path)
    else:
        out = RESULTS_DIR / f"{eval_run.run_id}.json"

    data = {
        "eval_run": json.loads(eval_run.model_dump_json()),
        "agent_results": [
            {
                "agent": json.loads(ar.model_dump_json()),
                "score": json.loads(sc.model_dump_json()),
            }
            for ar, sc in agent_results
        ],
    }

    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Results saved to: {out}")
    return out


async def main():
    parser = argparse.ArgumentParser(description="AutoRemediate Phase 2 Eval Harness")
    parser.add_argument("incidents", nargs="*", help="Incident IDs to evaluate (default: all)")
    parser.add_argument("--output", "-o", help="Output JSON path")
    parser.add_argument("--model", "-m", default="", help="Gemini model override")
    parser.add_argument("--api-key", default="", help="Google API key override")
    args = parser.parse_args()

    eval_run, agent_results = await run_eval(
        incident_ids=args.incidents or None,
        api_key=args.api_key,
        model=args.model,
    )

    save_results(eval_run, agent_results, args.output)


if __name__ == "__main__":
    asyncio.run(main())
