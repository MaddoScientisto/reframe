import asyncio
import base64
import copy
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
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

    def test_saved_preview_persists_rotation_and_dither_metadata(self):
        preview = Image.new("P", reframe.DISPLAY_IMAGE_SIZE)
        output = BytesIO()
        preview.save(output, format="PNG")
        encoded = base64.b64encode(output.getvalue()).decode("ascii")

        result = reframe.ImageProcessor.save_dithered_preview_by_id(
            "test",
            encoded_png=encoded,
            rotation=1,
            dithering_method="ordered",
            gb_color_palette="blue_yellow",
            photos_path=self.temp_dir.name,
            output_path=self.temp_dir.name,
        )

        self.assertTrue(result["success"])
        dithered_path = Path(self.temp_dir.name) / "test_dithered.png"
        with Image.open(dithered_path) as saved:
            self.assertEqual(saved.format, "PNG")
            self.assertEqual(saved.getexif().get(reframe.EXIF_ORIENTATION_TAG), 6)
            self.assertEqual(saved.info.get("reframe:dithering_method"), "ordered")
            self.assertEqual(saved.info.get("reframe:gb_color_palette"), "blue_yellow")
            display_image = reframe.ImageProcessor.prepare_dithered_for_display(saved)
        self.assertEqual(display_image.size, reframe.DISPLAY_IMAGE_SIZE)

    def test_capture_metadata_is_written_to_jpeg_exif(self):
        image = Image.new("RGB", (20, 10), "white")
        output_path = Path(self.temp_dir.name) / "captured.jpg"
        capture_time = datetime(2026, 9, 11, 13, 12, 45, 139352, tzinfo=timezone.utc)
        reframe.ImageProcessor.save_image_with_metadata(
            image,
            str(output_path),
            metadata={"ExposureTime": 20_000, "AnalogueGain": 2.0, "FNumber": 2.8},
            capture_time=capture_time,
            photo_metadata={
                "artist": "Maddo",
                "copyright": "Maddo 2026",
                "image_description": "ReFrame test capture",
            },
            camera_identity={
                "make": "Raspberry Pi",
                "model": "ReFrame Camera",
                "hardware_model": "Raspberry Pi Camera Module 3",
            },
            operating_system="Test OS",
        )

        with Image.open(output_path) as saved:
            exif = saved.getexif()
            exif_data = exif.get_ifd(reframe.EXIF_EXIF_IFD_TAG)
            self.assertEqual(exif.get(reframe.EXIF_ORIENTATION_TAG), 1)
            self.assertEqual(exif.get(reframe.EXIF_MAKE_TAG), "Raspberry Pi")
            self.assertEqual(exif.get(reframe.EXIF_MODEL_TAG), "ReFrame Camera")
            self.assertEqual(exif.get(reframe.EXIF_SOFTWARE_TAG), "reFrame (Test OS)")
            self.assertEqual(exif.get(reframe.EXIF_ARTIST_TAG), "Maddo")
            self.assertEqual(exif.get(reframe.EXIF_COPYRIGHT_TAG), "Maddo 2026")
            self.assertEqual(exif.get(reframe.EXIF_IMAGE_DESCRIPTION_TAG), "ReFrame test capture")
            self.assertEqual(exif_data.get(reframe.EXIF_LENS_MAKE_TAG), "Raspberry Pi")
            self.assertEqual(exif_data.get(reframe.EXIF_LENS_MODEL_TAG), "Raspberry Pi Camera Module 3")
            self.assertEqual(exif_data.get(reframe.EXIF_DATETIME_ORIGINAL_TAG), "2026:09:11 13:12:45")
            self.assertEqual(exif_data.get(reframe.EXIF_SUBSEC_TIME_ORIGINAL_TAG), "139352")
            self.assertEqual(exif_data.get(reframe.EXIF_OFFSET_TIME_ORIGINAL_TAG), "+00:00")
            self.assertAlmostEqual(float(exif_data.get(reframe.EXIF_EXPOSURE_TIME_TAG)), 0.02)
            self.assertEqual(exif_data.get(reframe.EXIF_ISO_TAG), 200)
            self.assertAlmostEqual(float(exif_data.get(reframe.EXIF_FNUMBER_TAG)), 2.8, places=2)

    def test_dithered_png_handles_source_without_interop_ifd(self):
        source_path = Path(self.temp_dir.name) / "source.jpg"
        output_path = Path(self.temp_dir.name) / "dithered.png"
        source = Image.new("RGB", (20, 10), "white")
        reframe.ImageProcessor.save_image_with_metadata(
            source,
            str(source_path),
            metadata={},
            capture_time=datetime(2026, 9, 11, 13, 12, 45, tzinfo=timezone.utc),
        )

        with Image.open(source_path) as saved_source:
            rendered = reframe.ImageProcessor.render_photo_with_settings(
                str(source_path),
                {"dithering_method": "ordered", "gb_color_palette": "blue_yellow"},
            )
            reframe.ImageProcessor.save_dithered_image(
                rendered,
                str(output_path),
                source_image=saved_source,
                dithering_method="ordered",
                gb_color_palette="blue_yellow",
            )
            rendered.close()

        with Image.open(output_path) as saved_dithered:
            self.assertEqual(saved_dithered.info.get("reframe:dithering_method"), "ordered")
            self.assertEqual(saved_dithered.getexif().get(reframe.EXIF_ORIENTATION_TAG), 1)

    def test_gallery_rotation_changes_orientation_without_changing_pixels(self):
        original = Image.new("RGB", (4, 3))
        original.putdata([
            (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
            (255, 0, 255), (0, 255, 255), (40, 40, 40), (80, 80, 80),
            (120, 120, 120), (160, 160, 160), (200, 200, 200), (240, 240, 240),
        ])
        original_path = Path(self.temp_dir.name) / "original.jpg"
        original.save(original_path, format="JPEG", quality=100)
        with Image.open(original_path) as saved_original:
            original_pixels = list(saved_original.convert("RGB").getdata())
            preview = saved_original.copy()
        encoded_output = BytesIO()
        preview.save(encoded_output, format="PNG")

        result = reframe.ImageProcessor.save_dithered_preview_by_id(
            "original",
            encoded_png=base64.b64encode(encoded_output.getvalue()).decode("ascii"),
            rotation=3,
            photos_path=self.temp_dir.name,
            output_path=self.temp_dir.name,
        )

        self.assertTrue(result["success"])
        with Image.open(original_path) as saved_original, Image.open(Path(self.temp_dir.name) / "original_dithered.png") as dithered:
            self.assertEqual(list(saved_original.convert("RGB").getdata()), original_pixels)
            self.assertEqual(list(dithered.convert("RGB").getdata()), list(preview.convert("RGB").getdata()))
            self.assertEqual(dithered.getexif().get(reframe.EXIF_ORIENTATION_TAG), 8)

    def test_dithered_metadata_exposes_scalar_capture_time_and_sensor_metadata(self):
        image = Image.new("RGB", (20, 10), "white")
        output_path = Path(self.temp_dir.name) / "captured.png"
        capture_time = datetime(2026, 9, 11, 13, 12, 45, 139352, tzinfo=timezone.utc)
        reframe.ImageProcessor.save_image_with_metadata(
            image,
            str(output_path),
            metadata={"SensorTimestamp": 266225241000, "LensPosition": 1.0},
            capture_time=capture_time,
        )

        metadata = reframe.ImageProcessor.read_dithered_metadata(str(output_path))

        self.assertEqual(metadata["capture_time"], capture_time.isoformat())
        self.assertEqual(metadata["sensor_metadata"], {
            "LensPosition": 1.0,
            "SensorTimestamp": 266225241000,
        })
        self.assertEqual(metadata["capture_metadata"]["schema"], 2)
        self.assertEqual(metadata["exif_metadata"]["camera"]["model"], "ReFrame Camera")
        self.assertEqual(metadata["exif_metadata"]["dates"]["original"], "2026:09:11 13:12:45")
        self.assertAlmostEqual(metadata["exif_metadata"]["technical"]["exposure_time"], 0.02)


if __name__ == "__main__":
    unittest.main()