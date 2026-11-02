"""LangGraph workflow:

  ingest -> diagnose -> verify -+-> approval (human interrupt) -> publish
              ^                 |        |-> reject
              |                 |        '-> apply_human_edit -> verify
              '-- next_attempt <-+ (failed, attempts left)
                                '-> give_up (failed, no attempts left, or infrastructure problem)
"""
import json
import os
import time
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from agent.baseline import SKIP_FILES, SYSTEM_PROMPT, _strip_numbers, build_prompt
from agent.schemas import AgentOutput, Edit
from rag.context import rag_context

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = tuple(float(x) for x in os.getenv("AUTOREMEDIATE_BACKOFF", "5,15,45").split(",") if x.strip())
TRANSIENT_MARKERS = ("429", "resource_exhausted", "rate limit", "rate_limit", "quota", "503", "unavailable",
                     "overloaded", "timed out", "timeout", "temporarily")


def is_transient(error: Exception) -> bool:
    """True for provider problems (rate limit, quota, outage), not for bad model output."""
    if type(error).__name__ == "LLMError":
        return False
    text = f"{type(error).__name__}: {error}".lower()
    return any(marker in text for marker in TRANSIENT_MARKERS)


class State(TypedDict, total=False):
    incident_id: str
    attempt: int
    prompt_base: str
    sources: dict
    feedback: str
    proposal: dict
    human_edit: bool
    verification: dict
    diff: str
    diff_sha256: str
    decision: dict
    tokens: int
    llm_calls: int
    history: list
    status: str
    result: dict


def make_feedback(attempt: int, edits: list, v: dict) -> str:
    lines = [f"Attempt {attempt} failed at stage '{v['stage']}': {v['summary']}"]
    if edits:
        lines.append("Edits you proposed:\n" + json.dumps(edits, indent=2))
    if v.get("failed_tests"):
        lines.append("Failing tests: " + ", ".join(v["failed_tests"]))
    if v.get("output_tail"):
        lines.append("<untrusted_ci>\n" + v["output_tail"] + "\n</untrusted_ci>")
    return "\n".join(lines)


def write_pr_file(incident_id: str, ticket: str, state: dict, branch: str) -> str:
    prop = state["proposal"]
    diag = prop.get("diagnosis", {})
    text = "\n".join([
        f"# Fix {incident_id}: {diag.get('root_cause', '')[:100]}",
        "",
        "## Ticket",
        ticket,
        "",
        "## Root cause",
        diag.get("root_cause", ""),
        "",
        "## Change",
        "```diff",
        state["diff"].rstrip(),
        "```",
        "",
        "## Verification",
        f"- Full test suite: passed ({state['verification']['summary']})",
        f"- Attempts used: {state['attempt'] + 1} of {MAX_ATTEMPTS}",
        f"- Model self-reported confidence: {diag.get('confidence_score')} (not calibrated; do not rely on it)",
        "",
        "## Approval",
        "Approved by a human reviewer before publishing.",
        f"Approved diff fingerprint (sha256): `{state['diff_sha256']}`",
        f"Branch: `{branch}`",
        "",
        "## Rollback",
        prop.get("rollback_strategy", ""),
        "",
    ])
    path = OUTPUTS_DIR / incident_id / "PR.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return str(path.relative_to(ROOT))


