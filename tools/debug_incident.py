"""Run incidents through the graph and print everything that happens, with an independent LLM call counter.

Usage (from the project root):
  python -m tools.debug_incident inc_003
  python -m tools.debug_incident inc_001 inc_002 inc_003 --rag
  python -m tools.debug_incident inc_001 --fake flaky
"""
import argparse
import json
import os
import uuid

from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import build_graph
from agent.tools_sync import SyncTools


class CountingLLM:
    """Wraps an LLM client and counts what every call really returned."""

    def __init__(self, inner):
        self.inner = inner
        self.model = getattr(inner, "model", "?")
        self.reset()

    def reset(self):
        self.calls = 0
        self.api_calls = 0
        self.tokens = 0

    def complete_json(self, system, user, model_cls, meta=None):
        self.calls += 1
        print(f"  [llm call {self.calls}] prompt chars={len(user)}", flush=True)
        try:
            out, usage, raw = self.inner.complete_json(system, user, model_cls, meta)
        except Exception as e:
            print(f"  [llm call {self.calls}] FAILED: {type(e).__name__}: {str(e)[:300]}", flush=True)
            raise
        self.api_calls += usage.calls
        self.tokens += usage.prompt_tokens + usage.completion_tokens
        print(f"  [llm call {self.calls}] ok: api_calls={usage.calls} prompt_tokens={usage.prompt_tokens} "
              f"completion_tokens={usage.completion_tokens}", flush=True)
        return out, usage, raw


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("incidents", nargs="+")
    ap.add_argument("--rag", action="store_true")
    ap.add_argument("--fake", nargs="?", const="oracle", choices=["oracle", "flaky", "bad"], default=None)
    args = ap.parse_args()
    if args.rag:
        os.environ["AUTOREMEDIATE_RAG"] = "1"
    if args.fake:
        from evals.fake_llm import FakeLLM
        llm = FakeLLM(args.fake)
    else:
        from agent.config import llm_settings
        from agent.llm import LLMClient
        try:
            llm = LLMClient(**llm_settings())
        except RuntimeError as e:
            raise SystemExit(str(e))
    llm = CountingLLM(llm)

    with SyncTools() as tools:
        graph = build_graph(tools, llm, InMemorySaver())
        for inc in args.incidents:
            print(f"\n=== {inc} ===", flush=True)
            llm.reset()
            cfg = {"configurable": {"thread_id": f"dbg-{inc}-{uuid.uuid4().hex[:6]}"}}
            for update in graph.stream({"incident_id": inc}, cfg, stream_mode="updates"):
                for node, val in update.items():
                    if node == "__interrupt__" or not isinstance(val, dict):
                        continue
                    if node == "diagnose":
                        p = val.get("proposal", {})
                        if p.get("error"):
                            print(f"[diagnose] ERROR: {p['error']}")
                        else:
                            print(f"[diagnose] root cause: {p['diagnosis']['root_cause']}")
                            print("[diagnose] edits: " + json.dumps(p["edits"], indent=2))
                        print(f"[diagnose] state counters: llm_calls={val.get('llm_calls')} tokens={val.get('tokens')}")
                    elif node == "verify":
                        v = val["verification"]
                        print(f"[verify] ok={v['ok']} stage={v['stage']} summary={v['summary']}")
                        if v.get("failed_tests"):
                            print(f"[verify] failed tests: {v['failed_tests']}")
                        if v.get("output_tail") and not v["ok"]:
                            print(v["output_tail"][-1500:])
                    else:
                        print(f"[{node}]")
            state = graph.get_state(cfg).values
            print(f"SUMMARY {inc}: graph state says llm_calls={state.get('llm_calls')} tokens={state.get('tokens')} "
                  f"attempts={len(state.get('history', []))} | independent count says calls={llm.calls} "
                  f"api_calls={llm.api_calls} tokens={llm.tokens}")


if __name__ == "__main__":
    main()