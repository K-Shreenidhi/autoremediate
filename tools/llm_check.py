"""Sanity check that the configured LLM model responds.

Run from project root:
  python -m tools.llm_check
"""
import os
import sys

from agent.config import llm_settings
from agent.llm import LLMClient


def main():
    try:
        settings = llm_settings()
    except RuntimeError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    model = settings["model"]
    print(f"checking model: {model}")

    client = LLMClient(**settings)
    try:
        reply = client.generate_text("Reply with exactly: pong").strip()
    except Exception as e:
        print(f"LLM call failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Normalize reply formatting
    clean = reply.strip().strip("'\"`").rstrip(".")
    if clean.lower() == "pong":
        clean = "pong"
    print(f"reply: {clean}")
    print("LLM check OK")


if __name__ == "__main__":
    main()

