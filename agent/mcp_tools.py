"""MCP tools interface for the AutoRemediate agent."""
from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Direct imports from mcp_servers for fast in-process fallback & direct imports
from mcp_servers.repo_server import (
    checkout_incident as _repo_checkout,
    list_files as _repo_list_files,
    read_file as _repo_read_file,
    search_code as _repo_search_code,
    replace_in_file as _repo_replace_in_file,
    apply_patch as _repo_apply_patch,
    get_diff as _repo_get_diff,
    run_tests as _repo_run_tests,
    diff_fingerprint as _repo_diff_fingerprint,
    publish_branch as _repo_publish_branch,
)
from mcp_servers.telemetry_server import (
    list_incidents as _telem_list_incidents,
    get_ticket as _telem_get_ticket,
    search_logs as _telem_search_logs,
    get_error_summary as _telem_get_error_summary,
    get_status_breakdown as _telem_get_status_breakdown,
)

ROOT = Path(__file__).resolve().parent.parent

TELEMETRY_TOOLS = {
    "list_incidents",
    "get_ticket",
    "search_logs",
    "get_error_summary",
    "get_status_breakdown",
}


def _server_params(module: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        cwd=str(ROOT),
        env={**os.environ},
    )


class MCPTools:
    """Convenience wrapper for calling MCP tools in the AutoRemediate environment."""

    @staticmethod
    def checkout_incident(incident_id: str) -> Dict[str, Any]:
        return _repo_checkout(incident_id)

    @staticmethod
    def list_files(incident_id: str) -> Dict[str, Any]:
        return _repo_list_files(incident_id)

    @staticmethod
    def read_file(incident_id: str, path: str, start_line: int = 1, end_line: int = 0) -> Dict[str, Any]:
        return _repo_read_file(incident_id, path, start_line, end_line)

    @staticmethod
    def search_code(incident_id: str, pattern: str, max_results: int = 20) -> Dict[str, Any]:
        return _repo_search_code(incident_id, pattern, max_results)

    @staticmethod
    def replace_in_file(incident_id: str, path: str, old_text: str = "", new_text: str = "", old: str = "", new: str = "") -> Dict[str, Any]:
        return _repo_replace_in_file(incident_id, path, old_text=old_text, new_text=new_text, old=old, new=new)

    @staticmethod
    def apply_patch(incident_id: str, diff: str) -> Dict[str, Any]:
        return _repo_apply_patch(incident_id, diff)

    @staticmethod
    def get_diff(incident_id: str) -> Dict[str, Any]:
        return _repo_get_diff(incident_id)

    @staticmethod
    def run_tests(incident_id: str, timeout_seconds: int = 60) -> Dict[str, Any]:
        return _repo_run_tests(incident_id, timeout_seconds)

    @staticmethod
    def list_incidents() -> Dict[str, Any]:
        return _telem_list_incidents()

    @staticmethod
    def get_ticket(incident_id: str) -> Dict[str, Any]:
        return _telem_get_ticket(incident_id)

    @staticmethod
    def search_logs(incident_id: str, level: str = "", query: str = "", limit: int = 50) -> Dict[str, Any]:
        return _telem_search_logs(incident_id, level, query, limit)

    @staticmethod
    def get_error_summary(incident_id: str) -> Dict[str, Any]:
        return _telem_get_error_summary(incident_id)

    @staticmethod
    def get_status_breakdown(incident_id: str) -> Dict[str, Any]:
        return _telem_get_status_breakdown(incident_id)

    @staticmethod
    def diff_fingerprint(incident_id: str) -> Dict[str, Any]:
        return _repo_diff_fingerprint(incident_id)

    @staticmethod
    def publish_branch(incident_id: str, diff_sha256: str, branch: str, message: str) -> Dict[str, Any]:
        return _repo_publish_branch(incident_id, diff_sha256, branch, message)


# Module-level aliases for direct function calls
checkout_incident = MCPTools.checkout_incident
list_files = MCPTools.list_files
read_file = MCPTools.read_file
search_code = MCPTools.search_code
replace_in_file = MCPTools.replace_in_file
apply_patch = MCPTools.apply_patch
get_diff = MCPTools.get_diff
run_tests = MCPTools.run_tests
diff_fingerprint = MCPTools.diff_fingerprint
publish_branch = MCPTools.publish_branch
list_incidents = MCPTools.list_incidents
get_ticket = MCPTools.get_ticket
search_logs = MCPTools.search_logs
get_error_summary = MCPTools.get_error_summary
get_status_breakdown = MCPTools.get_status_breakdown


class Tools:
    """Async-compatible adapter connecting to MCP servers via stdio_client."""

    def __init__(self):
        self._stack = AsyncExitStack()
        self._telemetry_session: Optional[ClientSession] = None
        self._repo_session: Optional[ClientSession] = None

    async def __aenter__(self):
        await self._stack.__aenter__()
        try:
            r_tel, w_tel = await self._stack.enter_async_context(
                stdio_client(_server_params("mcp_servers.telemetry_server"))
            )
            self._telemetry_session = await self._stack.enter_async_context(
                ClientSession(r_tel, w_tel)
            )
            await self._telemetry_session.initialize()

            r_repo, w_repo = await self._stack.enter_async_context(
                stdio_client(_server_params("mcp_servers.repo_server"))
            )
            self._repo_session = await self._stack.enter_async_context(
                ClientSession(r_repo, w_repo)
            )
            await self._repo_session.initialize()
        except Exception:
            # Fall back to in-process MCPTools if stdio_client fails
            pass
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self._stack.__aexit__(exc_type, exc_val, exc_tb)

    async def call(self, name: str, **args) -> Dict[str, Any]:
        session = self._telemetry_session if name in TELEMETRY_TOOLS else self._repo_session
        if session is not None:
            res = await session.call_tool(name, args)
            text = "".join(c.text for c in res.content if getattr(c, "type", "") == "text")
            if res.isError:
                return {"ok": False, "error": "TOOL ERROR: " + text}
            try:
                return json.loads(text)
            except Exception:
                return {"ok": True, "text": text}

        tool = getattr(MCPTools, name, None)
        if tool is None or not callable(tool):
            raise ValueError(f"Unknown MCP tool: {name}")

        return tool(**args)
