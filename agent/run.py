"""Command line for the AutoRemediate graph.

  python -m agent.run start inc_001 [--fake [oracle|flaky|bad]] [--thread ID]
  python -m agent.run status THREAD
  python -m agent.run resume THREAD approve
  python -m agent.run resume THREAD reject --reason "why"
  python -m agent.run resume THREAD edit --edits-file edits.json
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command

from agent.config import database_url
from agent.graph import build_graph
from agent.tools_sync import SyncTools


def say(text: str = "") -> None:
    print(text, flush=True)


def describe(update: dict) -> None:
    for node, val in update.items():
        if node == "__interrupt__" or not isinstance(val, dict):
            continue
        if node == "ingest":
            say(f"[ingest] context gathered from {len(val.get('sources', {}))} source file(s)")
        elif node == "diagnose":
            p = val.get("proposal", {})
            if p.get("error"):
                say(f"[diagnose] the model returned nothing usable: {p['error']}")
            else:
                say(f"[diagnose] model proposed {len(p.get('edits', []))} edit(s), "
                    f"self-reported confidence {p.get('diagnosis', {}).get('confidence_score')}")
        elif node == "verify":
            h = val["history"][-1]
            if h["ok"]:
                say(f"[verify] attempt {h['attempt']} PASSED: {h['summary']}")
            else:
                say(f"[verify] attempt {h['attempt']} FAILED at {h['stage']}: {h['summary'][:110]}")
        elif node == "next_attempt":
            say("[retry] feeding the failure back to the model")
        elif node == "apply_human_edit":
            say("[human edit] applying your edits and re-verifying")
        elif node in ("publish", "reject", "give_up"):
            say(f"[{node}] done")


def show_state(graph, cfg, thread: str) -> int:
    st = graph.get_state(cfg)
    if not st.values:
        say(f"thread {thread}: not found")
        return 1
    say()
    if st.next:
        payload = [i.value for t in st.tasks for i in t.interrupts][0]
        diag = payload["diagnosis"]
        say("=" * 64)
        say(f"AWAITING APPROVAL   thread={thread}")
        say(f"incident:    {payload['incident_id']}")
        say(f"root cause:  {diag.get('root_cause')}")
        say(f"confidence:  {diag.get('confidence_score')} (self-reported)")
        say(f"attempts:    {payload['attempts_used']} of {payload['max_attempts']}")
        say(f"tests:       {payload['tests']}")
        say("--- proposed diff ---")
        say(payload["diff"].rstrip())
        say("---------------------")
        say("resume with one of:")
        say(f"  python -m agent.run resume {thread} approve")
        say(f"  python -m agent.run resume {thread} reject --reason \"why\"")
        say(f"  python -m agent.run resume {thread} edit --edits-file edits.json")
        say("=" * 64)
        return 0
    v = st.values
    say(f"FINAL STATUS: {v.get('status')}")
    result = v.get("result", {})
    if v.get("status") == "published":
        say(f"branch: {result['branch']} (commit {result['commit']})")
        say(f"PR description: {result['pr_file']}")
    elif v.get("status") == "rejected":
        say(f"rejected by human: {result.get('reason') or '(no reason given)'}")
    else:
        say(f"reason: {result.get('error')}")
    say(f"attempts: {len(v.get('history', []))} | llm calls: {v.get('llm_calls')} | tokens: {v.get('tokens')}")
    return 0


def make_llm(fake):
    if fake:
        from evals.fake_llm import FakeLLM
        return FakeLLM(fake)
    from agent.config import llm_settings
    from agent.llm import LLMClient
    try:
        return LLMClient(**llm_settings())
    except RuntimeError as e:
        raise SystemExit(str(e))


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start")
    p.add_argument("incident")
    p.add_argument("--thread", default="")
    p.add_argument("--fake", nargs="?", const="oracle", choices=["oracle", "flaky", "bad"], default=None)
    p = sub.add_parser("status")
    p.add_argument("thread")
    p = sub.add_parser("resume")
    p.add_argument("thread")
    p.add_argument("action", choices=["approve", "reject", "edit"])
    p.add_argument("--reason", default="")
    p.add_argument("--edits-file", default="")
    args = ap.parse_args()

    llm = make_llm(args.fake) if args.cmd == "start" else None  # resume and status never call the LLM
    try:
        psycopg.connect(database_url(), connect_timeout=5).close()
    except psycopg.OperationalError:
        raise SystemExit("Cannot reach Postgres. Start it with: docker compose -f infra/docker-compose.yml up -d --wait")
    with PostgresSaver.from_conn_string(database_url()) as saver, SyncTools() as tools:
        saver.setup()
        graph = build_graph(tools, llm, saver)

        if args.cmd == "start":
            thread = args.thread or f"{args.incident}-{uuid.uuid4().hex[:6]}"
            cfg = {"configurable": {"thread_id": thread}}
            say(f"thread: {thread}")
            for update in graph.stream({"incident_id": args.incident}, cfg, stream_mode="updates"):
                describe(update)
            return show_state(graph, cfg, thread)

        cfg = {"configurable": {"thread_id": args.thread}}
        if args.cmd == "status":
            st = graph.get_state(cfg)
            label = "not found" if not st.values else ("awaiting_approval" if st.next else st.values.get("status"))
            say(f"status: {label}")
            return show_state(graph, cfg, args.thread)

        st = graph.get_state(cfg)
        if not st.values or not st.next:
            say(f"thread {args.thread} is not awaiting approval (status: "
                f"{'not found' if not st.values else st.values.get('status')})")
            return 1
        decision = {"action": args.action}
        if args.action == "reject":
            decision["reason"] = args.reason
        if args.action == "edit":
            if not args.edits_file:
                say("--edits-file is required for edit")
                return 1
            decision["edits"] = json.loads(Path(args.edits_file).read_text(encoding="utf-8-sig"))
        for update in graph.stream(Command(resume=decision), cfg, stream_mode="updates"):
            describe(update)
        return show_state(graph, cfg, args.thread)


if __name__ == "__main__":
    sys.exit(main())