import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from photo_contract import (
    canonical_original_filename,
    dithered_download_filename,
    resolve_capture_timestamp,
    safe_dither_method_token,
    short_content_hash,
)


class PhotoContractTests(unittest.TestCase):
    def test_hash_and_canonical_filename_are_stable(self):
        content = b"captured image bytes"
        capture_time = datetime(2026, 9, 11, 14, 30, 15, tzinfo=timezone.utc)
        filename, photo_id = canonical_original_filename(42, capture_time, content)

        self.assertEqual(photo_id, hashlib.sha256(content).hexdigest()[:8])
        local_timestamp = capture_time.astimezone().strftime("%Y%m%d_%H%M%S")
        self.assertEqual(filename, f"00042_{local_timestamp}_{photo_id}.jpg")
        self.assertEqual(short_content_hash(content), photo_id)

    def test_timestamp_priority_and_fallback(self):
        authoritative = resolve_capture_timestamp({"CaptureTimestamp": "2026-09-11T14:30:15+02:00"})
        self.assertEqual(authoritative.isoformat(), "2026-09-11T14:30:15+02:00")

        fallback = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(resolve_capture_timestamp({}, fallback), fallback)
        with self.assertRaises(ValueError):
            resolve_capture_timestamp({"CaptureTimestamp": "1970-01-01T00:00:00Z"})

    def test_download_name_escapes_method_and_keeps_x2_download_only(self):
        original = "00042_20260911_143015_16b9cc47.jpg"
        self.assertEqual(safe_dither_method_token("GB Default/Color"), "gb_default_color")
        self.assertEqual(safe_dither_method_token("///"), "unknown")
        self.assertEqual(
            dithered_download_filename(original, "GB Default/Color"),
            "00042_20260911_143015_16b9cc47_gb_default_color.png",
        )
        self.assertEqual(
            dithered_download_filename(original, "ordered", upscale_2x=True),
            "00042_20260911_143015_16b9cc47_ordered_x2.png",
        )

    def test_exposure_time_is_converted_from_microseconds_to_seconds(self):
        import sys
        import types

        picamera2 = types.ModuleType("picamera2")
        picamera2.Picamera2 = object
        sys.modules.setdefault("picamera2", picamera2)
        import reframe

        self.assertEqual(reframe.ImageProcessor._exposure_time_rational(10_000), (10_000, 1_000_000))


class LegacyFileDiscoveryTests(unittest.TestCase):
    def test_paginated_listing_reads_only_requested_records(self):
        import sys
        import types

        picamera2 = types.ModuleType("picamera2")
        picamera2.Picamera2 = object
        sys.modules.setdefault("picamera2", picamera2)
        import reframe

        with tempfile.TemporaryDirectory() as directory:
            processed = Path(directory) / "dithered"
            processed.mkdir()
            photos = Path(directory) / "photos"
            photos.mkdir()
            for index in range(5):
                (photos / f"{index:05d}.jpg").write_bytes(f"photo-{index}".encode())
            manager = reframe.FileManager(str(photos), str(processed))

            with patch.object(manager, "get_photo_info", wraps=manager.get_photo_info) as get_info:
                result = manager.list_photo_page(page=2, limit=2)

            self.assertEqual(len(result["photos"]), 2)
            self.assertEqual(result["pagination"]["total_photos"], 5)
            self.assertEqual(result["pagination"]["total_pages"], 3)
            self.assertEqual(get_info.call_count, 2)

    def test_legacy_and_canonical_records_are_addressable_without_renaming(self):
        import sys
        import types

        picamera2 = types.ModuleType("picamera2")
        picamera2.Picamera2 = object
        sys.modules.setdefault("picamera2", picamera2)
        import reframe

        with tempfile.TemporaryDirectory() as directory:
            processed = Path(directory) / "dithered"
            processed.mkdir()
            photos = Path(directory) / "photos"
            photos.mkdir()
            (photos / "00042.jpg").write_bytes(b"legacy")
            canonical_name, canonical_id = canonical_original_filename(
                43,
                datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc),
                b"canonical",
            )
            (photos / canonical_name).write_bytes(b"canonical")
            manager = reframe.FileManager(str(photos), str(processed))

            legacy = manager.get_photo_info("00042")
            canonical = manager.get_photo_info(canonical_id)
            self.assertEqual(legacy["id_kind"], "legacy")
            self.assertEqual(canonical["id"], canonical_id)
            with patch.object(manager, "_find_original_path", side_effect=AssertionError("listing rescanned photos")):
                listed = manager.list_all_photos()
            self.assertEqual(len(listed), 2)

            with patch.object(reframe.ImageProcessor, "process_photo_with_settings", return_value={"success": True}) as process:
                result = reframe.ImageProcessor.reprocess_photo_by_id(
                    canonical_id,
                    {"dithering_method": "ordered", "gb_color_palette": "blue_yellow"},
                    photos_path=str(photos),
                    output_path=str(processed),
                    file_manager=manager,
                )

            self.assertTrue(result["success"])
            process.assert_called_once_with(
                str(photos / canonical_name),
                str(processed / f"{canonical_id}_dithered.png"),
                {"dithering_method": "ordered", "gb_color_palette": "blue_yellow"},
                photo_id=canonical_id,
            )


if __name__ == "__main__":
    unittest.main()
