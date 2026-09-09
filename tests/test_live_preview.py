import asyncio
import sys
import tempfile
import threading
import time
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


class FakePreviewRequest:
    def __init__(self, frame, metadata):
        self.frame = frame
        self.metadata = metadata
        self.released = False

    def make_array(self, stream_name):
        self.asserted_stream = stream_name
        return self.frame

    def get_metadata(self):
        return self.metadata

    def release(self):
        self.released = True


class FakeFocusPicamera:
    camera_controls = {"LensPosition": (0.0, 10.0, 5.0)}
    camera_properties = {"PixelArraySize": (400, 300)}

    def __init__(self):
        self.control_calls = []
        self.metadata = [{"AfState": 2, "LensPosition": 4.5}]

    def set_controls(self, controls):
        self.control_calls.append(controls)

    def capture_metadata(self):
        return self.metadata.pop(0) if self.metadata else {"AfState": 2, "LensPosition": 4.5}


class FakeControlPicamera:
    camera_controls = {
        "AeEnable": (False, True, True),
        "ExposureTime": (100, 1_000_000, 10_000),
        "AnalogueGain": (1.0, 16.0, 1.0),
        "AwbEnable": (False, True, True),
        "AwbMode": (0, 6, 0),
        "ColourGains": ((0.0, 0.0), (8.0, 8.0), (1.0, 1.0)),
    }

    def __init__(self):
        self.control_calls = []

    def capture_metadata(self):
        return {"ExposureTime": 12000, "AnalogueGain": 2.0}

    def set_controls(self, controls):
        self.control_calls.append(controls)


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

    def test_preview_request_keeps_frame_metadata_together(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("NumPy is not installed")

        request = FakePreviewRequest(
            np.full((6, 4), 128, dtype=np.uint8),
            {"ExposureTime": 8000, "LensPosition": 2.5},
        )
        picamera = Mock()
        picamera.capture_request.return_value = request
        manager = object.__new__(reframe.CameraManager)
        manager.picam2 = picamera
        manager._camera_lock = threading.Lock()
        manager.preview_size = (4, 4)
        manager.last_activity_monotonic = time.monotonic()

        frame = manager.capture_preview_frame()

        self.assertIsNotNone(frame["jpeg"])
        self.assertEqual(frame["metadata"], {"ExposureTime": 8000, "LensPosition": 2.5})
        self.assertEqual(request.asserted_stream, "lores")
        self.assertTrue(request.released)

    def test_preview_telemetry_exposes_metadata_and_focus_range(self):
        manager = object.__new__(reframe.CameraManager)
        manager._preview_session_lock = threading.Lock()
        manager._focus_mode = 2
        manager.settings = {"camera": {"exposure_value": 0}}
        manager.picam2 = FakeFocusPicamera()
        session = reframe.CameraPreviewSession(manager, owner_id="desktop")
        session._active = True
        session._sequence = 7
        session._latest_frame = {
            "jpeg": b"jpeg",
            "metadata": {
                "ExposureTime": 8000,
                "AnalogueGain": 2.0,
                "AfMode": 2,
                "AfState": 2,
                "LensPosition": 4.5,
            },
            "captured_at": time.monotonic(),
        }
        session._frame_times = [time.monotonic() - 0.4, time.monotonic() - 0.2]
        manager._preview_session = session

        telemetry = manager.get_preview_telemetry("desktop")

        self.assertEqual(telemetry["frame_sequence"], 7)
        self.assertEqual(telemetry["exposure_time_us"], 8000)
        self.assertEqual(telemetry["focus_mode"], "continuous")
        self.assertEqual(telemetry["focus_state"], "focused")
        self.assertEqual(telemetry["focus_range"], {"min": 0, "max": 10, "step": 0.1})
        with self.assertRaises(reframe.PreviewSessionAccessDenied):
            manager.get_preview_telemetry("other")

    def test_manual_center_focus_restores_manual_mode(self):
        manager = object.__new__(reframe.CameraManager)
        manager.picam2 = FakeFocusPicamera()
        manager._camera_lock = threading.Lock()
        manager._focus_lock = threading.Lock()
        manager._focus_mode = 0
        manager._manual_focus_position = 2.0

        result = manager.focus_center()

        self.assertEqual(result["focus_mode"], "manual")
        self.assertEqual(result["lens_position"], 4.5)
        self.assertEqual(manager.picam2.control_calls[0]["AfWindows"], [(150, 112, 100, 75)])
        self.assertEqual(manager.picam2.control_calls[-1], {"AfMode": 0, "LensPosition": 4.5})

    def test_manual_position_rejects_auto_mode_and_out_of_range_values(self):
        manager = object.__new__(reframe.CameraManager)
        manager.picam2 = FakeFocusPicamera()
        manager._camera_lock = threading.Lock()
        manager._focus_lock = threading.Lock()
        manager._focus_mode = 2
        manager._manual_focus_position = None

        with self.assertRaises(reframe.FocusOperationError):
            manager.set_focus_position(4.0)

        manager._focus_mode = 0
        with self.assertRaises(reframe.FocusOperationError):
            manager.set_focus_position(11.0)

    def test_preview_controls_apply_exposure_and_manual_white_balance(self):
        manager = object.__new__(reframe.CameraManager)
        manager.picam2 = FakeControlPicamera()
        manager._camera_lock = threading.Lock()
        manager._focus_lock = threading.Lock()
        manager._exposure_mode = "auto"
        manager._white_balance_mode = "auto"
        manager._white_balance_preset = "daylight"
        manager._white_balance_gains = {"red": 1.0, "blue": 1.0}
        manager.settings = {"camera": {"exposure_value": 0}}

        capabilities = manager.get_white_balance_capabilities()
        self.assertTrue(capabilities["manual_supported"])
        self.assertEqual(capabilities["supported_presets"], [
            "auto", "incandescent", "tungsten", "fluorescent", "indoor", "daylight", "cloudy"
        ])

        white_balance = manager.set_white_balance("manual", red_gain=1.8, blue_gain=1.4)
        exposure = manager.set_exposure_value(0.75)

        self.assertEqual(white_balance["colour_gains"], {"red": 1.8, "blue": 1.4})
        self.assertEqual(exposure["exposure_value"], 0.75)
        self.assertEqual(manager.picam2.control_calls, [
            {"AwbEnable": False, "ColourGains": (1.8, 1.4)},
            {"ExposureValue": 0.75},
        ])

    def test_exposure_mode_locks_current_values_and_restores_auto(self):
        manager = object.__new__(reframe.CameraManager)
        manager.picam2 = FakeControlPicamera()
        manager._camera_lock = threading.Lock()
        manager._focus_lock = threading.Lock()
        manager._exposure_mode = "auto"
        manager._manual_exposure_time_us = None
        manager._manual_analogue_gain = None

        manual = manager.set_exposure_mode("manual")
        auto = manager.set_exposure_mode("auto")

        self.assertEqual(manual["exposure_time_us"], 12000)
        self.assertEqual(manual["analogue_gain"], 2.0)
        self.assertEqual(auto["exposure_mode"], "auto")
        self.assertEqual(manager.picam2.control_calls, [
            {"AeEnable": False, "ExposureTime": 12000, "AnalogueGain": 2.0},
            {"AeEnable": True},
        ])

    def test_exposure_value_rejects_manual_mode(self):
        manager = object.__new__(reframe.CameraManager)
        manager.picam2 = FakeControlPicamera()
        manager._camera_lock = threading.Lock()
        manager._focus_lock = threading.Lock()
        manager._exposure_mode = "manual"

        with self.assertRaises(reframe.PreviewControlError):
            manager.set_exposure_value(0.5)

    def test_manual_white_balance_rejects_out_of_range_gain(self):
        manager = object.__new__(reframe.CameraManager)
        manager.picam2 = FakeControlPicamera()
        manager._camera_lock = threading.Lock()
        manager._focus_lock = threading.Lock()
        manager._white_balance_gains = {"red": 1.0, "blue": 1.0}

        with self.assertRaises(reframe.PreviewControlError):
            manager.set_white_balance("manual", red_gain=9.0, blue_gain=1.0)

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
    def test_preview_controls_route_dispatches_exposure_value(self):
        camera_manager = Mock()
        camera_manager.get_preview_telemetry.return_value = {"success": True}
        camera_manager.set_exposure_value.return_value = {
            "success": True,
            "exposure_value": 0.5,
        }
        camera_system = SimpleNamespace(camera_manager=camera_manager, update_activity=Mock())

        with patch.object(reframe, "camera_system", camera_system):
            client = TestClient(reframe._create_fastapi_routes())
            response = client.post(
                "/api/preview/controls",
                json={
                    "client_id": "desktop",
                    "action": "set_exposure_value",
                    "exposure_value": 0.5,
                },
            )

        self.assertEqual(response.status_code, 200)
        camera_manager.set_exposure_value.assert_called_once_with(0.5)

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
