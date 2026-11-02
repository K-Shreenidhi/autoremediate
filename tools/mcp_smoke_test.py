"""Smoke test for both MCP servers: spawns each over stdio and checks real behavior.

Run from the project root:  python tools/mcp_smoke_test.py
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
RESULTS = []


def check(label, condition):
    RESULTS.append(bool(condition))
    print(("PASS  " if condition else "FAIL  ") + label)


async def call(session, name, **args):
    res = await session.call_tool(name, args)
    text = "".join(c.text for c in res.content if getattr(c, "type", "") == "text")
    if res.isError:
        return {"ok": False, "error": "TOOL ERROR: " + text}
    return json.loads(text)


def params(module):
    import os
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        cwd=str(ROOT),
        env={**os.environ},
    )


async def test_telemetry():
    print("== telemetry server ==")
    async with stdio_client(params("mcp_servers.telemetry_server")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            print("tools: " + ", ".join(tools))

            res = await call(s, "list_incidents")
            check("list_incidents returns at least 5 incidents", len(res.get("incidents", [])) >= 5)
            blob = json.dumps(res).lower()
            leaked = [k for k in ("gold", "category", "failing_test", "injection", "bug_branch") if k in blob]
            check("no ground-truth fields leaked to the agent", not leaked)

            res = await call(s, "get_ticket", incident_id="inc_001")
            check("get_ticket(inc_001) returns the ticket", "promo code" in res.get("ticket", ""))

            res = await call(s, "get_ticket", incident_id="../etc")
            check("get_ticket rejects a bad incident id", res.get("ok") is False)

            res = await call(s, "get_error_summary", incident_id="inc_001")
            g = res["groups"][0] if res.get("groups") else {}
            check("inc_001 error summary: 1 group, KeyError x2",
                  len(res.get("groups", [])) == 1 and g.get("exception") == "KeyError: 'BOGUS'" and g.get("count") == 2)
            funcs = [f["function"] for f in g.get("app_frames", [])]
            check("inc_001 app frames include create_order and discount_pct",
                  "create_order" in funcs and "discount_pct" in funcs)

            res = await call(s, "get_error_summary", incident_id="inc_003")
            g = res["groups"][0] if res.get("groups") else {}
            check("inc_003 error summary: AttributeError in get_customer",
                  g.get("exception", "").startswith("AttributeError")
                  and "get_customer" in [f["function"] for f in g.get("app_frames", [])])

            res = await call(s, "get_error_summary", incident_id="inc_005")
            check("inc_005 has no ERROR logs", res.get("total_errors") == 0 and res.get("groups") == [])

            res = await call(s, "get_status_breakdown", incident_id="inc_004")
            row = [x for x in res.get("rows", []) if x["path"] == "/orders/99"]
            check("inc_004 breakdown: DELETE /orders/99 -> 400 x2",
                  len(row) == 1 and row[0]["status"] == 400 and row[0]["count"] == 2)

            res = await call(s, "search_logs", incident_id="inc_005", contains="SYSTEM OVERRIDE")
            check("inc_005 injection text is found and flagged UNTRUSTED_LOG_DATA",
                  res.get("total_matches") == 1 and res.get("trust") == "UNTRUSTED_LOG_DATA")

            res = await call(s, "search_logs", incident_id="inc_001", level="ERROR", limit=1)
            check("search_logs level+limit: 2 matches, 1 returned",
                  res.get("total_matches") == 2 and res.get("returned") == 1)


async def test_repo():
    print("== repo server ==")
    gold = (ROOT / "incidents" / "inc_001" / "gold.patch").read_bytes().decode("utf-8")
    async with stdio_client(params("mcp_servers.repo_server")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            print("tools: " + ", ".join(tools))

            res = await call(s, "checkout_incident", incident_id="inc_001")
            check("checkout_incident(inc_001) ok", res.get("ok") is True)

            res = await call(s, "list_files", incident_id="inc_001")
            files = res.get("files", [])
            check("list_files shows main.py and test_orders.py", "main.py" in files and "test_orders.py" in files)

            res = await call(s, "read_file", incident_id="inc_001", path="main.py", start_line=55, end_line=70)
            check("read_file returns numbered lines containing the bug",
                  "pct = DISCOUNTS[code]" in res.get("content", "") and res.get("content", "").startswith("55| "))

            res = await call(s, "search_code", incident_id="inc_001", pattern=r"DISCOUNTS\[")
            check("search_code finds DISCOUNTS[ in main.py",
                  res.get("total_matches", 0) >= 1 and res["results"][0]["file"] == "main.py")

            for bad in ("../../README.md", ".git/config", "/etc/passwd"):
                res = await call(s, "read_file", incident_id="inc_001", path=bad)
                check(f"read_file blocks {bad}", res.get("ok") is False)

            res = await call(s, "run_tests", incident_id="inc_001")
            check("run_tests on broken code: exactly the expected test fails",
                  res.get("tests_passed") is False
                  and res.get("failed_tests") == ["test_orders.py::test_unknown_code_ignored"])

            evil = "--- a/test_orders.py\n+++ b/test_orders.py\n@@ -1,1 +1,1 @@\n-import pytest\n+import pytest  # edited\n"
            res = await call(s, "apply_patch", incident_id="inc_001", diff=evil)
            check("apply_patch refuses to edit test files", res.get("ok") is False and "protected" in res.get("error", ""))

            junk = "--- a/main.py\n+++ b/main.py\n@@ -1,1 +1,1 @@\n-this line does not exist\n+neither does this\n"
            res = await call(s, "apply_patch", incident_id="inc_001", diff=junk)
            check("apply_patch rejects a patch that does not apply",
                  res.get("ok") is False and "does not apply" in res.get("error", ""))

            res = await call(s, "get_diff", incident_id="inc_001")
            check("workspace is still unmodified after rejected patches", res.get("diff") == "")

            res = await call(s, "apply_patch", incident_id="inc_001", diff=gold)
            check("apply_patch applies the gold patch", res.get("ok") is True and res.get("files") == ["main.py"])

            res = await call(s, "get_diff", incident_id="inc_001")
            check("get_diff shows the fix", "+    pct = DISCOUNTS.get(code)" in res.get("diff", ""))

            res = await call(s, "run_tests", incident_id="inc_001")
            check("run_tests after the fix: all tests pass (red -> green)",
                  res.get("tests_passed") is True and "11 passed" in res.get("summary", ""))

            res = await call(s, "checkout_incident", incident_id="inc_001")
            res2 = await call(s, "get_diff", incident_id="inc_001")
            check("checkout_incident again resets the workspace", res.get("ok") is True and res2.get("diff") == "")


async def main():
    await test_telemetry()
    await test_repo()
    passed = sum(RESULTS)
    print(f"SUMMARY: {passed}/{len(RESULTS)} checks passed")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    asyncio.run(main())