import sys
import threading
import time
import types
import unittest


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

    def init(self):
        self.init_calls += 1

    def display(self, buffer, abort_event=None):
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


class DisplayControlTests(unittest.TestCase):
    def setUp(self):
        driver_module = types.ModuleType("waveshare_epd.epd4in0e")
        driver_module.EPD = FakeEPD
        package_module = types.ModuleType("waveshare_epd")
        package_module.epd4in0e = driver_module
        sys.modules["waveshare_epd"] = package_module
        sys.modules["waveshare_epd.epd4in0e"] = driver_module

        self.display = reframe.EInkDisplay()
        self.display.epd = FakeEPD()
        self.display._initialized = True

    def tearDown(self):
        if self.display.is_busy():
            self.display.force_stop()

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