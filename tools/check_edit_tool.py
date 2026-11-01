"""Checks the replace_in_file tool directly (no LLM needed). Run: python -m tools.check_edit_tool"""
from mcp_servers.repo_server import checkout_incident, get_diff, replace_in_file, run_tests


def show(label, ok):
    print(("PASS  " if ok else "FAIL  ") + label)
    return ok


results = []
results.append(show("checkout inc_001", checkout_incident("inc_001")["ok"]))

r = replace_in_file("inc_001", "test_orders.py", "import pytest", "import pytest  # x")
results.append(show("refuses to edit test files", r["ok"] is False and "protected" in r["error"]))

r = replace_in_file("inc_001", "main.py", "this text is not in the file", "x")
results.append(show("rejects old text that is not found", r["ok"] is False and "not found" in r["error"]))

r = replace_in_file("inc_001", "main.py", "return", "return")
results.append(show("rejects old text that matches several places", r["ok"] is False and "places" in r["error"]))

r = replace_in_file("inc_001", "main.py", "    pct = DISCOUNTS[code]", "    pct = DISCOUNTS[code")
results.append(show("rejects an edit that breaks Python syntax", r["ok"] is False and "syntax error" in r["error"]))
results.append(show("workspace unchanged after rejected edits", get_diff("inc_001")["diff"] == ""))

r = replace_in_file("inc_001", "main.py", "    pct = DISCOUNTS[code]", "    pct = DISCOUNTS.get(code)")
results.append(show("applies a valid edit", r["ok"] is True))
results.append(show("diff shows the fix", "+    pct = DISCOUNTS.get(code)" in get_diff("inc_001")["diff"]))

t = run_tests("inc_001")
results.append(show("tests pass after the edit", t["tests_passed"] is True and "11 passed" in t["summary"]))

print(f"SUMMARY: {sum(results)}/{len(results)} checks passed")