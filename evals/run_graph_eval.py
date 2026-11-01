"""Run the LangGraph agent (with retries) on all incidents and compare with the baseline.

No database or human needed: the run stops at the approval step, and a patch counts as
resolved when it was verified (full test suite green) before approval.

Usage (from the project root):
  python -m evals.run_graph_eval --fake flaky     # no API key; first attempt always fails
  python -m evals.run_graph_eval                  # real LLM from .env
  python -m evals.run_graph_eval --sleep 2
"""
import argparse
import json
import time
import uuid
from datetime import datetime

from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import build_graph
from agent.tools_sync import SyncTools
from evals.run_eval import INCIDENTS_DIR, RESULTS_DIR, ROOT, print_table, score, yn


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", nargs="?", const="oracle", choices=["oracle", "flaky", "bad"], default=None)
    ap.add_argument("--incidents", default="")
    ap.add_argument("--sleep", type=float, default=0.0)
    args = ap.parse_args()

    if args.fake:
        from evals.fake_llm import FakeLLM
        llm, tag = FakeLLM(args.fake), f"graph-fake-{args.fake}"
    else:
        from agent.config import llm_settings
        from agent.llm import LLMClient
        try:
            llm, tag = LLMClient(**llm_settings()), "graph"
        except RuntimeError as e:
            raise SystemExit(str(e))

    ids = args.incidents.split(",") if args.incidents else sorted(
        d.name for d in INCIDENTS_DIR.iterdir() if d.is_dir() and (d / "incident.json").exists()
    )
    rows, attempts = [], []
    with SyncTools() as tools:
        graph = build_graph(tools, llm, InMemorySaver())
        for n, inc in enumerate(ids):
            meta = json.loads((INCIDENTS_DIR / inc / "incident.json").read_text(encoding="utf-8"))
            cfg = {"configurable": {"thread_id": f"eval-{inc}-{uuid.uuid4().hex[:6]}"}}
            t0 = time.perf_counter()
            graph.invoke({"incident_id": inc}, cfg)
            v = graph.get_state(cfg).values
            ok = bool(v.get("verification", {}).get("ok"))
            prop = v.get("proposal", {})
            res = {
                "_sources": v.get("sources", {}),
                "diagnosis": prop.get("diagnosis"),
                "edits": prop.get("edits", []),
                "edits_applied": ok,
                "tests_passed": ok,
                "prompt_tokens": v.get("tokens", 0),
                "completion_tokens": 0,
                "llm_calls": v.get("llm_calls", 0),
                "seconds": round(time.perf_counter() - t0, 1),
                "llm_error": None,
                "apply_errors": [],
                "diff": v.get("diff", ""),
            }
            row = score(res, meta)
            row["attempts"] = len(v.get("history", []))
            row["failure"] = "" if ok else v.get("verification", {}).get("summary", "no verified patch")
            rows.append(row)
            attempts.append(row["attempts"])
            print(f"[{n + 1}/{len(ids)}] {inc} resolved={yn(row['resolved'])} attempts={row['attempts']} ({row['seconds']}s)", flush=True)
            if args.sleep and n < len(ids) - 1:
                time.sleep(args.sleep)

    print_table(rows)
    for r in rows:
        if r["failure"]:
            print(f"  {r['incident_id']}: not resolved after {r['attempts']} attempt(s): {r['failure']}")
    print(f"avg attempts per incident: {sum(attempts) / max(len(attempts), 1):.1f}")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{tag}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"model": getattr(llm, "model", "?"), "rows": rows}, f, indent=2)
    print(f"\nsaved: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()