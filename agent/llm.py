"""Google Gemini LLM client with structured output, budgets, and metrics.

All LLM calls go through this module so we can:
  1. Return Pydantic models via Gemini structured output (response_schema)
  2. Track token usage and enforce hard budgets
  3. Report cost metrics in the eval
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel

try:
    from google import genai
    from google.genai import types
except ImportError as exc:
    raise ImportError(
        "google-genai is required.  Install with:  pip install google-genai"
    ) from exc


T = TypeVar("T", bound=BaseModel)

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Hard budgets per incident (can be overridden via env)
MAX_LLM_CALLS = int(os.getenv("MAX_LLM_CALLS", "15"))
MAX_PROMPT_TOKENS = int(os.getenv("MAX_PROMPT_TOKENS", "100000"))
MAX_COMPLETION_TOKENS = int(os.getenv("MAX_COMPLETION_TOKENS", "20000"))

# Rough pricing (per 1M tokens) — used for cost reporting only
PRICING = {
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}


@dataclass
class LLMMetrics:
    """Accumulated metrics for all LLM calls in one agent run."""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    wall_time: float = 0.0
    errors: int = 0
    budget_exhausted: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def estimated_cost_usd(self, model: str) -> float:
        prices = PRICING.get(model, {"input": 0.50, "output": 1.50})
        return (
            self.prompt_tokens * prices["input"] / 1_000_000
            + self.completion_tokens * prices["output"] / 1_000_000
        )


class BudgetExhausted(Exception):
    """Raised when the agent exceeds its hard budget."""
    pass


@dataclass
class GeminiClient:
    """Thin wrapper around google-genai with structured output + budgets."""
    model: str = DEFAULT_MODEL
    api_key: str = ""
    metrics: LLMMetrics = field(default_factory=LLMMetrics)
    _client: object = field(default=None, repr=False)

    def __post_init__(self):
        if not self.api_key:
            self.api_key = os.getenv("GOOGLE_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "GOOGLE_API_KEY is required. Set it in your .env file or environment."
            )
        self._client = genai.Client(api_key=self.api_key)

    def _check_budget(self):
        if self.metrics.calls >= MAX_LLM_CALLS:
            self.metrics.budget_exhausted = True
            raise BudgetExhausted(
                f"LLM call budget exhausted: {self.metrics.calls}/{MAX_LLM_CALLS} calls"
            )
        if self.metrics.prompt_tokens >= MAX_PROMPT_TOKENS:
            self.metrics.budget_exhausted = True
            raise BudgetExhausted(
                f"Prompt token budget exhausted: {self.metrics.prompt_tokens}/{MAX_PROMPT_TOKENS}"
            )

    def generate(
        self,
        prompt: str,
        response_model: type[T],
        *,
        system_instruction: str = "",
        temperature: float = 0.2,
    ) -> T:
        """Call Gemini with structured output, returning a validated Pydantic model.

        Uses Gemini's native response_schema to guarantee the output
        conforms to the Pydantic model — no regex parsing needed.
        """
        self._check_budget()

        config = types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=response_model,
        )
        if system_instruction:
            config.system_instruction = system_instruction

        t0 = time.time()
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
            elapsed = time.time() - t0
        except Exception as e:
            self.metrics.errors += 1
            raise RuntimeError(f"Gemini API error: {e}") from e

        # Track metrics
        self.metrics.calls += 1
        self.metrics.wall_time += elapsed
        if response.usage_metadata:
            self.metrics.prompt_tokens += response.usage_metadata.prompt_token_count or 0
            self.metrics.completion_tokens += response.usage_metadata.candidates_token_count or 0

        # Parse structured output
        text = response.text
        if not text:
            raise RuntimeError("Gemini returned empty response")
        return response_model.model_validate_json(text)

    def generate_text(
        self,
        prompt: str,
        *,
        system_instruction: str = "",
        temperature: float = 0.2,
    ) -> str:
        """Plain text generation (no structured output). Used for free-form steps."""
        self._check_budget()

        config = types.GenerateContentConfig(temperature=temperature)
        if system_instruction:
            config.system_instruction = system_instruction

        t0 = time.time()
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
            elapsed = time.time() - t0
        except Exception as e:
            self.metrics.errors += 1
            raise RuntimeError(f"Gemini API error: {e}") from e

        self.metrics.calls += 1
        self.metrics.wall_time += elapsed
        if response.usage_metadata:
            self.metrics.prompt_tokens += response.usage_metadata.prompt_token_count or 0
            self.metrics.completion_tokens += response.usage_metadata.candidates_token_count or 0

        return response.text or ""
