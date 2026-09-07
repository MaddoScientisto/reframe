import asyncio
import copy
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

picamera2 = types.ModuleType("picamera2")
picamera2.Picamera2 = object
sys.modules.setdefault("picamera2", picamera2)

import dashboard
import reframe


class CarouselDashboardTests(unittest.TestCase):
    def test_settings_include_carousel_defaults_and_validate_interval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = dashboard.SettingsManager(str(Path(temp_dir) / "settings.json"))
            settings = manager.load_settings()

        self.assertEqual(settings["carousel"], {"interval_seconds": 30, "photo_ids": [], "shuffle": False})
        invalid = copy.deepcopy(settings)
        invalid["carousel"]["interval_seconds"] = 0
        with self.assertRaises(dashboard.SettingsValidationError):
            dashboard.validate_settings(invalid)
        invalid["carousel"]["interval_seconds"] = 30
        invalid["carousel"]["shuffle"] = "yes"
        with self.assertRaises(dashboard.SettingsValidationError):
            dashboard.validate_settings(invalid)

    def test_photo_listing_marks_and_filters_carousel_photos(self):
        hardware_photos = [
            {"id": "00002", "original_path": "/photos/00002.jpg"},
            {"id": "00001", "original_path": "/photos/00001.jpg"},
        ]
        settings = {"carousel": {"photo_ids": ["00001"], "interval_seconds": 30}}
        with patch.object(dashboard.reframe_client, "get", new=AsyncMock(return_value=hardware_photos)), \
                patch.object(dashboard.settings_manager, "load_settings", return_value=settings):
            all_photos = asyncio.run(dashboard.list_photos(page=1, limit=20))

        self.assertEqual([photo["carousel_enabled"] for photo in all_photos["photos"]], [False, True])

        with patch.object(dashboard.reframe_client, "get", new=AsyncMock(return_value=hardware_photos)), \
                patch.object(dashboard.settings_manager, "load_settings", return_value=settings):
            carousel_photos = asyncio.run(dashboard.list_photos(page=1, limit=20, carousel_only=True))

        self.assertEqual([photo["id"] for photo in carousel_photos["photos"]], ["00001"])
        self.assertEqual(carousel_photos["pagination"]["total_photos"], 1)

    def test_photo_selection_is_saved_and_reloaded(self):
        previous_settings = {"carousel": {"photo_ids": ["00001"], "interval_seconds": 30}}
        with patch.object(dashboard.settings_manager, "load_settings", return_value=copy.deepcopy(previous_settings)), \
                patch.object(dashboard.settings_manager, "save_settings") as save_settings, \
                patch.object(dashboard.reframe_client, "post", new=AsyncMock(return_value={"success": True})):
            response = TestClient(dashboard.app).post(
                "/api/carousel/photos/00002",
                json={"included": True},
            )

        self.assertEqual(response.status_code, 200)
        save_settings.assert_called_once_with({"carousel": {"photo_ids": ["00001", "00002"]}})


class CarouselWorkerTests(unittest.TestCase):
    def setUp(self):
        self.system = reframe.CameraSystem.__new__(reframe.CameraSystem)
        self.system.camera_manager = Mock()
        self.system.camera_manager.settings = {
            "carousel": {"interval_seconds": 1, "photo_ids": ["00001"], "shuffle": False}
        }
        self.system.file_manager = Mock()
        self.system.file_manager.get_photo_info.return_value = {"id": "00001"}
        self.system.update_activity = Mock()
        self.system._carousel_lock = threading.Lock()
        self.system._carousel_stop_event = None
        self.system._carousel_thread = None
        self.system._carousel_active = False
        self.system._carousel_queue = []
        self.system._carousel_current_photo_id = None
        self.system._carousel_advance_event = threading.Event()

    def test_start_and_stop_carousel_worker(self):
        displayed = threading.Event()

        def display_photo(photo_id):
            displayed.set()
            return {"success": True, "photo_id": photo_id}

        self.system.display_photo_api = display_photo
        result = self.system.start_carousel()

        self.assertTrue(result["active"])
        self.assertTrue(displayed.wait(1))
        stopped = self.system.stop_carousel()

        self.assertFalse(stopped["active"])
        self.assertFalse(self.system.carousel_status_api()["active"])

    def test_start_requires_an_existing_selected_photo(self):
        self.system.file_manager.get_photo_info.return_value = None

        result = self.system.start_carousel()

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "no_carousel_photos")

    def test_shuffle_is_applied_once_when_carousel_starts(self):
        self.system.camera_manager.settings["carousel"] = {
            "interval_seconds": 1,
            "photo_ids": ["00001", "00002", "00003"],
            "shuffle": True,
        }
        self.system.file_manager.get_photo_info.side_effect = lambda photo_id: {"id": photo_id}
        displayed = threading.Event()
        self.system.display_photo_api = lambda photo_id: (displayed.set() or {"success": True})

        with patch.object(reframe.CameraSystem, "_shuffle_carousel_queue", return_value=["00003", "00001", "00002"]):
            self.system.start_carousel()
            self.assertTrue(displayed.wait(1))
            with self.system._carousel_lock:
                self.assertEqual(self.system._carousel_queue, ["00003", "00001", "00002"])
        self.system.stop_carousel()

    def test_fisher_yates_shuffle_uses_each_random_swap(self):
        random_source = Mock()
        random_source.randrange.side_effect = [0, 0]

        with patch.object(reframe.random, "SystemRandom", return_value=random_source):
            shuffled = self.system._shuffle_carousel_queue(["00001", "00002", "00003"])

        self.assertEqual(shuffled, ["00002", "00003", "00001"])
        self.assertEqual(random_source.randrange.call_args_list, [((3,),), ((2,),)])

    def test_settings_reload_preserves_worker_and_syncs_queue(self):
        self.system._carousel_active = True
        self.system.camera_manager.reload_settings.return_value = {"success": True}

        with patch.object(self.system, "_sync_carousel_queue") as sync_queue, \
                patch.object(self.system, "stop_carousel") as stop_carousel:
            result = self.system.reload_settings_api()

        self.assertEqual(result, {"success": True})
        sync_queue.assert_called_once_with()
        stop_carousel.assert_not_called()

    def test_membership_changes_preserve_queue_and_advance_removed_current(self):
        self.system._carousel_active = True
        self.system._carousel_queue = ["00001", "00002", "00003"]
        self.system._carousel_current_photo_id = "00001"
        self.system.camera_manager.settings["carousel"]["photo_ids"] = ["00001", "00003"]
        self.system.file_manager.get_photo_info.side_effect = lambda photo_id: {"id": photo_id}

        self.system._sync_carousel_queue()

        self.assertEqual(self.system._carousel_queue, ["00001", "00003"])
        self.assertFalse(self.system._carousel_advance_event.is_set())

        self.system.camera_manager.settings["carousel"]["photo_ids"] = ["00003"]
        self.system._sync_carousel_queue()

        self.assertEqual(self.system._carousel_queue, ["00003"])
        self.assertTrue(self.system._carousel_advance_event.is_set())


if __name__ == "__main__":
    unittest.main()