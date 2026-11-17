"""Generate the hard incident suite from the second victim repo (autoremediate-victim2).

For each incident: create a bug branch with one injected bug, confirm exactly the expected
test fails there, replay traffic to record real logs, and save the gold patch.
Run from the project root:  python -m tools.make_incidents_v2
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp_servers.common import VICTIM_PY

ROOT = Path(__file__).resolve().parent.parent
VICTIM2 = Path(os.getenv("VICTIM2_REPO", str(ROOT.parent / "autoremediate-victim2"))).resolve()
OUT = ROOT / "incidents"

INCIDENTS = [
    {
        "id": "inc_201", "category": "logic_cross_step", "file": "pricing.py",
        "old": "tax = tax_cents(discounted)", "new": "tax = tax_cents(subtotal)",
        "ticket": "Customers who use a coupon code are being charged more sales tax than they expect on their receipts.",
        "failing_test": "test_pricing.py::test_coupon_discount_applies_before_tax",
        "gold_root_cause": "price_order calculates tax on the original subtotal instead of the discounted subtotal",
    },
    {
        "id": "inc_202", "category": "boundary", "file": "pricing.py",
        "old": "discounted_subtotal >= FREE_SHIPPING_FROM", "new": "discounted_subtotal > FREE_SHIPPING_FROM",
        "ticket": "Customers whose cart is exactly 50.00 are charged shipping even though shipping is supposed to be free at 50.00.",
        "failing_test": "test_pricing.py::test_free_shipping_at_threshold",
        "gold_root_cause": "shipping_cents uses a strict comparison, so an order exactly at the free-shipping threshold still pays shipping",
    },
    {
        "id": "inc_203", "category": "rounding", "file": "pricing.py",
        "old": "return div_round_half_up(subtotal * pct, 100)", "new": "return subtotal * pct // 100",
        "ticket": "Coupon discounts are sometimes one cent smaller than the amount customers calculate themselves.",
        "failing_test": "test_pricing.py::test_discount_rounds_half_up",
        "gold_root_cause": "discount_cents truncates the discount instead of rounding half up",
    },
    {
        "id": "inc_204", "category": "boundary", "file": "inventory.py",
        "old": "if self.available(sku) < qty:", "new": "if self.available(sku) <= qty:",
        "ticket": "Customers cannot buy the last units of an item: asking for exactly the number still in stock is rejected as out of stock.",
        "failing_test": "test_inventory.py::test_can_reserve_the_exact_remaining_stock",
        "gold_root_cause": "reserve rejects a request that equals the available stock because it uses <= instead of <",
    },
    {
        "id": "inc_205", "category": "cross_module_state", "file": "orders.py",
        "old": 'self.inventory.release(line["sku"], line["qty"])', "new": 'self.inventory.release(line["sku"], 1)',
        "ticket": "After customers cancel multi-item orders, the stock counts do not come back to what they were before the order.",
        "failing_test": "test_orders.py::test_cancel_returns_every_unit_to_stock",
        "gold_root_cause": "cancel releases a single unit per order line instead of the ordered quantity",
    },
    {
        "id": "inc_206", "category": "rollback_leak", "file": "orders.py",
        "old": "for done in reserved:", "new": "for done in reserved[:-1]:",
        "ticket": "When an order fails because one item is out of stock, the other items in that order stay missing from our stock counts.",
        "failing_test": "test_orders.py::test_failed_order_does_not_leak_stock",
        "gold_root_cause": "the rollback in create skips the last successfully reserved line (reserved[:-1]), so its stock is never returned",
    },
    {
        "id": "inc_207", "category": "status_mismatch", "file": "reports.py",
        "old": 'if order["status"] == "cancelled":', "new": 'if order["status"] == "refunded":',
        "ticket": "The daily sales report shows more orders and more revenue than we actually collected; cancelled orders seem to be included.",
        "failing_test": "test_reports.py::test_cancelled_orders_are_not_counted",
        "gold_root_cause": "daily_report skips orders with status refunded, but cancelled orders have status cancelled, so they are counted",
    },
    {
        "id": "inc_208", "category": "none_propagation", "file": "orders.py",
        "old": "stamp = created_at or datetime.now(timezone.utc)", "new": "stamp = created_at",
        "ticket": "The daily sales report page returns a server error after new orders are placed.",
        "failing_test": "test_orders.py::test_create_sets_created_at",
        "gold_root_cause": "create no longer defaults created_at to the current time, so orders have no timestamp and the report fails on them",
    },
    {
        "id": "inc_209", "category": "timezone", "file": "reports.py",
        "old": "return timedelta(hours=5, minutes=30)", "new": "return timedelta(hours=5)",
        "ticket": "Orders placed just after midnight in India show up on the previous day's sales report.",
        "failing_test": "test_reports.py::test_order_after_local_midnight_belongs_to_next_day",
        "gold_root_cause": "store_offset returns UTC+05:00 instead of UTC+05:30, so orders in the first 30 minutes of the local day land on the previous day",
    },
    {
        "id": "inc_210", "category": "missing_guard", "file": "orders.py",
        "old": 'if order["status"] != "pending":', "new": 'if order["status"] == "cancelled":',
        "ticket": "Finance noticed refunds being issued for orders that had already shipped; shipped orders should not be cancellable.",
        "failing_test": "test_orders.py::test_cannot_cancel_a_shipped_order",
        "gold_root_cause": "cancel only blocks orders that are already cancelled, so shipped orders can be cancelled and refunded",
    },
]


def git(*args):
    return subprocess.run(["git", *args], cwd=VICTIM2, capture_output=True, check=True)


def run_tests():
    env = {**os.environ, "LOG_FILE": os.devnull}
    p = subprocess.run(
        [str(VICTIM_PY), "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider"],
        cwd=VICTIM2, env=env, capture_output=True,
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

    path = VICTIM2 / inc["file"]
    src = path.read_bytes().decode("utf-8")
    if src.count(inc["old"]) != 1:
        sys.exit(f"{inc['id']}: expected exactly one match for {inc['old']!r} in {inc['file']}, found {src.count(inc['old'])}")
    path.write_bytes(src.replace(inc["old"], inc["new"]).encode("utf-8"))
    git("add", inc["file"])
    git("-c", "user.name=AutoRemediate Dev", "-c", "user.email=dev@autoremediate.local",
        "commit", "-q", "-m", f"introduce bug {inc['id']} ({inc['category']})")

    code, failed, text = run_tests()
    if code == 0 or failed != [inc["failing_test"]]:
        sys.exit(f"{inc['id']}: expected only {inc['failing_test']} to fail, got {failed}\n{text}")

    log_path = out_dir / "logs.jsonl"
    if log_path.exists():
        log_path.unlink()
    env = {**os.environ, "LOG_FILE": str(log_path)}
    p = subprocess.run([str(VICTIM_PY), "traffic.py", inc["id"]], cwd=VICTIM2, env=env, capture_output=True)
    if p.returncode != 0:
        sys.exit(f"{inc['id']}: traffic replay failed\n{p.stderr.decode('utf-8', 'replace')}")

    (out_dir / "gold.patch").write_bytes(git("diff", branch, "main").stdout)

    meta = {
        "id": inc["id"],
        "category": inc["category"],
        "bug_branch": branch,
        "ticket": inc["ticket"],
        "failing_test": inc["failing_test"],
        "gold_root_cause_file": inc["file"],
        "gold_root_cause": inc["gold_root_cause"],
        "injection_in_logs": False,
        "victim": "autoremediate-victim2",
        "suite": "hard",
    }
    with open(out_dir / "incident.json", "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(meta, indent=2) + "\n")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    errors = sum(1 for ln in lines if json.loads(ln)["level"] == "ERROR")
    print(f"{inc['id']}  {inc['category']:<18} log_lines={len(lines):<3} errors={errors}  failing={failed[0]}")


def main():
    if not VICTIM_PY.exists():
        sys.exit(f"python of the first victim's virtualenv not found: {VICTIM_PY}")
    if not VICTIM2.exists():
        sys.exit(f"second victim repo not found: {VICTIM2}")
    if git("status", "--porcelain").stdout.strip():
        sys.exit("the second victim repo has uncommitted changes; commit or discard them first")
    git("checkout", "-q", "main")
    code, failed, text = run_tests()
    if code != 0:
        sys.exit(f"tests must pass on main before generating incidents\n{text}")
    print("healthy main: all tests pass")
    try:
        for inc in INCIDENTS:
            build(inc)
    finally:
        git("checkout", "-q", "main")
    print(f"done: {len(INCIDENTS)} incidents in {OUT}")


if __name__ == "__main__":
    main()