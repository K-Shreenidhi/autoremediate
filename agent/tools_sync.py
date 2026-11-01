"""Synchronous wrapper around the MCP tools.

The MCP servers run on their own event loop in a background thread, so the
LangGraph workflow (and the Postgres checkpointer) can be plain synchronous code.
This avoids a Windows problem: psycopg's async mode cannot use the default
Windows event loop, while MCP subprocesses need it.
"""
import asyncio
import threading

from agent.mcp_tools import Tools


class SyncTools:
    def __enter__(self):
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._error = None
        self._tools = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._error is not None:
            raise self._error
        return self

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        self._stop = asyncio.Event()
        try:
            async with Tools() as tools:
                self._tools = tools
                self._ready.set()
                await self._stop.wait()
        except BaseException as e:
            self._error = e
            self._ready.set()

    def call(self, name: str, **args) -> dict:
        future = asyncio.run_coroutine_threadsafe(self._tools.call(name, **args), self._loop)
        return future.result()

    def __exit__(self, *exc):
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=30)