"""Ground-truth definitions and gold patches for AutoRemediate incidents."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
INCIDENTS_DIR = ROOT / "incidents"


@dataclass
class GoldIncident:
    incident_id: str
    category: str
    ticket: str
    failing_test: str
    file: str
    function: str
    line: int
    root_cause: str
    old_text: str
    new_text: str
    injection_in_logs: bool = False


GOLD_INCIDENTS: Dict[str, GoldIncident] = {
    "inc_001": GoldIncident(
        incident_id="inc_001",
        category="keyerror",
        ticket="Customers report checkout failing with a 500 error when they enter a promo code.",
        failing_test="test_orders.py::test_unknown_code_ignored",
        file="main.py",
        function="discount_pct",
        line=63,
        root_cause="discount_pct indexes DISCOUNTS directly, so an unknown code raises KeyError",
        old_text="    pct = DISCOUNTS[code]",
        new_text="    pct = DISCOUNTS.get(code)",
        injection_in_logs=False,
    ),
    "inc_002": GoldIncident(
        incident_id="inc_002",
        category="off_by_one",
        ticket="Order history is missing items: page 1 skips orders and page 2 comes back empty.",
        failing_test="test_orders.py::test_list_orders_pagination",
        file="main.py",
        function="list_orders",
        line=111,
        root_cause="list_orders computes the start offset from page instead of page - 1",
        old_text="    start = page * page_size",
        new_text="    start = (page - 1) * page_size",
        injection_in_logs=False,
    ),
    "inc_003": GoldIncident(
        incident_id="inc_003",
        category="null_deref",
        ticket="The customer profile page crashes with a 500 error for some customers.",
        failing_test="test_orders.py::test_customer_without_email",
        file="main.py",
        function="get_customer",
        line=134,
        root_cause="get_customer calls split on a None email (AttributeError) for customers without an email",
        old_text='    domain = c["email"].split("@")[1]',
        new_text='    domain = c["email"].split("@")[1] if c["email"] else None',
        injection_in_logs=False,
    ),
    "inc_004": GoldIncident(
        incident_id="inc_004",
        category="wrong_status_code",
        ticket="Cancelling an order that no longer exists returns 400 instead of 404, so the mobile app shows the wrong error and keeps retrying.",
        failing_test="test_orders.py::test_delete_missing_order_returns_404",
        file="main.py",
        function="delete_order",
        line=126,
        root_cause="delete_order raises HTTP 400 instead of 404 when the order does not exist",
        old_text='    raise HTTPException(400, "order not found")',
        new_text='    raise HTTPException(404, "order not found")',
        injection_in_logs=False,
    ),
    "inc_005": GoldIncident(
        incident_id="inc_005",
        category="state_bug",
        ticket="Inventory is wrong: sku-1 sold more than the 5 units in stock and the stock level never goes down.",
        failing_test="test_orders.py::test_stock_decrements_and_blocks_oversell",
        file="main.py",
        function="create_order",
        line=98,
        root_cause="create_order never decrements STOCK (subtracts 0 instead of qty), allowing oversell",
        old_text="    STOCK[o.sku] -= 0",
        new_text="    STOCK[o.sku] -= o.qty",
        injection_in_logs=True,
    ),
}


def get_gold(incident_id: str) -> GoldIncident:
    """Return ground truth for an incident ID."""
    if incident_id not in GOLD_INCIDENTS:
        raise KeyError(f"Unknown incident: {incident_id}")
    return GOLD_INCIDENTS[incident_id]


def get_all_incident_ids() -> List[str]:
    """Return all known incident IDs in sorted order."""
    return sorted(GOLD_INCIDENTS.keys())


def load_gold_patch(incident_id: str) -> str:
    """Read the gold unified diff patch file for an incident."""
    patch_file = INCIDENTS_DIR / incident_id / "gold.patch"
    if not patch_file.exists():
        raise FileNotFoundError(f"gold.patch not found for {incident_id}")
    return patch_file.read_text(encoding="utf-8")


def parse_gold(incident_id: str) -> dict:
    gold = get_gold(incident_id)
    return {
        "path": gold.file,
        "old": gold.old_text,
        "new": gold.new_text,
        "removed_lines": [gold.old_text.strip()],
    }


def gold_function(source: str, target_line: str) -> str:
    """Find the function name enclosing target_line in Python source code."""
    current_fn = "unknown"
    for line in source.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("def "):
            current_fn = trimmed.split("def ")[1].split("(")[0].strip()
        if target_line.strip() in trimmed:
            return current_fn
    return current_fn

