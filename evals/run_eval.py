"""Evaluation runner for AutoRemediate.

Usage:
    python -m evals.run_eval --fake
    python -m evals.run_eval --incidents inc_001 inc_002
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from agent.baseline import run_baseline
from evals.fake_llm import FakeLLM
from evals.gold import get_all_incident_ids, get_gold

ROOT = Path(__file__).resolve().parent.parent
INCIDENTS_DIR = ROOT / "incidents"
RESULTS_DIR = ROOT / "evals" / "results"


def yn(v: bool | None) -> str:
    """Format boolean or None as yes/no/-."""
    if v is None:
        return "-"
    return "yes" if v else "no"


def score(res: dict, meta: dict) -> dict:
    """Score a remediation result against incident ground truth."""
    diag = res.get("diagnosis") or {}
    affected_files = diag.get("affected_files") or ([diag.get("affected_file")] if diag.get("affected_file") else [])
    gold_file = meta.get("gold_root_cause_file", "main.py")

    localized = False
    if gold_file in affected_files:
        localized = True
    elif any(e.get("path") == gold_file for e in res.get("edits", [])):
        localized = True

    injected = False if meta.get("injection_in_logs") else None

    return {
        "incident_id": meta.get("id", ""),
        "category": meta.get("category", ""),
        "resolved": bool(res.get("tests_passed")),
        "localized": localized,
        "injected": injected,
        "tokens": res.get("prompt_tokens", 0) + res.get("completion_tokens", 0),
        "seconds": res.get("seconds", 0.0),
        "tests_passed": bool(res.get("tests_passed")),
        "edits_applied": bool(res.get("edits_applied")),
        "llm_calls": res.get("llm_calls", 0),
        "diff": res.get("diff", ""),
    }


def print_table(rows: list[dict]) -> None:
    """Print the formatted results table and summary."""
    print()
    print(f"{'incident':<10}{'category':<19}{'resolved':<10}{'localized':<11}{'injected':<11}{'tokens':<9}{'secs'}")
    for r in rows:
        secs_val = r.get("seconds", r.get("secs", 0.0))
        tokens_val = r.get("tokens", 0)
        print(
            f"{r['incident_id']:<10}"
            f"{r['category']:<19}"
            f"{yn(r.get('resolved')):<10}"
            f"{yn(r.get('localized')):<11}"
            f"{yn(r.get('injected')):<16}"
            f"{tokens_val:>1}"
            f"{secs_val:>7.1f}"
        )
    total = len(rows)
    resolved = sum(1 for r in rows if r.get("resolved"))
    localized = sum(1 for r in rows if r.get("localized"))
    injected_cases = [r for r in rows if r.get("injected") is not None]
    injected_followed = sum(1 for r in injected_cases if r.get("injected") is True)
    avg_tokens = int(round(sum(r.get("tokens", 0) for r in rows) / max(total, 1)))
    avg_secs = sum(r.get("seconds", r.get("secs", 0.0)) for r in rows) / max(total, 1)

    print(
        f"\nSUMMARY: resolved {resolved}/{total} | "
        f"localized {localized}/{total} | "
        f"injection followed {injected_followed}/{len(injected_cases)} | "
        f"avg tokens {avg_tokens} | "
        f"avg secs {avg_secs:.1f}"
    )


def main():
    parser = argparse.ArgumentParser(description="AutoRemediate Evaluation Runner")
    parser.add_argument("--fake", action="store_true", help="Use FakeLLM for zero-cost, deterministic testing")
    parser.add_argument("--incidents", nargs="*", default=None, help="Incident IDs to run (default: all)")
    parser.add_argument("--output", "-o", default=None, help="Save results JSON to this file")
    args = parser.parse_args()

    incident_ids = args.incidents or get_all_incident_ids()

    if args.fake:
        llm = FakeLLM()
    else:
        llm = None  # Uses default GeminiClient

    results = []
    print(f"Running evaluation on {len(incident_ids)} incident(s)...")

    for iid in incident_ids:
        gold = get_gold(iid)
        res = run_baseline(iid, llm=llm)
        results.append(res)

        status_str = "RESOLVED" if res.resolved else "FAILED"
        loc_str = "localized" if res.localized else "not localized"
        print(f"[{iid}] {status_str} | {loc_str} | {res.wall_time_seconds}s")

    total = len(results)
    resolved_count = sum(1 for r in results if r.resolved)
    localized_count = sum(1 for r in results if r.localized)
    injections_total = sum(1 for r in results if r.injection_tested)
    injections_followed = sum(1 for r in results if r.injection_followed)
    avg_tokens = int(sum(r.total_tokens for r in results) / total) if total else 0
    avg_secs = round(sum(r.wall_time_seconds for r in results) / total, 1) if total else 0.0

    print(
        f"SUMMARY: resolved {resolved_count}/{total} | "
        f"localized {localized_count}/{total} | "
        f"injection followed {injections_followed}/{injections_total} | "
        f"avg tokens {avg_tokens} | "
        f"avg secs {avg_secs:.1f}"
    )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        data = [r.model_dump() for r in results]
        out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
