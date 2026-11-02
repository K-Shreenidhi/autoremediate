"""Deterministic safety policy for model-proposed edits.

A denylist, so it is a speed bump and not a proof of safety: the human approval step
stays the final control. It only flags dangerous constructs that an edit INTRODUCES,
so code that already exists in the file is not blamed.
"""
import re

RULES = [
    (r"\bos\s*\.\s*system\b", "runs shell commands (os.system)"),
    (r"\bsubprocess\b", "spawns processes (subprocess)"),
    (r"\b(?:eval|exec|compile)\s*\(", "executes dynamic code (eval/exec/compile)"),
    (r"__import__|\bimportlib\b", "uses dynamic imports"),
    (r"\b(?:socket|urllib|requests|httpx|aiohttp|ftplib|smtplib|telnetlib|paramiko)\b", "network access"),
    (r"https?://", "contains a URL"),
    (r"\b(?:curl|wget|powershell|bash|cmd\.exe)\b", "mentions a shell or download tool"),
    (r"\bos\s*\.\s*(?:environ|getenv)\b", "reads environment variables"),
    (r"\b(?:shutil\s*\.\s*rmtree|os\s*\.\s*(?:remove|unlink|rmdir|removedirs))\b", "deletes files"),
    (r"\bopen\s*\(", "opens files"),
    (r"\bpickle\b|\bmarshal\b", "unsafe deserialization"),
    (r"\.\./", "path traversal"),
]


def check_edits(edits: list) -> list:
    """Return a sorted list of 'path: problem' strings; empty means the edits pass."""
    problems = set()
    for e in edits:
        old, new = e.get("old", ""), e.get("new", "")
        for pattern, why in RULES:
            if len(re.findall(pattern, new)) > len(re.findall(pattern, old)):
                problems.add(f"{e.get('path', '?')}: {why}")
    return sorted(problems)