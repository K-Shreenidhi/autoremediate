"""Shared paths and helpers for the MCP servers."""
import functools
import json
import os
import re
import shutil
import stat
import sys
import time
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


def rmtree_force(path: Path, attempts: int = 6) -> None:
    """Delete a directory tree, including read-only files.

    Retries because Windows can briefly lock files (antivirus, the search indexer,
    a git process that just exited).
    """

    def handler(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    last = None
    for n in range(attempts):
        if not path.exists():
            return
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=handler)
            else:
                shutil.rmtree(path, onerror=handler)
            return
        except OSError as e:
            last = e
            time.sleep(0.4 * (n + 1))
    raise ValueError(
        f"could not delete {path}: a file is locked by another program ({last}). "
        "Close editors or Explorer windows on that folder, or exclude the project folder from antivirus scanning."
    )


def safe(fn):
    """Turn expected errors (ValueError) into {"ok": False, "error": ...} results."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    return wrapper

    
def victim_path(meta: dict) -> Path:
    """Folder of the victim repo an incident belongs to (default: the original victim)."""
    name = meta.get("victim")
    if os.getenv("VICTIM_REPO") or not name:
        return VICTIM
    return (ROOT.parent / name).resolve()