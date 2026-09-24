"""Load this checkout as a real Xircuits library without editing site-packages."""
import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("xai_components.xai_xpressai", ROOT / "__init__.py",
                                             submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)


@pytest.fixture
def api(monkeypatch):
    state = SimpleNamespace(requests=[], responses=[])

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            payload = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            state.requests.append((self.command, self.path, dict(self.headers), json.loads(payload) if payload else None))
            status, body, headers = state.responses.pop(0)
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = handle_request

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("XPRESSAI_PLATFORM_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("XPRESSAI_PROJECT_ID", "personal-owner")
    monkeypatch.setenv("XPRESSAI_NAMESPACE", "owner-ns")
    monkeypatch.setenv("XPRESSAI_API_TOKEN", "workload-test-token")
    monkeypatch.setenv("XPRESSAI_RELAY_TOKEN", "must-not-be-used")
    monkeypatch.setenv("AGENT_NAME", "service-name-is-not-agent-proof")
    monkeypatch.setenv("XPRESSAI_AGENT_TOKEN", "must-not-be-used-either")
    monkeypatch.delenv("XPRESSAI_JOB_ID", raising=False)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
