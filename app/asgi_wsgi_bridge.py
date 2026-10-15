"""
Minimal ASGI → WSGI HTTP bridge for Phusion Passenger / cPanel.

No third-party deps. Prefer `a2wsgi.ASGIMiddleware` when installed;
this module is the offline fallback used by passenger_wsgi.py.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Iterable, List, Optional, Tuple

StartResponse = Callable[..., Any]
WsgiApp = Callable[[dict, StartResponse], Iterable[bytes]]


def _build_scope(environ: dict) -> dict:
    """Translate a WSGI environ into an ASGI HTTP scope."""
    script_name = environ.get("SCRIPT_NAME", "") or ""
    path_info = environ.get("PATH_INFO", "") or ""
    if not path_info:
        path_info = "/"

    raw_uri = environ.get("RAW_URI") or environ.get("REQUEST_URI") or ""
    query_string = environ.get("QUERY_STRING", "") or ""
    if not query_string and "?" in raw_uri:
        query_string = raw_uri.split("?", 1)[1]

    headers: List[List[bytes]] = []
    for key, value in environ.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").lower().encode("latin-1")
            headers.append([name, value.encode("latin-1")])
        elif key in ("CONTENT_TYPE", "CONTENT_LENGTH") and value:
            name = key.replace("_", "-").lower().encode("latin-1")
            headers.append([name, value.encode("latin-1")])

    scheme = environ.get("wsgi.url_scheme") or "http"
    if environ.get("HTTP_X_FORWARDED_PROTO"):
        scheme = environ["HTTP_X_FORWARDED_PROTO"].split(",")[0].strip() or scheme

    client_addr = environ.get("REMOTE_ADDR")
    client_port = environ.get("REMOTE_PORT")
    client: Optional[list] = None
    if client_addr:
        try:
            client = [client_addr, int(client_port or 0)]
        except (TypeError, ValueError):
            client = [client_addr, 0]

    server_host = environ.get("SERVER_NAME") or "localhost"
    try:
        server_port = int(environ.get("SERVER_PORT") or (443 if scheme == "https" else 80))
    except (TypeError, ValueError):
        server_port = 80

    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": (environ.get("SERVER_PROTOCOL") or "HTTP/1.1").split("/")[-1],
        "method": environ.get("REQUEST_METHOD", "GET"),
        "scheme": scheme,
        "path": path_info,
        "raw_path": path_info.encode("utf-8"),
        "root_path": script_name,
        "query_string": query_string.encode("latin-1"),
        "headers": headers,
        "client": client,
        "server": [server_host, server_port],
    }


def _read_body(environ: dict) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    stream = environ.get("wsgi.input")
    if stream is None:
        return b""
    if length > 0:
        return stream.read(length) or b""
    # No Content-Length: do not block on unbounded read under Passenger
    return b""


class AsgiToWsgi:
    """Thread-backed ASGI→WSGI adapter (HTTP request/response only)."""

    def __init__(self, asgi_app: Any, *, timeout: float = 120.0) -> None:
        self.app = asgi_app
        self.timeout = timeout
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="asgi-wsgi-bridge",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("ASGI event loop failed to start")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def __call__(self, environ: dict, start_response: StartResponse) -> Iterable[bytes]:
        future = asyncio.run_coroutine_threadsafe(
            self._handle(environ),
            self._loop,
        )
        try:
            status, headers, body = future.result(timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 — surface as WSGI 500
            status = "500 Internal Server Error"
            headers = [("Content-Type", "text/plain; charset=utf-8")]
            body = ("ASGI bridge error: %s" % exc).encode("utf-8")
        start_response(status, headers)
        return [body]

    async def _handle(
        self, environ: dict
    ) -> Tuple[str, List[Tuple[str, str]], bytes]:
        scope = _build_scope(environ)
        request_body = _read_body(environ)
        body_sent = False
        response_started = False
        status_code = 500
        raw_headers: List[Tuple[str, str]] = []
        chunks: List[bytes] = []

        async def receive() -> dict:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {
                    "type": "http.request",
                    "body": request_body,
                    "more_body": False,
                }
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            nonlocal response_started, status_code, raw_headers
            msg_type = message.get("type")
            if msg_type == "http.response.start":
                response_started = True
                status_code = int(message.get("status") or 500)
                raw_headers = []
                for key, value in message.get("headers") or []:
                    k = key.decode("latin-1") if isinstance(key, (bytes, bytearray)) else str(key)
                    v = (
                        value.decode("latin-1")
                        if isinstance(value, (bytes, bytearray))
                        else str(value)
                    )
                    # WSGI forbids hop-by-hop Transfer-Encoding from app
                    if k.lower() == "transfer-encoding":
                        continue
                    raw_headers.append((k, v))
            elif msg_type == "http.response.body":
                chunk = message.get("body") or b""
                if chunk:
                    chunks.append(bytes(chunk))
            elif msg_type == "http.response.push":
                pass  # not supported under WSGI/Passenger

        await self.app(scope, receive, send)

        if not response_started:
            status_code = 500
            raw_headers = [("Content-Type", "text/plain; charset=utf-8")]
            chunks = [b"ASGI app did not start a response"]

        reason = {
            200: "OK",
            201: "Created",
            204: "No Content",
            301: "Moved Permanently",
            302: "Found",
            304: "Not Modified",
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            405: "Method Not Allowed",
            422: "Unprocessable Entity",
            500: "Internal Server Error",
            502: "Bad Gateway",
            503: "Service Unavailable",
        }.get(status_code, "OK" if status_code < 400 else "Error")
        status = "%s %s" % (status_code, reason)
        return status, raw_headers, b"".join(chunks)


def wrap_asgi(asgi_app: Any) -> WsgiApp:
    """
    Prefer a2wsgi when available in the venv; otherwise use built-in bridge.
    """
    try:
        from a2wsgi import ASGIMiddleware  # type: ignore

        return ASGIMiddleware(asgi_app)
    except ImportError:
        return AsgiToWsgi(asgi_app)
