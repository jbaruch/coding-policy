#!/usr/bin/env python3
"""Verify the pinned real CLI sends no project/user instructions or hooks.

Uses a dummy API credential and a loopback server that captures the initial
model request and returns a deliberate 401. No model call, real credential or
external service is needed. Run after installing the pinned CLI in review CI.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


def main() -> int:
    spec = importlib.util.spec_from_file_location("review", Path(__file__).with_name("review.py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    requests: list[dict] = []
    captured = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.split("?")[0] == "/v1/messages":
                requests.append(payload)
                captured.set()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"type":"error","error":{"type":"authentication_error","message":"isolation probe complete"}}')

        def log_message(self, *_args):
            pass  # Loopback probe contains no operational diagnostics.

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="claude-isolation-") as temp:
            root = Path(temp)
            source, config = root / "source", root / "config"
            source.mkdir()
            config.mkdir()
            sentinel = "FORBIDDEN_AGENT_CONTEXT_ISOLATION_SENTINEL"
            for path in (root / "CLAUDE.md", source / "CLAUDE.md", source / "AGENTS.md", config / "CLAUDE.md"):
                path.write_text(sentinel)
            hook_marker = root / "hook-ran"
            settings = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": f"touch {hook_marker}"}]}]}}
            (config / "settings.json").write_text(json.dumps(settings))
            env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ}
            env.update(CLAUDE_CONFIG_DIR=str(config),
                       ANTHROPIC_API_KEY="dummy-isolation-probe-key",
                       ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
                       CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
            prompt = root / "prompt.txt"
            prompt.write_text("Review this diff: +return total / len(items)")
            with prompt.open() as stream, subprocess.Popen(
                    module.claude_command(), cwd=source, env=env, stdin=stream,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
                try:
                    if not captured.wait(30):
                        raise RuntimeError("CLI sent no model request to the loopback server")
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            if hook_marker.exists() or sentinel in json.dumps(requests):
                raise RuntimeError("CLI loaded disabled agent context or ran a hook")
            tools = {tool["name"] for request in requests for tool in request.get("tools", [])}
            if not {"Read", "Glob", "Grep"}.issubset(tools) or tools - {"Read", "Glob", "Grep", "StructuredOutput", "EndConversation", "DeferredToolPlaceholder"}:
                raise RuntimeError("CLI exposed an unexpected tool set: " + ", ".join(sorted(tools)))
        print(json.dumps({"isolation": "pass", "requests": len(requests), "tools": sorted(tools)}))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print("error: Claude isolation probe failed: " + str(exc), file=sys.stderr)
        return 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    sys.exit(main())
