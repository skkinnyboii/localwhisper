"""Local web settings UI, served by the app itself.

Bound to 127.0.0.1 only — nothing is reachable from the network, and every
request must carry a localhost Host header (blocks DNS-rebinding tricks).
Opened from the menu bar ("Settings…"). Config edits are written to
~/.config/localwhisper/config.json and applied to the running pipeline
immediately; changes to the model, engine, language or hotkey report
restart_required=true and take effect on the next launch.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

import requests

from . import history as history_mod
from .config import CONFIG_PATH

HOST = "127.0.0.1"
PORT = 43117

# a change to these keys only takes effect on relaunch
RESTART_KEYS = ("model", "engine", "compute_type", "language", "hotkey")


def _page_html() -> str:
    return (
        resources.files("localwhisper")
        .joinpath("settings_page.html")
        .read_text(encoding="utf-8")
    )


class SettingsServer:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.url = f"http://{HOST}:{PORT}/"
        self._httpd = None

    def start(self) -> None:
        handler = _make_handler(self.pipeline)
        try:
            self._httpd = ThreadingHTTPServer((HOST, PORT), handler)
        except OSError as exc:  # port taken: settings unavailable, app fine
            print(f"settings server not started: {exc}", flush=True)
            return
        self._httpd.daemon_threads = True
        self._httpd._settings = self  # handler needs apply_config
        threading.Thread(
            target=self._httpd.serve_forever, daemon=True, name="settings-http"
        ).start()
        print(f"settings UI at {self.url}", flush=True)

    def apply_config(self, new_config: dict) -> bool:
        """Persist + live-apply a full config. Returns restart_required."""
        pipeline = self.pipeline
        restart = any(
            new_config.get(key) != pipeline.config.get(key)
            for key in RESTART_KEYS
        )
        CONFIG_PATH.write_text(
            json.dumps(new_config, indent=2, ensure_ascii=False) + "\n"
        )
        pipeline.config.clear()
        pipeline.config.update(new_config)
        vocabulary = new_config.get("vocabulary") or []
        pipeline.initial_prompt = (
            f"Glossary: {', '.join(vocabulary)}." if vocabulary else None
        )
        return restart


def _make_handler(pipeline):
    class Handler(BaseHTTPRequestHandler):
        server_version = "localwhisper"

        def log_message(self, *args):  # keep the app log clean
            pass

        # ---- helpers ----

        def _local(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, code: int = 200) -> None:
            self._send(
                code,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json",
            )

        # ---- routes ----

        def do_GET(self):
            if not self._local():
                return self._json({"error": "forbidden"}, 403)
            parsed = urlparse(self.path)
            if parsed.path == "/":
                return self._send(200, _page_html().encode(), "text/html")
            if parsed.path == "/api/config":
                return self._json(pipeline.config)
            if parsed.path == "/api/status":
                recent = []
                try:
                    recent = history_mod.query(limit=1)
                except Exception:
                    pass
                return self._json(
                    {
                        "state": pipeline.state,
                        "model": pipeline.config["model"],
                        "engine": pipeline.transcriber.engine,
                        "mode": pipeline.mode_override or "auto",
                        "config_path": str(CONFIG_PATH),
                        "last": recent[0] if recent else None,
                    }
                )
            if parsed.path == "/api/llm-models":
                return self._json(self._llm_models())
            if parsed.path == "/api/history":
                params = parse_qs(parsed.query)
                try:
                    rows = history_mod.query(
                        search=(params.get("q") or [""])[0],
                        limit=int((params.get("limit") or ["50"])[0]),
                    )
                except Exception as exc:
                    return self._json({"error": str(exc)}, 500)
                return self._json(rows)
            self._json({"error": "not found"}, 404)

        def do_POST(self):
            if not self._local():
                return self._json({"error": "forbidden"}, 403)
            path = urlparse(self.path).path
            if path == "/api/mode":
                return self._post_mode()
            if path != "/api/config":
                return self._json({"error": "not found"}, 404)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                new_config = json.loads(self.rfile.read(length))
                if not isinstance(new_config, dict) or "model" not in new_config:
                    raise ValueError("not a full localwhisper config")
                restart = self.server._settings.apply_config(new_config)
            except Exception as exc:
                return self._json({"error": str(exc)}, 400)
            self._json({"ok": True, "restart_required": restart})

        def _post_mode(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                mode = json.loads(self.rfile.read(length)).get("mode") or None
                if mode is not None and mode not in pipeline.config[
                    "context_instructions"
                ]:
                    raise ValueError(f"unknown mode: {mode}")
            except Exception as exc:
                return self._json({"error": str(exc)}, 400)
            pipeline.mode_override = mode
            if pipeline.on_mode_change:
                try:
                    pipeline.on_mode_change()
                except Exception:
                    pass
            self._json({"ok": True, "mode": mode or "auto"})

        def _llm_models(self) -> list[dict]:
            base_url = pipeline.config["cleanup"]["base_url"].rstrip("/")
            root = base_url[: -len("/v1")] if base_url.endswith("/v1") else base_url
            try:  # LM Studio native API includes load state
                data = requests.get(f"{root}/api/v0/models", timeout=3).json()
                return [
                    {"id": m["id"], "state": m.get("state", "")}
                    for m in data.get("data", [])
                    if m.get("type") in ("llm", "vlm")
                ]
            except Exception:
                pass
            try:
                data = requests.get(f"{base_url}/models", timeout=3).json()
                return [{"id": m["id"], "state": ""} for m in data.get("data", [])]
            except Exception:
                return []

    return Handler


def start_server(pipeline) -> SettingsServer:
    server = SettingsServer(pipeline)
    server.start()
    return server
