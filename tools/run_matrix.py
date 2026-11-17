"""Run the graph eval for several configurations and print one comparison table.

Examples (from the project root):
  python -m tools.run_matrix --fake flaky --attempts 1,3
  python -m tools.run_matrix --models "model-a,model-b" --attempts 1,3 --rag 0,1 --sleep 15 --pause 60

Every configuration runs in its own process so the environment settings take effect:
  attempts -> AUTOREMEDIATE_MAX_ATTEMPTS, rag -> AUTOREMEDIATE_RAG, model -> LLM_MODEL
Before running, each model gets a pre-flight check (one test message). A model that fails it is skipped
and shown as PREFLIGHT FAILED, so a wrong model id can never produce a fake "0/5" result.
"""
import argparse
import itertools
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "evals" / "results"


def result_files() -> set:
    return set(RESULTS_DIR.glob("graph*.json")) if RESULTS_DIR.exists() else set()


def default_model_name() -> str:
    try:
        from agent.config import llm_settings

        return llm_settings()["model"]
    except Exception:
        return "?"


def preflight(model: str) -> str:
    """Returns '' when the model answers a test message, otherwise a short reason."""
    if model and ("<" in model or ">" in model or " " in model):
        return f"model id {model!r} looks like a placeholder or contains spaces"
    env = {**os.environ}
    if model:
        env["LLM_MODEL"] = model
    try:
        p = subprocess.run([sys.executable, "-W", "ignore", "-m", "tools.llm_check"], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    except subprocess.TimeoutExpired:
        return "the test message timed out after 180s"
    out = (p.stdout + p.stderr).strip()
    if p.returncode != 0 or "LLM check OK" not in out:
        return out.splitlines()[-1][:300] if out else "the check printed nothing"
    return ""


def run_config(model: str, attempts: int, rag: bool, args):
    env = {**os.environ, "AUTOREMEDIATE_MAX_ATTEMPTS": str(attempts), "AUTOREMEDIATE_RAG": "1" if rag else "0"}
    if model:
        env["LLM_MODEL"] = model
    cmd = [sys.executable, "-W", "ignore", "-m", "evals.run_graph_eval", "--sleep", str(args.sleep)]
    if args.fake:
        cmd += ["--fake", args.fake]
    if args.incidents:
        cmd += ["--incidents", args.incidents]
    if args.suite:
        cmd += ["--suite", args.suite]    
    before = result_files()
    p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    new = sorted(result_files() - before, key=lambda f: f.stat().st_mtime)
    if p.returncode != 0 or not new:
        return None, (p.stdout + p.stderr).strip()[-500:]
    return json.loads(new[-1].read_text(encoding="utf-8"))["rows"], ""


def summarize(rows: list) -> dict:
    n = len(rows)
    inj = [r for r in rows if (r.get("injection_followed") is not None or r.get("injected") is not None)]
    return {
        "n": n,
        "resolved": sum(bool(r["resolved"]) for r in rows),
        "first": sum(1 for r in rows if r["resolved"] and r["attempts"] == 1),
        "attempts": sum(r["attempts"] for r in rows) / n,
        "secs": sum(r["seconds"] for r in rows) / n,
        "inj_followed": sum(bool(r.get("injection_followed") if r.get("injection_followed") is not None else r.get("injected")) for r in inj),
        "inj_total": len(inj),
        "infra": sum(1 for r in rows if r.get("infra_error")),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="", help="comma-separated model ids (default: LLM_MODEL from .env)")
    ap.add_argument("--attempts", default="3", help="for example 1 or 1,3")
    ap.add_argument("--rag", default="0", help="0, 1 or 0,1")
    ap.add_argument("--fake", choices=["oracle", "flaky", "bad", "gullible", "ratelimit_once", "ratelimit"], default=None)
    ap.add_argument("--incidents", default="")
    ap.add_argument("--suite", choices=["", "hard"], default="")
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds between incidents")
    ap.add_argument("--pause", type=float, default=0.0, help="seconds between configurations")
    ap.add_argument("--skip-preflight", action="store_true", help="do not send a test message to each model first")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()] or [""]
    attempts_list = [int(x) for x in args.attempts.split(",")]
    rag_list = [x.strip() == "1" for x in args.rag.split(",")]
    configs = list(itertools.product(models, attempts_list, rag_list))
    base_name = f"fake-{args.fake}" if args.fake else default_model_name()
    print(f"{len(configs)} configuration(s)", flush=True)

    bad = {}
    if not args.fake and not args.skip_preflight:
        for m in dict.fromkeys(models):
            name = m or base_name
            reason = preflight(m)
            if reason:
                bad[m] = reason
                print(f"preflight FAILED for {name}: {reason}", flush=True)
            else:
                print(f"preflight ok: {name}", flush=True)

    table = []
    for i, (model, att, rag) in enumerate(configs, start=1):
        label = f"{model or base_name} | attempts={att} | rag={'on' if rag else 'off'}"
        print(f"[{i}/{len(configs)}] {label}", flush=True)
        if model in bad:
            print("    skipped (preflight failed)", flush=True)
            table.append((label, None, f"PREFLIGHT FAILED: {bad[model]}"))
            continue
        rows, err = run_config(model, att, rag, args)
        if rows is None:
            print(f"    run failed: {err}", flush=True)
            table.append((label, None, "run failed"))
        else:
            s = summarize(rows)
            note = f"  (INVALID: {s['infra']} infrastructure error(s))" if s["infra"] else ""
            print(f"    resolved {s['resolved']}/{s['n']}{note}", flush=True)
            table.append((label, s, ""))
        if args.pause and i < len(configs):
            time.sleep(args.pause)

    width = max(len(label) for label, _, _ in table)
    lines = [f"{'config':<{width}}  {'resolved':>8}  {'1st-try':>7}  {'avg att':>7}  {'avg secs':>8}  {'injected':>8}  note"]
    for label, s, note in table:
        if s is None:
            lines.append(f"{label:<{width}}  {note}")
            continue
        flag = f"INVALID: {s['infra']} infra error(s)" if s["infra"] else ""
        lines.append(f"{label:<{width}}  {s['resolved']:>3}/{s['n']:<4}  {s['first']:>3}/{s['n']:<3}  "
                     f"{s['attempts']:>7.1f}  {s['secs']:>8.1f}  {s['inj_followed']:>4}/{s['inj_total']:<3}  {flag}")
    print("\n" + "\n".join(lines))

    md = ["| Configuration | Resolved | First attempt | Avg attempts | Avg secs | Injection followed |",
          "|---|---|---|---|---|---|"]
    for label, s, note in table:
        if s is None:
            md.append(f"| {label} | INVALID: {note} | | | | |")
        elif s["infra"]:
            md.append(f"| {label} | INVALID: {s['infra']} infrastructure error(s) | | | | |")
        else:
            md.append(f"| {label} | {s['resolved']}/{s['n']} | {s['first']}/{s['n']} | {s['attempts']:.1f} | "
                      f"{s['secs']:.1f} | {s['inj_followed']}/{s['inj_total']} |")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"matrix-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(md) + "\n")
    print(f"\nREADME-ready table saved: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()