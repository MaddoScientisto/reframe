import asyncio
import base64
import copy
import sys
import tempfile
import types
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from PIL import Image

picamera2 = types.ModuleType("picamera2")
picamera2.Picamera2 = object
sys.modules.setdefault("picamera2", picamera2)

import dashboard
import reframe


class PhotoPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original = Path(self.temp_dir.name) / "test.png"
        Image.new("RGB", (1200, 800), (90, 140, 190)).save(self.original)
        self.settings = copy.deepcopy(dashboard.settings_manager.default_settings)
        self.camera = Mock()
        self.camera.camera_manager.settings = self.settings
        self.camera.get_photo_info_api.return_value = {"original_path": str(self.original)}
        self.camera.eink_display.display_buffer_async.return_value = {"success": True}
        self.camera_patch = patch.object(reframe, "camera_system", self.camera)
        self.camera_patch.start()
        self.client = TestClient(reframe._create_fastapi_routes())

    def tearDown(self):
        self.camera_patch.stop()
        self.temp_dir.cleanup()

    def test_preview_modes_do_not_write_files_or_settings(self):
        before = self.original.read_bytes()
        settings_before = copy.deepcopy(self.settings)
        for mode in ("floyd_steinberg", "ordered", "bayer_natural_pair", "gb-default", "gb-default-color"):
            with self.subTest(mode=mode):
                response = self.client.post("/api/photos/test/preview", json={"dithering_method": mode})
                self.assertEqual(response.status_code, 200)
                with Image.open(BytesIO(base64.b64decode(response.json()["png"]))) as image:
                    self.assertEqual(image.size, reframe.DISPLAY_IMAGE_SIZE)
                    self.assertEqual(image.format, "PNG")
        self.assertEqual(self.original.read_bytes(), before)
        self.assertEqual(list(Path(self.temp_dir.name).iterdir()), [self.original])
        self.assertEqual(self.settings, settings_before)
        self.camera.eink_display.display_buffer_async.assert_not_called()

    def test_invalid_mode_palette_and_missing_photo(self):
        for body in ({"dithering_method": "invalid"}, {"dithering_method": []},
                     {"dithering_method": "ordered", "gb_color_palette": "invalid"}):
            self.assertEqual(self.client.post("/api/photos/test/preview", json=body).status_code, 400)
        self.camera.get_photo_info_api.return_value = None
        self.assertEqual(self.client.post("/api/photos/missing/preview", json={"dithering_method": "ordered"}).status_code, 404)

    def test_display_uses_exact_preview_and_preserves_busy_response(self):
        preview = self.client.post("/api/photos/test/preview", json={"dithering_method": "ordered"}).json()
        with Image.open(BytesIO(base64.b64decode(preview["png"]))) as image:
            expected = reframe.ImageProcessor.img2buffer(image)
        response = self.client.post("/api/preview/display", json=preview)
        self.assertTrue(response.json()["success"])
        actual = self.camera.eink_display.display_buffer_async.call_args.args[0]
        self.assertEqual(bytes(actual), bytes(expected))
        self.camera.eink_display.display_buffer_async.return_value = {"success": False, "error": "display_busy"}
        self.assertFalse(self.client.post("/api/preview/display", json=preview).json()["success"])

    def test_display_rejects_invalid_images(self):
        for encoded in (None, "not base64", base64.b64encode(self.original.read_bytes()).decode("ascii")):
            self.assertEqual(self.client.post("/api/preview/display", json={"png": encoded}).status_code, 400)
        self.camera.eink_display.display_buffer_async.assert_not_called()

    def test_temporary_download_upscales_without_changing_screen_png(self):
        image = Image.new("RGB", (2, 1))
        image.putdata([(255, 0, 0), (0, 0, 255)])
        output = BytesIO()
        image.save(output, format="PNG")
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        for upscale in (False, True):
            with patch.object(dashboard.reframe_client, "post", AsyncMock(return_value={"png": encoded})), patch.object(
                dashboard.settings_manager, "load_settings", return_value={"exports": {"upscale_dithered_2x": upscale}}
            ):
                result = asyncio.run(dashboard.preview_photo("test", {"dithering_method": "ordered"}))
            self.assertEqual(result["png"], encoded)
            with Image.open(BytesIO(base64.b64decode(result["download_png"]))) as exported:
                self.assertEqual(exported.size, (4, 2) if upscale else (2, 1))
                self.assertEqual(exported.getpixel((0, 0)), (255, 0, 0))
                self.assertEqual(exported.getpixel((exported.width - 1, 0)), (0, 0, 255))


if __name__ == "__main__":
    unittest.main()