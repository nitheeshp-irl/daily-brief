"""Session manager for the third-party LinkedIn MCP server (streamable-http transport).

Lifecycle: start (free port) -> initialize handshake -> tool calls -> stop.
We never kill processes we didn't start, and we pin the server version by default.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
from typing import Any

from .errors import MCPError

log = logging.getLogger(__name__)

_call_id = 100


def _next_id() -> int:
    global _call_id
    _call_id += 1
    return _call_id


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def parse_response(raw: str, call_id: int) -> dict | None:
    """The server answers with plain JSON or an SSE stream ("data: {...}")."""
    for line in raw.splitlines():
        line = line[5:].strip() if line.startswith("data:") else line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("id") == call_id:
            return msg
    try:
        msg = json.loads(raw)
        if isinstance(msg, dict) and msg.get("id") == call_id:
            return msg
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def package_spec(package: str, version: str) -> str:
    return f"{package}=={version}" if version else f"{package}@latest"


class MCPClient:
    def __init__(self, package: str = "mcp-server-linkedin", version: str = "4.29.0",
                 retries: int = 2):
        self.spec = package_spec(package, version)
        self.retries = retries
        self.port = 0
        self._proc: asyncio.subprocess.Process | None = None
        self._session_id: str | None = None
        if not version:
            log.warning("MCP server version is not pinned (sources.linkedin.mcp_version); "
                        "a new upstream release would run with your logged-in session.")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    async def __aenter__(self) -> MCPClient:
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    async def start(self) -> None:
        import aiohttp

        if not shutil.which("uvx"):
            raise MCPError("`uvx` not found. Install uv: https://docs.astral.sh/uv/")
        self.port = free_port()
        log.info("Starting LinkedIn MCP server %s on port %d", self.spec, self.port)
        self._proc = await asyncio.create_subprocess_exec(
            "uvx", self.spec, "--transport", "streamable-http",
            "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "WARNING",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "UV_HTTP_TIMEOUT": "300"},
        )
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 60
        while loop.time() < deadline:
            if self._proc.returncode is not None:
                raise MCPError(f"MCP server exited early (code {self._proc.returncode}). "
                               f"Try running `uvx {self.spec} --status` manually.")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    break
            except OSError:
                await asyncio.sleep(0.5)
        else:
            await self.stop()
            raise MCPError("MCP server did not open its port within 60s")

        json_hdr = {"Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream"}
        async with aiohttp.ClientSession() as s:
            init = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "daily-brief", "version": "0.2"}}}
            async with s.post(self.url, data=json.dumps(init), headers=json_hdr,
                              timeout=aiohttp.ClientTimeout(total=20)) as r:
                self._session_id = r.headers.get("mcp-session-id")
                await r.read()
            if not self._session_id:
                await self.stop()
                raise MCPError("MCP server returned no session id on initialize")
            async with s.post(
                self.url, data=json.dumps({"jsonrpc": "2.0",
                                           "method": "notifications/initialized"}),
                headers={**json_hdr, "mcp-session-id": self._session_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                await r.read()
        log.info("MCP ready")

    async def stop(self) -> None:
        self._session_id = None
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None

    async def call(self, tool: str, arguments: dict, timeout: float = 180) -> Any:
        """Call a tool. Retries transient failures; raises MCPError otherwise."""
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return await self._call_once(tool, arguments, timeout)
            except (asyncio.TimeoutError, OSError) as e:
                last = e
            except MCPError as e:
                last = e
                if "transient" not in str(e):
                    raise
            if attempt < self.retries:
                delay = 2 ** (attempt + 1)
                log.warning("MCP %s failed (%s); retrying in %ds", tool, last, delay)
                await asyncio.sleep(delay)
        raise MCPError(f"MCP call {tool} failed after {self.retries + 1} attempts: {last}")

    async def _call_once(self, tool: str, arguments: dict, timeout: float) -> Any:
        import aiohttp

        if not self._session_id:
            raise MCPError("MCP session not active")
        cid = _next_id()
        log.debug("MCP -> %s", tool)
        body = json.dumps({"jsonrpc": "2.0", "id": cid, "method": "tools/call",
                           "params": {"name": tool, "arguments": arguments}})
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "mcp-session-id": self._session_id}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(self.url, data=body, headers=headers,
                                  timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                    raw = await r.text()
        except aiohttp.ClientError as e:
            raise MCPError(f"transient HTTP error calling {tool}: {e}") from e

        resp = parse_response(raw, cid)
        if resp is None:
            raise MCPError(f"transient: unparseable response for {tool}")
        if "error" in resp:
            raise MCPError(f"{tool}: {resp['error']}")
        content = resp.get("result", {}).get("content", [])
        text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
        try:
            return json.loads(text)
        except ValueError:
            return text
