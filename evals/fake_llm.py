"""Fake LLMs for testing without an API key.

mode "oracle": always returns the gold fix.
mode "flaky":  first attempt is a harmless no-op edit (tests still fail), then the gold fix.
mode "bad":    always returns the no-op edit (never fixes anything).
"""
import json

from agent.llm import Usage
from evals.gold import gold_function, parse_gold


class FakeLLM:
    def __init__(self, mode: str = "oracle"):
        self.mode = mode
        self.model = f"fake-{mode}"

    def complete_json(self, system, user, model_cls, meta=None):
        gold = parse_gold(meta["incident_id"])
        fn = gold_function(meta["sources"][gold["path"]], gold["removed_lines"][0])
        attempt = meta.get("attempt", 0)
        noop = self.mode == "bad" or (self.mode == "flaky" and attempt == 0)
        new_text = gold["old"] + "  # attempted fix" if noop else gold["new"]
        out = {
            "diagnosis": {
                "root_cause": f"[fake {self.mode}] bug is in {fn}",
                "confidence_score": 0.9,
                "affected_files": [gold["path"]],
                "affected_functions": [fn],
            },
            "patch": {
                "edits": [{"path": gold["path"], "old": gold["old"], "new": new_text}],
                "rollback_strategy": "git revert the commit",
            },
        }
        return model_cls.model_validate(out), Usage(), json.dumps(out)