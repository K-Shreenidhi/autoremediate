"""End-to-end check of Phase 3 with fake LLMs (no API key). Needs Postgres running.

Each step runs in a separate process, so resuming proves state survives a restart.
Run from the project root:  python -m tools.check_phase3
"""
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from evals.gold import parse_gold
from mcp_servers.common import ROOT, VICTIM
from mcp_servers.repo_server import checkout_incident, diff_fingerprint, publish_branch, replace_in_file

RESULTS = []
RUN = uuid.uuid4().hex[:6]


def check(label, condition):
    RESULTS.append(bool(condition))
    print(("PASS  " if condition else "FAIL  ") + label, flush=True)


def agent(*args):
    p = subprocess.run([sys.executable, "-W", "ignore", "-m", "agent.run", *args], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def victim_git(*args):
    p = subprocess.run(["git", *args], cwd=VICTIM, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.stdout


print("== A: retry loop, restart, approve (inc_001)")
t = f"chk-{RUN}-a"
rc, out = agent("start", "inc_001", "--fake", "flaky", "--thread", t)
check("first attempt fails and is retried, second attempt passes",
      "attempt 1 FAILED" in out and "attempt 2 PASSED" in out)
check("graph pauses and asks for approval", rc == 0 and "AWAITING APPROVAL" in out)
rc, out = agent("status", t)
check("status from a NEW process: awaiting_approval", "status: awaiting_approval" in out)
rc, out = agent("resume", t, "approve")
check("resume from a NEW process publishes", "FINAL STATUS: published" in out and "autoremediate/inc_001" in out)
check("victim repo has the branch with the fix",
      "autoremediate/inc_001" in victim_git("branch", "--list", "autoremediate/inc_001")
      and "DISCOUNTS.get(code)" in victim_git("show", "autoremediate/inc_001:main.py"))
pr = ROOT / "outputs" / "inc_001" / "PR.md"
check("PR.md was written with the approval note", pr.exists() and "Approved by a human reviewer" in pr.read_text(encoding="utf-8"))
rc, out = agent("resume", t, "approve")
check("resuming a finished thread is refused", rc != 0 and "not awaiting approval" in out)

print("== B: reject (inc_002)")
t = f"chk-{RUN}-b"
agent("start", "inc_002", "--fake", "oracle", "--thread", t)
rc, out = agent("resume", t, "reject", "--reason", "style")
check("reject ends the run without publishing", "FINAL STATUS: rejected" in out and "style" in out)
check("no branch was created for the rejected fix", victim_git("branch", "--list", "autoremediate/inc_002").strip() == "")

print("== C: human edit (inc_003)")
t = f"chk-{RUN}-c"
agent("start", "inc_003", "--fake", "oracle", "--thread", t)
gold = parse_gold("inc_003")
edits = [{"path": gold["path"], "old": gold["old"], "new": gold["new"] + "  # reviewed by human"}]
edits_file = Path(tempfile.gettempdir()) / f"edits-{RUN}.json"
edits_file.write_text(json.dumps(edits), encoding="utf-8")
rc, out = agent("resume", t, "edit", "--edits-file", str(edits_file))
check("edited patch is re-verified and approval is requested again",
      "[human edit]" in out and "PASSED" in out and "AWAITING APPROVAL" in out)
rc, out = agent("resume", t, "approve")
check("approving the edited patch publishes it",
      "FINAL STATUS: published" in out and "# reviewed by human" in victim_git("show", "autoremediate/inc_003:main.py"))

print("== D: gives up after 3 failed attempts (inc_004)")
t = f"chk-{RUN}-d"
rc, out = agent("start", "inc_004", "--fake", "bad", "--thread", t)
check("three attempts fail and the run ends as failed, never asking for approval",
      "attempt 3 FAILED" in out and "FINAL STATUS: failed" in out and "AWAITING APPROVAL" not in out)

print("== E: approval is bound to the exact diff (inc_005)")
gold = parse_gold("inc_005")
checkout_incident("inc_005")
replace_in_file("inc_005", gold["path"], gold["old"], gold["new"])
fp = diff_fingerprint("inc_005")["sha256"]
replace_in_file("inc_005", gold["path"], gold["new"], gold["new"] + "  # tampered after approval")
r = publish_branch("inc_005", fp, "autoremediate/inc_005", "msg")
check("publish refuses when the diff changed after approval", r["ok"] is False and "changed after approval" in r["error"])
r = publish_branch("inc_005", fp, "main", "msg")
check("publish refuses a branch name outside autoremediate/", r["ok"] is False and "autoremediate/" in r["error"])
check("nothing was pushed for inc_005", victim_git("branch", "--list", "autoremediate/inc_005").strip() == "")

print(f"SUMMARY: {sum(RESULTS)}/{len(RESULTS)} checks passed")
sys.exit(0 if all(RESULTS) else 1)