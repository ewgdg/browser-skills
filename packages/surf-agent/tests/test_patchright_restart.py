from __future__ import annotations

from contextlib import contextmanager
import threading
from pathlib import Path

from surf_agent.backends.patchright.backend import PatchrightBridgeClient
from surf_agent.backends.patchright.bridge import PatchrightHTTPServer, RequestHandler
from surf_agent.backends.patchright.constants import CONTEXT_RESTART_REQUIRED


class RestartRequiredRuntime:
    restart_requested = False

    def call(self, _name: str, _args: dict[str, object]) -> str:
        self.restart_requested = True
        raise RuntimeError(CONTEXT_RESTART_REQUIRED)

    def service_actions(self) -> bool:
        return False

    def stop(self) -> str:
        return "stopped\n"


class HealthyRuntime:
    restart_requested = False

    def call(self, name: str, args: dict[str, object]) -> str:
        assert name == "open"
        return f"opened {args['url']}\n"

    def after_response(self, _name: str) -> None:
        return

    def service_actions(self) -> bool:
        return False

    def stop(self) -> str:
        return "stopped\n"


def serve(server: PatchrightHTTPServer) -> None:
    try:
        server.serve_forever()
    finally:
        server.server_close()


def test_patchright_client_restarts_bridge_and_retries_interrupted_request(tmp_path: Path) -> None:
    first_runtime = RestartRequiredRuntime()
    RequestHandler.runtime = first_runtime
    first_server = PatchrightHTTPServer(("127.0.0.1", 0), RequestHandler)
    first_thread = threading.Thread(target=serve, args=(first_server,), daemon=True)
    first_thread.start()

    client = PatchrightBridgeClient(
        timeout_s=1,
        port=first_server.server_address[1],
        profile_dir=tmp_path / "profile",
    )
    started_servers: list[tuple[PatchrightHTTPServer, threading.Thread]] = []

    @contextmanager
    def start_replacement_bridge():
        replacement_runtime = HealthyRuntime()
        RequestHandler.runtime = replacement_runtime
        replacement_server = PatchrightHTTPServer(("127.0.0.1", 0), RequestHandler)
        replacement_thread = threading.Thread(target=serve, args=(replacement_server,), daemon=True)
        replacement_thread.start()
        started_servers.append((replacement_server, replacement_thread))
        client.port = replacement_server.server_address[1]
        yield

    client.before_start = start_replacement_bridge

    try:
        assert client.call_tool("open", {"thread": "default", "url": "https://reddit.com"}) == "opened https://reddit.com\n"
    finally:
        first_server.shutdown()
        first_thread.join(timeout=1)
        for server, thread in started_servers:
            server.shutdown()
            thread.join(timeout=1)