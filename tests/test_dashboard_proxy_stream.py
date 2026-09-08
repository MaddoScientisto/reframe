import threading
import unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import dashboard_proxy


FIRST_CHUNK = b"--frame\r\nContent-Type: image/jpeg\r\n\r\nfirst"
SECOND_CHUNK = b"\r\n--frame\r\nsecond\r\n"


class StreamingBackendHandler(BaseHTTPRequestHandler):
    first_chunk_sent = threading.Event()
    allow_finish = threading.Event()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        self.wfile.write(FIRST_CHUNK)
        self.wfile.flush()
        self.first_chunk_sent.set()
        self.allow_finish.wait(2)
        self.wfile.write(SECOND_CHUNK)
        self.wfile.flush()

    def log_message(self, fmt, *args):
        pass


class ZipStreamingBackendHandler(BaseHTTPRequestHandler):
    first_chunk_sent = threading.Event()
    allow_finish = threading.Event()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.end_headers()
        self.wfile.write(b"zip-first")
        self.wfile.flush()
        self.first_chunk_sent.set()
        self.allow_finish.wait(2)
        self.wfile.write(b"zip-second")
        self.wfile.flush()

    def log_message(self, fmt, *args):
        pass


class DashboardProxyStreamTests(unittest.TestCase):
    def test_proxy_forwards_first_chunk_before_upstream_finishes(self):
        StreamingBackendHandler.first_chunk_sent.clear()
        StreamingBackendHandler.allow_finish.clear()
        backend = ThreadingHTTPServer(("127.0.0.1", 0), StreamingBackendHandler)
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_proxy.DashboardProxyHandler)
        backend_thread = threading.Thread(target=backend.serve_forever, daemon=True)
        proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        backend_thread.start()
        proxy_thread.start()

        try:
            with patch.object(dashboard_proxy, "TARGET_HOST", "127.0.0.1"), patch.object(
                dashboard_proxy, "TARGET_PORT", backend.server_port
            ):
                connection = HTTPConnection("127.0.0.1", proxy.server_port, timeout=3)
                connection.request("GET", "/api/preview/stream")
                response = connection.getresponse()
                first = response.read(len(FIRST_CHUNK))

            self.assertEqual(response.status, 200)
            self.assertEqual(first, FIRST_CHUNK)
            self.assertTrue(StreamingBackendHandler.first_chunk_sent.is_set())
            self.assertFalse(StreamingBackendHandler.allow_finish.is_set())
        finally:
            StreamingBackendHandler.allow_finish.set()
            try:
                connection.close()
            except UnboundLocalError:
                pass
            proxy.shutdown()
            backend.shutdown()
            proxy.server_close()
            backend.server_close()
            proxy_thread.join(timeout=2)
            backend_thread.join(timeout=2)

    def test_proxy_streams_zip_result_before_upstream_finishes(self):
        ZipStreamingBackendHandler.first_chunk_sent.clear()
        ZipStreamingBackendHandler.allow_finish.clear()
        backend = ThreadingHTTPServer(("127.0.0.1", 0), ZipStreamingBackendHandler)
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_proxy.DashboardProxyHandler)
        backend_thread = threading.Thread(target=backend.serve_forever, daemon=True)
        proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        backend_thread.start()
        proxy_thread.start()

        try:
            with patch.object(dashboard_proxy, "TARGET_HOST", "127.0.0.1"), patch.object(
                dashboard_proxy, "TARGET_PORT", backend.server_port
            ):
                connection = HTTPConnection("127.0.0.1", proxy.server_port, timeout=3)
                connection.request("GET", "/api/photos/download-all/result")
                response = connection.getresponse()
                first = response.read(len(b"zip-first"))

            self.assertEqual(response.status, 200)
            self.assertEqual(first, b"zip-first")
            self.assertTrue(ZipStreamingBackendHandler.first_chunk_sent.is_set())
            self.assertFalse(ZipStreamingBackendHandler.allow_finish.is_set())
        finally:
            ZipStreamingBackendHandler.allow_finish.set()
            try:
                connection.close()
            except UnboundLocalError:
                pass
            proxy.shutdown()
            backend.shutdown()
            proxy.server_close()
            backend.server_close()
            proxy_thread.join(timeout=2)
            backend_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
