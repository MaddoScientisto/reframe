import json
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path


picamera2 = types.ModuleType("picamera2")
picamera2.Picamera2 = object
sys.modules.setdefault("picamera2", picamera2)

import reframe


class FakeEPD:
    def __init__(self):
        self.display_started = threading.Event()
        self.reset_called = False
        self.force_stop_called = False
        self.shutdown_called = False
        self.init_calls = 0
        self.refresh_modes = []

    def init(self):
        self.init_calls += 1

    def display(self, buffer, abort_event=None, fast_refresh=False):
        self.refresh_modes.append(fast_refresh)
        self.display_started.set()
        while abort_event is not None and not abort_event.is_set():
            time.sleep(0.001)
        if abort_event is not None and abort_event.is_set():
            raise RuntimeError("fake refresh interrupted")

    def reset(self):
        self.reset_called = True

    def force_stop(self):
        self.force_stop_called = True

    def shutdown(self):
        self.shutdown_called = True

    def Clear(self, fast_refresh=False):
        self.refresh_modes.append(fast_refresh)


class DisplayControlTests(unittest.TestCase):
    def setUp(self):
        self.settings_dir = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.settings_dir.name) / "settings.json"
        driver_module = types.ModuleType("waveshare_epd.epd4in0e")
        driver_module.EPD = FakeEPD
        package_module = types.ModuleType("waveshare_epd")
        package_module.epd4in0e = driver_module
        sys.modules["waveshare_epd"] = package_module
        sys.modules["waveshare_epd.epd4in0e"] = driver_module

        self.display = reframe.EInkDisplay(str(self.settings_path))
        self.display.epd = FakeEPD()
        self.display._initialized = True

    def tearDown(self):
        if self.display.is_busy():
            self.display.force_stop()
        self.settings_dir.cleanup()

    def test_fast_refresh_follows_saved_setting_for_display_and_clear(self):
        self.display.display_buffer(b"first")
        self.assertEqual(self.display.epd.refresh_modes, [False])

        self.settings_path.write_text(json.dumps({"display": {"fast_refresh": True}}))
        self.display.display_buffer(b"second")
        self.display.clear_display()
        self.assertEqual(self.display.epd.refresh_modes, [False, True, True])

        self.assertTrue(self.display.display_buffer_async(b"third")["success"])
        self.assertTrue(self.display.epd.display_started.wait(1))
        self.assertEqual(self.display.epd.refresh_modes[-1], True)

        self.settings_path.write_text(json.dumps({"display": {"fast_refresh": False}}))
        self.display.force_stop()
        self.display.display_buffer(b"fourth")
        self.assertFalse(self.display.epd.refresh_modes[-1])

    def test_fast_refresh_override_applies_to_one_refresh_only(self):
        self.settings_path.write_text(json.dumps({"display": {"fast_refresh": True}}))

        self.display.display_buffer(b"configured")
        self.display.display_buffer(b"one-shot-slow", fast_refresh=False)
        self.display.display_buffer(b"one-shot-fast", fast_refresh=True)
        self.assertEqual(self.display.epd.refresh_modes, [True, False, True])

        self.display.epd.display_started.clear()
        self.assertTrue(self.display.display_buffer_async(b"async-one-shot", fast_refresh=False)["success"])
        self.assertTrue(self.display.epd.display_started.wait(1))
        self.assertFalse(self.display.epd.refresh_modes[-1])
        self.assertTrue(json.loads(self.settings_path.read_text())["display"]["fast_refresh"])

    def test_missing_carousel_refresh_setting_defaults_to_standard(self):
        self.settings_path.write_text(json.dumps({"display": {"fast_refresh": True}}))
        camera_manager = object.__new__(reframe.CameraManager)
        camera_manager.settings_path = str(self.settings_path)

        settings = reframe.CameraManager.load_settings(camera_manager)

        self.assertFalse(settings["carousel"]["fast_refresh"])

    def test_carousel_refresh_uses_its_setting_independently(self):
        for carousel_fast_refresh in (False, True):
            camera_system = object.__new__(reframe.CameraSystem)
            camera_system._carousel_lock = threading.Lock()
            camera_system._carousel_active = True
            camera_system._carousel_queue = ["photo-id"]
            camera_system._carousel_current_photo_id = None
            camera_system._carousel_advance_event = threading.Event()
            stop_event = threading.Event()
            camera_system._carousel_stop_event = stop_event
            camera_system._carousel_thread = None
            camera_system.camera_manager = types.SimpleNamespace(settings={
                "display": {"fast_refresh": not carousel_fast_refresh},
                "carousel": {"fast_refresh": carousel_fast_refresh, "interval_seconds": 30},
            })
            camera_system.update_activity = lambda: None
            display_modes = []

            def display_photo(photo_id, fast_refresh=None):
                display_modes.append((photo_id, fast_refresh))
                stop_event.set()
                camera_system._carousel_advance_event.set()
                return {"success": True}

            camera_system.display_photo_api = display_photo
            camera_system._carousel_loop(stop_event)

            self.assertEqual(display_modes, [("photo-id", carousel_fast_refresh)])

    def test_force_reset_interrupts_refresh_and_redraw_reinitializes(self):
        self.assertTrue(self.display.display_buffer_async(b"first")["success"])
        self.assertTrue(self.display.epd.display_started.wait(1))

        result = self.display.force_reset()

        self.assertTrue(result["success"])
        self.assertTrue(self.display.epd.reset_called)
        self.assertFalse(self.display.is_busy())
        self.display.epd.display_started.clear()
        self.assertTrue(self.display.redraw_last_display()["success"])
        self.assertTrue(self.display.epd.display_started.wait(1))
        self.assertEqual(self.display.epd.init_calls, 1)

    def test_force_stop_interrupts_refresh_and_releases_resources(self):
        self.assertTrue(self.display.display_buffer_async(b"first")["success"])
        self.assertTrue(self.display.epd.display_started.wait(1))

        result = self.display.force_stop()

        self.assertTrue(result["success"])
        self.assertFalse(self.display.is_busy())
        self.assertIsNone(self.display.epd)

        self.assertTrue(self.display.redraw_last_display()["success"])
        self.assertTrue(self.display.is_busy())


if __name__ == "__main__":
    unittest.main()