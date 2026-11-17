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

from openai import OpenAI
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

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

# Hard budgets per incident (can be overridden via env)
MAX_LLM_CALLS = int(os.getenv("MAX_LLM_CALLS", "15"))
MAX_PROMPT_TOKENS = int(os.getenv("MAX_PROMPT_TOKENS", "100000"))
MAX_COMPLETION_TOKENS = int(os.getenv("MAX_COMPLETION_TOKENS", "20000"))

# Rough pricing (per 1M tokens) — used for cost reporting only
PRICING = {
    "gemini-3-flash-preview": {"input": 0.15, "output": 0.60},
    "gemini-3.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-flash-lite": {"input": 0.075, "output": 0.30},
    "gemini-3.1-flash-lite": {"input": 0.075, "output": 0.30},
    "gemini-3.8-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}

# Automatic aliases for deprecated/unavailable Google model identifiers
MODEL_ALIASES = {
    "gemini-2.5-flash": "gemini-3.5-flash",
    "models/gemini-2.5-flash": "gemini-3.5-flash",
    "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
    "models/gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite": "gemini-3.5-flash-lite",
    "models/gemini-3.1-flash-lite": "gemini-3.5-flash-lite",
    "gemini-3.8-flash": "gemini-3.5-flash",
    "models/gemini-3.8-flash": "gemini-3.5-flash",
}


@dataclass
class Usage:
    """Token usage tracking."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 1

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


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
        if self.model in MODEL_ALIASES:
            self.model = MODEL_ALIASES[self.model]
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

    def _call_api(self, config, contents):
        delays = [2.0, 5.0, 10.0]
        last_exc = None
        for attempt in range(len(delays) + 1):
            try:
                return self._client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
            except Exception as e:
                last_exc = e
                msg = str(e).lower()
                is_transient = any(m in msg for m in ("503", "429", "unavailable", "resource_exhausted", "high demand", "spikes in demand"))
                if is_transient and attempt < len(delays):
                    time.sleep(delays[attempt])
                    continue
                raise
        raise last_exc

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
            response = self._call_api(config=config, contents=prompt)
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
            response = self._call_api(config=config, contents=prompt)
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


class LLMClient:
    """Standard LLM client adapter for agent.graph and agent.run."""
    def __init__(self, api_key: str = "", model: str = "", base_url: str = None):
        self.api_key = api_key or os.getenv("LLM_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
        raw_model = model or os.getenv("LLM_MODEL", "") or DEFAULT_MODEL
        self.model = MODEL_ALIASES.get(raw_model, raw_model)
        self.base_url = base_url or os.getenv("LLM_BASE_URL", None)
        self._gemini = None
        self._openai = None
        try:
            self._gemini = GeminiClient(model=self.model, api_key=self.api_key)
        except Exception:
            pass
        try:
            self._openai = OpenAI(api_key=self.api_key or "none", base_url=self.base_url)
        except Exception:
            pass

    def complete_json(self, system: str, prompt: str, model_cls: type, meta: dict = None):
        if self._openai is not None and (self.base_url or (not self.api_key.startswith("AQ.") and not self.api_key.startswith("AIza") and "gemini" not in self.model)):
            try:
                response = self._openai.beta.chat.completions.parse(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    response_format=model_cls,
                )
                res = response.choices[0].message.parsed
                usage = Usage(
                    prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
                    completion_tokens=response.usage.completion_tokens if response.usage else 0,
                    calls=1,
                )
                raw_text = response.choices[0].message.content or ""
                return res, usage, raw_text
            except Exception as e:
                if self._gemini is None:
                    raise
        if self._gemini is not None:
            res = self._gemini.generate(prompt, model_cls, system_instruction=system)
            usage = Usage(
                prompt_tokens=self._gemini.metrics.prompt_tokens,
                completion_tokens=self._gemini.metrics.completion_tokens,
                calls=1,
            )
            raw_text = res.model_dump_json() if hasattr(res, "model_dump_json") else json.dumps(res.model_dump())
            return res, usage, raw_text
        raise RuntimeError("No working LLM backend (neither Gemini nor OpenAI could be initialized)")

    def generate_text(self, prompt: str, system: str = "") -> str:
        if self._gemini is not None and ("gemini" in self.model or self.api_key.startswith("AIza") or self.api_key.startswith("AQ.")):
            return self._gemini.generate_text(prompt, system_instruction=system)
        if self._openai is not None:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = self._openai.chat.completions.create(model=self.model, messages=messages)
            return resp.choices[0].message.content or ""
        if self._gemini is not None:
            return self._gemini.generate_text(prompt, system_instruction=system)
        raise RuntimeError("No working LLM backend (neither Gemini nor OpenAI could be initialized)")
