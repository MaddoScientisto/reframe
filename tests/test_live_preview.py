import asyncio
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

picamera2 = types.ModuleType("picamera2")
picamera2.Picamera2 = object
sys.modules.setdefault("picamera2", picamera2)

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

import reframe

if TestClient is not None:
    import dashboard
else:
    dashboard = None


class FakePicamera:
    def __init__(self):
        self.configurations = []
        self.started = False

    def stop(self):
        self.started = False

    def create_still_configuration(self, **kwargs):
        self.configurations.append(kwargs)
        return {}

    def configure(self, configuration):
        self.configuration = configuration

    def set_controls(self, controls):
        self.controls = controls

    def start(self):
        self.started = True


class LivePreviewTests(unittest.TestCase):
    def test_yuv420_conversion_returns_requested_rgb_size(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("NumPy is not installed")

        frame = np.full((6, 4), 128, dtype=np.uint8)
        image = reframe.yuv420_to_image(frame, (4, 4))

        self.assertEqual(image.size, (4, 4))
        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.getpixel((0, 0)), (128, 128, 128))

    def test_camera_uses_persistent_preview_stream_configuration(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(reframe, "Picamera2", FakePicamera):
            settings_path = Path(temp_dir) / "settings.json"
            manager = reframe.CameraManager(str(settings_path))

        configuration = manager.picam2.configurations[-1]
        self.assertEqual(configuration["main"], {"size": (1200, 800), "format": "RGB888"})
        self.assertEqual(configuration["lores"], {"size": (636, 424), "format": "YUV420"})
        self.assertEqual(configuration["buffer_count"], 3)
        self.assertTrue(manager.picam2.started)

    def test_preview_size_preserves_still_aspect_ratio(self):
        preview_width, preview_height = reframe.get_preview_size({"width": 1920, "height": 1080})

        self.assertEqual((preview_width, preview_height), (640, 360))
        self.assertEqual(preview_width / preview_height, 1920 / 1080)

    def test_preview_session_keeps_latest_frame_and_stops(self):
        frame_ready = threading.Event()
        frame_count = 0

        def capture_preview_jpeg():
            nonlocal frame_count
            frame_count += 1
            frame_ready.set()
            return f"frame-{frame_count}".encode("ascii")

        manager = SimpleNamespace(capture_preview_jpeg=capture_preview_jpeg)
        session = reframe.CameraPreviewSession(manager)
        session.start()
        self.assertTrue(frame_ready.wait(1))

        sequence, frame = session.wait_for_frame(0)
        self.assertGreaterEqual(sequence, 1)
        self.assertEqual(frame, f"frame-{sequence}".encode("ascii"))

        session.stop()
        self.assertIsNone(session.wait_for_frame(sequence, timeout=0.01))

    def test_preview_session_can_only_be_stopped_by_owner(self):
        manager = object.__new__(reframe.CameraManager)
        manager._preview_session_lock = threading.Lock()
        session = Mock(owner_id="desktop")
        manager._preview_session = session

        self.assertFalse(manager.close_preview_session_for_owner("mobile"))
        self.assertIs(manager._preview_session, session)
        self.assertTrue(manager.close_preview_session_for_owner("desktop"))
        self.assertIsNone(manager._preview_session)
        session.stop.assert_called_once_with()

    @unittest.skipUnless(TestClient is not None, "FastAPI test dependencies are not installed")
    def test_preview_route_frames_and_releases_session(self):
        session = Mock()
        session.wait_for_frame.side_effect = [(1, b"jpeg-bytes"), (1, b"jpeg-bytes"), None]
        camera_manager = Mock()
        camera_manager.open_preview_session.return_value = session
        camera_system = SimpleNamespace(camera_manager=camera_manager)

        with patch.object(reframe, "camera_system", camera_system):
            client = TestClient(reframe._create_fastapi_routes())
            response = client.get("/api/preview/stream")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"--frame\r\n", response.content)
        self.assertIn(b"Content-Type: image/jpeg\r\n", response.content)
        self.assertIn(b"Content-Length: 10\r\n", response.content)
        self.assertIn(b"jpeg-bytes\r\n", response.content)
        camera_manager.close_preview_session.assert_called_once_with(session)

    @unittest.skipUnless(TestClient is not None, "FastAPI test dependencies are not installed")
    def test_preview_stop_route_releases_owned_session(self):
        camera_manager = Mock()
        camera_manager.close_preview_session_for_owner.return_value = True
        camera_system = SimpleNamespace(camera_manager=camera_manager)

        with patch.object(reframe, "camera_system", camera_system):
            client = TestClient(reframe._create_fastapi_routes())
            response = client.post("/api/preview/stop?client_id=desktop")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True})
        camera_manager.close_preview_session_for_owner.assert_called_once_with("desktop")

    @unittest.skipUnless(TestClient is not None, "FastAPI test dependencies are not installed")
    def test_dashboard_forwarding_preserves_bytes_and_closes_upstream(self):
        class FakeResponse:
            status_code = 200
            headers = {"content-type": "multipart/x-mixed-replace; boundary=frame"}

            async def aiter_bytes(self):
                yield b"first"
                yield b"second"

        stream_context = Mock()
        stream_context.__aexit__ = AsyncMock()
        client = Mock()
        client.aclose = AsyncMock()
        response = FakeResponse()

        with patch.object(
            dashboard.reframe_client,
            "open_stream",
            new=AsyncMock(return_value=(client, stream_context, response)),
        ):
            request = SimpleNamespace(url=SimpleNamespace(query=""))
            streaming_response = asyncio.run(dashboard.preview_stream(request))
            chunks = asyncio.run(consume_async_iterator(streaming_response.body_iterator))

        self.assertEqual(chunks, [b"first", b"second"])
        stream_context.__aexit__.assert_awaited_once()
        client.aclose.assert_awaited_once()


async def consume_async_iterator(iterator):
    return [chunk async for chunk in iterator]


if __name__ == "__main__":
    unittest.main()
