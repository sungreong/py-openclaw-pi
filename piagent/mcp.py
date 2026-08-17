from __future__ import annotations

from .deps import *
from .utils import _render_mcp_result

class McpStdioClient:
    def __init__(self, name: str, command: str, args: Sequence[str], env: dict[str, str], timeout_s: int):
        self.name = name
        self.command = command
        self.args = list(args)
        self.env = dict(env)
        self.timeout_s = max(1, int(timeout_s))
        self._proc: Optional[subprocess.Popen[Any]] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._jsonrpc_id = 0
        self._lock = threading.Lock()
        self._tools: list[dict[str, Any]] = []

    def start(self) -> None:
        if self._proc is not None:
            return
        merged_env = dict(os.environ)
        merged_env.update({str(k): str(v) for k, v in self.env.items()})
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            env=merged_env,
        )
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True, name=f"mcp-reader-{self.name}")
        self._reader_thread.start()
        self._initialize()
        self._tools = self._list_tools()

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if not proc:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._request("tools/call", {"name": tool_name, "arguments": arguments or {}})
        if not isinstance(result, dict):
            return {"content": [{"type": "text", "text": str(result)}]}
        return result

    def _read_one_message(self) -> dict[str, Any]:
        if self._proc is None or self._proc.stdout is None:
            raise RuntimeError("MCP process is not running")
        stdout = self._proc.stdout
        headers: dict[str, str] = {}
        while True:
            line = stdout.readline()
            if not line:
                raise RuntimeError("MCP stdout closed")
            if line in (b"\r\n", b"\n"):
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if ":" in decoded:
                key, value = decoded.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        content_len = int(headers.get("content-length", "0"))
        if content_len <= 0:
            raise RuntimeError("MCP message missing Content-Length")
        payload = stdout.read(content_len)
        if not payload:
            raise RuntimeError("MCP message payload is empty")
        return json.loads(payload.decode("utf-8", errors="replace"))

    def _reader_loop(self) -> None:
        try:
            while self._proc is not None:
                msg = self._read_one_message()
                self._messages.put(msg)
        except Exception as e:
            self._messages.put({"__error__": str(e)})

    def _write_message(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("MCP process is not running")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._proc.stdin.write(header + body)
        self._proc.stdin.flush()

    def _next_id(self) -> int:
        with self._lock:
            self._jsonrpc_id += 1
            return self._jsonrpc_id

    def _request(self, method: str, params: Optional[dict[str, Any]] = None) -> Any:
        rid = self._next_id()
        self._write_message({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.time() + float(self.timeout_s)
        while True:
            remaining = max(0.01, deadline - time.time())
            if remaining <= 0:
                raise TimeoutError(f"MCP request timed out: {method}")
            try:
                msg = self._messages.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"MCP response timeout: {method}") from None
            if "__error__" in msg:
                raise RuntimeError(str(msg["__error__"]))
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                raise RuntimeError(json.dumps(msg.get("error"), ensure_ascii=False))
            return msg.get("result")

    def _initialize(self) -> None:
        _ = self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "openclaw-pi", "version": "1.1.0"},
            },
        )
        try:
            self._write_message({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        except Exception:
            pass

    def _list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list", {})
        if isinstance(result, dict):
            tools = result.get("tools", [])
            if isinstance(tools, list):
                return [x for x in tools if isinstance(x, dict)]
        return []

    def list_resources(self) -> list[dict[str, Any]]:
        result = self._request("resources/list", {})
        if isinstance(result, dict):
            rows = result.get("resources", [])
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
        return []

    def list_resource_templates(self) -> list[dict[str, Any]]:
        result = self._request("resources/templates/list", {})
        if isinstance(result, dict):
            rows = result.get("resourceTemplates", [])
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
        return []

    def read_resource(self, uri: str) -> dict[str, Any]:
        result = self._request("resources/read", {"uri": str(uri)})
        if isinstance(result, dict):
            return result
        return {"content": [{"type": "text", "text": str(result)}]}

__all__ = [name for name in globals() if not name.startswith("__")]
