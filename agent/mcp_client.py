"""
MCP (Model Context Protocol) client — stdio transport.

Connects to any MCP-compatible server as a subprocess and exposes its tools
to the nessocode agent via the standard OpenAI tool-calling interface.

Protocol reference: https://modelcontextprotocol.io/specification
"""
import json
import logging
import os
import subprocess
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MCPError(Exception):
    """Raised when an MCP server returns a JSON-RPC error."""


class MCPClient:
    """
    Single MCP server connection over stdio JSON-RPC transport.

    Lifecycle:
        client = MCPClient("git", "uvx", ["mcp-server-git", "--repository", "."])
        client.tools          # list of discovered tool dicts
        result = client.call_tool("git_status", {})
        client.shutdown()
    """

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(
        self,
        name: str,
        command: str,
        args: List[str],
        env: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
    ):
        self.name = name
        self.timeout = timeout
        self.tools: List[Dict[str, Any]] = []

        self._req_id = 0
        self._lock = threading.Lock()
        self._pending: Dict[int, threading.Event] = {}
        self._responses: Dict[int, dict] = {}

        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)

        self._proc = subprocess.Popen(
            [command] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=proc_env,
            text=True,
            bufsize=1,
        )

        # Background reader thread
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        self._initialize()

    # ------------------------------------------------------------------
    # Internal transport layer
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        """Continuously read JSON-RPC messages from the server's stdout."""
        for raw_line in iter(self._proc.stdout.readline, ""):
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("MCP[%s] non-JSON line: %s", self.name, line[:80])
                continue

            req_id = msg.get("id")
            if req_id is not None:
                self._responses[req_id] = msg
                ev = self._pending.get(req_id)
                if ev:
                    ev.set()

    def _next_id(self) -> int:
        with self._lock:
            self._req_id += 1
            return self._req_id

    def _send_request(self, method: str, params: Optional[dict] = None) -> dict:
        """Send a JSON-RPC request and block until the response arrives."""
        req_id = self._next_id()
        event = threading.Event()
        self._pending[req_id] = event

        msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params

        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

        if not event.wait(self.timeout):
            self._pending.pop(req_id, None)
            raise TimeoutError(
                f"MCP server '{self.name}' did not respond to '{method}' within {self.timeout}s"
            )

        self._pending.pop(req_id, None)
        response = self._responses.pop(req_id)

        if "error" in response:
            err = response["error"]
            raise MCPError(f"[{err.get('code')}] {err.get('message', 'unknown error')}")

        return response.get("result", {})

    def _send_notification(self, method: str, params: Optional[dict] = None) -> None:
        """Send a JSON-RPC notification (fire-and-forget, no id)."""
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    # ------------------------------------------------------------------
    # MCP protocol handshake
    # ------------------------------------------------------------------

    def _initialize(self) -> None:
        self._send_request("initialize", {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "nessocode", "version": "0.1.0"},
        })
        self._send_notification("notifications/initialized")
        self._discover_tools()

    def _discover_tools(self) -> None:
        result = self._send_request("tools/list", {})
        for tool in result.get("tools", []):
            # Prefix the tool name with the server name to avoid collisions
            prefixed_name = f"mcp_{self.name}__{tool['name']}"
            self.tools.append({
                "prefixed_name":  prefixed_name,
                "original_name":  tool["name"],
                "description":    tool.get("description", ""),
                "input_schema":   tool.get("inputSchema", {"type": "object", "properties": {}}),
                "server":         self.name,
            })

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def call_tool(self, original_name: str, arguments: dict) -> str:
        """Invoke a tool on this server and return the text result."""
        result = self._send_request("tools/call", {
            "name": original_name,
            "arguments": arguments,
        })
        is_error = result.get("isError", False)
        parts = []
        for item in result.get("content", []):
            t = item.get("type")
            if t == "text":
                parts.append(item["text"])
            elif t == "image":
                mime = item.get("mimeType", "unknown")
                parts.append(f"[image/{mime} — not renderable in terminal]")
            elif t == "resource":
                uri = item.get("resource", {}).get("uri", "?")
                parts.append(f"[resource: {uri}]")

        text = "\n".join(parts)
        return f"error: {text}" if is_error else text

    def shutdown(self) -> None:
        """Gracefully stop the MCP server process."""
        try:
            self._send_notification("shutdown")
        except Exception:
            pass
        try:
            self._proc.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Manager — owns multiple clients
# ---------------------------------------------------------------------------

class MCPManager:
    """
    Manages a pool of MCP server connections.

    Usage:
        mgr = MCPManager()
        mgr.add_server("git", "uvx", ["mcp-server-git", "--repository", "."])
        tools = mgr.get_openai_tools()        # inject into LLM call
        result = mgr.call_tool("mcp_git__git_status", {})
    """

    def __init__(self) -> None:
        self._clients: Dict[str, MCPClient] = {}
        # prefixed_name -> (client, original_name)
        self._tool_index: Dict[str, Tuple[MCPClient, str]] = {}

    def add_server(
        self,
        name: str,
        command: str,
        args: List[str],
        env: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        Start and connect to an MCP server.
        Returns True on success, False on failure (logs a warning).
        """
        try:
            client = MCPClient(name, command, args, env)
            self._clients[name] = client
            for tool in client.tools:
                self._tool_index[tool["prefixed_name"]] = (client, tool["original_name"])
            return True
        except Exception as exc:
            logger.warning("Failed to connect to MCP server '%s': %s", name, exc)
            return False

    @property
    def clients(self) -> Dict[str, MCPClient]:
        return self._clients

    def is_mcp_tool(self, name: str) -> bool:
        return name in self._tool_index

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        """Return OpenAI-compatible tool defs for every tool on every connected server."""
        result = []
        for client in self._clients.values():
            for tool in client.tools:
                result.append({
                    "type": "function",
                    "function": {
                        "name": tool["prefixed_name"],
                        "description": f"[MCP:{tool['server']}] {tool['description']}",
                        "parameters": tool["input_schema"],
                    },
                })
        return result

    def call_tool(self, prefixed_name: str, arguments: dict) -> str:
        if prefixed_name not in self._tool_index:
            return f"error: unknown MCP tool '{prefixed_name}'"
        client, original_name = self._tool_index[prefixed_name]
        try:
            return client.call_tool(original_name, arguments)
        except MCPError as e:
            return f"MCP error: {e}"
        except TimeoutError as e:
            return f"MCP timeout: {e}"
        except Exception as e:
            return f"MCP error ({type(e).__name__}): {e}"

    def tool_server(self, prefixed_name: str) -> Optional[str]:
        """Return the server name for a prefixed tool name, or None."""
        entry = self._tool_index.get(prefixed_name)
        return entry[0].name if entry else None

    def shutdown_all(self) -> None:
        for client in self._clients.values():
            client.shutdown()
        self._clients.clear()
        self._tool_index.clear()
