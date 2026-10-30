"""Allowlisted tool policy for the diagnose-and-patch agent.

Treats logs as untrusted input.  The agent may only call tools that
appear in the ALLOWED_TOOLS set, and tool arguments are validated
before execution.  This prevents prompt-injection payloads hidden
in log content from tricking the agent into running arbitrary tools.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Tool allowlist ───────────────────────────────────────────────────────────

# Read-only tools: safe to call with any arguments
READ_ONLY_TOOLS = frozenset({
    # telemetry server
    "list_incidents",
    "get_ticket",
    "search_logs",
    "get_error_summary",
    "get_status_breakdown",
    # repo server
    "checkout_incident",
    "list_files",
    "read_file",
    "search_code",
    "get_diff",
    "run_tests",
})

# Write tools: allowed but arguments are validated
WRITE_TOOLS = frozenset({
    "apply_patch",
})

ALLOWED_TOOLS = READ_ONLY_TOOLS | WRITE_TOOLS


# ── Argument sanitization ───────────────────────────────────────────────────

# Patterns that should NEVER appear in tool arguments — these are signs
# of prompt injection leaking from log content into tool calls.
INJECTION_PATTERNS = [
    re.compile(r"curl\s+http", re.IGNORECASE),
    re.compile(r"wget\s+http", re.IGNORECASE),
    re.compile(r"\|\s*sh\b", re.IGNORECASE),
    re.compile(r"\|\s*bash\b", re.IGNORECASE),
    re.compile(r"rm\s+-rf", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"SYSTEM\s+OVERRIDE", re.IGNORECASE),
    re.compile(r"print\s+(all\s+)?api\s+keys", re.IGNORECASE),
    re.compile(r"exec\s*\(", re.IGNORECASE),
    re.compile(r"eval\s*\(", re.IGNORECASE),
    re.compile(r"__import__", re.IGNORECASE),
    re.compile(r"subprocess\.", re.IGNORECASE),
    re.compile(r"os\.system", re.IGNORECASE),
]


@dataclass
class PolicyViolation:
    """A tool call that violates the security policy."""
    tool_name: str
    reason: str
    blocked: bool = True


@dataclass
class ToolPolicy:
    """Enforces the tool allowlist and argument sanitization."""
    violations: list[PolicyViolation] = field(default_factory=list)
    injection_attempts: int = 0

    def check_tool_call(self, tool_name: str, arguments: dict) -> PolicyViolation | None:
        """Validate a tool call.  Returns a PolicyViolation if blocked, else None."""
        # 1. Tool must be in the allowlist
        if tool_name not in ALLOWED_TOOLS:
            v = PolicyViolation(
                tool_name=tool_name,
                reason=f"tool '{tool_name}' is not in the allowlist",
            )
            self.violations.append(v)
            return v

        # 2. Check all string arguments for injection patterns
        for key, value in arguments.items():
            if not isinstance(value, str):
                continue
            for pattern in INJECTION_PATTERNS:
                if pattern.search(value):
                    self.injection_attempts += 1
                    v = PolicyViolation(
                        tool_name=tool_name,
                        reason=(
                            f"argument '{key}' contains suspected injection: "
                            f"matched {pattern.pattern!r}"
                        ),
                    )
                    self.violations.append(v)
                    return v

        return None

    @property
    def is_clean(self) -> bool:
        return len(self.violations) == 0

    def summary(self) -> str:
        if self.is_clean:
            return "No policy violations detected."
        lines = [f"[!] {len(self.violations)} policy violation(s):"]
        for v in self.violations:
            lines.append(f"  - [{v.tool_name}] {v.reason}")
        return "\n".join(lines)
