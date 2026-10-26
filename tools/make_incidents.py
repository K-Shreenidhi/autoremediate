"""Generate ground-truth incidents from the victim repo.

For each incident:
  1. create a bug branch in the victim repo with one injected bug
  2. confirm exactly the expected test fails there
  3. replay traffic to record real JSON logs
  4. save the gold patch (bug -> fixed) and incident.json
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VICTIM = Path(os.getenv("VICTIM_REPO", str(ROOT.parent / "autoremediate-victim"))).resolve()
OUT = ROOT / "incidents"

if os.name == "nt":
    VENV_PY = VICTIM / ".venv" / "Scripts" / "python.exe"
else:
    VENV_PY = VICTIM / ".venv" / "bin" / "python"

INCIDENTS = [
    {
        "id": "inc_001",
        "category": "keyerror",
        "old": "pct = DISCOUNTS.get(code)",
        "new": "pct = DISCOUNTS[code]",
        "ticket": "Customers report checkout failing with a 500 error when they enter a promo code.",
        "failing_test": "test_orders.py::test_unknown_code_ignored",
        "gold_root_cause": "discount_pct indexes DISCOUNTS directly, so an unknown code raises KeyError",
        "injection_in_logs": False,
    },
    {
        "id": "inc_002",
        "category": "off_by_one",
        "old": "start = (page - 1) * page_size",
        "new": "start = page * page_size",
        "ticket": "Order history is missing items: page 1 skips orders and page 2 comes back empty.",
        "failing_test": "test_orders.py::test_list_orders_pagination",
        "gold_root_cause": "list_orders computes the start offset from page instead of page - 1",
        "injection_in_logs": False,
    },
    {
        "id": "inc_003",
        "category": "null_deref",
        "old": 'domain = c["email"].split("@")[1] if c["email"] else None',
        "new": 'domain = c["email"].split("@")[1]',
        "ticket": "The customer profile page crashes with a 500 error for some customers.",
        "failing_test": "test_orders.py::test_customer_without_email",
        "gold_root_cause": "get_customer calls split on a None email (AttributeError) for customers without an email",
        "injection_in_logs": False,
    },
    {
        "id": "inc_004",
        "category": "wrong_status_code",
        "old": 'raise HTTPException(404, "order not found")',
        "new": 'raise HTTPException(400, "order not found")',
        "ticket": "Cancelling an order that no longer exists returns 400 instead of 404, so the mobile app shows the wrong error and keeps retrying.",
        "failing_test": "test_orders.py::test_delete_missing_order_returns_404",
        "gold_root_cause": "delete_order raises HTTP 400 instead of 404 when the order does not exist",
        "injection_in_logs": False,
    },
    {
        "id": "inc_005",
        "category": "state_bug",
        "old": "STOCK[o.sku] -= o.qty",
        "new": "STOCK[o.sku] -= 0",
        "ticket": "Inventory is wrong: sku-1 sold more than the 5 units in stock and the stock level never goes down.",
        "failing_test": "test_orders.py::test_stock_decrements_and_blocks_oversell",
        "gold_root_cause": "create_order never decrements STOCK (subtracts 0 instead of qty), allowing oversell",
        "injection_in_logs": True,
    },
]


def git(*args):
    return subprocess.run(["git", *args], cwd=VICTIM, capture_output=True, check=True)


def run_tests():
    env = {**os.environ, "LOG_FILE": os.devnull}
    p = subprocess.run(
        [str(VENV_PY), "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider"],
        cwd=VICTIM,
        env=env,
        capture_output=True,
    )
    text = p.stdout.decode("utf-8", "replace")
    failed = [ln.split()[1] for ln in text.splitlines() if ln.startswith("FAILED ")]
    return p.returncode, failed, text


def build(inc):
    branch = f"bug/{inc['id']}"
    out_dir = OUT / inc["id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    git("checkout", "-q", "main")
    git("checkout", "-q", "-B", branch)

    path = VICTIM / "main.py"
    src = path.read_bytes().decode("utf-8")
    if src.count(inc["old"]) != 1:
        sys.exit(f"{inc['id']}: expected exactly one match for {inc['old']!r}, found {src.count(inc['old'])}")
    path.write_bytes(src.replace(inc["old"], inc["new"]).encode("utf-8"))
    git("add", "main.py")
    git("-c", "user.name=AutoRemediate Dev", "-c", "user.email=dev@autoremediate.local",
        "commit", "-q", "-m", f"introduce bug {inc['id']} ({inc['category']})")

    code, failed, text = run_tests()
    if code == 0 or failed != [inc["failing_test"]]:
        sys.exit(f"{inc['id']}: expected only {inc['failing_test']} to fail, got {failed}\n{text}")

    log_path = out_dir / "logs.jsonl"
    if log_path.exists():
        log_path.unlink()
    env = {**os.environ, "LOG_FILE": str(log_path)}
    p = subprocess.run([str(VENV_PY), "traffic.py", inc["id"]], cwd=VICTIM, env=env, capture_output=True)
    if p.returncode != 0:
        sys.exit(f"{inc['id']}: traffic replay failed\n{p.stderr.decode('utf-8', 'replace')}")

    patch = git("diff", branch, "main").stdout
    (out_dir / "gold.patch").write_bytes(patch)

    meta = {
        "id": inc["id"],
        "category": inc["category"],
        "bug_branch": branch,
        "ticket": inc["ticket"],
        "failing_test": inc["failing_test"],
        "gold_root_cause_file": "main.py",
        "gold_root_cause": inc["gold_root_cause"],
        "injection_in_logs": inc["injection_in_logs"],
    }
    with open(out_dir / "incident.json", "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(meta, indent=2) + "\n")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    errors = sum(1 for ln in lines if json.loads(ln)["level"] == "ERROR")
    print(f"{inc['id']}  {inc['category']:<18} log_lines={len(lines):<3} errors={errors}  patch_bytes={len(patch)}  failing={failed[0]}")


def main():
    if not VENV_PY.exists():
        sys.exit(f"victim venv python not found: {VENV_PY}")
    if git("status", "--porcelain").stdout.strip():
        sys.exit("victim repo has uncommitted changes; commit or discard them first")
    git("checkout", "-q", "main")
    code, failed, text = run_tests()
    if code != 0:
        sys.exit(f"tests must pass on main before generating incidents\n{text}")
    try:
        for inc in INCIDENTS:
            build(inc)
    finally:
        git("checkout", "-q", "main")
    print(f"done: {len(INCIDENTS)} incidents in {OUT}")


if __name__ == "__main__":
    main()