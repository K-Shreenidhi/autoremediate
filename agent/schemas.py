"""Pydantic structured-output models and schemas for the AutoRemediate agent."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

# Re-export from agent.models if available
try:
    from agent.models import (
        Severity,
        AppFrame,
        Diagnosis as ModelDiagnosis,
        PatchProposal as ModelPatchProposal,
        ToolCall,
        AgentStep,
        AgentResult as ModelAgentResult,
        IncidentScore,
        EvalRun,
    )
except ImportError:
    pass


class Severity(str, Enum):
    """Incident severity level."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AppFrame(BaseModel):
    """A single frame from an application stack trace."""
    file: str = Field(description="Filename where the error occurs")
    line: int = Field(description="Line number in the file")
    function: str = Field(description="Function or method name")


class Diagnosis(BaseModel):
    """Structured diagnosis produced by the agent."""
    incident_id: str = Field(description="The incident being diagnosed, e.g. inc_001")
    root_cause: str = Field(description="One-sentence root cause explanation")
    severity: Severity = Field(default=Severity.MEDIUM, description="Severity assessment")
    affected_file: str = Field(description="Primary file that needs to be patched")
    affected_function: str = Field(description="Function containing the bug")
    affected_line: int = Field(description="Approximate line number of the bug")
    evidence: List[str] = Field(
        default_factory=list,
        description="Key log entries or code snippets that support the diagnosis",
    )
    reasoning: str = Field(default="", description="Step-by-step reasoning that led to this diagnosis")


class Edit(BaseModel):
    """An exact string replacement in a file."""
    path: str
    old: str
    new: str


class DiagnosisOutput(BaseModel):
    root_cause: str
    confidence_score: float = 1.0
    affected_files: List[str] = Field(default_factory=list)
    affected_functions: List[str] = Field(default_factory=list)


class PatchOutput(BaseModel):
    edits: List[Edit] = Field(default_factory=list)
    rollback_strategy: str = ""


class AgentOutput(BaseModel):
    diagnosis: DiagnosisOutput
    patch: PatchOutput


class FileEdit(BaseModel):
    """An exact string replacement in a file."""
    path: str = Field(description="Path to the file to modify")
    old_text: str = Field(default="", description="Exact string to replace")
    new_text: str = Field(default="", description="New string to substitute")


class PatchProposal(BaseModel):
    """A patch or edit proposal produced by the agent."""
    incident_id: str = Field(description="The incident being patched")
    diagnosis_summary: str = Field(default="", description="One-line summary of the root cause")
    unified_diff: str = Field(
        default="",
        description="Complete unified diff if using diff-based patching",
    )
    edit: Optional[FileEdit] = Field(
        default=None,
        description="Exact file edit if using replace_in_file",
    )
    explanation: str = Field(default="", description="Explanation of what the fix changes and why")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Self-assessed confidence (0-1)")


class ToolCall(BaseModel):
    """Record of a tool invocation."""
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    result_ok: Optional[bool] = None
    result_snippet: str = ""


class AgentStep(BaseModel):
    """One step in agent execution trace."""
    step_number: int
    action: str
    tool_calls: List[ToolCall] = Field(default_factory=list)
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0


class AgentResult(BaseModel):
    """Full result from one agent run against one incident."""
    incident_id: str
    diagnosis: Optional[Diagnosis] = None
    patch: Optional[PatchProposal] = None
    resolved: bool = Field(default=False, description="Whether tests pass after fix")
    localized: bool = Field(default=False, description="Whether the bug location was correctly identified")
    injection_tested: bool = Field(default=False, description="Whether incident has prompt injection in logs")
    injection_detected: bool = Field(default=False, description="Whether injection was detected")
    injection_followed: bool = Field(default=False, description="Whether injection was followed (FAILURE)")
    steps: List[AgentStep] = Field(default_factory=list)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_llm_calls: int = 0
    wall_time_seconds: float = 0.0
    error: Optional[str] = None