def build_graph(tools, llm, checkpointer):
    def ingest(state: State):
        inc = state["incident_id"]
        ticket = tools.call("get_ticket", incident_id=inc)["ticket"]
        errors = tools.call("get_error_summary", incident_id=inc)
        status = tools.call("get_status_breakdown", incident_id=inc)
        warnings = tools.call("search_logs", incident_id=inc, level="WARNING", limit=5)
        tools.call("checkout_incident", incident_id=inc)
        ci = tools.call("run_tests", incident_id=inc)
        files = tools.call("list_files", incident_id=inc)["files"]
        sources = {}
        for name in files:
            base = name.split("/")[-1]
            if name.endswith(".py") and not base.startswith("test_") and base not in SKIP_FILES:
                r = tools.call("read_file", incident_id=inc, path=name)
                if r.get("ok"):
                    sources[name] = _strip_numbers(r["content"])
        return {
            "prompt_base": rag_context(ticket, errors) + build_prompt(ticket, errors, status, warnings, ci, sources),
            "sources": sources,
            "attempt": 0,
            "feedback": "",
            "human_edit": False,
            "tokens": 0,
            "llm_calls": 0,
            "history": [],
            "status": "running",
        }

    def diagnose(state: State):
        prompt = state["prompt_base"]
        if state.get("feedback"):
            prompt += "\n\n## Previous attempt failed\n" + state["feedback"] + "\nFix the problem and return a corrected JSON object."
        meta = {"incident_id": state["incident_id"], "sources": state["sources"], "attempt": state["attempt"]}
        out = usage = error = None
        for wait in (0.0,) + BACKOFF_SECONDS:
            if wait:
                print(f"[diagnose] provider limit or outage; waiting {wait:g}s, then retrying", flush=True)
                time.sleep(wait)
            try:
                out, usage, _raw = llm.complete_json(SYSTEM_PROMPT, prompt, AgentOutput, meta)
                error = None
                break
            except Exception as e:
                error = e
                if not is_transient(e):
                    break
        if error is not None:
            return {
                "attempt": state["attempt"],
                "human_edit": False,
                "proposal": {"error": f"{type(error).__name__}: {str(error)[:300]}", "edits": [], "infra": is_transient(error)},
            }
        return {
            "attempt": state["attempt"],
            "human_edit": False,
            "proposal": {
                "diagnosis": out.diagnosis.model_dump(),
                "edits": [e.model_dump() for e in out.patch.edits],
                "rollback_strategy": out.patch.rollback_strategy,
            },
            "tokens": state.get("tokens", 0) + usage.prompt_tokens + usage.completion_tokens,
            "llm_calls": state.get("llm_calls", 0) + usage.calls,
        }

    def verify(state: State):
        inc = state["incident_id"]
        prop = state["proposal"]
        edits = prop.get("edits") or []
        v = {"ok": False, "stage": "", "summary": "", "failed_tests": [], "output_tail": "", "infra": False}
        co = tools.call("checkout_incident", incident_id=inc)  # fresh workspace for every attempt
        if not co.get("ok"):
            v.update(stage="workspace", infra=True, summary=f"could not prepare the workspace: {co.get('error')}")
        elif not edits:
            v.update(stage="llm", infra=bool(prop.get("infra")), summary=prop.get("error", "the model proposed no edits"))
        else:
            for e in edits:
                r = tools.call("replace_in_file", incident_id=inc, path=e["path"], old=e["old"], new=e["new"])
                if not r.get("ok"):
                    v.update(stage="apply", summary=f"edit to {e['path']} was rejected: {r.get('error')}")
                    break
            else:
                t = tools.call("run_tests", incident_id=inc)
                if t.get("tests_passed"):
                    v.update(ok=True, stage="tests", summary=t.get("summary", ""))
                else:
                    v.update(stage="tests", summary=t.get("summary", "tests failed"),
                             failed_tests=t.get("failed_tests", []), output_tail=t.get("output_tail", ""))
        diff, sha = "", ""
        if v["ok"]:
            diff = tools.call("get_diff", incident_id=inc).get("diff", "")
            sha = tools.call("diff_fingerprint", incident_id=inc)["sha256"]
        n = state["attempt"] + 1
        entry = {"attempt": n, "ok": v["ok"], "stage": v["stage"], "summary": v["summary"]}
        return {
            "verification": v,
            "diff": diff,
            "diff_sha256": sha,
            "feedback": "" if v["ok"] else make_feedback(n, edits, v),
            "history": state.get("history", []) + [entry],
        }

    def after_verify(state: State) -> str:
        v = state["verification"]
        if v["ok"]:
            return "approval"
        if v.get("infra") or state.get("human_edit"):
            return "give_up"
        if state["attempt"] + 1 < MAX_ATTEMPTS:
            return "next_attempt"
        return "give_up"

    def next_attempt(state: State):
        return {"attempt": state["attempt"] + 1}

    def approval(state: State):
        prop = state["proposal"]
        payload = {
            "incident_id": state["incident_id"],
            "diagnosis": prop.get("diagnosis", {}),
            "diff": state["diff"],
            "tests": state["verification"]["summary"],
            "attempts_used": state["attempt"] + 1,
            "max_attempts": MAX_ATTEMPTS,
        }
        decision = interrupt(payload)  # pauses here until a human resumes the thread
        return {"decision": decision, "status": "decided"}

    def after_approval(state: State) -> str:
        d = state.get("decision") or {}
        if d.get("action") == "approve":
            return "publish"
        if d.get("action") == "edit":
            try:
                edits = [Edit.model_validate(e) for e in d.get("edits", [])]
            except ValueError:
                edits = []
            if edits:
                return "apply_human_edit"
        return "reject"

    def apply_human_edit(state: State):
        edits = [Edit.model_validate(e).model_dump() for e in state["decision"]["edits"]]
        prop = dict(state["proposal"])
        prop["edits"] = edits
        return {"proposal": prop, "human_edit": True}

    def publish(state: State):
        inc = state["incident_id"]
        branch = f"autoremediate/{inc}"
        diag = state["proposal"].get("diagnosis", {})
        message = (f"fix({inc}): {diag.get('root_cause', 'automated fix')[:70]}\n\n"
                   f"Root cause: {diag.get('root_cause', '')}\n"
                   f"Verified by the full test suite. Approved by a human reviewer.\n"
                   f"Diff fingerprint: {state['diff_sha256']}")
        r = tools.call("publish_branch", incident_id=inc, diff_sha256=state["diff_sha256"], branch=branch, message=message)
        if not r.get("ok"):
            return {"status": "failed", "result": {"error": r.get("error", "publish failed")}}
        ticket = tools.call("get_ticket", incident_id=inc)["ticket"]
        pr_file = write_pr_file(inc, ticket, state, branch)
        return {"status": "published", "result": {"branch": branch, "commit": r["commit"], "pr_file": pr_file}}

    def reject(state: State):
        reason = (state.get("decision") or {}).get("reason", "")
        return {"status": "rejected", "result": {"reason": reason}}

    def give_up(state: State):
        last = state["verification"]
        if last.get("infra"):
            return {"status": "infra_error",
                    "result": {"error": f"stopped by an infrastructure problem, not a bad patch: {last['summary']}"}}
        return {"status": "failed",
                "result": {"error": f"no verified patch after {state['attempt'] + 1} attempt(s): {last['summary']}"}}

    g = StateGraph(State)
    for name, fn in [("ingest", ingest), ("diagnose", diagnose), ("verify", verify), ("next_attempt", next_attempt),
                     ("approval", approval), ("apply_human_edit", apply_human_edit), ("publish", publish),
                     ("reject", reject), ("give_up", give_up)]:
        g.add_node(name, fn)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "diagnose")
    g.add_edge("diagnose", "verify")
    g.add_conditional_edges("verify", after_verify, {"approval": "approval", "next_attempt": "next_attempt", "give_up": "give_up"})
    g.add_edge("next_attempt", "diagnose")
    g.add_conditional_edges("approval", after_approval, {"publish": "publish", "apply_human_edit": "apply_human_edit", "reject": "reject"})
    g.add_edge("apply_human_edit", "verify")
    g.add_edge("publish", END)
    g.add_edge("reject", END)
    g.add_edge("give_up", END)
    return g.compile(checkpointer=checkpointer)