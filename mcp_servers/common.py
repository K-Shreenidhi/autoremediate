"""Shared paths and helpers for the MCP servers."""
import functools
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCIDENTS_DIR = ROOT / "incidents"
WORKSPACES_DIR = ROOT / "workspaces"
VICTIM = Path(os.getenv("VICTIM_REPO", str(ROOT.parent / "autoremediate-victim"))).resolve()

if os.name == "nt":
    VICTIM_PY = VICTIM / ".venv" / "Scripts" / "python.exe"
else:
    VICTIM_PY = VICTIM / ".venv" / "bin" / "python"

INCIDENT_ID_RE = re.compile(r"^inc_\d{3}$")


def load_incident(incident_id: str) -> dict:
    """Load incident metadata. Raises ValueError for bad or unknown ids."""
    if not INCIDENT_ID_RE.match(incident_id):
        raise ValueError(f"invalid incident id: {incident_id!r}")
    path = INCIDENTS_DIR / incident_id / "incident.json"
    if not path.exists():
        raise ValueError(f"unknown incident: {incident_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def rmtree_force(path: Path) -> None:
    """Delete a directory tree, including read-only files (git objects on Windows)."""

    def handler(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=handler)
    else:
        shutil.rmtree(path, onerror=handler)


def safe(fn):
    """Turn expected errors (ValueError) into {"ok": False, "error": ...} results."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    return wrapper