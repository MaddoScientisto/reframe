#!/usr/bin/env python3

"""Tiny local reverse proxy for the reFrame dashboard.

Runs on port 80 and forwards requests to dashboard.py on 127.0.0.1:8000.
This keeps the dashboard service unprivileged while giving phones a clean
http://hostname.local URL.
"""

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import errno
import os
import time


TARGET_HOST = os.environ.get("REFRAME_DASHBOARD_HOST", "127.0.0.1")
TARGET_PORT = int(os.environ.get("REFRAME_DASHBOARD_PORT", "8000"))
LISTEN_HOST = os.environ.get("REFRAME_PROXY_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("REFRAME_PROXY_PORT", "80"))
BACKEND_CONNECT_ATTEMPTS = int(os.environ.get("REFRAME_PROXY_CONNECT_ATTEMPTS", "20"))
BACKEND_CONNECT_DELAY = float(os.environ.get("REFRAME_PROXY_CONNECT_DELAY", "0.25"))


def request_backend(method, path, body, headers):
    """Send one request, briefly retrying while the dashboard starts."""
    attempts = max(BACKEND_CONNECT_ATTEMPTS, 1)

    for attempt in range(attempts):
        conn = HTTPConnection(TARGET_HOST, TARGET_PORT, timeout=30)
        try:
            conn.request(method, path, body=body, headers=headers)
            return conn, conn.getresponse()
        except OSError as exc:
            conn.close()
            is_refused = exc.errno == errno.ECONNREFUSED
            if not is_refused or attempt == attempts - 1:
                raise
            time.sleep(max(BACKEND_CONNECT_DELAY, 0))

    raise RuntimeError("Dashboard backend retry loop exited unexpectedly")


class DashboardProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def do_OPTIONS(self):
        self._proxy()

    def do_HEAD(self):
        self._proxy(method_override="GET", send_body=False)

    def _proxy(self, method_override=None, send_body=True):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "connection", "keep-alive", "proxy-connection", "transfer-encoding"}
        }
        headers["Host"] = f"{TARGET_HOST}:{TARGET_PORT}"
        headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        headers["X-Forwarded-Proto"] = "http"

        conn = None
        try:
            conn, response = request_backend(
                method_override or self.command,
                self.path,
                body,
                headers,
            )
            request_path = self.path.split("?", 1)[0]
            is_stream = request_path in {
                "/api/preview/stream",
                "/api/photos/download-all/result",
            }
            if is_stream and response.status < 400:
                self._forward_stream(response, send_body)
                return

            response_body = response.read()

            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() in {"connection", "content-length", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            if send_body:
                self.wfile.write(response_body)
        except Exception as e:
            unavailable = isinstance(e, OSError) and e.errno == errno.ECONNREFUSED
            if unavailable:
                message = (
                    "reFrame dashboard is starting or unavailable.\n"
                    "Try again shortly. If this persists, run:\n"
                    "sudo systemctl status reframe-dashboard.service\n"
                ).encode("utf-8")
                status = 503
            else:
                message = f"Dashboard proxy error: {e}".encode("utf-8")
                status = 502

            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            if unavailable:
                self.send_header("Retry-After", "2")
            self.end_headers()
            if send_body:
                self.wfile.write(message)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _forward_stream(self, response, send_body):
        self.send_response(response.status, response.reason)
        for key, value in response.getheaders():
            if key.lower() in {"connection", "content-length", "keep-alive", "transfer-encoding"}:
                continue
            self.send_header(key, value)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if not send_body:
            return

        read_chunk = getattr(response, "read1", response.read)
        try:
            while True:
                chunk = read_chunk(64 * 1024)
                if not chunk:
                    return
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except OSError as exc:
            if exc.errno in {errno.EPIPE, errno.ECONNRESET, errno.ECONNABORTED}:
                return
            raise

    def log_message(self, fmt, *args):
        print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), fmt % args), flush=True)


if __name__ == "__main__":
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), DashboardProxyHandler)
    print(f"reFrame dashboard proxy listening on {LISTEN_HOST}:{LISTEN_PORT} -> {TARGET_HOST}:{TARGET_PORT}", flush=True)
    server.serve_forever()
