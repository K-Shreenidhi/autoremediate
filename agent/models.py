"""Pydantic structured-output models for the diagnose-and-patch agent.

Every LLM call returns one of these models via Gemini structured output,
so downstream code never parses free-form text.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Diagnosis ────────────────────────────────────────────────────────────────

class Severity(str, Enum):
    """Incident severity as judged by the agent."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AppFrame(BaseModel):
    """A single frame from an application stack trace."""
    file: str = Field(description="Filename (basename) where the error occurs")
    line: int = Field(description="Line number in the file")
    function: str = Field(description="Function or method name")


class Diagnosis(BaseModel):
    """Structured diagnosis produced by the agent after inspecting logs + code."""
    incident_id: str = Field(description="The incident being diagnosed, e.g. inc_001")
    root_cause: str = Field(description="One-sentence root cause explanation")
    severity: Severity = Field(description="Severity assessment")
    affected_file: str = Field(description="Primary file that needs to be patched")
    affected_function: str = Field(description="Function containing the bug")
    affected_line: int = Field(description="Approximate line number of the bug")
    evidence: list[str] = Field(
        default_factory=list,
        description="Key log entries or code snippets that support the diagnosis (max 5)",
    )
    reasoning: str = Field(description="Step-by-step reasoning that led to this diagnosis")


# ── Patch proposal ───────────────────────────────────────────────────────────

class PatchProposal(BaseModel):
    """A unified diff proposed by the agent to fix the incident."""
    incident_id: str = Field(description="The incident being patched")
    diagnosis_summary: str = Field(description="One-line summary of the root cause")
    unified_diff: str = Field(
        description=(
            "Complete unified diff (with --- a/ and +++ b/ headers) "
            "that fixes the bug. Must be directly applyable with git apply."
        )
    )
    explanation: str = Field(
        description="Plain-English explanation of what the patch changes and why"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Agent's self-assessed confidence that this patch is correct (0-1)",
    )


# ── Agent step log (for tracing / eval) ──────────────────────────────────────

class ToolCall(BaseModel):
    """Record of a single tool invocation by the agent."""
    tool_name: str
    arguments: dict
    result_ok: Optional[bool] = None
    result_snippet: str = Field(
        default="",
        description="First 500 chars of the tool result for audit",
    )


class AgentStep(BaseModel):
    """One step in the agent's execution trace."""
    step_number: int
    action: str = Field(description="What the agent decided to do")
    tool_calls: list[ToolCall] = Field(default_factory=list)
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0


class AgentResult(BaseModel):
    """Full result from one agent run against one incident."""
    incident_id: str
    diagnosis: Optional[Diagnosis] = None
    patch: Optional[PatchProposal] = None
    steps: list[AgentStep] = Field(default_factory=list)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_llm_calls: int = 0
    wall_time_seconds: float = 0.0
    error: Optional[str] = None
    injection_detected: bool = Field(
        default=False,
        description="True if the agent detected prompt injection in logs",
    )
    injection_followed: bool = Field(
        default=False,
        description="True if the agent followed injected instructions (FAILURE)",
    )


# ── Eval results ─────────────────────────────────────────────────────────────

class IncidentScore(BaseModel):
    """Evaluation score for one incident."""
    incident_id: str
    category: str
    tests_passed_before: bool = False
    tests_passed_after: bool = False
    patch_applied: bool = False
    target_test_fixed: bool = Field(
        default=False,
        description="The specific failing test went red→green",
    )
    full_suite_green: bool = Field(
        default=False,
        description="All tests pass after the patch",
    )
    patch_correct: bool = Field(
        default=False,
        description="Functionally equivalent to the gold patch",
    )
    injection_safe: bool = Field(
        default=True,
        description="Agent did NOT follow injected instructions",
    )
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    wall_time_seconds: float = 0.0
    error: Optional[str] = None


class EvalRun(BaseModel):
    """Summary of a full evaluation run across all incidents."""
    run_id: str
    model: str
    timestamp: str
    incidents: list[IncidentScore] = Field(default_factory=list)
    pass_at_1: float = Field(
        default=0.0,
        description="Fraction of incidents where full_suite_green=True",
    )
    total_llm_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_wall_time_seconds: float = 0.0
