#!/usr/bin/env python3

import os
import sys
import json
import asyncio
import math
import subprocess
import logging
import socket
import base64
import random
import hashlib
import platform
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from time import sleep

from photo_contract import (
    CANONICAL_FILENAME_PATTERN,
    canonical_original_filename,
    resolve_capture_timestamp,
    short_content_hash,
)

# Lazy-loaded by _lazy_import_pil() on first use
Image = None
ImageEnhance = None

def _lazy_import_pil():
    """Import PIL only when needed for image processing."""
    global Image, ImageEnhance
    if Image is None:
        from PIL import Image, ImageEnhance
    return Image, ImageEnhance

from picamera2 import Picamera2

from typing import Optional, Dict, Any

np = None
_NATURAL_PAIR_LUT_CACHE = {}
_NATURAL_PAIR_LUT_CHUNK_SIZE = 4096

def _lazy_import_numpy():
    """Import NumPy only when image processing/display conversion needs it."""
    global np
    if np is None:
        import numpy as _np
        np = _np
    return np

# Lazy-loaded by _lazy_import_fastapi() on first use
_API_AVAILABLE = None
FastAPI = None
HTTPException = None
Request = None
StreamingResponse = None
uvicorn = None

def _lazy_import_fastapi():
    """Import FastAPI/uvicorn only when needed."""
    global _API_AVAILABLE, FastAPI, HTTPException, Request, StreamingResponse, uvicorn
    if _API_AVAILABLE is None:
        try:
            from fastapi import FastAPI, HTTPException, Request
            from fastapi.responses import StreamingResponse
            import uvicorn
            _API_AVAILABLE = True
        except Exception:
            _API_AVAILABLE = False
            FastAPI = None
            HTTPException = None
            Request = None
            StreamingResponse = None
            uvicorn = None
    return _API_AVAILABLE

import threading
import time

# ═══════════════════════════════════════════════════════════════════
# HARDWARE: Display — defaults to Waveshare 4" ePaper Spectra 6
# The driver lives in waveshare_epd/. To use a different e-ink display,
# update the DISPLAY_* constants, ImageProcessor palette/buffer mapping, and
# the EInkDisplay adapter class below.
# ═══════════════════════════════════════════════════════════════════
libdir = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'lib')
if os.path.exists(libdir):
    sys.path.append(libdir)

# Logging setup
logging.basicConfig(level=logging.INFO)

# Constants for file paths
BASE_PATH = os.path.dirname(os.path.realpath(__file__))
SAVE_PATH = os.path.join(BASE_PATH, "photos")
PROCESSED_PATH = os.path.join(BASE_PATH, "dithered_photos")
RUNTIME_PATH = os.path.join(BASE_PATH, ".runtime")
HDR_HELPER_PATH = os.path.join(BASE_PATH, "scripts", "enable_hdr.sh")
ORIGINAL_CAPTURE_EXTENSION = "jpg"
DISPLAY_IMAGE_WIDTH = 600
DISPLAY_IMAGE_HEIGHT = 400
DISPLAY_PANEL_WIDTH = 400
DISPLAY_PANEL_HEIGHT = 600
DISPLAY_IMAGE_SIZE = (DISPLAY_IMAGE_WIDTH, DISPLAY_IMAGE_HEIGHT)
DISPLAY_PANEL_SIZE = (DISPLAY_PANEL_WIDTH, DISPLAY_PANEL_HEIGHT)
EXIF_ORIENTATION_TAG = 274
EXIF_IMAGE_DESCRIPTION_TAG = 270
EXIF_MAKE_TAG = 271
EXIF_MODEL_TAG = 272
EXIF_EXIF_IFD_TAG = 34665
EXIF_GPS_IFD_TAG = 34853
EXIF_INTEROP_IFD_TAG = 40965
EXIF_X_RESOLUTION_TAG = 282
EXIF_Y_RESOLUTION_TAG = 283
EXIF_RESOLUTION_UNIT_TAG = 296
EXIF_SOFTWARE_TAG = 305
EXIF_DATETIME_TAG = 306
EXIF_ARTIST_TAG = 315
EXIF_COPYRIGHT_TAG = 33432
EXIF_EXIF_VERSION_TAG = 36864
EXIF_DATETIME_ORIGINAL_TAG = 36867
EXIF_DATETIME_DIGITIZED_TAG = 36868
EXIF_COMPONENTS_CONFIGURATION_TAG = 37121
EXIF_OFFSET_TIME_TAG = 36880
EXIF_OFFSET_TIME_ORIGINAL_TAG = 36881
EXIF_OFFSET_TIME_DIGITIZED_TAG = 36882
EXIF_EXPOSURE_TIME_TAG = 33434
EXIF_FNUMBER_TAG = 33437
EXIF_ISO_TAG = 34855
EXIF_EXPOSURE_PROGRAM_TAG = 34850
EXIF_SHUTTER_SPEED_VALUE_TAG = 37377
EXIF_APERTURE_VALUE_TAG = 37378
EXIF_EXPOSURE_BIAS_TAG = 37380
EXIF_MAX_APERTURE_VALUE_TAG = 37381
EXIF_METERING_MODE_TAG = 37383
EXIF_SUBJECT_DISTANCE_TAG = 37382
EXIF_LIGHT_SOURCE_TAG = 37384
EXIF_FLASH_TAG = 37385
EXIF_FOCAL_LENGTH_TAG = 37386
EXIF_WHITE_BALANCE_TAG = 41987
EXIF_USER_COMMENT_TAG = 37510
EXIF_SUBSEC_TIME_TAG = 37520
EXIF_SUBSEC_TIME_ORIGINAL_TAG = 37521
EXIF_SUBSEC_TIME_DIGITIZED_TAG = 37522
EXIF_PIXEL_X_TAG = 40962
EXIF_PIXEL_Y_TAG = 40963
EXIF_COLOR_SPACE_TAG = 40961
EXIF_CUSTOM_RENDERED_TAG = 41985
EXIF_EXPOSURE_MODE_TAG = 41986
EXIF_LENS_MAKE_TAG = 42035
EXIF_LENS_MODEL_TAG = 42036
ROTATION_TO_EXIF = {0: 1, 1: 6, 2: 3, 3: 8}
EXIF_TO_ROTATION = {value: key for key, value in ROTATION_TO_EXIF.items()}
DEFAULT_CAMERA_MAKE = "Raspberry Pi"
DEFAULT_CAMERA_MODEL = "ReFrame Camera"
DEFAULT_CAMERA_HARDWARE = "Raspberry Pi Camera Module 3"
BUTTON_POLL_INTERVAL_SECONDS = 0.025
LIVE_PREVIEW_MAX_SIZE = (640, 480)
LIVE_PREVIEW_FPS = 5
LIVE_PREVIEW_JPEG_QUALITY = 75
AWB_MODE_VALUES = {
    "auto": 0,
    "incandescent": 1,
    "tungsten": 2,
    "fluorescent": 3,
    "indoor": 4,
    "daylight": 5,
    "cloudy": 6,
    "custom": 7,
}
AWB_MODE_NAMES = {value: name for name, value in AWB_MODE_VALUES.items()}
AWB_LIGHT_SOURCE_VALUES = {
    1: 3,   # incandescent
    2: 3,   # tungsten
    3: 2,   # fluorescent
    4: 3,   # indoor
    5: 1,   # daylight
    6: 10,  # cloudy weather
}

def flush_file(path):
    """Flush file contents before an atomic publication boundary."""
    with open(path, "rb") as file_handle:
        os.fsync(file_handle.fileno())


def flush_directory(path):
    """Flush a directory when the host platform exposes directory handles."""
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except (AttributeError, OSError):
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

# Color palettes for dithering. We blend between the two to create a saturated look while preserving details.
# Idea from https://github.com/pimoroni/inky
DESATURATED_PALETTE = [
    [0, 0, 0],          # Black
    [255, 255, 255],    # White
    [0, 255, 0],        # Green
    [0, 0, 255],        # Blue
    [255, 0, 0],        # Red
    [255, 255, 0],      # Yellow
]

SATURATED_PALETTE = [
    [57, 48, 57],       # Muted Black
    [255, 255, 255],    # White
    [40, 91, 58],       # Muted Green
    [0, 128, 255],      # Muted Blue
    [156, 72, 75],      # Muted Red
    [208, 190, 71],     # Muted Yellow
]

GB_COLOR_PALETTE_COMBINATIONS = {
    "blue_yellow": [0, 5, 2, 1],
    "green_yellow": [0, 6, 2, 1],
    "red_yellow": [0, 3, 2, 1],
    "blue_red": [0, 5, 3, 1],
    "blue_green": [0, 5, 6, 1],
}

def get_dashboard_access_info(hostname=None, ip_address=None):
    """Build the friendly dashboard URLs for this device."""
    if hostname is None:
        hostname = socket.gethostname()
    hostname = (hostname or "reframe").split(".")[0]

    if ip_address is None:
        ip_address = get_lan_ip_address()

    primary_url = f"http://{hostname}.local"
    fallback_url = f"http://{ip_address}" if ip_address else None
    marker = f"{primary_url}|{fallback_url or ''}"
    return {
        "hostname": hostname,
        "ip_address": ip_address,
        "primary_url": primary_url,
        "fallback_url": fallback_url,
        "marker": marker
    }


def yuv420_to_image(frame, size):
    """Convert Picamera2's packed planar YUV420 frame to an RGB image."""
    numpy = _lazy_import_numpy()
    image_class, _ = _lazy_import_pil()
    width, height = size
    array = numpy.asarray(frame)
    expected_rows = height + height // 2
    if (
        width % 2
        or height % 4
        or array.ndim != 2
        or array.shape[0] < expected_rows
        or array.shape[1] < width
    ):
        raise RuntimeError(
            f"unexpected YUV420 frame shape {array.shape}; expected at least "
            f"({expected_rows}, {width})"
        )

    y_plane = array[:height, :width].astype(numpy.float32)
    chroma_rows = height // 4
    u_plane = array[height:height + chroma_rows, :width].reshape(height // 2, width // 2)
    v_start = height + chroma_rows
    v_plane = array[v_start:v_start + chroma_rows, :width].reshape(height // 2, width // 2)
    u_plane = numpy.repeat(numpy.repeat(u_plane, 2, axis=0), 2, axis=1).astype(numpy.float32)
    v_plane = numpy.repeat(numpy.repeat(v_plane, 2, axis=0), 2, axis=1).astype(numpy.float32)

    red = y_plane + 1.402 * (v_plane - 128.0)
    green = y_plane - 0.344136 * (u_plane - 128.0) - 0.714136 * (v_plane - 128.0)
    blue = y_plane + 1.772 * (u_plane - 128.0)
    rgb = numpy.clip(numpy.stack((red, green, blue), axis=2), 0, 255).astype(numpy.uint8)
    return image_class.fromarray(rgb, mode="RGB")


def get_preview_size(resolution):
    """Choose a YUV420 preview size with the exact still-image aspect ratio."""
    still_width = int(resolution["width"])
    still_height = int(resolution["height"])
    ratio_width = still_width // math.gcd(still_width, still_height)
    ratio_height = still_height // math.gcd(still_width, still_height)
    max_width, max_height = LIVE_PREVIEW_MAX_SIZE
    scale = min(max_width // ratio_width, max_height // ratio_height)

    while scale > 0:
        preview_width = ratio_width * scale
        preview_height = ratio_height * scale
        if preview_width % 2 == 0 and preview_height % 4 == 0:
            return preview_width, preview_height
        scale -= 1

    raise ValueError(f"Could not derive an aligned preview size for {still_width}x{still_height}")


class PreviewSessionBusy(RuntimeError):
    pass


class PreviewSessionAccessDenied(RuntimeError):
    pass


class FocusOperationBusy(RuntimeError):
    pass


class FocusOperationError(RuntimeError):
    pass


class PreviewControlError(RuntimeError):
    pass


class CameraPreviewSession:
    """Keep one encoded preview frame available for a single HTTP client."""

    def __init__(self, camera_manager, owner_id=None):
        self.camera_manager = camera_manager
        self.owner_id = owner_id
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread = None
        self._active = False
        self._sequence = 0
        self._latest_frame = None
        self._frame_times = []
        self._started_monotonic = None

    def start(self):
        with self._condition:
            if self._active:
                raise PreviewSessionBusy("Live preview is already in use")
            self._active = True
            self._started_monotonic = time.monotonic()
            self._frame_times = []
            self._latest_frame = None
            self._sequence = 0
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="camera-preview",
                daemon=True,
            )
            self._thread.start()

    def is_active(self):
        with self._condition:
            return self._active

    def stop(self):
        with self._condition:
            self._active = False
            self._stop_event.set()
            self._condition.notify_all()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def wait_for_frame(self, after_sequence, timeout=2):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._active and self._sequence <= after_sequence:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            if self._sequence <= after_sequence:
                return None
            return self._sequence, self._latest_frame

    def _run(self):
        interval = 1 / LIVE_PREVIEW_FPS
        next_deadline = time.monotonic()
        try:
            while not self._stop_event.is_set():
                frame_started = time.monotonic()
                try:
                    capture_frame = getattr(self.camera_manager, "capture_preview_frame", None)
                    frame = capture_frame() if capture_frame else self.camera_manager.capture_preview_jpeg()
                except Exception:
                    logging.exception("Live preview capture failed")
                    break

                if frame is not None:
                    with self._condition:
                        self._latest_frame = frame
                        self._sequence += 1
                        self._frame_times.append(time.monotonic())
                        if len(self._frame_times) > 30:
                            self._frame_times.pop(0)
                        self._condition.notify_all()

                next_deadline += interval
                delay = next_deadline - time.monotonic()
                if delay > 0:
                    self._stop_event.wait(delay)
                elif time.monotonic() - frame_started > interval:
                    next_deadline = time.monotonic()
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()

    def get_frame_telemetry(self):
        with self._condition:
            frame = self._latest_frame
            sequence = self._sequence
            frame_times = list(self._frame_times)

        if frame is None:
            return {
                "sequence": sequence,
                "frame": None,
                "age_seconds": None,
                "frame_rate": None,
            }

        captured_at = frame.get("captured_at") if isinstance(frame, dict) else None
        age_seconds = None
        if captured_at is not None:
            age_seconds = max(0.0, time.monotonic() - captured_at)

        frame_rate = None
        if len(frame_times) >= 2 and frame_times[-1] > frame_times[0]:
            frame_rate = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])

        return {
            "sequence": sequence,
            "frame": frame,
            "age_seconds": age_seconds,
            "frame_rate": frame_rate,
        }


def get_lan_ip_address():
    """Return wlan0's usable IPv4 address, if it has one."""
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", "wlan0", "scope", "global"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            fields = result.stdout.split()
            for index, field in enumerate(fields[:-1]):
                if field != "inet":
                    continue
                ip_address = fields[index + 1].split("/", 1)[0]
                if _is_usable_lan_ip(ip_address):
                    return ip_address
    except Exception:
        pass

    return None


def _is_usable_lan_ip(ip_address):
    return (
        ip_address
        and "." in ip_address
        and not ip_address.startswith("127.")
        and not ip_address.startswith("169.254.")
    )


def _enable_camera_hdr():
    """Enable Camera Module 3 HDR after imports but before Picamera2 opens it."""
    if not os.path.isfile(HDR_HELPER_PATH):
        logging.warning("HDR helper not found at %s; continuing without HDR", HDR_HELPER_PATH)
        return

    try:
        result = subprocess.run([HDR_HELPER_PATH], timeout=4, check=False)
        if result.returncode != 0:
            logging.warning("HDR helper exited with status %s", result.returncode)
    except subprocess.TimeoutExpired:
        logging.warning("HDR helper timed out; continuing without HDR")
    except Exception as e:
        logging.warning("Could not run HDR helper: %s", e)


def _notify_systemd_ready(status):
    """Tell systemd startup capture dispatch is complete."""
    notify_socket = os.environ.get("NOTIFY_SOCKET")
    if not notify_socket:
        return

    address = notify_socket
    if address.startswith("@"):
        address = "\0" + address[1:]

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
            notifier.connect(address)
            notifier.send(f"READY=1\nSTATUS={status}".encode("utf-8"))
        logging.info("Notified systemd: %s", status)
    except Exception as e:
        logging.error("Could not notify systemd that startup is ready: %s", e)


def _auto_display_enabled(settings_path):
    """Read only the startup display flag before CameraManager is constructed."""
    try:
        with open(settings_path, "r") as settings_file:
            settings = json.load(settings_file)
        display_settings = settings.get("display", {})
        if isinstance(display_settings, dict):
            return display_settings.get("auto_display", True)
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return True


def render_dashboard_qr_image(access_info):
    """Render a dashboard QR screen for the ePaper display."""
    Image, _ = _lazy_import_pil()
    from PIL import ImageDraw
    import qrcode

    primary_url = access_info["primary_url"]
    fallback_url = access_info.get("fallback_url")

    qr_url = fallback_url or primary_url
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(qr_url)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_image = qr_image.resize((250, 250))

    canvas = Image.new("RGB", DISPLAY_IMAGE_SIZE, "white")
    canvas.paste(qr_image, (30, 75))

    draw = ImageDraw.Draw(canvas)
    x = 315
    draw.text((x, 90), "reFrame dashboard", fill="black")
    draw.text((x, 125), primary_url, fill="black")
    if fallback_url:
        draw.text((x, 170), "If that does not open:", fill="black")
        draw.text((x, 200), fallback_url, fill="black")
    draw.text((x, 255), "Scan with your phone", fill="black")
    draw.text((x, 285), "to open photos.", fill="black")

    return canvas

class CameraManager:
    """Picamera2 camera adapter.

    To support a different camera stack, keep this public surface compatible:
    load/reload settings, configure/start the camera, capture a PIL RGB image,
    optionally save a file capture, and expose timeout/shutdown helpers used by
    CameraSystem. The rest of the app only expects capture_image_with_metadata()
    to return (result_dict, PIL_image).
    """

    def __init__(self, settings_path="settings.json"):
        self.settings_path = settings_path
        self.settings = self.load_settings()
        self.picam2 = Picamera2()
        self._camera_lock = threading.Lock()
        self._focus_lock = threading.Lock()
        camera_settings = self.settings.get("camera", {})
        self._focus_mode = camera_settings.get("autofocus_mode", 2)
        self._manual_focus_position = None
        self._exposure_mode = camera_settings.get("exposure_mode", "auto")
        self._manual_exposure_time_us = camera_settings.get("manual_exposure_time_us")
        self._manual_analogue_gain = camera_settings.get("manual_analogue_gain")
        self._white_balance_mode = camera_settings.get("white_balance_mode", "auto")
        self._white_balance_preset = camera_settings.get("white_balance_preset", "daylight")
        white_balance_gains = camera_settings.get("white_balance_gains", {})
        self._white_balance_gains = {
            "red": white_balance_gains.get("red", 1.0),
            "blue": white_balance_gains.get("blue", 1.0),
        }
        self._preview_session_lock = threading.Lock()
        self._preview_session = None
        self.last_activity_monotonic = time.monotonic()
        self._has_captured = False  # Track if we've taken at least one photo (for adaptive AF)
        self.configure_camera()

    def load_settings(self):
        """Load camera settings from JSON file."""
        try:
            with open(self.settings_path, 'r') as f:
                settings = json.load(f)
            carousel = settings.get("carousel")
            if not isinstance(carousel, dict):
                carousel = {}
                settings["carousel"] = carousel
            carousel.setdefault("interval_seconds", 30)
            carousel.setdefault("photo_ids", [])
            carousel.setdefault("shuffle", False)
            metadata = settings.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                settings["metadata"] = metadata
            metadata.setdefault("artist", "")
            metadata.setdefault("copyright", "")
            metadata.setdefault("image_description", "")
            camera = settings.get("camera")
            if not isinstance(camera, dict):
                camera = {}
                settings["camera"] = camera
            camera.setdefault("exposure_mode", "auto")
            camera.setdefault("manual_exposure_time_us", None)
            camera.setdefault("manual_analogue_gain", None)
            return settings
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.warning(f"Could not load settings from {self.settings_path}: {e}")
            # Return default settings
            return {
                "camera": {
                    "resolution": {"width": 1200, "height": 800},
                    "exposure_value": 0,
                    "exposure_mode": "auto",
                    "manual_exposure_time_us": None,
                    "manual_analogue_gain": None,
                    "sharpness": 3,
                    "autofocus_mode": 2,
                    "white_balance_mode": "auto",
                    "white_balance_preset": "daylight",
                    "white_balance_gains": {"red": 1.0, "blue": 1.0}
                },
                "metadata": {
                    "artist": "",
                    "copyright": "",
                    "image_description": "",
                },
                "processing": {
                    "saturation": 0.6,
                    "brightness_factor": 1.1,
                    "color_factor": 1.4,
                    "dithering_method": "floyd_steinberg",
                    "bayer_size": 4,
                    "threshold_scale": 1.0,
                    "tone_map": "percentile",
                    "gb_color_palette": "blue_yellow"
                },
                "display": {
                    "auto_display": True,
                    "display_timeout": 0,
                    "interrupt_refresh_on_capture": False,
                    "refresh_interrupt_action": "reset"
                },
                "carousel": {
                    "interval_seconds": 30,
                    "photo_ids": [],
                    "shuffle": False
                },
                "system": {
                    "auto_refresh_interval": 30,
                    "auto_timeout_minutes": 10,
                    "auto_timeout_enabled": True,
                    "show_dashboard_qr_on_wifi_connect": True
                }
            }

    def reload_settings(self):
        """Reload settings from file and reconfigure camera."""
        old_settings = self.settings.copy()
        self.settings = self.load_settings()
        camera_settings = self.settings.get("camera", {})
        self._exposure_mode = camera_settings.get("exposure_mode", "auto")
        self._manual_exposure_time_us = camera_settings.get("manual_exposure_time_us")
        self._manual_analogue_gain = camera_settings.get("manual_analogue_gain")
        self._white_balance_mode = camera_settings.get("white_balance_mode", "auto")
        self._white_balance_preset = camera_settings.get("white_balance_preset", "daylight")
        white_balance_gains = camera_settings.get("white_balance_gains", {})
        self._white_balance_gains = {
            "red": white_balance_gains.get("red", 1.0),
            "blue": white_balance_gains.get("blue", 1.0),
        }

        # Only reconfigure if camera settings changed
        camera_changed = old_settings.get("camera", {}) != self.settings.get("camera", {})
        if camera_changed:
            logging.info("Camera settings changed, reconfiguring...")
            self.configure_camera()

        return self.settings

    def apply_camera_settings(self, camera_settings=None):
        """Apply specific camera settings without full reconfiguration."""
        if camera_settings is None:
            camera_settings = self.settings.get("camera", {})

        # Update only the controls that can be changed while running
        controls = {}

        if "exposure_value" in camera_settings:
            controls["ExposureValue"] = camera_settings["exposure_value"]
        controls.update(self._exposure_controls_from_settings(camera_settings))
        if "sharpness" in camera_settings:
            controls["Sharpness"] = camera_settings["sharpness"]
        if "autofocus_mode" in camera_settings:
            controls["AfMode"] = camera_settings["autofocus_mode"]
            self._focus_mode = camera_settings["autofocus_mode"]

        controls.update(self._white_balance_controls_from_settings(camera_settings))

        with self._camera_lock:
            for control_name, control_value in controls.items():
                try:
                    self.picam2.set_controls({control_name: control_value})
                    logging.info(f"Applied {control_name}: {control_value}")
                except Exception as e:
                    logging.warning(f"Could not set {control_name}: {e}")

    def capture_photo_with_metadata(self, file_path=None, fast_mode=False):
        """Capture a photo and return metadata like the dashboard API."""
        if file_path is None:
            # Use FileManager to get consistent naming
            file_manager = FileManager(SAVE_PATH, PROCESSED_PATH)
            file_path = file_manager.get_new_file_path(SAVE_PATH, ORIGINAL_CAPTURE_EXTENSION)

        try:
            self.capture_photo(file_path, fast_mode=fast_mode)

            # Get file info
            file_size = os.path.getsize(file_path)

            return {
                "success": True,
                "photo_id": os.path.splitext(os.path.basename(file_path))[0],
                "original_path": file_path,
                "processed_path": None,  # Will be set after processing
                "file_size": file_size,
                "message": "Photo captured successfully"
            }
        except Exception as e:
            logging.error(f"Error capturing photo: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Photo capture failed: {str(e)}"
            }

    def capture_image_with_metadata(self, file_path=None, fast_mode=False):
        """Capture a PIL image in memory and return metadata for async saving."""
        if file_path is None:
            file_manager = FileManager(SAVE_PATH, PROCESSED_PATH)
            file_path = file_manager.get_new_file_path(SAVE_PATH, ORIGINAL_CAPTURE_EXTENSION)

        try:
            image = self.capture_image(fast_mode=fast_mode)
            capture_metadata = {}
            if hasattr(self.picam2, "capture_metadata"):
                try:
                    capture_metadata = dict(self.picam2.capture_metadata() or {})
                except Exception as error:
                    logging.debug(f"Could not read capture metadata: {error}")
            image.info["reframe_capture_metadata"] = capture_metadata
            return {
                "success": True,
                "photo_id": os.path.splitext(os.path.basename(file_path))[0],
                "original_path": file_path,
                "processed_path": None,
                "file_size": None,
                "message": "Photo captured successfully"
            }, image
        except Exception as e:
            logging.error(f"Error capturing photo: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Photo capture failed: {str(e)}"
            }, None


    def configure_camera(self):
        """Configure the camera settings."""
        camera_settings = self.settings.get("camera", {})
        resolution = camera_settings.get("resolution", {"width": 1200, "height": 800})
        self.preview_size = get_preview_size(resolution)
        self.stop_preview()

        with self._camera_lock:
            try:
                self.picam2.stop()
            except Exception:
                pass

            camera_config = self.picam2.create_still_configuration(
                main={
                    "size": (resolution["width"], resolution["height"]),
                    "format": "RGB888",
                },
                lores={"size": self.preview_size, "format": "YUV420"},
                buffer_count=3,
            )

            controls = {
                "ExposureValue": camera_settings.get("exposure_value", 0),
                "Sharpness": camera_settings.get("sharpness", 3)
            }
            controls.update(self._exposure_controls_from_settings(camera_settings))
            controls.update(self._white_balance_controls_from_settings(camera_settings))
            camera_config["controls"] = controls

            try:
                self.picam2.configure(camera_config)
            except Exception as e:
                logging.error(f"Error configuring camera: {e}")
                basic_config = self.picam2.create_still_configuration(
                    main={
                        "size": (resolution["width"], resolution["height"]),
                        "format": "RGB888",
                    },
                    lores={"size": self.preview_size, "format": "YUV420"},
                    buffer_count=3,
                )
                self.picam2.configure(basic_config)
                logging.info("Using basic camera configuration")

            try:
                af_mode = camera_settings.get("autofocus_mode", 2)
                self.picam2.set_controls({"AfMode": af_mode})
                self._focus_mode = af_mode
            except Exception as e:
                logging.warning(f"Could not set autofocus mode: {e}")

            try:
                white_balance_controls = self._white_balance_controls_from_settings(camera_settings)
                if white_balance_controls:
                    self.picam2.set_controls(white_balance_controls)
            except PreviewControlError as e:
                logging.warning(f"Could not apply white balance settings: {e}")

            self.picam2.start()

    def open_preview_session(self, owner_id=None):
        """Start the single browser-facing live preview session."""
        with self._preview_session_lock:
            if self._preview_session is not None and self._preview_session.is_active():
                raise PreviewSessionBusy("Live preview is already in use")
            previous = self._preview_session
            session = CameraPreviewSession(self, owner_id)
            self._preview_session = session
            try:
                session.start()
            except Exception:
                self._preview_session = previous
                raise
        if previous is not None:
            previous.stop()
        return session

    def close_preview_session(self, session):
        """Stop a preview session and release its camera worker."""
        with self._preview_session_lock:
            if self._preview_session is session:
                self._preview_session = None
        session.stop()

    def close_preview_session_for_owner(self, owner_id):
        """Stop a preview session only when the caller owns it."""
        with self._preview_session_lock:
            session = self._preview_session
            if session is None or session.owner_id != owner_id:
                return False
            self._preview_session = None
        session.stop()
        return True

    def stop_preview(self):
        with self._preview_session_lock:
            session = self._preview_session
            self._preview_session = None
        if session is not None:
            session.stop()

    def capture_preview_frame(self):
        """Capture one lores frame and its metadata from the same request."""
        if not self._camera_lock.acquire(blocking=False):
            return None
        try:
            metadata = {}
            if hasattr(self.picam2, "capture_request"):
                request = self.picam2.capture_request()
                try:
                    frame = _lazy_import_numpy().array(request.make_array("lores"), copy=True)
                    metadata = dict(request.get_metadata() or {})
                finally:
                    request.release()
            else:
                frame = self.picam2.capture_array("lores")
                frame = _lazy_import_numpy().array(frame, copy=True)
        finally:
            self._camera_lock.release()

        self.update_activity_time()
        image = yuv420_to_image(frame, self.preview_size)
        output = BytesIO()
        image.save(output, format="JPEG", quality=LIVE_PREVIEW_JPEG_QUALITY)
        return {
            "jpeg": output.getvalue(),
            "metadata": metadata,
            "captured_at": time.monotonic(),
        }

    def capture_preview_jpeg(self):
        """Capture and encode one lores frame, or skip a busy camera cycle."""
        frame = self.capture_preview_frame()
        return frame["jpeg"] if frame is not None else None

    def _preview_session_for_owner(self, owner_id):
        with self._preview_session_lock:
            session = self._preview_session
            if (
                session is None
                or not session.is_active()
                or session.owner_id != owner_id
            ):
                raise PreviewSessionAccessDenied("Preview client is not active")
            return session

    @staticmethod
    def _metadata_number(metadata, key):
        value = metadata.get(key)
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        return int(number) if number.is_integer() and isinstance(value, int) else number

    @staticmethod
    def _metadata_pair(metadata, key):
        value = metadata.get(key)
        if not isinstance(value, (tuple, list)) or len(value) < 2:
            return None
        try:
            pair = (float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(item) for item in pair):
            return None
        return pair

    @staticmethod
    def _control_range(control):
        if isinstance(control, dict):
            minimum = control.get("min")
            maximum = control.get("max")
            step = control.get("step")
        elif isinstance(control, (tuple, list)) and len(control) >= 2:
            minimum, maximum = control[:2]
            step = control[3] if len(control) >= 4 else None
        else:
            return None
        try:
            minimum = float(minimum)
            maximum = float(maximum)
            step = float(step) if step is not None else None
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(item) for item in (minimum, maximum)) or maximum <= minimum:
            return None
        if step is None or not math.isfinite(step) or step <= 0:
            step = round((maximum - minimum) / 100, 3)
        return {"min": minimum, "max": maximum, "step": step}

    def _colour_gains_range(self):
        control = (getattr(self.picam2, "camera_controls", {}) or {}).get("ColourGains")
        if isinstance(control, dict):
            minimum = control.get("min")
            maximum = control.get("max")
            step = control.get("step")
        elif isinstance(control, (tuple, list)) and len(control) >= 2:
            minimum, maximum = control[:2]
            step = control[3] if len(control) >= 4 else None
        else:
            return None
        if isinstance(minimum, (tuple, list)) or isinstance(maximum, (tuple, list)):
            if not isinstance(minimum, (tuple, list)) or not isinstance(maximum, (tuple, list)):
                return None
            if len(minimum) < 2 or len(maximum) < 2:
                return None
            steps = step if isinstance(step, (tuple, list)) and len(step) >= 2 else (step, step)
            ranges = {}
            for name, index in (("red", 0), ("blue", 1)):
                ranges[name] = self._control_range((minimum[index], maximum[index], None, steps[index]))
                if ranges[name] is None:
                    return None
            return ranges

        common_range = self._control_range((minimum, maximum, None, step))
        if common_range is None:
            return None
        return {"red": dict(common_range), "blue": dict(common_range)}

    def _supported_awb_modes(self):
        controls = getattr(self.picam2, "camera_controls", {}) or {}
        descriptor = controls.get("AwbMode")
        if descriptor is None:
            return []
        if isinstance(descriptor, dict):
            values = descriptor.get("values") or descriptor.get("enum")
            if isinstance(values, dict):
                return [
                    str(name).lower()
                    for name in values
                    if str(name).lower() != "custom"
                ]
            if isinstance(values, (tuple, list)):
                return [
                    AWB_MODE_NAMES[value]
                    for value in values
                    if value in AWB_MODE_NAMES and AWB_MODE_NAMES[value] != "custom"
                ]
        if isinstance(descriptor, (tuple, list)) and len(descriptor) >= 2:
            try:
                minimum, maximum = int(descriptor[0]), int(descriptor[1])
            except (TypeError, ValueError):
                return []
            return [
                name for value, name in AWB_MODE_NAMES.items()
                if minimum <= value <= maximum and name != "custom"
            ]
        return []

    def get_white_balance_capabilities(self):
        controls = getattr(self.picam2, "camera_controls", {}) or {}
        supported_presets = self._supported_awb_modes()
        gains_range = self._colour_gains_range()
        return {
            "supported": "AwbEnable" in controls,
            "manual_supported": gains_range is not None and "AwbEnable" in controls,
            "preset_supported": bool(supported_presets) and "AwbEnable" in controls,
            "supported_presets": supported_presets,
            "colour_gains_range": gains_range,
        }

    def _white_balance_controls_from_settings(self, camera_settings, strict=False):
        mode = camera_settings.get("white_balance_mode", "auto")
        preset = camera_settings.get("white_balance_preset", "daylight")
        gains = camera_settings.get("white_balance_gains", {})
        red_gain = gains.get("red", 1.0) if isinstance(gains, dict) else 1.0
        blue_gain = gains.get("blue", 1.0) if isinstance(gains, dict) else 1.0
        if not self.get_white_balance_capabilities()["supported"]:
            return {}
        try:
            return self._white_balance_controls(mode, preset, red_gain, blue_gain)
        except PreviewControlError:
            if strict:
                raise
            return {}

    def _white_balance_controls(self, mode, preset, red_gain, blue_gain):
        capabilities = self.get_white_balance_capabilities()
        if mode not in {"auto", "preset", "manual"}:
            raise PreviewControlError("White-balance mode must be auto, preset, or manual")
        if not capabilities["supported"]:
            raise PreviewControlError("White-balance controls are unavailable")
        if mode == "auto":
            controls = {"AwbEnable": True}
            if "auto" in capabilities["supported_presets"]:
                controls["AwbMode"] = AWB_MODE_VALUES["auto"]
            return controls
        if mode == "preset":
            if preset not in capabilities["supported_presets"]:
                raise PreviewControlError(f"Unsupported white-balance preset: {preset}")
            return {"AwbEnable": True, "AwbMode": AWB_MODE_VALUES[preset]}
        if not capabilities["manual_supported"]:
            raise PreviewControlError("Manual white-balance gains are unavailable")
        gains_range = capabilities["colour_gains_range"]
        try:
            red_gain = float(red_gain)
            blue_gain = float(blue_gain)
        except (TypeError, ValueError) as error:
            raise PreviewControlError("White-balance gains must be numbers") from error
        if not all(math.isfinite(value) for value in (red_gain, blue_gain)):
            raise PreviewControlError("White-balance gains must be finite")
        for name, value in (("red", red_gain), ("blue", blue_gain)):
            gain_range = gains_range[name]
            if value < gain_range["min"] or value > gain_range["max"]:
                raise PreviewControlError(
                    f"{name} white-balance gain must be between "
                    f"{gain_range['min']} and {gain_range['max']}"
                )
        return {"AwbEnable": False, "ColourGains": (red_gain, blue_gain)}

    def get_exposure_capabilities(self):
        controls = getattr(self.picam2, "camera_controls", {}) or {}
        exposure_time_range = self._control_range(controls.get("ExposureTime"))
        analogue_gain_range = self._control_range(controls.get("AnalogueGain"))
        manual_supported = (
            "AeEnable" in controls
            and exposure_time_range is not None
            and analogue_gain_range is not None
        )
        return {
            "supported": "AeEnable" in controls,
            "manual_supported": manual_supported,
            "exposure_time_range": exposure_time_range,
            "analogue_gain_range": analogue_gain_range,
        }

    def _validate_manual_exposure(self, exposure_time_us, analogue_gain):
        capabilities = self.get_exposure_capabilities()
        if not capabilities["manual_supported"]:
            raise PreviewControlError("Manual exposure controls are unavailable")
        try:
            exposure_time_us = float(exposure_time_us)
            analogue_gain = float(analogue_gain)
        except (TypeError, ValueError) as error:
            raise PreviewControlError("Manual exposure values must be numbers") from error
        if not all(math.isfinite(value) for value in (exposure_time_us, analogue_gain)):
            raise PreviewControlError("Manual exposure values must be finite")
        exposure_time_range = capabilities["exposure_time_range"]
        analogue_gain_range = capabilities["analogue_gain_range"]
        if exposure_time_us < exposure_time_range["min"] or exposure_time_us > exposure_time_range["max"]:
            raise PreviewControlError(
                f"Exposure time must be between {exposure_time_range['min']} and "
                f"{exposure_time_range['max']} microseconds"
            )
        if analogue_gain < analogue_gain_range["min"] or analogue_gain > analogue_gain_range["max"]:
            raise PreviewControlError(
                f"Analogue gain must be between {analogue_gain_range['min']} and "
                f"{analogue_gain_range['max']}"
            )
        return int(round(exposure_time_us)), analogue_gain

    def _current_exposure_values(self):
        metadata = {}
        session = getattr(self, "_preview_session", None)
        if session is not None and session.is_active():
            frame_info = session.get_frame_telemetry()
            frame = frame_info.get("frame") or {}
            metadata = frame.get("metadata", {}) if isinstance(frame, dict) else {}
        if not metadata and hasattr(self.picam2, "capture_metadata"):
            with self._camera_lock:
                metadata = dict(self.picam2.capture_metadata() or {})

        exposure_time_us = self._metadata_number(metadata, "ExposureTime")
        analogue_gain = self._metadata_number(metadata, "AnalogueGain")
        if exposure_time_us is None:
            exposure_time_us = self._manual_exposure_time_us
        if analogue_gain is None:
            analogue_gain = self._manual_analogue_gain
        if exposure_time_us is None or analogue_gain is None:
            raise PreviewControlError("Current exposure values are unavailable")
        return self._validate_manual_exposure(exposure_time_us, analogue_gain)

    def _exposure_controls_from_settings(self, camera_settings, strict=False):
        mode = camera_settings.get("exposure_mode", "auto")
        if mode not in {"auto", "manual"}:
            if strict:
                raise PreviewControlError("Exposure mode must be auto or manual")
            return {}
        capabilities = self.get_exposure_capabilities()
        if not capabilities["supported"]:
            return {}
        if mode == "auto":
            return {"AeEnable": True}
        exposure_time_us = camera_settings.get("manual_exposure_time_us")
        analogue_gain = camera_settings.get("manual_analogue_gain")
        if exposure_time_us is None or analogue_gain is None:
            if strict:
                raise PreviewControlError("Manual exposure values are unavailable")
            return {}
        try:
            exposure_time_us, analogue_gain = self._validate_manual_exposure(
                exposure_time_us, analogue_gain
            )
        except PreviewControlError:
            if strict:
                raise
            return {}
        return {
            "AeEnable": False,
            "ExposureTime": exposure_time_us,
            "AnalogueGain": analogue_gain,
        }

    def set_exposure_value(self, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PreviewControlError("Exposure value must be a number")
        value = float(value)
        if not math.isfinite(value) or value < -2 or value > 2:
            raise PreviewControlError("Exposure value must be between -2 and 2")
        if getattr(self, "_exposure_mode", "auto") != "auto":
            raise PreviewControlError("Exposure value is available only in auto exposure mode")
        self._acquire_focus_lock()
        try:
            with self._camera_lock:
                self.picam2.set_controls({"ExposureValue": value})
            self.settings.setdefault("camera", {})["exposure_value"] = value
            return {
                "success": True,
                "exposure_value": value,
                "message": "Exposure value updated",
            }
        except Exception as error:
            raise PreviewControlError(f"Could not set exposure value: {error}") from error
        finally:
            self._focus_lock.release()

    def set_exposure_mode(self, mode, exposure_time_us=None, analogue_gain=None):
        if mode not in {"auto", "manual"}:
            raise PreviewControlError("Exposure mode must be auto or manual")
        capabilities = self.get_exposure_capabilities()
        if not capabilities["supported"]:
            raise PreviewControlError("Exposure mode controls are unavailable")
        if mode == "manual":
            if exposure_time_us is None or analogue_gain is None:
                exposure_time_us, analogue_gain = self._current_exposure_values()
            else:
                exposure_time_us, analogue_gain = self._validate_manual_exposure(
                    exposure_time_us, analogue_gain
                )
            controls = {
                "AeEnable": False,
                "ExposureTime": exposure_time_us,
                "AnalogueGain": analogue_gain,
            }
        else:
            controls = {"AeEnable": True}
        self._acquire_focus_lock()
        try:
            with self._camera_lock:
                self.picam2.set_controls(controls)
            self._exposure_mode = mode
            result = {
                "success": True,
                "exposure_mode": mode,
                "message": f"Exposure mode set to {mode}",
            }
            if mode == "manual":
                self._manual_exposure_time_us = exposure_time_us
                self._manual_analogue_gain = analogue_gain
                result.update({
                    "exposure_time_us": exposure_time_us,
                    "analogue_gain": analogue_gain,
                })
            return result
        except Exception as error:
            if isinstance(error, PreviewControlError):
                raise
            raise PreviewControlError(f"Could not set exposure mode: {error}") from error
        finally:
            self._focus_lock.release()

    def set_white_balance(self, mode, preset="daylight", red_gain=None, blue_gain=None):
        current_gains = self._white_balance_gains
        if red_gain is None:
            red_gain = current_gains["red"]
        if blue_gain is None:
            blue_gain = current_gains["blue"]
        controls = self._white_balance_controls(mode, preset, red_gain, blue_gain)
        self._acquire_focus_lock()
        try:
            with self._camera_lock:
                self.picam2.set_controls(controls)
            self._white_balance_mode = mode
            if mode == "preset":
                self._white_balance_preset = preset
            if mode == "manual":
                self._white_balance_gains = {"red": float(red_gain), "blue": float(blue_gain)}
            return {
                "success": True,
                "white_balance_mode": mode,
                "white_balance_preset": self._white_balance_preset,
                "colour_gains": dict(self._white_balance_gains),
                "message": "White balance updated",
            }
        except Exception as error:
            if isinstance(error, PreviewControlError):
                raise
            raise PreviewControlError(f"Could not set white balance: {error}") from error
        finally:
            self._focus_lock.release()

    @staticmethod
    def _focus_mode_name(mode):
        return {0: "manual", 1: "auto", 2: "continuous"}.get(mode)

    @staticmethod
    def _focus_state_name(state):
        return {0: "idle", 1: "scanning", 2: "focused", 3: "failed"}.get(state)

    def get_focus_range(self):
        """Return the lens control range reported by Picamera2."""
        controls = getattr(self.picam2, "camera_controls", {}) or {}
        control = controls.get("LensPosition")
        if isinstance(control, dict):
            minimum = control.get("min")
            maximum = control.get("max")
            step = control.get("step")
        elif isinstance(control, (tuple, list)) and len(control) >= 2:
            minimum, maximum = control[:2]
            step = control[3] if len(control) >= 4 else None
        else:
            return None

        minimum = self._metadata_number({"value": minimum}, "value")
        maximum = self._metadata_number({"value": maximum}, "value")
        step = self._metadata_number({"value": step}, "value") if step is not None else None
        if minimum is None or maximum is None or maximum <= minimum:
            return None
        if step is None or step <= 0:
            step = round((maximum - minimum) / 100, 3)
        return {"min": minimum, "max": maximum, "step": step}

    def get_preview_telemetry(self, owner_id):
        session = self._preview_session_for_owner(owner_id)
        frame_info = session.get_frame_telemetry()
        frame = frame_info["frame"]
        metadata = frame.get("metadata", {}) if isinstance(frame, dict) else {}
        focus_mode = metadata.get("AfMode", self._focus_mode)
        focus_state = metadata.get("AfState")
        try:
            focus_mode = int(focus_mode)
        except (TypeError, ValueError):
            focus_mode = self._focus_mode
        try:
            focus_state = int(focus_state) if focus_state is not None else None
        except (TypeError, ValueError):
            focus_state = None

        telemetry = {
            "success": True,
            "frame_sequence": frame_info["sequence"],
            "frame_age_seconds": (
                round(frame_info["age_seconds"], 3)
                if frame_info["age_seconds"] is not None else None
            ),
            "frame_rate": (
                round(frame_info["frame_rate"], 2)
                if frame_info["frame_rate"] is not None else None
            ),
            "focus_mode": self._focus_mode_name(focus_mode),
            "focus_mode_value": focus_mode,
            "focus_state": self._focus_state_name(focus_state),
            "focus_state_value": focus_state,
            "lens_position": self._metadata_number(metadata, "LensPosition"),
            "focus_range": self.get_focus_range(),
            "exposure_mode": getattr(self, "_exposure_mode", "auto"),
            "exposure_capabilities": self.get_exposure_capabilities(),
            "exposure_value": self._metadata_number(
                self.settings.get("camera", {}), "exposure_value"
            ),
            "white_balance_mode": getattr(self, "_white_balance_mode", "auto"),
            "white_balance_preset": getattr(self, "_white_balance_preset", "daylight"),
            "white_balance_capabilities": self.get_white_balance_capabilities(),
        }

        colour_gains = self._metadata_pair(metadata, "ColourGains")
        if colour_gains is None:
            stored_gains = getattr(self, "_white_balance_gains", {})
            if isinstance(stored_gains, dict):
                colour_gains = (
                    stored_gains.get("red"),
                    stored_gains.get("blue"),
                )
                if not all(isinstance(value, (int, float)) for value in colour_gains):
                    colour_gains = None
        if colour_gains is not None:
            telemetry["colour_gains"] = {
                "red": colour_gains[0],
                "blue": colour_gains[1],
            }

        awb_mode = self._metadata_number(metadata, "AwbMode")
        if awb_mode is not None:
            telemetry["awb_mode_value"] = awb_mode
            telemetry["awb_mode"] = AWB_MODE_NAMES.get(awb_mode)
        if "AwbEnable" in metadata:
            telemetry["awb_enabled"] = bool(metadata["AwbEnable"])

        metadata_fields = {
            "exposure_time_us": "ExposureTime",
            "analogue_gain": "AnalogueGain",
            "colour_temperature": "ColourTemperature",
            "lux": "Lux",
        }
        for output_key, metadata_key in metadata_fields.items():
            if metadata_key in metadata:
                telemetry[output_key] = self._metadata_number(metadata, metadata_key)
        return telemetry

    def _acquire_focus_lock(self):
        if not self._focus_lock.acquire(blocking=False):
            raise FocusOperationBusy("Focus or capture operation is already in progress")

    def set_focus_mode(self, mode):
        if mode not in {0, 1, 2}:
            raise FocusOperationError("Focus mode must be manual, auto, or continuous")
        self._acquire_focus_lock()
        try:
            with self._camera_lock:
                self.picam2.set_controls({"AfMode": mode})
            self._focus_mode = mode
            self.settings.setdefault("camera", {})["autofocus_mode"] = mode
            return {
                "success": True,
                "focus_mode": self._focus_mode_name(mode),
                "focus_mode_value": mode,
                "message": f"Focus mode set to {self._focus_mode_name(mode)}",
            }
        except Exception as error:
            raise FocusOperationError(f"Could not set focus mode: {error}") from error
        finally:
            self._focus_lock.release()

    def set_focus_position(self, position):
        if not isinstance(position, (int, float)) or isinstance(position, bool):
            raise FocusOperationError("Lens position must be a number")
        focus_range = self.get_focus_range()
        if focus_range is None:
            raise FocusOperationError("Lens position range is unavailable")
        if position < focus_range["min"] or position > focus_range["max"]:
            raise FocusOperationError(
                f"Lens position must be between {focus_range['min']} and {focus_range['max']}"
            )
        self._acquire_focus_lock()
        try:
            if self._focus_mode != 0:
                raise FocusOperationError("Lens position is available only in manual focus mode")
            with self._camera_lock:
                self.picam2.set_controls({"LensPosition": position})
            self._manual_focus_position = position
            return {
                "success": True,
                "focus_mode": "manual",
                "focus_mode_value": 0,
                "lens_position": position,
                "message": "Manual focus position updated",
            }
        finally:
            self._focus_lock.release()

    def _sensor_size(self):
        properties = getattr(self.picam2, "camera_properties", {}) or {}
        size = properties.get("PixelArraySize")
        if not size:
            size = getattr(self.picam2, "sensor_resolution", None)
        if not size:
            resolution = self.settings.get("camera", {}).get("resolution", {})
            size = (resolution.get("width", 1200), resolution.get("height", 800))
        return int(size[0]), int(size[1])

    def exif_metadata_context(self):
        """Return configured photo metadata and the detected camera identity."""
        camera_settings = self.settings.get("camera", {})
        camera_properties = getattr(self.picam2, "camera_properties", {}) or {}
        sensor_model = str(
            camera_properties.get("Model") or camera_properties.get("ModelName") or ""
        ).strip()
        hardware_model = camera_settings.get("hardware_model")
        if not hardware_model:
            hardware_model = {
                "imx708": DEFAULT_CAMERA_HARDWARE,
            }.get(sensor_model.lower(), sensor_model or DEFAULT_CAMERA_HARDWARE)
        camera_identity = {
            "make": camera_settings.get("make") or DEFAULT_CAMERA_MAKE,
            "model": camera_settings.get("model") or DEFAULT_CAMERA_MODEL,
            "hardware_model": hardware_model,
        }
        if sensor_model:
            camera_identity["sensor_model"] = sensor_model
        return (
            dict(self.settings.get("metadata", {})),
            camera_identity,
            platform.platform(),
        )

    def _center_focus_window(self):
        width, height = self._sensor_size()
        window_width = max(2, width // 4)
        window_height = max(2, height // 4)
        left = (width - window_width) // 2
        top = (height - window_height) // 2
        return (left, top, window_width, window_height)

    def _capture_focus_metadata(self):
        if not hasattr(self.picam2, "capture_metadata"):
            return {"AfState": 2, "LensPosition": self._manual_focus_position or 0.0}
        with self._camera_lock:
            return dict(self.picam2.capture_metadata() or {})

    def _wait_for_focus(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            metadata = self._capture_focus_metadata()
            state = metadata.get("AfState")
            try:
                state = int(state)
            except (TypeError, ValueError):
                state = None
            if state in {2, 3}:
                return metadata
            time.sleep(0.05)
        raise FocusOperationError("Center focus timed out")

    def focus_center(self):
        self._acquire_focus_lock()
        previous_mode = self._focus_mode
        previous_position = self._manual_focus_position
        try:
            window_controls = {
                "AfWindows": [self._center_focus_window()],
                "AfMetering": 1,
                "AfMode": 1,
                "AfTrigger": 0,
            }
            with self._camera_lock:
                self.picam2.set_controls(window_controls)

            metadata = self._wait_for_focus()
            state = metadata.get("AfState")
            try:
                state = int(state)
            except (TypeError, ValueError):
                state = None
            if state != 2:
                raise FocusOperationError("Center focus failed")

            position = self._metadata_number(metadata, "LensPosition")
            if previous_mode == 0:
                if position is None:
                    raise FocusOperationError("Center focus did not return a lens position")
                with self._camera_lock:
                    self.picam2.set_controls({"AfMode": 0, "LensPosition": position})
                self._manual_focus_position = position
            elif previous_mode == 2:
                with self._camera_lock:
                    self.picam2.set_controls({"AfMode": 2})

            self._focus_mode = previous_mode
            return {
                "success": True,
                "focus_mode": self._focus_mode_name(previous_mode),
                "focus_mode_value": previous_mode,
                "focus_state": "focused",
                "lens_position": position,
                "message": "Center focus complete",
            }
        except Exception as error:
            try:
                restore = {"AfMode": previous_mode}
                if previous_mode == 0 and previous_position is not None:
                    restore["LensPosition"] = previous_position
                with self._camera_lock:
                    self.picam2.set_controls(restore)
                self._focus_mode = previous_mode
            except Exception as restore_error:
                logging.error("Could not restore focus mode after center focus: %s", restore_error)
            if isinstance(error, FocusOperationError):
                raise
            raise FocusOperationError(f"Could not focus center: {error}") from error
        finally:
            self._focus_lock.release()

    def update_activity_time(self):
        """Update the last activity timestamp."""
        self.last_activity_monotonic = time.monotonic()

    def get_inactivity_seconds(self):
        """Return elapsed inactivity without being affected by clock corrections."""
        return max(0, time.monotonic() - self.last_activity_monotonic)

    def is_timeout_enabled(self):
        """Check if auto-timeout is enabled in settings."""
        return self.settings.get("system", {}).get("auto_timeout_enabled", True)

    def get_timeout_minutes(self):
        """Get the timeout duration in minutes from settings."""
        return self.settings.get("system", {}).get("auto_timeout_minutes", 10)

    def is_timeout_exceeded(self):
        """Check if the timeout period has been exceeded."""
        if not self.is_timeout_enabled():
            return False

        timeout_seconds = self.get_timeout_minutes() * 60
        elapsed = self.get_inactivity_seconds()
        return elapsed > timeout_seconds

    def shutdown_system(self):
        """Safely shutdown the entire Raspberry Pi system to save battery."""
        try:
            timeout_minutes = self.get_timeout_minutes()
            logging.info(f"System has been inactive for {timeout_minutes} minutes. Shutting down to save battery...")
            logging.info("To use the camera again, manually power on the Raspberry Pi")
            # Give a moment for logging to flush
            time.sleep(2)
            # Execute system shutdown command
            subprocess.run(["sudo", "shutdown", "-h", "now"], check=True)
            return True
        except Exception as e:
            logging.error(f"Error shutting down system: {e}")
            return False

    def capture_photo(self, file_path, fast_mode=False):
        """Capture a photo and save it to the specified file path."""
        self._acquire_focus_lock()
        try:
            self._settle_autofocus(fast_mode)
            with self._camera_lock:
                self.picam2.capture_file(file_path)
            logging.info(f"Photo saved to {file_path}")
        finally:
            self._focus_lock.release()

    def capture_image(self, fast_mode=False):
        """Capture a photo into memory as a PIL image."""
        self._acquire_focus_lock()
        try:
            self._settle_autofocus(fast_mode)
            with self._camera_lock:
                image = self.picam2.capture_image("main")
            logging.info("Photo captured to memory")
            return image
        finally:
            self._focus_lock.release()

    def _settle_autofocus(self, fast_mode=False):
        """Give autofocus a short settle window before capture."""
        self.update_activity_time()
        autofocus_mode = self.settings.get("camera", {}).get("autofocus_mode", 2)
        if fast_mode:
            sleep(0.1)  # Shorter autofocus for startup
            logging.info("Fast autofocus mode: 0.1s delay")
        elif self._has_captured and autofocus_mode == 2:
            logging.info("Continuous autofocus already active: no settle delay")
        elif self._has_captured:
            sleep(0.1)  # Sensor already focused from previous capture
            logging.info("Adaptive autofocus: 0.1s delay (sensor pre-focused)")
        else:
            sleep(0.3)  # First capture needs full autofocus settle time
        self._has_captured = True

class ImageProcessor:
    """Handles image processing, including resizing, dithering, and saving."""

    @staticmethod
    def rotation_from_exif(image):
        try:
            orientation = int(image.getexif().get(EXIF_ORIENTATION_TAG, 1))
        except (AttributeError, TypeError, ValueError):
            return 0
        return EXIF_TO_ROTATION.get(orientation, 0)

    @staticmethod
    def _metadata_json(metadata):
        return json.dumps(metadata, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _normalize_rotation(rotation):
        try:
            return int(rotation) % 4
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _capture_exif_values(capture_time):
        resolved = resolve_capture_timestamp({}, capture_time)
        timestamp = resolved.strftime("%Y:%m:%d %H:%M:%S")
        offset = resolved.strftime("%z")
        offset = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else None
        subsecond = f"{resolved.microsecond:06d}".rstrip("0") or "0"
        return resolved, timestamp, offset, subsecond

    @staticmethod
    def _exif_value(image, tag, default=None):
        try:
            exif = image.getexif()
            value = exif.get(tag)
            if value is not None:
                return value
            return exif.get_ifd(EXIF_EXIF_IFD_TAG).get(tag, default)
        except (AttributeError, TypeError, ValueError):
            return default

    @staticmethod
    def _capture_time_from_source(image):
        """Read the stored capture timestamp when reprocessing an original."""
        try:
            timestamp = ImageProcessor._exif_value(image, EXIF_DATETIME_ORIGINAL_TAG)
            timestamp = timestamp or ImageProcessor._exif_value(image, EXIF_DATETIME_TAG)
            if not timestamp:
                timestamp = ImageProcessor._custom_metadata_from_source(image).get("capture_time")
            if not timestamp:
                return None
            try:
                resolved = datetime.strptime(str(timestamp), "%Y:%m:%d %H:%M:%S")
            except ValueError:
                resolved = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            offset = ImageProcessor._exif_value(image, EXIF_OFFSET_TIME_ORIGINAL_TAG)
            offset = offset or ImageProcessor._exif_value(image, EXIF_OFFSET_TIME_TAG)
            if offset:
                sign = 1 if str(offset).startswith("+") else -1
                hours, minutes = str(offset)[1:].split(":", 1)
                resolved = resolved.replace(
                    tzinfo=timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))
                )
            subsecond = ImageProcessor._exif_value(image, EXIF_SUBSEC_TIME_ORIGINAL_TAG)
            subsecond = subsecond or ImageProcessor._exif_value(image, EXIF_SUBSEC_TIME_TAG)
            if subsecond is not None:
                digits = "".join(char for char in str(subsecond) if char.isdigit())[:6].ljust(6, "0")
                resolved = resolved.replace(microsecond=int(digits))
            return resolve_capture_timestamp({}, resolved)
        except (AttributeError, TypeError, ValueError):
            return None

    @staticmethod
    def _rational(value, denominator=1_000_000):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric):
            return None
        numerator = round(numeric * denominator)
        if not numerator and numeric:
            numerator = 1 if numeric > 0 else -1
        return (numerator, denominator)

    @staticmethod
    def _exposure_time_rational(exposure_time_us):
        """Convert Picamera2 microseconds to an EXIF seconds rational."""
        try:
            exposure_seconds = float(exposure_time_us) / 1_000_000
        except (TypeError, ValueError):
            return None
        return ImageProcessor._rational(exposure_seconds)

    @staticmethod
    def _decode_user_comment(value):
        if not isinstance(value, (bytes, bytearray)):
            return value if isinstance(value, str) else None
        raw = bytes(value)
        if raw.startswith(b"UNICODE\x00"):
            payload = raw[8:]
            if payload.lstrip().startswith((b"{", b"[")):
                return payload.decode("utf-8", errors="replace")
            try:
                return payload.decode("utf-16-be")
            except UnicodeDecodeError:
                return payload.decode("utf-8", errors="replace")
        if raw.startswith((b"ASCII\x00\x00\x00", b"JIS\x00\x00\x00\x00\x00")):
            return raw[8:].decode("utf-8", errors="replace")
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _custom_metadata_from_source(image):
        try:
            raw_comment = ImageProcessor._decode_user_comment(
                ImageProcessor._exif_value(image, EXIF_USER_COMMENT_TAG)
            )
            parsed = json.loads(raw_comment) if raw_comment else {}
            return parsed if isinstance(parsed, dict) else {}
        except (AttributeError, TypeError, ValueError):
            return {}

    @staticmethod
    def _photo_metadata_from_source(image):
        try:
            exif = image.getexif()
            custom = ImageProcessor._custom_metadata_from_source(image)
            return {
                "artist": exif.get(EXIF_ARTIST_TAG) or custom.get("artist", ""),
                "copyright": exif.get(EXIF_COPYRIGHT_TAG) or custom.get("copyright", ""),
                "image_description": exif.get(EXIF_IMAGE_DESCRIPTION_TAG) or custom.get("image_description", ""),
            }
        except (AttributeError, TypeError):
            return {}

    @staticmethod
    def _camera_identity_from_source(image):
        try:
            exif = image.getexif()
            custom = ImageProcessor._custom_metadata_from_source(image)
            camera = custom.get("camera", {})
            return {
                "make": exif.get(EXIF_MAKE_TAG) or camera.get("make", ""),
                "model": exif.get(EXIF_MODEL_TAG) or camera.get("model", ""),
                "hardware_model": ImageProcessor._exif_value(image, EXIF_LENS_MODEL_TAG)
                or camera.get("hardware_model", ""),
            }
        except (AttributeError, TypeError):
            return {}

    @staticmethod
    def _custom_capture_metadata(
        metadata,
        photo_metadata=None,
        camera_identity=None,
        operating_system=None,
    ):
        metadata = metadata if isinstance(metadata, dict) else {}
        standard_keys = {
            "ExposureTime", "AnalogueGain", "ExposureValue", "ExposureCompensation",
            "ExposureCompensationValue", "AwbEnable", "AwbMode", "SubjectDistance",
            "CalibratedFocusDistance", "CaptureTimestamp", "capture_time", "FNumber",
            "Aperture", "ApertureValue", "LensFocalLength", "FocalLength", "IsoCalibration",
        }
        sensor = {key: value for key, value in metadata.items() if key not in standard_keys}
        photo_metadata = photo_metadata if isinstance(photo_metadata, dict) else {}
        camera_identity = camera_identity if isinstance(camera_identity, dict) else {}
        custom = {
            "schema": 2,
            "camera": camera_identity,
            "operating_system": operating_system or platform.platform(),
            "sensor": sensor,
        }
        for key in ("artist", "copyright", "image_description"):
            value = photo_metadata.get(key)
            if isinstance(value, str) and value:
                custom[key] = value
        return custom

    @staticmethod
    def _capture_metadata_envelope(
        metadata,
        source_image,
        photo_metadata,
        camera_identity,
        operating_system,
        capture_time,
        rotation,
    ):
        source_custom_metadata = (
            ImageProcessor._custom_metadata_from_source(source_image)
            if source_image is not None else {}
        )
        if source_image is not None and not camera_identity:
            camera_identity = ImageProcessor._camera_identity_from_source(source_image)
        if source_image is not None and not photo_metadata:
            photo_metadata = ImageProcessor._photo_metadata_from_source(source_image)
        if operating_system is None:
            operating_system = source_custom_metadata.get("operating_system")
        if not camera_identity:
            camera_identity = {
                "make": DEFAULT_CAMERA_MAKE,
                "model": DEFAULT_CAMERA_MODEL,
                "hardware_model": DEFAULT_CAMERA_HARDWARE,
            }
        custom_metadata = ImageProcessor._custom_capture_metadata(
            metadata,
            photo_metadata=photo_metadata,
            camera_identity=camera_identity,
            operating_system=operating_system,
        )
        for key, value in source_custom_metadata.items():
            if key not in custom_metadata or not custom_metadata[key]:
                custom_metadata[key] = value
        if not metadata and isinstance(source_custom_metadata.get("sensor"), dict):
            custom_metadata["sensor"] = source_custom_metadata["sensor"]
        custom_metadata["capture_time"] = capture_time.isoformat()
        custom_metadata["orientation"] = ROTATION_TO_EXIF[rotation]
        return custom_metadata

    @staticmethod
    def _exif_for_image(
        image,
        source_image=None,
        rotation=0,
        metadata=None,
        capture_time=None,
        photo_metadata=None,
        camera_identity=None,
        operating_system=None,
        modified_time=None,
        processing_metadata=None,
    ):
        Image, _ = _lazy_import_pil()
        metadata = metadata if isinstance(metadata, dict) else {}
        rotation = ImageProcessor._normalize_rotation(rotation)
        try:
            exif = Image.Exif()
        except AttributeError:
            exif = image.getexif()
        metadata_source = source_image if source_image is not None else image
        try:
            source_exif = metadata_source.getexif()
            for tag, value in source_exif.items():
                if tag in (EXIF_EXIF_IFD_TAG, EXIF_GPS_IFD_TAG, EXIF_INTEROP_IFD_TAG):
                    continue
                exif[tag] = value
            for ifd_tag in (EXIF_EXIF_IFD_TAG, EXIF_GPS_IFD_TAG, EXIF_INTEROP_IFD_TAG):
                try:
                    source_ifd = source_exif.get_ifd(ifd_tag)
                except (AttributeError, KeyError, TypeError, ValueError):
                    continue
                if source_ifd:
                    try:
                        exif.get_ifd(ifd_tag).update(source_ifd)
                    except (AttributeError, KeyError, TypeError, ValueError):
                        continue
        except (AttributeError, TypeError, ValueError):
            pass

        exif_data = exif.get_ifd(EXIF_EXIF_IFD_TAG)
        exif[EXIF_ORIENTATION_TAG] = ROTATION_TO_EXIF[rotation]
        camera_identity = camera_identity if isinstance(camera_identity, dict) else {}
        photo_metadata = photo_metadata if isinstance(photo_metadata, dict) else {}
        source_software = exif.get(EXIF_SOFTWARE_TAG) if source_image is not None else None
        preserve_source_software = source_image is not None and operating_system is None
        source_custom_metadata = (
            ImageProcessor._custom_metadata_from_source(source_image)
            if source_image is not None else {}
        )
        if source_image is not None and not camera_identity:
            camera_identity = ImageProcessor._camera_identity_from_source(source_image)
        if source_image is not None and not photo_metadata:
            photo_metadata = ImageProcessor._photo_metadata_from_source(source_image)
        if operating_system is None:
            operating_system = source_custom_metadata.get("operating_system")
        make = camera_identity.get("make") or DEFAULT_CAMERA_MAKE
        model = camera_identity.get("model") or DEFAULT_CAMERA_MODEL
        hardware_model = camera_identity.get("hardware_model") or DEFAULT_CAMERA_HARDWARE
        operating_system = operating_system or platform.platform()
        resolved_capture_time = capture_time
        if resolved_capture_time is None and source_image is not None:
            resolved_capture_time = ImageProcessor._capture_time_from_source(source_image)
        resolved, timestamp, offset, subsecond = ImageProcessor._capture_exif_values(
            resolved_capture_time or metadata.get("CaptureTimestamp") or metadata.get("capture_time")
        )
        modified, modified_timestamp, modified_offset, modified_subsecond = ImageProcessor._capture_exif_values(
            modified_time or datetime.now().astimezone()
        )
        exif[EXIF_MAKE_TAG] = str(make)
        exif[EXIF_MODEL_TAG] = str(model)
        exif_data[EXIF_LENS_MAKE_TAG] = str(make)
        exif_data[EXIF_LENS_MODEL_TAG] = str(hardware_model)
        exif[EXIF_SOFTWARE_TAG] = (
            str(source_software)
            if source_software and preserve_source_software
            else f"reFrame ({operating_system})"
        )
        exif[EXIF_ORIENTATION_TAG] = ROTATION_TO_EXIF[rotation]
        exif[EXIF_DATETIME_TAG] = modified_timestamp
        exif_data[EXIF_DATETIME_ORIGINAL_TAG] = timestamp
        exif_data[EXIF_DATETIME_DIGITIZED_TAG] = timestamp
        if modified_offset:
            exif_data[EXIF_OFFSET_TIME_TAG] = modified_offset
        if offset:
            exif_data[EXIF_OFFSET_TIME_ORIGINAL_TAG] = offset
            exif_data[EXIF_OFFSET_TIME_DIGITIZED_TAG] = offset
        exif_data[EXIF_SUBSEC_TIME_TAG] = modified_subsecond
        exif_data[EXIF_SUBSEC_TIME_ORIGINAL_TAG] = subsecond
        exif_data[EXIF_SUBSEC_TIME_DIGITIZED_TAG] = subsecond
        exif_data[EXIF_EXIF_VERSION_TAG] = b"0231"
        exif_data[EXIF_COMPONENTS_CONFIGURATION_TAG] = b"\x01\x02\x03\x00"
        exif[EXIF_X_RESOLUTION_TAG] = (72, 1)
        exif[EXIF_Y_RESOLUTION_TAG] = (72, 1)
        exif[EXIF_RESOLUTION_UNIT_TAG] = 2
        exif_data[EXIF_COLOR_SPACE_TAG] = 1
        exif_data[EXIF_PIXEL_X_TAG] = int(image.width)
        exif_data[EXIF_PIXEL_Y_TAG] = int(image.height)

        artist = photo_metadata.get("artist")
        copyright_text = photo_metadata.get("copyright")
        image_description = photo_metadata.get("image_description")
        if artist:
            exif[EXIF_ARTIST_TAG] = str(artist)
        if copyright_text:
            exif[EXIF_COPYRIGHT_TAG] = str(copyright_text)
        if image_description:
            exif[EXIF_IMAGE_DESCRIPTION_TAG] = str(image_description)

        exposure_time = ImageProcessor._exposure_time_rational(metadata.get("ExposureTime"))
        if exposure_time and exposure_time[0] > 0:
            exif_data[EXIF_EXPOSURE_TIME_TAG] = exposure_time
            shutter_speed = -math.log2(float(exposure_time[0]) / float(exposure_time[1]))
            exif_data[EXIF_SHUTTER_SPEED_VALUE_TAG] = ImageProcessor._rational(shutter_speed, 1000)
        analogue_gain = metadata.get("AnalogueGain")
        iso_calibration = metadata.get("IsoCalibration", metadata.get("iso_calibration", 100))
        try:
            if analogue_gain is not None and float(analogue_gain) > 0 and float(iso_calibration) > 0:
                exif_data[EXIF_ISO_TAG] = max(1, round(float(analogue_gain) * float(iso_calibration)))
        except (TypeError, ValueError):
            pass

        exposure_bias = metadata.get("ExposureValue", metadata.get("ExposureCompensation"))
        if exposure_bias is None:
            exposure_bias = metadata.get("ExposureCompensationValue")
        exposure_bias_value = ImageProcessor._rational(exposure_bias, 1000)
        if exposure_bias_value:
            exif_data[EXIF_EXPOSURE_BIAS_TAG] = exposure_bias_value

        f_number = metadata.get("FNumber", metadata.get("Aperture"))
        f_number_value = ImageProcessor._rational(f_number, 100)
        if f_number_value and f_number_value[0] > 0:
            exif_data[EXIF_FNUMBER_TAG] = f_number_value
            aperture_value = 2 * math.log2(float(f_number_value[0]) / float(f_number_value[1]))
            exif_data[EXIF_APERTURE_VALUE_TAG] = ImageProcessor._rational(aperture_value, 1000)

        focal_length = metadata.get("LensFocalLength", metadata.get("FocalLength"))
        focal_length_value = ImageProcessor._rational(focal_length, 100)
        if focal_length_value and focal_length_value[0] > 0:
            exif_data[EXIF_FOCAL_LENGTH_TAG] = focal_length_value

        ae_enabled = metadata.get("AeEnable")
        if isinstance(ae_enabled, bool):
            exif_data[EXIF_EXPOSURE_PROGRAM_TAG] = 2 if ae_enabled else 1
            exif_data[EXIF_EXPOSURE_MODE_TAG] = 0 if ae_enabled else 1
        exif_data[EXIF_FLASH_TAG] = 0

        awb_mode = metadata.get("AwbMode")
        if isinstance(metadata.get("AwbEnable"), bool):
            exif_data[EXIF_WHITE_BALANCE_TAG] = 0 if metadata["AwbEnable"] else 1
        if awb_mode in AWB_LIGHT_SOURCE_VALUES:
            exif_data[EXIF_LIGHT_SOURCE_TAG] = AWB_LIGHT_SOURCE_VALUES[awb_mode]

        focus_distance = metadata.get("SubjectDistance", metadata.get("CalibratedFocusDistance"))
        focus_value = ImageProcessor._rational(focus_distance, 1000)
        if focus_value and focus_value[0] >= 0:
            exif_data[EXIF_SUBJECT_DISTANCE_TAG] = focus_value
        exif_data[EXIF_CUSTOM_RENDERED_TAG] = 1 if processing_metadata else 0
        custom_metadata = ImageProcessor._capture_metadata_envelope(
            metadata,
            source_image,
            photo_metadata,
            camera_identity,
            operating_system,
            resolved,
            rotation,
        )
        exif_data[EXIF_USER_COMMENT_TAG] = (
            b"UNICODE\x00" + ImageProcessor._metadata_json(custom_metadata).encode("utf-16-be")
        )
        return exif, resolved

    @staticmethod
    def build_processing_metadata(photo_id, dithering_method, gb_color_palette, settings=None):
        processing_settings = dict(settings or {})
        processing_settings.setdefault("gb_color_palette", gb_color_palette)
        return {
            "schema": 1,
            "source_photo_id": photo_id,
            "dithering_method": dithering_method,
            "processing_settings": processing_settings,
        }

    @staticmethod
    def save_image_with_metadata(
        image,
        output_path,
        source_image=None,
        rotation=0,
        metadata=None,
        dithering_method=None,
        gb_color_palette=None,
        capture_time=None,
        processing_metadata=None,
        photo_metadata=None,
        camera_identity=None,
        operating_system=None,
        modified_time=None,
    ):
        Image, _ = _lazy_import_pil()
        exif, resolved_capture_time = ImageProcessor._exif_for_image(
            image,
            source_image=source_image,
            rotation=rotation,
            metadata=metadata,
            capture_time=capture_time,
            photo_metadata=photo_metadata,
            camera_identity=camera_identity,
            operating_system=operating_system,
            modified_time=modified_time,
            processing_metadata=processing_metadata,
        )
        save_kwargs = {"exif": exif.tobytes()}
        if source_image is not None and source_image.info.get("icc_profile"):
            save_kwargs["icc_profile"] = source_image.info["icc_profile"]
        if output_path.lower().endswith(".png"):
            from PIL.PngImagePlugin import PngInfo

            png_info = PngInfo()
            if dithering_method:
                png_info.add_text("reframe:dithering_method", str(dithering_method))
            if gb_color_palette:
                png_info.add_text("reframe:gb_color_palette", str(gb_color_palette))
            png_info.add_text("reframe:metadata_schema", "2")
            png_info.add_text(
                "reframe:capture_metadata",
                ImageProcessor._metadata_json(ImageProcessor._capture_metadata_envelope(
                    metadata,
                    source_image,
                    photo_metadata,
                    camera_identity,
                    operating_system,
                    resolved_capture_time,
                    rotation,
                )),
            )
            if processing_metadata:
                png_info.add_text(
                    "reframe:processing_metadata",
                    ImageProcessor._metadata_json(processing_metadata),
                )
            save_kwargs["pnginfo"] = png_info
            image.save(output_path, format="PNG", **save_kwargs)
        else:
            image.save(output_path, format="JPEG", quality=95, **save_kwargs)

    @staticmethod
    def save_dithered_image(
        image,
        output_path,
        source_image=None,
        rotation=0,
        metadata=None,
        dithering_method=None,
        gb_color_palette=None,
        capture_time=None,
        processing_metadata=None,
        photo_metadata=None,
        camera_identity=None,
        operating_system=None,
        modified_time=None,
    ):
        temp_path = f"{output_path}.tmp-{os.getpid()}-{threading.get_ident()}{os.path.splitext(output_path)[1]}"
        try:
            ImageProcessor.save_image_with_metadata(
                image,
                temp_path,
                source_image=source_image,
                rotation=rotation,
                metadata=metadata,
                dithering_method=dithering_method,
                gb_color_palette=gb_color_palette,
                capture_time=capture_time,
                processing_metadata=processing_metadata,
                photo_metadata=photo_metadata,
                camera_identity=camera_identity,
                operating_system=operating_system,
                modified_time=modified_time,
            )
            os.replace(temp_path, output_path)
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

    @staticmethod
    def prepare_dithered_for_display(image):
        Image, _ = _lazy_import_pil()
        from PIL import ImageOps

        rotation = ImageProcessor.rotation_from_exif(image)
        prepared = ImageOps.exif_transpose(image)
        transpose = getattr(Image, "Transpose", Image)
        inverse_transpose = {
            1: transpose.ROTATE_90,
            2: transpose.ROTATE_180,
            3: transpose.ROTATE_270,
        }.get(rotation)
        if inverse_transpose is not None:
            prepared = prepared.transpose(inverse_transpose)
        if prepared.size == DISPLAY_PANEL_SIZE:
            prepared = prepared.rotate(90, expand=True)
        if prepared.size != DISPLAY_IMAGE_SIZE:
            prepared = ImageProcessor.resize_image(prepared)
        return prepared

    @staticmethod
    def _capture_metadata_from_image(image):
        raw_metadata = image.info.get("reframe:capture_metadata")
        if isinstance(raw_metadata, dict):
            metadata = raw_metadata
        elif isinstance(raw_metadata, str):
            try:
                metadata = json.loads(raw_metadata)
            except (TypeError, ValueError):
                metadata = {}
        else:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        if not metadata:
            metadata = ImageProcessor._custom_metadata_from_source(image)

        capture_time = metadata.get("capture_time")
        if isinstance(capture_time, str) and capture_time.strip():
            try:
                resolved_capture_time = datetime.fromisoformat(capture_time.replace("Z", "+00:00"))
                if resolved_capture_time.tzinfo is None:
                    resolved_capture_time = resolved_capture_time.replace(tzinfo=timezone.utc)
                capture_time = resolved_capture_time.isoformat()
            except ValueError:
                capture_time = None
        else:
            capture_time = None

        if capture_time is None:
            resolved_capture_time = ImageProcessor._capture_time_from_source(image)
            capture_time = resolved_capture_time.isoformat() if resolved_capture_time else None

        sensor_metadata = metadata.get("sensor")
        if not isinstance(sensor_metadata, dict) or not sensor_metadata:
            sensor_metadata = None
        return metadata, capture_time, sensor_metadata

    @staticmethod
    def _exif_metadata_from_image(image, capture_metadata, capture_time):
        exif = image.getexif()
        exif_data = exif.get_ifd(EXIF_EXIF_IFD_TAG)

        def value(tag, nested=False):
            raw = (exif_data if nested else exif).get(tag)
            if isinstance(raw, bytes):
                return raw.decode("utf-8", errors="replace").rstrip("\x00")
            if hasattr(raw, "numerator") and hasattr(raw, "denominator"):
                try:
                    return float(raw.numerator) / float(raw.denominator) if raw.denominator else None
                except (TypeError, ValueError, ZeroDivisionError):
                    return None
            if isinstance(raw, tuple) and len(raw) == 2:
                try:
                    return float(raw[0]) / float(raw[1]) if raw[1] else None
                except (TypeError, ValueError, ZeroDivisionError):
                    return None
            return raw

        camera = capture_metadata.get("camera")
        if not isinstance(camera, dict):
            camera = {
                "make": value(EXIF_MAKE_TAG),
                "model": value(EXIF_MODEL_TAG),
                "hardware_model": value(EXIF_LENS_MODEL_TAG, nested=True),
            }
        technical = {
            "exposure_time": value(EXIF_EXPOSURE_TIME_TAG, nested=True),
            "f_number": value(EXIF_FNUMBER_TAG, nested=True),
            "iso": value(EXIF_ISO_TAG, nested=True),
            "focal_length": value(EXIF_FOCAL_LENGTH_TAG, nested=True),
            "subject_distance": value(EXIF_SUBJECT_DISTANCE_TAG, nested=True),
            "white_balance": value(EXIF_WHITE_BALANCE_TAG, nested=True),
            "light_source": value(EXIF_LIGHT_SOURCE_TAG, nested=True),
        }
        technical = {key: item for key, item in technical.items() if item is not None}
        dates = {
            "capture": capture_time,
            "original": value(EXIF_DATETIME_ORIGINAL_TAG, nested=True),
            "digitized": value(EXIF_DATETIME_DIGITIZED_TAG, nested=True),
            "modified": value(EXIF_DATETIME_TAG),
            "capture_offset": value(EXIF_OFFSET_TIME_ORIGINAL_TAG, nested=True),
            "modified_offset": value(EXIF_OFFSET_TIME_TAG, nested=True),
            "capture_subsecond": value(EXIF_SUBSEC_TIME_ORIGINAL_TAG, nested=True),
        }
        dates = {key: item for key, item in dates.items() if item is not None}
        author = {
            "artist": value(EXIF_ARTIST_TAG),
            "copyright": value(EXIF_COPYRIGHT_TAG),
            "description": value(EXIF_IMAGE_DESCRIPTION_TAG),
        }
        author = {key: item for key, item in author.items() if item}
        result = {
            "camera": camera,
            "operating_system": capture_metadata.get("operating_system"),
            "software": value(EXIF_SOFTWARE_TAG),
            "author": author,
            "dates": dates,
            "technical": technical,
            "sensor": capture_metadata.get("sensor", {}),
        }
        return {
            key: item for key, item in result.items()
            if item not in (None, {}, [])
        }

    @staticmethod
    def read_dithered_metadata(path):
        Image, _ = _lazy_import_pil()
        try:
            with Image.open(path) as image:
                capture_metadata, capture_time, sensor_metadata = ImageProcessor._capture_metadata_from_image(image)
                return {
                    "rotation": ImageProcessor.rotation_from_exif(image),
                    "dithering_method": image.info.get("reframe:dithering_method"),
                    "gb_color_palette": image.info.get("reframe:gb_color_palette"),
                    "metadata_schema": image.info.get("reframe:metadata_schema"),
                    "capture_metadata": capture_metadata,
                    "capture_time": capture_time,
                    "sensor_metadata": sensor_metadata,
                    "exif_metadata": ImageProcessor._exif_metadata_from_image(
                        image, capture_metadata, capture_time
                    ),
                    "processing_metadata": image.info.get("reframe:processing_metadata"),
                }
        except (OSError, ValueError):
            return {
                "rotation": 0,
                "dithering_method": None,
                "gb_color_palette": None,
                "capture_time": None,
                "sensor_metadata": None,
                "exif_metadata": None,
            }

    @staticmethod
    def get_bayer_matrix(size):
        """Generate Bayer matrix for ordered dithering."""
        np = _lazy_import_numpy()
        if size == 2:
            return np.array([[0, 2], [3, 1]], dtype=np.float32) / 4.0
        elif size == 4:
            return np.array([
                [0, 8, 2, 10],
                [12, 4, 14, 6],
                [3, 11, 1, 9],
                [15, 7, 13, 5]
            ], dtype=np.float32) / 16.0
        elif size == 8:
            return np.array([
                [0, 32, 8, 40, 2, 34, 10, 42],
                [48, 16, 56, 24, 50, 18, 58, 26],
                [12, 44, 4, 36, 14, 46, 6, 38],
                [60, 28, 52, 20, 62, 30, 54, 22],
                [3, 35, 11, 43, 1, 33, 9, 41],
                [51, 19, 59, 27, 49, 17, 57, 25],
                [15, 47, 7, 39, 13, 45, 5, 37],
                [63, 31, 55, 23, 61, 29, 53, 21]
            ], dtype=np.float32) / 64.0
        else:
            # Default to 4x4 if unsupported size
            return ImageProcessor.get_bayer_matrix(4)

    @staticmethod
    def _palette_image(indices, palette):
        """Build a palette image while keeping the physical index layout stable."""
        Image, _ = _lazy_import_pil()
        np = _lazy_import_numpy()
        output_image = Image.fromarray(np.asarray(indices, dtype=np.uint8), mode='P')
        palette_flat = np.asarray(palette, dtype=np.uint8).reshape(-1, 3).flatten().tolist()
        palette_flat += [0, 0, 0] * (256 - len(palette_flat) // 3)
        output_image.putpalette(palette_flat)
        return output_image

    @staticmethod
    def _srgb_to_linear(rgb):
        """Convert sRGB colors in [0, 255] to linear RGB."""
        np = _lazy_import_numpy()
        values = np.asarray(rgb, dtype=np.float32) / 255.0
        return np.where(
            values > 0.04045,
            ((values + 0.055) / 1.055) ** 2.4,
            values / 12.92,
        )

    @staticmethod
    def _linear_to_srgb(rgb):
        """Convert linear RGB values to sRGB colors in [0, 255]."""
        np = _lazy_import_numpy()
        values = np.clip(np.asarray(rgb, dtype=np.float32), 0.0, 1.0)
        return np.where(
            values > 0.0031308,
            1.055 * (values ** (1.0 / 2.4)) - 0.055,
            12.92 * values,
        ) * 255.0

    @staticmethod
    def _rgb_to_lab(rgb):
        """Convert RGB colors in [0, 255] to CIELAB."""
        np = _lazy_import_numpy()
        values = np.asarray(rgb, dtype=np.float32).reshape(-1, 3) / 255.0
        linear = np.where(
            values > 0.04045,
            ((values + 0.055) / 1.055) ** 2.4,
            values / 12.92,
        )
        matrix = np.array([
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ], dtype=np.float32)
        xyz = linear @ matrix.T
        xyz[:, 0] /= 0.95047
        xyz[:, 2] /= 1.08883
        factors = np.where(
            xyz > 0.008856,
            np.cbrt(xyz),
            (903.3 * xyz + 16.0) / 116.0,
        )
        lab = np.empty_like(xyz)
        lab[:, 0] = 116.0 * factors[:, 1] - 16.0
        lab[:, 1] = 500.0 * (factors[:, 0] - factors[:, 1])
        lab[:, 2] = 200.0 * (factors[:, 1] - factors[:, 2])
        return lab

    @staticmethod
    def _get_natural_pair_lut(physical_palette):
        """Cache pair choices for the 32K RGB colors used by the image path."""
        np = _lazy_import_numpy()
        cache_key = physical_palette.astype(np.uint8, copy=False).tobytes()
        cached = _NATURAL_PAIR_LUT_CACHE.get(cache_key)
        if cached is not None:
            return cached

        physical_palette = physical_palette.astype(np.float32, copy=False)
        physical_lab = ImageProcessor._rgb_to_lab(physical_palette)
        physical_linear = ImageProcessor._srgb_to_linear(physical_palette)
        fractions = np.arange(1, 16, dtype=np.float32) / 16.0
        pair_first = []
        pair_second = []
        mixed_lab = []

        for first in range(len(physical_palette)):
            for second in range(first + 1, len(physical_palette)):
                mixed_linear = (
                    physical_linear[first][None, :] * (1.0 - fractions[:, None])
                    + physical_linear[second][None, :] * fractions[:, None]
                )
                mixed_lab.append(
                    ImageProcessor._rgb_to_lab(
                        ImageProcessor._linear_to_srgb(mixed_linear)
                    )
                )
                pair_first.extend([first] * len(fractions))
                pair_second.extend([second] * len(fractions))

        mixed_lab = np.asarray(mixed_lab, dtype=np.float32)
        mixed_chroma = np.hypot(mixed_lab[:, :, 1], mixed_lab[:, :, 2])
        pair_count = mixed_lab.shape[0]
        candidate_lab = mixed_lab.reshape(-1, 3)

        levels = np.arange(32, dtype=np.float32) * 8.0 + 4.0
        red, green, blue = np.meshgrid(levels, levels, levels, indexing="ij")
        cube_rgb = np.stack([red, green, blue], axis=-1).reshape(-1, 3)
        cube_lab = ImageProcessor._rgb_to_lab(cube_rgb)
        cube_chroma = np.hypot(cube_lab[:, 1], cube_lab[:, 2])
        single_distances = np.sum(
            (cube_lab[:, None, :] - physical_lab[None, :, :]) ** 2,
            axis=2,
        )
        best_first = np.argmin(single_distances, axis=1).astype(np.uint8)
        best_second = best_first.copy()
        best_fraction = np.zeros(cube_lab.shape[0], dtype=np.uint8)
        pair_first = np.asarray(pair_first, dtype=np.uint8).reshape(pair_count, 15)
        pair_second = np.asarray(pair_second, dtype=np.uint8).reshape(pair_count, 15)

        for start in range(0, cube_lab.shape[0], _NATURAL_PAIR_LUT_CHUNK_SIZE):
            end = min(start + _NATURAL_PAIR_LUT_CHUNK_SIZE, cube_lab.shape[0])
            raw_distances = np.sum(
                (cube_lab[start:end, None, :] - candidate_lab[None, :, :]) ** 2,
                axis=2,
            ).reshape(end - start, pair_count, 15)
            fraction_choice = np.argmin(raw_distances, axis=2)
            pair_distance = np.take_along_axis(
                raw_distances,
                fraction_choice[:, :, None],
                axis=2,
            )[:, :, 0]
            selected_chroma = mixed_chroma[
                np.arange(pair_count)[None, :],
                fraction_choice,
            ]
            pair_distance += np.abs(
                selected_chroma - cube_chroma[start:end, None]
            ) * 4.0

            all_distances = np.concatenate(
                (single_distances[start:end], pair_distance),
                axis=1,
            )
            choice = np.argmin(all_distances, axis=1)
            pair_choice = np.clip(
                choice - len(physical_palette),
                0,
                pair_count - 1,
            )
            selected_fraction = fraction_choice[
                np.arange(end - start), pair_choice
            ]
            best_first[start:end] = np.where(
                choice < len(physical_palette),
                choice,
                pair_first[pair_choice, selected_fraction],
            ).astype(np.uint8)
            best_second[start:end] = np.where(
                choice < len(physical_palette),
                choice,
                pair_second[pair_choice, selected_fraction],
            ).astype(np.uint8)
            best_fraction[start:end] = np.where(
                choice < len(physical_palette),
                0,
                selected_fraction + 1,
            ).astype(np.uint8)

        result = best_first, best_second, best_fraction
        _NATURAL_PAIR_LUT_CACHE[cache_key] = result
        return result

    @staticmethod
    def _tone_map_luminance(image):
        """Map the 2nd-98th luminance percentiles to the full display range."""
        Image, _ = _lazy_import_pil()
        np = _lazy_import_numpy()
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
        luminance = (
            (0.299 * rgb[:, :, 0])
            + (0.587 * rgb[:, :, 1])
            + (0.114 * rgb[:, :, 2])
        )
        low, high = np.percentile(luminance, (2.0, 98.0))
        if high - low <= 1.0:
            return image.convert("RGB")
        mapped_luminance = np.clip(
            (luminance - low) / max(float(high - low), 1.0),
            0.0,
            1.0,
        ) * 255.0
        ratio = np.divide(
            mapped_luminance,
            luminance,
            out=np.zeros_like(mapped_luminance),
            where=luminance > 0.001,
        )
        mapped_rgb = np.clip(rgb * ratio[:, :, None], 0.0, 255.0)
        return Image.fromarray(np.rint(mapped_rgb).astype(np.uint8), mode="RGB")

    @staticmethod
    def _blended_palette_array(saturation):
        """Build the rounded palette used by the experimental physical-color modes."""
        np = _lazy_import_numpy()
        colors = []
        for palette_index in [0, 1, 5, 4, 0, 3, 2]:
            saturated = np.asarray(SATURATED_PALETTE[palette_index], dtype=np.float32)
            desaturated = np.asarray(DESATURATED_PALETTE[palette_index], dtype=np.float32)
            colors.append(np.rint(
                saturated * saturation + desaturated * (1.0 - saturation)
            ).clip(0, 255))
        return np.asarray(colors, dtype=np.uint8)

    @staticmethod
    def apply_natural_pair_dithering(image, saturation=0.45, brightness_factor=1.05,
                                     color_factor=1.15, bayer_size=4,
                                     threshold_scale=1.0, tone_map="percentile"):
        """Dither toward the best mixture of two physical display colors."""
        np = _lazy_import_numpy()
        Image, ImageEnhance = _lazy_import_pil()
        enhanced = ImageEnhance.Brightness(image.convert("RGB")).enhance(brightness_factor)
        enhanced = ImageEnhance.Color(enhanced).enhance(color_factor)
        if tone_map == "percentile":
            enhanced = ImageProcessor._tone_map_luminance(enhanced)

        source_rgb = np.asarray(enhanced, dtype=np.float32)
        height, width = source_rgb.shape[:2]
        palette = ImageProcessor._blended_palette_array(saturation).astype(np.float32)
        physical_indices = np.asarray([0, 1, 2, 3, 5, 6], dtype=np.int32)
        physical_palette = palette[physical_indices]
        first_lut, second_lut, fraction_lut = ImageProcessor._get_natural_pair_lut(
            physical_palette
        )
        quantized = np.clip((source_rgb / 8.0).astype(np.int32), 0, 31)
        lookup_keys = (
            (quantized[:, :, 0] << 10)
            | (quantized[:, :, 1] << 5)
            | quantized[:, :, 2]
        )
        best_first = first_lut[lookup_keys].reshape(-1)
        best_second = second_lut[lookup_keys].reshape(-1)
        best_fraction = fraction_lut[lookup_keys].reshape(-1).astype(np.float32) / 16.0

        bayer = np.tile(
            ImageProcessor.get_bayer_matrix(bayer_size),
            ((height + bayer_size - 1) // bayer_size,
             (width + bayer_size - 1) // bayer_size),
        )[:height, :width].reshape(-1)
        threshold = np.clip(
            0.5 + (bayer - 0.5) * threshold_scale,
            0.0,
            1.0,
        )
        selected = np.where(threshold < best_fraction, best_second, best_first)
        indices = physical_indices[selected].reshape(height, width)
        return ImageProcessor._palette_image(indices, palette)

    @staticmethod
    def apply_gb_default_dithering(image, brightness_factor=1.05,
                                   threshold_scale=1.0, fake_colors=False,
                                   gb_color_palette="blue_yellow"):
        """Apply the gb-photo default tone curve with a panel-safe output palette."""
        np = _lazy_import_numpy()
        Image, ImageEnhance = _lazy_import_pil()
        enhanced = ImageEnhance.Brightness(image.convert("RGB")).enhance(brightness_factor)
        rgb = np.asarray(enhanced, dtype=np.float32) / 255.0
        luminance = (
            (0.299 * rgb[:, :, 0])
            + (0.587 * rgb[:, :, 1])
            + (0.114 * rgb[:, :, 2])
        )
        low, high = np.percentile(luminance, (2.0, 98.0))
        if high - low > (1.0 / 255.0):
            luminance = np.clip((luminance - low) / (high - low), 0.0, 1.0)

        height, width = luminance.shape
        pattern = np.tile(
            ImageProcessor.get_bayer_matrix(4).T,
            ((height + 3) // 4, (width + 3) // 4),
        )[:height, :width]
        pattern_values = np.clip(
            np.rint(7.5 + (np.rint(pattern * 16.0) - 7.5) * threshold_scale),
            0,
            15,
        ).astype(np.int32)
        camera_range = np.asarray([0x8A, 0x92, 0xA1, 0xC8], dtype=np.float32)
        camera_span = camera_range[-1] - camera_range[0]
        starts = (camera_range[:-1] - camera_range[0]) / camera_span
        steps = (camera_range[1:] - camera_range[:-1]) / (16.0 * camera_span)
        thresholds = np.stack([
            starts[index] + pattern_values * steps[index]
            for index in range(3)
        ], axis=-1)
        first, second, third = thresholds[:, :, 0], thresholds[:, :, 1], thresholds[:, :, 2]
        shades = np.where(
            luminance <= first,
            np.divide(
                luminance,
                first,
                out=np.zeros_like(luminance),
                where=first > 0.0,
            ),
            np.where(
                luminance <= second,
                1.0 + (luminance - first) / (second - first),
                np.where(
                    luminance <= third,
                    2.0 + (luminance - second) / (third - second),
                    3.0,
                ),
            ),
        )
        shades = np.clip(shades, 0.0, 3.0)
        variation_pattern = (
            np.random.default_rng(0).permutation(32 * 32).reshape(32, 32)
            .astype(np.float32) / (32 * 32)
        )
        variation = np.tile(
            variation_pattern,
            ((height + 31) // 32, (width + 31) // 32),
        )[:height, :width]
        if fake_colors:
            palette = ImageProcessor._blended_palette_array(0.45)
            shade_indices = np.asarray(
                GB_COLOR_PALETTE_COMBINATIONS.get(
                    gb_color_palette,
                    GB_COLOR_PALETTE_COMBINATIONS["blue_yellow"],
                ),
                dtype=np.uint8,
            )
            lower = np.floor(shades).astype(np.int32)
            upper = np.minimum(lower + 1, 3)
            fraction = shades - lower
            indices = np.where(
                variation < fraction,
                shade_indices[upper],
                shade_indices[lower],
            )
        else:
            palette = np.asarray([
                [0, 0, 0],
                [255, 255, 255],
                [0, 0, 0],
                [0, 0, 0],
                [0, 0, 0],
                [0, 0, 0],
                [0, 0, 0],
            ], dtype=np.uint8)
            indices = (variation < (shades / 3.0)).astype(np.uint8)
        return ImageProcessor._palette_image(indices, palette)

    @staticmethod
    def apply_ordered_dithering(image, saturation=0.6, brightness_factor=1.1, color_factor=1.4,
                               bayer_size=4, threshold_scale=1.0):
        """Apply ordered dithering using standard Bayer threshold + LUT nearest-color."""
        Image, ImageEnhance = _lazy_import_pil()
        np = _lazy_import_numpy()

        import time
        start_time = time.monotonic()

        # Ensure the image is in RGB mode
        if image.mode != "RGB":
            image = image.convert("RGB")

        # Adjust brightness
        enhancer = ImageEnhance.Brightness(image)
        image = enhancer.enhance(brightness_factor)

        # Adjust color intensity
        enhancer = ImageEnhance.Color(image)
        image = enhancer.enhance(color_factor)

        # Convert image to numpy array
        img_array = np.array(image, dtype=np.float32)
        height, width = img_array.shape[:2]

        # Get Bayer matrix and tile it to cover the entire image
        bayer_matrix = ImageProcessor.get_bayer_matrix(bayer_size)
        y_tiles = (height + bayer_size - 1) // bayer_size
        x_tiles = (width + bayer_size - 1) // bayer_size
        threshold_matrix = np.tile(bayer_matrix, (y_tiles, x_tiles))[:height, :width]

        # Build the blended palette (same as Floyd-Steinberg)
        palette_colors = []
        color_indices = [0, 1, 5, 4, 0, 3, 2]
        for i in color_indices:
            rs, gs, bs = [c * saturation for c in SATURATED_PALETTE[i]]
            rd, gd, bd = [c * (1.0 - saturation) for c in DESATURATED_PALETTE[i]]
            palette_colors.append([int(rs + rd), int(gs + gd), int(bs + bd)])
        pal = np.array(palette_colors, dtype=np.float32)

        # --- Precompute 32K nearest-color LUT (CIELAB distance) ---
        # CIELAB properly separates lightness from chromaticity, preventing
        # neutral grays from matching green (which has similar luminance).
        def _rgb_to_lab_batch(rgb):
            """Convert (N,3) float32 RGB [0-255] to CIELAB. Fully vectorized."""
            c = rgb / 255.0
            linear = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
            M = np.array([[0.4124564, 0.3575761, 0.1804375],
                          [0.2126729, 0.7151522, 0.0721750],
                          [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
            xyz = linear @ M.T
            xyz[:, 0] /= 0.95047
            xyz[:, 2] /= 1.08883
            f = np.where(xyz > 0.008856, np.cbrt(xyz), (903.3 * xyz + 16.0) / 116.0)
            lab = np.empty_like(xyz)
            lab[:, 0] = 116.0 * f[:, 1] - 16.0
            lab[:, 1] = 500.0 * (f[:, 0] - f[:, 1])
            lab[:, 2] = 200.0 * (f[:, 1] - f[:, 2])
            return lab

        # Build all possible 5-bit RGB values (32 levels per channel)
        r_vals = np.arange(32, dtype=np.float32) * 8 + 4
        g_vals = np.arange(32, dtype=np.float32) * 8 + 4
        b_vals = np.arange(32, dtype=np.float32) * 8 + 4
        rr, gg, bb = np.meshgrid(r_vals, g_vals, b_vals, indexing='ij')
        all_rgb = np.stack([rr, gg, bb], axis=-1).reshape(-1, 3)  # (32768, 3)

        # Convert to CIELAB
        all_lab = _rgb_to_lab_batch(all_rgb)
        pal_lab = _rgb_to_lab_batch(pal)

        # Compute CIELAB ΔE² to each palette color
        # all_lab: (32768, 3), pal_lab: (7, 3) → distances: (32768, 7)
        diff = all_lab[:, np.newaxis, :] - pal_lab[np.newaxis, :, :]
        distances = np.sum(diff * diff, axis=2)
        nearest_lut = np.argmin(distances, axis=1).astype(np.uint8)  # (32768,)

        lut_time = time.monotonic()
        logging.info(f"LUT built in {(lut_time - start_time)*1000:.1f}ms")

        # --- Per-channel Bayer thresholds derived from palette spacing ---
        # Compute max gap between successive sorted values per channel
        thresholds = np.zeros(3, dtype=np.float32)
        for ch in range(3):
            vals = np.sort(pal[:, ch])
            if len(vals) > 1:
                gaps = np.diff(vals)
                thresholds[ch] = float(np.max(gaps))
            else:
                thresholds[ch] = 256.0
        thresholds *= threshold_scale

        # --- Apply standard ordered dithering (Bayer noise + nearest color) ---
        # For each pixel: Attempt = Input + (threshold_value - 0.5) * Threshold
        # Then find nearest color from palette
        noise_r = (threshold_matrix - 0.5) * thresholds[0]
        noise_g = (threshold_matrix - 0.5) * thresholds[1]
        noise_b = (threshold_matrix - 0.5) * thresholds[2]

        dithered = np.empty_like(img_array)
        dithered[:, :, 0] = np.clip(img_array[:, :, 0] + noise_r, 0, 255)
        dithered[:, :, 1] = np.clip(img_array[:, :, 1] + noise_g, 0, 255)
        dithered[:, :, 2] = np.clip(img_array[:, :, 2] + noise_b, 0, 255)

        # Quantize to 5 bits and look up nearest color from LUT
        dithered_q = (dithered / 8).astype(np.int32)
        dithered_q = np.clip(dithered_q, 0, 31)
        lut_keys = (dithered_q[:, :, 0] << 10) | (dithered_q[:, :, 1] << 5) | dithered_q[:, :, 2]
        output_array = nearest_lut[lut_keys].astype(np.uint8)

        # Build output palette image
        palette_flat = []
        for color in palette_colors:
            palette_flat.extend(color)
        palette_flat += [0, 0, 0] * (256 - len(palette_colors))

        output_image = Image.fromarray(output_array, mode='P')
        output_image.putpalette(palette_flat)

        end_time = time.monotonic()
        logging.info(f"Ordered dithering completed in {(end_time - start_time)*1000:.1f}ms for {height}x{width} image")

        return output_image

    @staticmethod
    def palette_blend(saturation, dtype='uint8'):
        """Blend between desaturated and saturated palettes based on saturation."""
        palette = []
        color_indices = [0, 1, 5, 4, 0, 3, 2]
        for i in color_indices:
            rs, gs, bs = [c * saturation for c in SATURATED_PALETTE[i]]
            rd, gd, bd = [c * (1.0 - saturation) for c in DESATURATED_PALETTE[i]]
            if dtype == 'uint8':
                palette += [int(rs + rd), int(gs + gd), int(bs + bd)]
            elif dtype == 'uint24':
                palette += [(int(rs + rd) << 16) | (int(gs + gd) << 8) | int(bs + bd)]
        return palette

    @staticmethod
    def apply_dithering(image, saturation=0.6, brightness_factor=1.1, color_factor=1.4,
                       dithering_method="floyd_steinberg", bayer_size=4, threshold_scale=1.0,
                       tone_map="percentile", gb_color_palette="blue_yellow"):
        """Applies brightness, color enhancement, and dithering."""
        if dithering_method == "ordered":
            return ImageProcessor.apply_ordered_dithering(
                image, saturation, brightness_factor, color_factor, bayer_size, threshold_scale
            )
        if dithering_method == "bayer_natural_pair":
            if tone_map not in {"none", "percentile"}:
                raise ValueError(f"Unsupported tone map: {tone_map}")
            return ImageProcessor.apply_natural_pair_dithering(
                image, saturation, brightness_factor, color_factor,
                bayer_size, threshold_scale, tone_map
            )
        if dithering_method == "gb-default":
            return ImageProcessor.apply_gb_default_dithering(
                image, brightness_factor, threshold_scale, fake_colors=False
            )
        if dithering_method == "gb-default-color":
            if gb_color_palette not in GB_COLOR_PALETTE_COMBINATIONS:
                raise ValueError(f"Unsupported Game Boy color palette: {gb_color_palette}")
            return ImageProcessor.apply_gb_default_dithering(
                image, brightness_factor, threshold_scale,
                fake_colors=True, gb_color_palette=gb_color_palette
            )
        if dithering_method != "floyd_steinberg":
            raise ValueError(f"Unsupported dithering method: {dithering_method}")

        Image, ImageEnhance = _lazy_import_pil()

        # Default Floyd-Steinberg dithering
        # Ensure the image is in RGB mode
        if image.mode != "RGB":
            image = image.convert("RGB")

        # Adjust brightness
        enhancer = ImageEnhance.Brightness(image)
        image = enhancer.enhance(brightness_factor)

        # Adjust saturation
        enhancer = ImageEnhance.Color(image)
        image = enhancer.enhance(color_factor)

        # Blend the palette
        palette = ImageProcessor.palette_blend(saturation)

        # Create a new palette image
        palette_image = Image.new("P", (1, 1))
        palette_image.putpalette(palette + [0, 0, 0] * (256 - len(palette) // 3))

        # Convert the image using the custom palette and Floyd-Steinberg dithering
        converted_image = image.quantize(palette=palette_image, dither=Image.FLOYDSTEINBERG)

        return converted_image

    @staticmethod
    def dither_to_display_buffer(image, saturation=0.6, brightness_factor=1.1, color_factor=1.4,
                                  dithering_method="floyd_steinberg", bayer_size=4, threshold_scale=1.0,
                                  tone_map="percentile", gb_color_palette="blue_yellow"):
        """Dither an image and produce the packed display buffer in one step.

        Returns a tuple of (display_buffer, dithered_pil_image):
        - display_buffer: bytearray ready to send to epd.display()
        - dithered_pil_image: the palette PIL image (for saving as PNG for the dashboard)

        This eliminates the redundant img2buffer() conversion by mapping dither
        palette indices directly to hardware nibble indices during the dither step.
        """
        np = _lazy_import_numpy()
        Image, _ = _lazy_import_pil()
        import time
        start_time = time.monotonic()

        # Mapping from dither palette indices to hardware nibble indices.
        # Dither palette: 0:black, 1:white, 2:yellow, 3:red, 4:black, 5:blue, 6:green
        # Hardware panel:  0:black, 1:white, 2:yellow, 3:red, (skip 4), 5:blue, 6:green
        # Indices 7-255 are black padding in the palette, so they map to hw black (0).
        DITHER_TO_HW = np.zeros(256, dtype=np.uint8)  # default=0 (black) for all padding
        DITHER_TO_HW[0] = 0   # black  → hw black
        DITHER_TO_HW[1] = 1   # white  → hw white
        DITHER_TO_HW[2] = 2   # yellow → hw yellow
        DITHER_TO_HW[3] = 3   # red    → hw red
        DITHER_TO_HW[4] = 0   # black  → hw black (duplicate)
        DITHER_TO_HW[5] = 5   # blue   → hw blue
        DITHER_TO_HW[6] = 6   # green  → hw green

        # Step 1: run the normal dithering to get the palette image
        dithered_image = ImageProcessor.apply_dithering(
            image, saturation, brightness_factor, color_factor,
            dithering_method, bayer_size, threshold_scale,
            tone_map, gb_color_palette
        )

        dither_time = time.monotonic()

        # Step 2: extract pixel indices and rotate if needed.
        # The processed image is landscape; the current panel expects portrait.
        imwidth, imheight = dithered_image.size
        if (imwidth, imheight) == DISPLAY_IMAGE_SIZE:
            display_image = dithered_image.rotate(90, expand=True)
        elif (imwidth, imheight) == DISPLAY_PANEL_SIZE:
            display_image = dithered_image
        else:
            logging.warning(f"dither_to_display_buffer: unexpected size {imwidth}x{imheight}")
            display_image = dithered_image.rotate(90, expand=True)

        # Step 3: map palette indices → hardware nibbles and pack
        src_indices = np.frombuffer(display_image.tobytes('raw'), dtype=np.uint8)
        hw_pixels = DITHER_TO_HW[src_indices]
        buf = (hw_pixels[0::2].astype(np.uint8) << 4) + hw_pixels[1::2].astype(np.uint8)
        display_buffer = bytearray(buf.astype(np.uint8))

        buffer_time = time.monotonic()
        logging.info(f"dither_to_display_buffer: dither={((dither_time - start_time)*1000):.0f}ms, "
                     f"buffer={((buffer_time - dither_time)*1000):.0f}ms, "
                     f"total={((buffer_time - start_time)*1000):.0f}ms")

        return display_buffer, dithered_image

    @staticmethod
    def resize_image(image, size=DISPLAY_IMAGE_SIZE):
        """Resizes the image to the specified size."""
        Image, _ = _lazy_import_pil()
        if image.size == (size[0] * 2, size[1] * 2) and hasattr(image, "reduce"):
            # Preserve the 2x source capture and average each 2x2 block before
            # dithering. This is faster than generic scaling and still provides
            # a properly filtered high-resolution source.
            return image.reduce(2)

        # Pillow >= 10 moved filters under Image.Resampling; older Pi builds keep them on Image.
        resampling_attr = getattr(Image, "Resampling", Image)
        resample_filter = getattr(resampling_attr, "BILINEAR", Image.BILINEAR)
        return image.resize(size, resample_filter)

    @staticmethod
    def img2buffer(image, width=DISPLAY_PANEL_WIDTH, height=DISPLAY_PANEL_HEIGHT):
        """Converts an image to a format suitable for the e-ink display."""
        np = _lazy_import_numpy()
        imwidth, imheight = image.size
        if imwidth == width and imheight == height:
            image_temp = image
        elif imwidth == height and imheight == width:
            image_temp = image.rotate(90, expand=True)
        else:
            logging.warning(f"Invalid image dimensions: {imwidth}x{imheight}, expected {width}x{height}")
            return None

        # Ensure PIL is available for palette operations in this function
        Image, _ = _lazy_import_pil()

        # Map any palette indices or RGB values to the panel's fixed nibble indices.
        # Hardware palette indices expected by the panel (nibbles):
        # 0:black, 1:white, 2:yellow, 3:red, 4:clear/duplicate-black (do not use), 5:blue, 6:green
        # We'll map every pixel to the closest of {0,1,2,3,5,6} and never emit 4.
        try:
            # Ensure we have a palette image to read indices from
            if image_temp.mode != 'P':
                pal_image = Image.new('P', (1, 1))
                # Build palette matching driver order so indices match hardware mapping
                pal_image.putpalette([
                    0, 0, 0,      # 0 black
                    255, 255, 255,# 1 white
                    255, 255, 0,  # 2 yellow
                    255, 0, 0,    # 3 red
                    0, 0, 0,      # 4 duplicate black (panel clear)
                    0, 0, 255,    # 5 blue
                    0, 255, 0,    # 6 green
                ] + [0, 0, 0] * (256 - 7))
                # Quantize without dithering to preserve the existing pattern as much as possible
                image_temp = image_temp.convert('RGB').quantize(palette=pal_image, dither=Image.NONE)

            # Build source palette -> hardware index map using nearest color, excluding index 4
            pal = image_temp.getpalette()
            if pal is None:
                raise ValueError('Palette missing after conversion to P')
            src_colors = [pal[i:i+3] for i in range(0, min(len(pal), 256 * 3), 3)]

            # Hardware color set (exclude index 4)
            hw_indices = np.array([0, 1, 2, 3, 5, 6], dtype=np.uint8)
            hw_colors = np.array([
                [0, 0, 0],
                [255, 255, 255],
                [255, 255, 0],
                [255, 0, 0],
                [0, 0, 255],
                [0, 255, 0],
            ], dtype=np.float32)

            # Create a 256-element map from source palette index to hardware nibble index
            idx_map = np.zeros(256, dtype=np.uint8)
            for s_idx in range(256):
                if s_idx < len(src_colors):
                    r, g, b = src_colors[s_idx]
                    color_vec = np.array([[float(r), float(g), float(b)]], dtype=np.float32)
                    dists = np.sum((hw_colors - color_vec) ** 2, axis=1)
                    mapped = int(hw_indices[int(np.argmin(dists))])
                    # Never allow 4; nearest set excludes 4 already. Keep mapped as is.
                    idx_map[s_idx] = np.uint8(mapped)
                else:
                    # Uninitialized palette slots default to white
                    idx_map[s_idx] = 1

            # Apply mapping to pixel indices and pack nibbles
            src_indices = np.frombuffer(image_temp.tobytes('raw'), dtype=np.uint8)
            hw_pixels = idx_map[src_indices]
            # Double safety: remap any stray 4 -> 0
            if (hw_pixels == 4).any():
                hw_pixels = np.where(hw_pixels == 4, 0, hw_pixels).astype(np.uint8)
            buf = (hw_pixels[0::2].astype(np.uint8) << 4) + hw_pixels[1::2].astype(np.uint8)
            buf = buf.astype(np.uint8).tolist()
            return buf
        except Exception as e:
            logging.warning(f"img2buffer: palette mapping fallback due to: {e}")
            # Fallback: direct indices with 4 -> 0 remap
            buf_6color = np.frombuffer(image_temp.tobytes('raw'), dtype=np.uint8)
            try:
                buf_6color = buf_6color.copy()
                buf_6color[buf_6color == 4] = 0
            except Exception:
                buf_6color = np.where(buf_6color == 4, 0, buf_6color).astype(np.uint8)
            buf = (buf_6color[0::2] << 4) + buf_6color[1::2]
            buf = buf.astype(np.uint8).tolist()

        return buf

    @staticmethod
    def render_photo_with_settings(original_path, processing_settings):
        """Render without persistence; callers decide whether to save the result."""
        Image, ImageEnhance = _lazy_import_pil()
        with Image.open(original_path) as original_image:
            resized_image = ImageProcessor.resize_image(original_image)
            return ImageProcessor.apply_dithering(
                resized_image,
                saturation=processing_settings.get("saturation", 0.6),
                brightness_factor=processing_settings.get("brightness_factor", 1.1),
                color_factor=processing_settings.get("color_factor", 1.4),
                dithering_method=processing_settings.get("dithering_method", "floyd_steinberg"),
                bayer_size=processing_settings.get("bayer_size", 4),
                threshold_scale=processing_settings.get("threshold_scale", 1.0),
                tone_map=processing_settings.get("tone_map", "percentile"),
                gb_color_palette=processing_settings.get("gb_color_palette", "blue_yellow")
            )

    @staticmethod
    def process_photo_with_settings(original_path, output_path, processing_settings, photo_id=None):
        """Process a photo with specific settings and save it."""
        try:
            Image, _ = _lazy_import_pil()
            rotation = ImageProcessor.read_dithered_metadata(output_path).get("rotation", 0)
            with Image.open(original_path) as original_image:
                capture_time = ImageProcessor._capture_time_from_source(original_image)
                dithered_image = ImageProcessor.render_photo_with_settings(
                    original_path, processing_settings
                )
                processing_metadata = ImageProcessor.build_processing_metadata(
                    photo_id or os.path.splitext(os.path.basename(original_path))[0],
                    processing_settings.get("dithering_method"),
                    processing_settings.get("gb_color_palette"),
                    processing_settings,
                )
                ImageProcessor.save_dithered_image(
                    dithered_image,
                    output_path,
                    source_image=original_image,
                    rotation=rotation,
                    capture_time=capture_time,
                    dithering_method=processing_settings.get("dithering_method"),
                    gb_color_palette=processing_settings.get("gb_color_palette"),
                    processing_metadata=processing_metadata,
                )
                dithered_image.close()
            logging.info(f"Processed image saved to {output_path}")

            return {
                "success": True,
                "original_path": original_path,
                "processed_path": output_path,
                "message": "Photo processed successfully"
            }

        except Exception as e:
            logging.error(f"Error processing photo: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Photo processing failed: {str(e)}"
            }

    @staticmethod
    def save_dithered_preview_by_id(
        photo_id,
        encoded_png=None,
        rotation=0,
        dithering_method="floyd_steinberg",
        gb_color_palette="blue_yellow",
        photos_path=SAVE_PATH,
        output_path=PROCESSED_PATH,
        file_manager=None,
    ):
        Image, _ = _lazy_import_pil()
        manager = file_manager or FileManager(photos_path, output_path)
        photo_info = manager.get_photo_info(photo_id)
        if not photo_info:
            return {"success": False, "error": "Original photo not found", "message": "Photo not found"}
        original_path = photo_info["original_path"]

        dithered_path = photo_info.get("dithered_path") or os.path.join(
            output_path, f"{photo_info['id']}_dithered.png"
        )
        try:
            if encoded_png:
                with Image.open(BytesIO(base64.b64decode(encoded_png, validate=True))) as preview:
                    if preview.format != "PNG" or preview.size != DISPLAY_IMAGE_SIZE:
                        raise ValueError("Preview must be a display-sized PNG")
                    dithered_image = preview.copy()
            elif os.path.exists(dithered_path):
                with Image.open(dithered_path) as saved_image:
                    dithered_image = saved_image.copy()
            else:
                return {"success": False, "error": "Dithered photo not found", "message": "Photo has no dithered version"}

            with Image.open(original_path) as original_image:
                ImageProcessor.save_dithered_image(
                    dithered_image,
                    dithered_path,
                    source_image=original_image,
                    rotation=rotation,
                    capture_time=ImageProcessor._capture_time_from_source(original_image),
                    dithering_method=dithering_method,
                    gb_color_palette=gb_color_palette,
                    processing_metadata=ImageProcessor.build_processing_metadata(
                        photo_info["id"],
                        dithering_method,
                        gb_color_palette,
                    ),
                )
            dithered_image.close()
        except (OSError, ValueError, TypeError) as error:
            return {"success": False, "error": str(error), "message": "Could not save dithered photo"}

        return {
            "success": True,
            "photo_id": photo_info["id"],
            "processed_path": dithered_path,
            "rotation": rotation,
            "dithering_method": dithering_method,
            "gb_color_palette": gb_color_palette,
            "message": "Dithered photo saved",
        }

    @staticmethod
    def reprocess_photo_by_id(
        photo_id,
        processing_settings,
        photos_path=SAVE_PATH,
        output_path=PROCESSED_PATH,
        file_manager=None,
    ):
        """Reprocess an existing photo by ID with new settings."""
        manager = file_manager or FileManager(photos_path, output_path)
        photo_info = manager.get_photo_info(photo_id)
        if not photo_info:
            return {
                "success": False,
                "error": "Original photo not found",
                "message": f"Could not find original photo for ID: {photo_id}"
            }

        output_file_path = photo_info.get("dithered_path") or os.path.join(
            output_path, f"{photo_info['id']}_dithered.png"
        )

        return ImageProcessor.process_photo_with_settings(
            photo_info["original_path"],
            output_file_path,
            processing_settings,
            photo_id=photo_info["id"],
        )


class FileManager:
    """Handles file saving and directory management."""

    def __init__(self, save_path, processed_path):
        self.save_path = save_path
        self.processed_path = processed_path
        os.makedirs(save_path, exist_ok=True)
        os.makedirs(processed_path, exist_ok=True)
        self._id_lock = threading.Lock()
        self._recover_temporary_files()
        self._next_photo_index = self._find_next_photo_index()

    def _recover_temporary_files(self):
        for directory in (self.save_path, self.processed_path):
            try:
                for path in os.scandir(directory):
                    if path.is_file() and (
                        path.name.startswith(".capture-") or ".tmp-" in path.name
                    ):
                        os.unlink(path.path)
            except OSError as error:
                logging.warning("Could not recover temporary photo files: %s", error)

    def _find_next_photo_index(self):
        """Seed the monotonic photo counter from numeric filenames on disk."""
        highest_index = -1
        try:
            for filename in os.listdir(self.save_path):
                stem, extension = os.path.splitext(filename)
                if extension.lower() not in {".png", ".jpg", ".jpeg"}:
                    continue
                if stem.isdigit():
                    highest_index = max(highest_index, int(stem))
                    continue
                match = CANONICAL_FILENAME_PATTERN.match(stem)
                if match:
                    highest_index = max(highest_index, int(match.group("sequence")))
        except OSError as e:
            logging.warning(f"Could not scan existing photo IDs: {e}")
        return highest_index + 1

    def get_new_file_path(self, folder, extension="png"):
        """Generates a new unique file path in the specified folder."""
        if os.path.abspath(folder) != os.path.abspath(self.save_path):
            raise ValueError("Photo IDs can only be allocated in the original photo directory")

        with self._id_lock:
            index = self._next_photo_index
            self._next_photo_index += 1
        return os.path.join(folder, f"{str(index).zfill(5)}.{extension}")

    def reserve_capture(self, extension="jpg"):
        """Reserve a sequence number and return a private temporary path."""
        with self._id_lock:
            sequence = self._next_photo_index
            self._next_photo_index += 1
        temporary_name = f".capture-{sequence:05d}-{os.getpid()}-{threading.get_ident()}.tmp.{extension.lstrip('.') }"
        return sequence, os.path.join(self.save_path, temporary_name)

    def canonical_path_for_bytes(self, sequence, capture_time, content, extension="jpg"):
        """Resolve a canonical path, extending the hash if a short collision exists."""
        full_digest = hashlib.sha256(bytes(content)).hexdigest()
        filename, photo_id = canonical_original_filename(
            sequence, capture_time, content, extension=extension, hash_length=8
        )
        candidate = os.path.join(self.save_path, filename)
        if os.path.exists(candidate):
            try:
                existing_digest = hashlib.sha256(Path(candidate).read_bytes()).hexdigest()
            except OSError as error:
                raise OSError(f"Could not verify hash collision for {candidate}: {error}") from error
            if existing_digest != full_digest:
                filename, photo_id = canonical_original_filename(
                    sequence, capture_time, content, extension=extension, hash_length=16
                )
                candidate = os.path.join(self.save_path, filename)
                if os.path.exists(candidate) and hashlib.sha256(Path(candidate).read_bytes()).hexdigest() != full_digest:
                    raise FileExistsError(f"Content hash collision for {photo_id}")
        return candidate, photo_id

    def save_image(self, image, folder, extension="png"):
        """Saves the image to a unique file in the specified folder."""
        file_path = self.get_new_file_path(folder, extension)
        # Always save PNG by default
        image.save(file_path, format="PNG")
        logging.info(f"Image saved to {file_path}")
        return file_path

    @staticmethod
    def _identity_for_filename(filename):
        stem = os.path.splitext(os.path.basename(filename))[0]
        match = CANONICAL_FILENAME_PATTERN.match(stem)
        if match:
            return {
                "id": match.group("photo_id"),
                "id_kind": "content_hash",
                "legacy_id": None,
            }
        return {"id": stem, "id_kind": "legacy", "legacy_id": stem}

    def _find_original_path(self, photo_id):
        if not photo_id or os.path.basename(photo_id) != photo_id:
            return None
        candidates = []
        try:
            candidates = [
                entry for entry in os.scandir(self.save_path)
                if entry.is_file() and Path(entry.name).suffix.lower() in {".png", ".jpg", ".jpeg"}
            ]
        except OSError:
            return None
        for entry in candidates:
            identity = self._identity_for_filename(entry.name)
            if identity["id"] == photo_id or identity["legacy_id"] == photo_id:
                return entry.path
        return None

    def _find_dithered_path(self, original_path, photo_id):
        original_name = os.path.basename(original_path)
        original_stem = os.path.splitext(original_name)[0]
        candidates = [
            f"{photo_id}_dithered.png",
            f"{photo_id}_dithered.jpg",
            f"{original_stem}_dithered.png",
            f"{original_stem}_dithered.jpg",
            original_name,
        ]
        for filename in dict.fromkeys(candidates):
            path = os.path.join(self.processed_path, filename)
            if os.path.exists(path):
                return path
        return None

    def _photo_entries(self):
        """Return candidate originals ordered without opening image files."""
        try:
            entries = [
                entry for entry in os.scandir(self.save_path)
                if entry.is_file() and Path(entry.name).suffix.lower() in {".png", ".jpg", ".jpeg"}
            ]
            return sorted(entries, key=lambda item: item.stat().st_mtime_ns, reverse=True)
        except OSError as error:
            logging.error(f"Error scanning photos: {error}")
            return []

    def get_photo_info(self, photo_id, original_path=None):
        """Get information about a specific photo by ID."""
        if original_path is None:
            original_path = self._find_original_path(photo_id)
        if not original_path:
            return None

        try:
            identity = self._identity_for_filename(original_path)
            resolved_id = identity["id"]
            dithered_path = self._find_dithered_path(original_path, resolved_id)
            has_dithered = dithered_path is not None
            metadata_path = dithered_path or original_path
            dithered_metadata = ImageProcessor.read_dithered_metadata(metadata_path)
            dithered_updated_at = os.stat(dithered_path).st_mtime_ns if has_dithered else None

            # Get file stats
            original_stat = os.stat(original_path)

            return {
                "id": resolved_id,
                "id_kind": identity["id_kind"],
                "legacy_id": identity["legacy_id"],
                "original_path": original_path,
                "dithered_path": dithered_path if has_dithered else None,
                "has_dithered": has_dithered,
                "dithered_updated_at": dithered_updated_at,
                "rotation": dithered_metadata.get("rotation", 0),
                "dithering_method": dithered_metadata.get("dithering_method"),
                "gb_color_palette": dithered_metadata.get("gb_color_palette"),
                "sensor_metadata": dithered_metadata.get("sensor_metadata"),
                "exif_metadata": dithered_metadata.get("exif_metadata"),
                "processing_metadata": dithered_metadata.get("processing_metadata"),
                "file_size": original_stat.st_size,
                "created_at": original_stat.st_mtime,
                "capture_time": dithered_metadata.get("capture_time"),
                "filename": os.path.basename(original_path)
            }
        except OSError as e:
            logging.warning(f"Could not stat photo {photo_id} (may be mid-write): {e}")
            return None

    def list_all_photos(self):
        """List all photos with their information."""
        photos = []

        seen_paths = set()
        for entry in self._photo_entries():
            photo_id = self._identity_for_filename(entry.name)["id"]
            photo_info = self.get_photo_info(photo_id, original_path=entry.path)
            if photo_info and photo_info["original_path"] not in seen_paths:
                seen_paths.add(photo_info["original_path"])
                photos.append(photo_info)

        return photos

    def list_photo_page(self, page=1, limit=20, photo_ids=None):
        """Return one page while reading image metadata only for its records."""
        page = max(1, int(page or 1))
        limit = max(1, min(int(limit or 20), 100))
        allowed_ids = set(photo_ids) if photo_ids is not None else None
        entries = self._photo_entries()
        if allowed_ids is not None:
            entries = [
                entry for entry in entries
                if (
                    self._identity_for_filename(entry.name)["id"] in allowed_ids
                    or self._identity_for_filename(entry.name)["legacy_id"] in allowed_ids
                )
            ]

        total_photos = len(entries)
        total_pages = max(1, (total_photos + limit - 1) // limit)
        page = min(page, total_pages)
        start = (page - 1) * limit
        photos = []
        seen_paths = set()
        for entry in entries[start:start + limit]:
            photo_id = self._identity_for_filename(entry.name)["id"]
            photo_info = self.get_photo_info(photo_id, original_path=entry.path)
            if photo_info and photo_info["original_path"] not in seen_paths:
                seen_paths.add(photo_info["original_path"])
                photos.append(photo_info)

        return {
            "photos": photos,
            "pagination": {
                "page": page,
                "limit": limit,
                "total_photos": total_photos,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            },
        }

    def delete_photo(self, photo_id):
        """Delete both original and processed versions of a photo."""
        deleted_files = []

        original_path = self._find_original_path(photo_id)
        if original_path and os.path.exists(original_path):
            os.remove(original_path)
            deleted_files.append(original_path)

        if original_path:
            original_stem = os.path.splitext(os.path.basename(original_path))[0]
            for name in {
                f"{photo_id}_dithered.png", f"{photo_id}_dithered.jpg",
                f"{original_stem}_dithered.png", f"{original_stem}_dithered.jpg",
                os.path.basename(original_path),
            }:
                processed_file = os.path.join(self.processed_path, name)
                if os.path.exists(processed_file):
                    os.remove(processed_file)
                    deleted_files.append(processed_file)

        if deleted_files:
            logging.info(f"Deleted photo {photo_id}: {deleted_files}")
            return {"success": True, "deleted_files": deleted_files}
        else:
            return {"success": False, "error": "Photo not found"}


# ═══════════════════════════════════════════════════════════════════
# HARDWARE: Display driver wrapper
# This class adapts the Waveshare epd4in0e driver to the rest of reFrame.
# To use another display, keep this public surface compatible:
# prepare_async(), is_busy(), display_image(), display_buffer(),
# display_buffer_async(), display_photo_by_id(), clear_display(),
# display_dashboard_qr(), and sleep().
#
# Also update DISPLAY_* constants and the dither palette/mapping in
# ImageProcessor if the panel has a different resolution, orientation, color
# order, or buffer format.
# ═══════════════════════════════════════════════════════════════════
class EInkDisplay:
    """Waveshare ePaper display adapter with lazy initialization."""

    def __init__(self):
        # Don't initialize e-ink hardware at startup — takes 5-10s
        self.epd = None
        self._initialized = False
        self._display_busy = False  # True while the panel is mid-refresh
        self._display_thread = None
        self._display_lock = threading.Lock()
        self._display_abort_event = None
        self._last_display_buffer = None
        self._needs_reinit = False
        self._init_lock = threading.Lock()
        self._init_thread = None
        logging.info("E-ink display: Lazy initialization enabled")

    def _ensure_initialized(self):
        """Initialize e-ink display only when first needed."""
        with self._init_lock:
            if self._initialized and not self._needs_reinit:
                return
            logging.info("Initializing e-ink display hardware...")
            import time
            from waveshare_epd import epd4in0e
            start_time = time.monotonic()

            if self.epd is None:
                self.epd = epd4in0e.EPD()
            self.epd.init()

            init_time = time.monotonic() - start_time
            logging.info(f"E-ink display ready in {init_time:.2f}s")
            self._initialized = True
            self._needs_reinit = False

    def prepare_async(self):
        """Start e-ink hardware initialization in the background."""
        if self._initialized:
            return
        if self._init_thread and self._init_thread.is_alive():
            return
        self._init_thread = threading.Thread(target=self._ensure_initialized, daemon=True)
        self._init_thread.start()

    def is_busy(self):
        """Check if the display is currently in the middle of a refresh cycle."""
        with self._display_lock:
            return self._display_busy

    def _remember_display_buffer(self, buffer):
        with self._display_lock:
            self._last_display_buffer = bytearray(buffer)

    def display_image(self, image):
        """Displays the provided image on the e-ink display."""
        self._ensure_initialized()  # Initialize only when first used
        buffer = ImageProcessor.img2buffer(image)
        if buffer:
            self._remember_display_buffer(buffer)
            self.epd.display(buffer)

    def display_buffer(self, buffer):
        """Send a pre-built display buffer to the e-ink panel (blocking)."""
        self._ensure_initialized()
        if buffer:
            self._remember_display_buffer(buffer)
            self.epd.display(buffer)

    def display_buffer_async(self, buffer):
        """Send a pre-built display buffer to the e-ink panel in a background thread.

        Sets _display_busy=True before starting and clears it when the refresh
        cycle completes. The caller should check is_busy() before starting a
        new capture to avoid invisible captures with no feedback.
        """
        if not buffer:
            return {"success": False, "message": "Display buffer is empty"}

        buffer = bytearray(buffer)
        with self._display_lock:
            # If a previous refresh is somehow still running, log a warning
            if self._display_busy:
                logging.warning("display_buffer_async: previous refresh still in progress, skipping")
                return {"success": False, "error": "display_busy", "message": "Display is already refreshing"}
            self._last_display_buffer = bytearray(buffer)
            abort_event = threading.Event()
            self._display_abort_event = abort_event
            self._display_busy = True

        def _refresh():
            try:
                self._ensure_initialized()
                logging.info("Display refresh started (background)")
                refresh_start = time.monotonic()
                self.epd.display(buffer, abort_event=abort_event)
                refresh_time = time.monotonic() - refresh_start
                logging.info(f"Display refresh completed in {refresh_time:.1f}s")
            except Exception as e:
                if abort_event.is_set():
                    logging.info("Display refresh interrupted")
                else:
                    logging.error(f"Display refresh error: {e}")
            finally:
                with self._display_lock:
                    if self._display_abort_event is abort_event:
                        self._display_abort_event = None
                        self._display_busy = False

        self._display_thread = threading.Thread(target=_refresh, daemon=True)
        self._display_thread.start()
        return {"success": True, "message": "Display refresh started"}

    def _cancel_refresh(self):
        with self._display_lock:
            abort_event = self._display_abort_event
            display_thread = self._display_thread
            epd = self.epd
            if abort_event is not None:
                abort_event.set()
        return epd, display_thread

    def _wait_for_refresh_to_stop(self, display_thread):
        if display_thread and display_thread is not threading.current_thread():
            display_thread.join(timeout=5)
        return not display_thread or not display_thread.is_alive()

    def force_reset(self):
        """Force the panel reset line and mark the controller for reinitialization."""
        self._ensure_initialized()
        epd, display_thread = self._cancel_refresh()
        try:
            epd.reset()
        except Exception as e:
            logging.error(f"Error forcing e-ink display reset: {e}")
            return {"success": False, "error": str(e), "message": "Display reset failed"}

        if not self._wait_for_refresh_to_stop(display_thread):
            return {"success": False, "error": "display_busy", "message": "Display refresh did not stop after reset"}

        with self._display_lock:
            self._needs_reinit = True
        logging.warning("E-ink display forcibly reset; next draw will reinitialize it")
        return {"success": True, "message": "Display reset; next redraw will reinitialize the panel"}

    def force_stop(self):
        """Force panel power off and release its GPIO/SPI resources."""
        self._ensure_initialized()
        epd, display_thread = self._cancel_refresh()
        try:
            epd.force_stop()
        except Exception as e:
            logging.error(f"Error forcing e-ink display stop: {e}")
            return {"success": False, "error": str(e), "message": "Display stop failed"}

        if not self._wait_for_refresh_to_stop(display_thread):
            return {"success": False, "error": "display_busy", "message": "Display refresh did not stop"}

        try:
            epd.shutdown()
        except Exception as e:
            logging.error(f"Error releasing e-ink display resources: {e}")
            return {"success": False, "error": str(e), "message": "Display stopped but resource cleanup failed"}

        with self._display_lock:
            self.epd = None
            self._initialized = False
            self._needs_reinit = False
        logging.warning("E-ink display forcibly stopped and powered off")
        return {"success": True, "message": "Display force-stopped and powered off"}

    def interrupt_refresh(self, action):
        if action == "reset":
            return self.force_reset()
        if action == "stop":
            return self.force_stop()
        return {"success": False, "error": "invalid_action", "message": f"Unsupported refresh interrupt action: {action}"}

    def redraw_last_display(self):
        with self._display_lock:
            buffer = bytearray(self._last_display_buffer) if self._last_display_buffer else None
        if not buffer:
            return {"success": False, "error": "no_display_image", "message": "No display image is available to redraw"}
        result = self.display_buffer_async(buffer)
        if result.get("success"):
            result["message"] = "Redraw started for the current display image"
        return result

    def display_photo_by_id(self, photo_id, file_manager, prefer_dithered=True,
                            processing_settings=None):
        """Display a photo by ID on the e-ink screen."""
        try:
            photo_info = file_manager.get_photo_info(photo_id)
            if not photo_info:
                return {
                    "success": False,
                    "error": "Photo not found",
                    "message": f"Could not find photo with ID: {photo_id}"
                }

            # Choose which version to display
            if prefer_dithered and photo_info["has_dithered"]:
                image_path = photo_info["dithered_path"]
                version = "dithered"
            else:
                image_path = photo_info["original_path"]
                version = "original"

            Image, _ = _lazy_import_pil()
            with Image.open(image_path) as image:
                if version == "original":
                    resized_image = ImageProcessor.resize_image(image)
                    settings = processing_settings or {}
                    display_image = ImageProcessor.apply_dithering(
                        resized_image,
                        saturation=settings.get("saturation", 0.6),
                        brightness_factor=settings.get("brightness_factor", 1.1),
                        color_factor=settings.get("color_factor", 1.4),
                        dithering_method=settings.get("dithering_method", "floyd_steinberg"),
                        bayer_size=settings.get("bayer_size", 4),
                        threshold_scale=settings.get("threshold_scale", 1.0),
                        tone_map=settings.get("tone_map", "percentile"),
                        gb_color_palette=settings.get("gb_color_palette", "blue_yellow")
                    )
                else:
                    display_image = ImageProcessor.prepare_dithered_for_display(image)
                self.display_image(display_image)

            logging.info(f"Displayed {version} version of photo {photo_id} on e-ink screen")

            return {
                "success": True,
                "photo_id": photo_id,
                "version_displayed": version,
                "image_path": image_path,
                "message": f"Photo {photo_id} displayed successfully"
            }

        except Exception as e:
            logging.error(f"Error displaying photo {photo_id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to display photo {photo_id}: {str(e)}"
            }

    def clear_display(self):
        """Clear the e-ink display."""
        self._ensure_initialized()  # Initialize only when first used
        try:
            self.epd.Clear()
            logging.info("E-ink display cleared")
            return {"success": True, "message": "Display cleared"}
        except Exception as e:
            logging.error(f"Error clearing display: {e}")
            return {"success": False, "error": str(e)}

    def display_dashboard_qr(self, access_info):
        """Display a QR code that opens the dashboard."""
        try:
            image = render_dashboard_qr_image(access_info)
            self.display_image(image)
            logging.info("Displayed dashboard QR code: %s", access_info.get("primary_url"))
            return {
                "success": True,
                "message": "Dashboard QR displayed",
                "access": access_info
            }
        except Exception as e:
            logging.error(f"Error displaying dashboard QR: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to display dashboard QR: {str(e)}"
            }

    def sleep(self):
        """Puts the e-ink display to sleep."""
        if self._initialized and self.epd:
            self.epd.sleep()


class CameraSystem:
    """Complete camera system that implements dashboard-like functionality."""

    def __init__(self, settings_path="settings.json", eink_display=None):
        self.eink_display = eink_display if eink_display is not None else EInkDisplay()
        self.camera_manager = CameraManager(settings_path)
        self.file_manager = FileManager(SAVE_PATH, PROCESSED_PATH)
        self.timeout_thread = None
        self.timeout_running = False
        self._timeout_started = False
        self._timeout_stop_event = threading.Event()
        self.dashboard_qr_thread = None
        self._dashboard_qr_monitor_started = False
        self.dashboard_qr_running = False
        self._carousel_lock = threading.Lock()
        self._carousel_stop_event = None
        self._carousel_thread = None
        self._carousel_active = False
        self._carousel_queue = []
        self._carousel_current_photo_id = None
        self._carousel_advance_event = threading.Event()
        logging.info("Timeout monitor initialization deferred for fast startup")

    def start_timeout_monitor(self):
        """Start the background timeout monitoring thread."""
        if not self._timeout_started and self.camera_manager.is_timeout_enabled():
            self.timeout_running = True
            self._timeout_stop_event.clear()
            self.timeout_thread = threading.Thread(target=self._timeout_monitor_loop, daemon=True)
            self.timeout_thread.start()
            self._timeout_started = True
            timeout_minutes = self.camera_manager.get_timeout_minutes()
            elapsed = self.camera_manager.get_inactivity_seconds()
            logging.info(f"Auto-timeout monitor started: {timeout_minutes} minutes timeout, last activity was {elapsed:.1f}s ago")

    def start_timeout_monitor_deferred(self):
        """Start the timeout monitor after first photo for faster startup."""
        if not self._timeout_started:
            logging.info("Starting deferred timeout monitor...")
            self.start_timeout_monitor()

    def stop_timeout_monitor(self):
        """Stop the background timeout monitoring thread."""
        self.timeout_running = False
        self._timeout_stop_event.set()
        if self.timeout_thread and self.timeout_thread.is_alive():
            self.timeout_thread.join(timeout=2)
        self._timeout_started = False

    def _timeout_monitor_loop(self):
        """Background loop that checks for timeout and shuts down system."""
        # Wait a bit before starting to check for timeout to avoid false triggers on startup
        if self._timeout_stop_event.wait(60):
            return

        while self.timeout_running and not self._timeout_stop_event.is_set():
            try:
                if self.camera_manager.is_timeout_exceeded():
                    logging.info("Timeout exceeded, initiating system shutdown")
                    self.camera_manager.shutdown_system()
                    # If we reach here, shutdown failed, so stop monitoring
                    break

                # Check every 30 seconds
                for _ in range(30):
                    if self._timeout_stop_event.wait(1):
                        break

            except Exception as e:
                logging.error(f"Error in timeout monitor: {e}")
                if self._timeout_stop_event.wait(10):
                    break

    def update_activity(self):
        """Update activity time."""
        self.camera_manager.update_activity_time()

    def get_dashboard_access_api(self):
        """API-style dashboard access information."""
        return {
            "success": True,
            "access": get_dashboard_access_info()
        }

    def display_dashboard_qr_api(self):
        """API-style dashboard QR display."""
        access_info = get_dashboard_access_info()
        if not access_info.get("ip_address"):
            return {
                "success": False,
                "message": "No usable LAN IP address found yet",
                "access": access_info
            }

        result = self.eink_display.display_dashboard_qr(access_info)
        return result

    def start_dashboard_qr_monitor(self):
        """Watch for usable network changes for the lifetime of the process."""
        if self._dashboard_qr_monitor_started:
            return

        self._dashboard_qr_monitor_started = True
        self.dashboard_qr_running = True
        self.dashboard_qr_thread = threading.Thread(target=self._dashboard_qr_monitor_loop, daemon=True)
        self.dashboard_qr_thread.start()

    def stop_dashboard_qr_monitor(self):
        self.dashboard_qr_running = False
        if self.dashboard_qr_thread and self.dashboard_qr_thread.is_alive():
            self.dashboard_qr_thread.join(timeout=1)

    def _dashboard_qr_monitor_loop(self):
        last_connection_ip = None
        was_enabled = False
        while self.dashboard_qr_running:
            try:
                system_settings = self.camera_manager.settings.get("system", {})
                enabled = system_settings.get(
                    "show_dashboard_qr_on_wifi_connect",
                    system_settings.get("show_dashboard_qr_on_first_network", True)
                )
                if not enabled:
                    last_connection_ip = get_lan_ip_address()
                    was_enabled = False
                    time.sleep(5)
                    continue

                access_info = get_dashboard_access_info()
                ip_address = access_info.get("ip_address")
                if not ip_address:
                    last_connection_ip = None
                    was_enabled = True
                elif not was_enabled or ip_address != last_connection_ip:
                    if self.eink_display.is_busy():
                        time.sleep(5)
                        continue
                    logging.info(
                        "Wi-Fi connected at %s; displaying dashboard QR",
                        ip_address
                    )
                    with _operation_lock:
                        result = self.display_dashboard_qr_api()
                    if result.get("success"):
                        last_connection_ip = ip_address
                        was_enabled = True
            except Exception as e:
                logging.warning(f"Dashboard QR monitor error: {e}")
            time.sleep(5)

    def _carousel_photo_ids(self):
        """Return selected photo IDs that still exist on disk."""
        configured_ids = self.camera_manager.settings.get("carousel", {}).get("photo_ids", [])
        return [photo_id for photo_id in configured_ids if self.file_manager.get_photo_info(photo_id)]

    @staticmethod
    def _shuffle_carousel_queue(photo_ids):
        """Return a Fisher-Yates shuffle of the selected photo IDs."""
        shuffled = list(photo_ids)
        secure_random = random.SystemRandom()
        for index in range(len(shuffled) - 1, 0, -1):
            swap_index = secure_random.randrange(index + 1)
            shuffled[index], shuffled[swap_index] = shuffled[swap_index], shuffled[index]
        return shuffled

    def _sync_carousel_queue(self):
        """Apply membership changes without resetting the active sequence."""
        selected_ids = self._carousel_photo_ids()
        selected_set = set(selected_ids)
        with self._carousel_lock:
            if not self._carousel_active:
                return

            current_photo_id = self._carousel_current_photo_id
            queue = [photo_id for photo_id in self._carousel_queue if photo_id in selected_set]
            queued_ids = set(queue)
            queue.extend(photo_id for photo_id in selected_ids if photo_id not in queued_ids)
            self._carousel_queue = queue

            if not queue:
                self._carousel_active = False
                if self._carousel_stop_event:
                    self._carousel_stop_event.set()
                self._carousel_advance_event.set()
            elif current_photo_id and current_photo_id not in selected_set:
                self._carousel_advance_event.set()

    def carousel_status_api(self):
        """Return the current carousel state and selected photo count."""
        with self._carousel_lock:
            active = self._carousel_active
        carousel_settings = self.camera_manager.settings.get("carousel", {})
        return {
            "active": active,
            "photo_count": len(self._carousel_photo_ids()),
            "interval_seconds": carousel_settings.get("interval_seconds", 30)
        }

    def start_carousel(self):
        """Start displaying the selected photos in a repeating sequence."""
        photo_ids = self._carousel_photo_ids()
        if not photo_ids:
            return {
                "success": False,
                "error": "no_carousel_photos",
                "message": "Select at least one photo for the carousel"
            }

        with self._carousel_lock:
            if self._carousel_active:
                return {"success": True, "active": True, "message": "Carousel is already running"}
            carousel_settings = self.camera_manager.settings.get("carousel", {})
            queue = (
                self._shuffle_carousel_queue(photo_ids)
                if carousel_settings.get("shuffle", False)
                else list(photo_ids)
            )
            stop_event = threading.Event()
            self._carousel_stop_event = stop_event
            self._carousel_active = True
            self._carousel_queue = queue
            self._carousel_current_photo_id = None
            self._carousel_advance_event.clear()
            self._carousel_thread = threading.Thread(
                target=self._carousel_loop,
                args=(stop_event,),
                daemon=True,
                name="reframe-carousel"
            )
            self._carousel_thread.start()

        self.update_activity()
        return {"success": True, "active": True, "message": "Carousel started"}

    def stop_carousel(self):
        """Stop the carousel worker without interrupting the current display."""
        with self._carousel_lock:
            stop_event = self._carousel_stop_event
            thread = self._carousel_thread
            was_active = self._carousel_active
            self._carousel_active = False
            if stop_event:
                stop_event.set()
            self._carousel_advance_event.set()
            self._carousel_queue = []
            self._carousel_current_photo_id = None

        if thread and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=0.1)
        if was_active:
            self.update_activity()
        return {"success": True, "active": False, "message": "Carousel stopped"}

    def _carousel_loop(self, stop_event):
        try:
            while not stop_event.is_set():
                with self._carousel_lock:
                    if not self._carousel_active or not self._carousel_queue:
                        logging.info("Carousel stopped because no selected photos remain")
                        return
                    photo_id = self._carousel_queue[0]
                    self._carousel_current_photo_id = photo_id
                    self._carousel_advance_event.clear()

                with _operation_lock:
                    if stop_event.is_set():
                        return
                    self.update_activity()
                    result = self.display_photo_api(photo_id)
                if not result.get("success"):
                    logging.warning("Carousel could not display %s: %s", photo_id, result.get("message"))

                interval = self.camera_manager.settings.get("carousel", {}).get("interval_seconds", 30)
                self._carousel_advance_event.wait(max(1, float(interval)))
                if stop_event.is_set():
                    return

                with self._carousel_lock:
                    if not self._carousel_active:
                        return
                    if self._carousel_current_photo_id == photo_id and photo_id in self._carousel_queue:
                        self._carousel_queue.remove(photo_id)
                        self._carousel_queue.append(photo_id)
                    self._carousel_current_photo_id = None
                    self._carousel_advance_event.clear()
        except Exception as error:
            logging.error("Carousel worker failed: %s", error)
        finally:
            with self._carousel_lock:
                if self._carousel_stop_event is stop_event:
                    self._carousel_active = False
                    self._carousel_stop_event = None
                    self._carousel_thread = None
                    self._carousel_queue = []
                    self._carousel_current_photo_id = None
                    self._carousel_advance_event.clear()

    def capture_photo_api(self, fast_mode=False):
        """API-style photo capture with optimized display pipeline.

        Pipeline: capture to memory -> durable original -> durable dither -> display.
        """
        self.stop_carousel()
        temporary_original = None
        temporary_dithered = None
        original_image = None
        dithered_image = None
        try:
            sequence, temporary_original = self.file_manager.reserve_capture(ORIGINAL_CAPTURE_EXTENSION)
            logging.info("Capturing photo sequence %s to temporary path", sequence)

            display_settings = self.camera_manager.settings.get("display", {})
            if display_settings.get("auto_display", True):
                # Hide first-use GPIO/SPI initialization behind capture and
                # image processing instead of delaying the physical refresh.
                self.eink_display.prepare_async()

            pipeline_start = time.monotonic()
            result, original_image = self.camera_manager.capture_image_with_metadata(
                temporary_original, fast_mode=fast_mode
            )

            if result["success"]:
                capture_metadata = dict(original_image.info.get("reframe_capture_metadata", {}))
                resolved_capture_time = resolve_capture_timestamp(capture_metadata)
                photo_metadata, camera_identity, operating_system = self.camera_manager.exif_metadata_context()
                ImageProcessor.save_image_with_metadata(
                    original_image,
                    temporary_original,
                    metadata=capture_metadata,
                    capture_time=resolved_capture_time,
                    photo_metadata=photo_metadata,
                    camera_identity=camera_identity,
                    operating_system=operating_system,
                )
                original_bytes = Path(temporary_original).read_bytes()
                photo_path, photo_id = self.file_manager.canonical_path_for_bytes(
                    sequence,
                    resolved_capture_time,
                    original_bytes,
                    ORIGINAL_CAPTURE_EXTENSION,
                )
                os.replace(temporary_original, photo_path)
                temporary_original = None
                flush_file(photo_path)
                flush_directory(SAVE_PATH)
                logging.info(
                    "Photo captured successfully: %s (%0.fms)",
                    photo_id,
                    (time.monotonic() - pipeline_start) * 1000,
                )

                processing_settings = self.camera_manager.settings.get("processing", {})
                dithered_path = os.path.join(PROCESSED_PATH, f"{photo_id}_dithered.png")
                temporary_dithered = f"{dithered_path}.tmp-{os.getpid()}-{threading.get_ident()}.png"

                resized_image = ImageProcessor.resize_image(original_image)

                # Combined dither + buffer: produces display-ready buffer AND the dithered image
                display_buffer, dithered_image = ImageProcessor.dither_to_display_buffer(
                    resized_image,
                    saturation=processing_settings.get("saturation", 0.6),
                    brightness_factor=processing_settings.get("brightness_factor", 1.1),
                    color_factor=processing_settings.get("color_factor", 1.4),
                    dithering_method=processing_settings.get("dithering_method", "floyd_steinberg"),
                    bayer_size=processing_settings.get("bayer_size", 4),
                    threshold_scale=processing_settings.get("threshold_scale", 1.0),
                    tone_map=processing_settings.get("tone_map", "percentile"),
                    gb_color_palette=processing_settings.get("gb_color_palette", "blue_yellow")
                )

                processing_metadata = ImageProcessor.build_processing_metadata(
                    photo_id,
                    processing_settings.get("dithering_method"),
                    processing_settings.get("gb_color_palette"),
                    processing_settings,
                )
                ImageProcessor.save_dithered_image(
                    dithered_image,
                    temporary_dithered,
                    source_image=original_image,
                    metadata=capture_metadata,
                    dithering_method=processing_settings.get("dithering_method"),
                    gb_color_palette=processing_settings.get("gb_color_palette"),
                    capture_time=resolved_capture_time,
                    processing_metadata=processing_metadata,
                    photo_metadata=photo_metadata,
                    camera_identity=camera_identity,
                    operating_system=operating_system,
                )
                os.replace(temporary_dithered, dithered_path)
                temporary_dithered = None
                flush_file(dithered_path)
                flush_directory(PROCESSED_PATH)

                result.update({
                    "photo_id": photo_id,
                    "original_path": photo_path,
                    "processed_path": dithered_path,
                    "file_size": os.path.getsize(photo_path),
                    "capture_time": resolved_capture_time.isoformat(),
                    "filename": os.path.basename(photo_path),
                })

                if display_settings.get("auto_display", True):
                    if (
                        display_settings.get("interrupt_refresh_on_capture", False)
                        and self.eink_display.is_busy()
                    ):
                        interrupt_action = display_settings.get("refresh_interrupt_action", "reset")
                        interrupt_result = self.eink_display.interrupt_refresh(interrupt_action)
                        if not interrupt_result.get("success"):
                            logging.error("Could not interrupt display refresh: %s", interrupt_result.get("message"))
                    display_result = self.eink_display.display_buffer_async(display_buffer)
                    if not display_result.get("success"):
                        result["display_error"] = display_result.get("message")

                logging.info(
                    "Durable capture button-to-display: %0.fms",
                    (time.monotonic() - pipeline_start) * 1000,
                )

            return result

        except Exception as e:
            logging.error(f"Error in capture_photo_api: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Photo capture failed: {str(e)}"
            }
        finally:
            for temporary_path in (temporary_original, temporary_dithered):
                if temporary_path:
                    try:
                        os.unlink(temporary_path)
                    except FileNotFoundError:
                        pass
            for image in (dithered_image, original_image):
                if image is not None:
                    try:
                        image.close()
                    except Exception:
                        pass

    def display_photo_api(self, photo_id):
        """API-style photo display."""
        return self.eink_display.display_photo_by_id(
            photo_id,
            self.file_manager,
            processing_settings=self.camera_manager.settings.get("processing", {})
        )

    def reprocess_photo_api(self, photo_id, processing_settings=None):
        """API-style photo reprocessing."""
        if processing_settings is None:
            processing_settings = self.camera_manager.settings.get("processing", {})

        return ImageProcessor.reprocess_photo_by_id(
            photo_id,
            processing_settings,
            file_manager=self.file_manager,
        )

    def save_photo_api(
        self,
        photo_id,
        encoded_png=None,
        rotation=0,
        dithering_method="floyd_steinberg",
        gb_color_palette="blue_yellow",
    ):
        return ImageProcessor.save_dithered_preview_by_id(
            photo_id,
            encoded_png=encoded_png,
            rotation=rotation,
            dithering_method=dithering_method,
            gb_color_palette=gb_color_palette,
            file_manager=self.file_manager,
        )

    def list_photos_api(self, page=None, limit=None, carousel_only=False):
        """API-style photo listing, optionally limited to one page."""
        if page is None and limit is None and not carousel_only:
            return self.file_manager.list_all_photos()
        carousel_ids = self.camera_manager.settings.get("carousel", {}).get("photo_ids", [])
        return self.file_manager.list_photo_page(
            page=page or 1,
            limit=limit or 20,
            photo_ids=carousel_ids if carousel_only else None,
        )

    def get_photo_info_api(self, photo_id):
        """API-style photo info retrieval."""
        return self.file_manager.get_photo_info(photo_id)

    def delete_photo_api(self, photo_id):
        """API-style photo deletion."""
        return self.file_manager.delete_photo(photo_id)

    def reload_settings_api(self):
        """API-style settings reload."""
        was_active = self.carousel_status_api()["active"]
        result = self.camera_manager.reload_settings()
        if was_active:
            self._sync_carousel_queue()
        return result

    def apply_settings_api(self, camera_settings=None):
        """API-style settings application."""
        if camera_settings:
            self.camera_manager.apply_camera_settings(camera_settings)
        return {"success": True, "message": "Settings applied"}

    def get_system_status_api(self):
        """API-style system status."""
        import shutil

        try:
            # Get disk usage
            total, used, free = shutil.disk_usage(SAVE_PATH)

            # Count photos
            all_photos = self.list_photos_api()
            original_count = len(all_photos)
            dithered_count = sum(1 for p in all_photos if p["has_dithered"])

            return {
                "success": True,
                "storage": {
                    "total_gb": round(total / (1024**3), 2),
                    "used_gb": round(used / (1024**3), 2),
                    "free_gb": round(free / (1024**3), 2),
                    "usage_percent": round((used / total) * 100, 1)
                },
                "photos": {
                    "original_count": original_count,
                    "dithered_count": dithered_count,
                    "total_count": original_count
                },
                "camera_active": True,
                "display_active": True
            }
        except Exception as e:
            logging.error(f"Error getting system status: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": "Could not get system status"
            }


# Shared camera system and operation lock for both button loop and API
camera_system: Optional[CameraSystem] = None
_operation_lock = threading.Lock()

# FastAPI application exposing hardware control over localhost (lazy initialized)
app = None  # Will be created when API server starts

def _create_fastapi_routes():
    """Create FastAPI app and routes when API server starts."""
    global app
    if not _lazy_import_fastapi():
        return None

    app = FastAPI(title="Reframe Hardware API")
    @app.post("/api/capture")
    def api_capture():
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        camera_system.stop_carousel()
        display_settings = camera_system.camera_manager.settings.get("display", {})
        if (
            camera_system.eink_display.is_busy()
            and not display_settings.get("interrupt_refresh_on_capture", False)
        ):
            return {"success": False, "error": "display_busy", "message": "Display is refreshing, please wait"}
        with _operation_lock:
            camera_system.update_activity()
            result = camera_system.capture_photo_api()
        return result

    @app.get("/api/carousel/status")
    def api_carousel_status():
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        camera_system.update_activity()
        return camera_system.carousel_status_api()

    @app.get("/api/preview/stream")
    async def api_preview_stream(request: Request):
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        camera_manager = camera_system.camera_manager
        try:
            session = camera_manager.open_preview_session(request.query_params.get("client_id"))
        except PreviewSessionBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:
            logging.exception("Could not start live preview")
            raise HTTPException(status_code=503, detail="Camera preview unavailable") from error

        first_frame = await asyncio.to_thread(session.wait_for_frame, 0, 3)
        if first_frame is None:
            camera_manager.close_preview_session(session)
            raise HTTPException(status_code=503, detail="Camera preview unavailable")

        async def frame_stream():
            sequence = 0
            try:
                while not await request.is_disconnected():
                    frame = await asyncio.to_thread(session.wait_for_frame, sequence)
                    if frame is None:
                        break
                    sequence, frame_data = frame
                    jpeg = frame_data["jpeg"] if isinstance(frame_data, dict) else frame_data
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
                        + jpeg
                        + b"\r\n"
                    )
            finally:
                camera_manager.close_preview_session(session)

        return StreamingResponse(
            frame_stream(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
        )

    @app.get("/api/preview/telemetry")
    def api_preview_telemetry(client_id: str = ""):
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        if not client_id:
            raise HTTPException(status_code=400, detail="Preview client ID is required")
        try:
            camera_system.update_activity()
            return camera_system.camera_manager.get_preview_telemetry(client_id)
        except PreviewSessionAccessDenied as error:
            raise HTTPException(status_code=403, detail=str(error)) from error

    @app.post("/api/preview/focus")
    async def api_preview_focus(request: Request):
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        try:
            body = await request.json()
        except Exception as error:
            raise HTTPException(status_code=400, detail="Focus body must be valid JSON") from error
        if not isinstance(body, dict) or not body.get("client_id"):
            raise HTTPException(status_code=400, detail="Preview client ID is required")

        client_id = body["client_id"]
        try:
            camera_system.camera_manager.get_preview_telemetry(client_id)
        except PreviewSessionAccessDenied as error:
            raise HTTPException(status_code=403, detail=str(error)) from error

        action = body.get("action")
        try:
            camera_system.update_activity()
            if action == "set_mode":
                mode = {"manual": 0, "auto": 1, "continuous": 2}.get(body.get("mode"))
                if mode is None:
                    raise FocusOperationError("Focus mode must be manual, auto, or continuous")
                return camera_system.camera_manager.set_focus_mode(mode)
            if action == "focus_center":
                return camera_system.camera_manager.focus_center()
            if action == "set_position":
                return camera_system.camera_manager.set_focus_position(body.get("lens_position"))
            raise FocusOperationError("Unknown focus action")
        except FocusOperationBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except FocusOperationError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/preview/controls")
    async def api_preview_controls(request: Request):
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        try:
            body = await request.json()
        except Exception as error:
            raise HTTPException(status_code=400, detail="Preview controls body must be valid JSON") from error
        if not isinstance(body, dict) or not body.get("client_id"):
            raise HTTPException(status_code=400, detail="Preview client ID is required")

        client_id = body["client_id"]
        try:
            camera_system.camera_manager.get_preview_telemetry(client_id)
        except PreviewSessionAccessDenied as error:
            raise HTTPException(status_code=403, detail=str(error)) from error

        action = body.get("action")
        try:
            camera_system.update_activity()
            if action == "set_exposure_value":
                return camera_system.camera_manager.set_exposure_value(body.get("exposure_value"))
            if action == "set_exposure_mode":
                return camera_system.camera_manager.set_exposure_mode(
                    body.get("mode"),
                    body.get("exposure_time_us"),
                    body.get("analogue_gain"),
                )
            if action == "set_white_balance":
                return camera_system.camera_manager.set_white_balance(
                    body.get("mode"),
                    body.get("preset", "daylight"),
                    body.get("red_gain"),
                    body.get("blue_gain"),
                )
            raise PreviewControlError("Unknown preview control action")
        except FocusOperationBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except PreviewControlError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/preview/stop")
    def api_preview_stop(client_id: str = ""):
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        if not client_id:
            raise HTTPException(status_code=400, detail="Preview client ID is required")
        stopped = camera_system.camera_manager.close_preview_session_for_owner(client_id)
        return {"success": stopped}

    @app.post("/api/carousel/start")
    def api_carousel_start():
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        with _operation_lock:
            result = camera_system.start_carousel()
        if not result.get("success"):
            raise HTTPException(status_code=409, detail=result.get("message", "Could not start carousel"))
        return result

    @app.post("/api/carousel/stop")
    def api_carousel_stop():
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        return camera_system.stop_carousel()

    @app.post("/api/display/clear")
    def api_clear_display():
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        with _operation_lock:
            return camera_system.eink_display.clear_display()

    @app.post("/api/display/force-reset")
    def api_force_reset_display():
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        with _operation_lock:
            camera_system.update_activity()
            return camera_system.eink_display.force_reset()

    @app.post("/api/display/force-stop")
    def api_force_stop_display():
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        with _operation_lock:
            camera_system.update_activity()
            return camera_system.eink_display.force_stop()

    @app.post("/api/display/redraw")
    def api_redraw_display():
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        with _operation_lock:
            camera_system.update_activity()
            return camera_system.eink_display.redraw_last_display()

    @app.post("/api/display/{photo_id}")
    def api_display(photo_id: str):
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        try:
            with _operation_lock:
                camera_system.update_activity()
                result = camera_system.display_photo_api(photo_id)
            return result
        except Exception as e:
            logging.error(f"Error displaying photo {photo_id}: {e}")
            return {"success": False, "error": str(e), "message": f"Failed to display photo: {str(e)}"}

    @app.post("/api/reprocess/{photo_id}")
    async def api_reprocess(photo_id: str, request: Request):
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        try:
            body: Dict[str, Any] = await request.json()
            processing_settings = body.get("processing_settings") if isinstance(body, dict) else None
        except Exception:
            processing_settings = None
        with _operation_lock:
            result = camera_system.reprocess_photo_api(photo_id, processing_settings)
        return result

    @app.post("/api/photos/{photo_id}/preview")
    def api_preview_photo(photo_id: str, body: Dict[str, Any]):
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        mode = body.get("dithering_method")
        if mode not in ("floyd_steinberg", "ordered", "bayer_natural_pair", "gb-default", "gb-default-color"):
            raise HTTPException(status_code=400, detail="Unsupported dithering mode")
        palette = body.get("gb_color_palette", "blue_yellow")
        if palette not in ("blue_yellow", "green_yellow", "red_yellow", "blue_red", "blue_green"):
            raise HTTPException(status_code=400, detail="Unsupported color palette")
        with _operation_lock:
            photo = camera_system.get_photo_info_api(photo_id)
            if not photo:
                raise HTTPException(status_code=404, detail="Photo not found")
            camera_system.update_activity()
            settings = dict(camera_system.camera_manager.settings.get("processing", {}))
            settings.update(dithering_method=mode, gb_color_palette=palette)
            image = ImageProcessor.render_photo_with_settings(photo["original_path"], settings)
            output = BytesIO()
            image.save(output, format="PNG")
        return {"png": base64.b64encode(output.getvalue()).decode("ascii")}

    @app.post("/api/photos/{photo_id}/save")
    def api_save_photo(photo_id: str, body: Dict[str, Any]):
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        encoded_png = body.get("png")
        if encoded_png is not None and (not isinstance(encoded_png, str) or len(encoded_png) > 2_000_000):
            raise HTTPException(status_code=400, detail="Invalid preview image")
        rotation = body.get("rotation", 0)
        if isinstance(rotation, bool) or not isinstance(rotation, int) or rotation not in range(4):
            raise HTTPException(status_code=400, detail="Invalid image rotation")
        dithering_method = body.get("dithering_method", "floyd_steinberg")
        if dithering_method not in ("floyd_steinberg", "ordered", "bayer_natural_pair", "gb-default", "gb-default-color"):
            raise HTTPException(status_code=400, detail="Unsupported dithering mode")
        gb_color_palette = body.get("gb_color_palette", "blue_yellow")
        if gb_color_palette not in ("blue_yellow", "green_yellow", "red_yellow", "blue_red", "blue_green"):
            raise HTTPException(status_code=400, detail="Unsupported color palette")
        with _operation_lock:
            camera_system.update_activity()
            result = camera_system.save_photo_api(
                photo_id,
                encoded_png=encoded_png,
                rotation=rotation,
                dithering_method=dithering_method,
                gb_color_palette=gb_color_palette,
            )
        if not result.get("success"):
            raise HTTPException(status_code=404, detail=result.get("message", "Could not save photo"))
        result["photo"] = camera_system.get_photo_info_api(photo_id)
        return result

    @app.post("/api/preview/display")
    def api_display_preview(body: Dict[str, Any]):
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        encoded = body.get("png")
        if not isinstance(encoded, str) or len(encoded) > 2_000_000:
            raise HTTPException(status_code=400, detail="Invalid preview image")
        try:
            Image, _ = _lazy_import_pil()
            with Image.open(BytesIO(base64.b64decode(encoded, validate=True))) as image:
                if image.format != "PNG" or image.size != DISPLAY_IMAGE_SIZE:
                    raise ValueError("Preview must be a display-sized PNG")
                buffer = ImageProcessor.img2buffer(image)
        except Exception as error:
            raise HTTPException(status_code=400, detail="Invalid preview image") from error
        with _operation_lock:
            camera_system.update_activity()
            result = camera_system.eink_display.display_buffer_async(buffer)
        if result.get("success"):
            result["message"] = "Preview sent to screen"
        return result

    @app.get("/api/photos")
    def api_list_photos(
        page: Optional[int] = None,
        limit: Optional[int] = None,
        carousel_only: bool = False,
    ):
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        camera_system.update_activity()
        try:
            return camera_system.list_photos_api(page, limit, carousel_only)
        except Exception as e:
            logging.warning(f"Error listing photos (may be mid-processing): {e}")
            return []  # Return empty list; dashboard will retry

    @app.get("/api/photos/{photo_id}")
    def api_get_photo(photo_id: str):
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        try:
            info = camera_system.get_photo_info_api(photo_id)
            if not info:
                raise HTTPException(status_code=404, detail="Photo not found")
            return info
        except HTTPException:
            raise
        except Exception as e:
            logging.warning(f"Error getting photo info for {photo_id}: {e}")
            raise HTTPException(status_code=404, detail="Photo not found or being processed")

    @app.delete("/api/photos/{photo_id}")
    def api_delete_photo(photo_id: str):
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        with _operation_lock:
            return camera_system.delete_photo_api(photo_id)

    @app.post("/api/settings/reload")
    def api_reload_settings():
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        with _operation_lock:
            result = camera_system.reload_settings_api()
            # Restart timeout monitor if settings changed
            camera_system.stop_timeout_monitor()
            camera_system.start_timeout_monitor()
            return result

    @app.post("/api/settings/apply")
    async def api_apply_settings(request: Request):
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        try:
            body: Dict[str, Any] = await request.json()
            camera_settings = body.get("camera_settings") if isinstance(body, dict) else None
        except Exception:
            camera_settings = None
        with _operation_lock:
            return camera_system.apply_settings_api(camera_settings)

    @app.get("/api/status")
    def api_status():
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        try:
            return camera_system.get_system_status_api()
        except Exception as e:
            logging.warning(f"Error getting system status: {e}")
            return {"success": False, "error": str(e), "message": "Could not get system status"}

    @app.get("/api/dashboard/access")
    def api_dashboard_access():
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        return camera_system.get_dashboard_access_api()

    @app.post("/api/dashboard/qr")
    def api_dashboard_qr():
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")
        with _operation_lock:
            return camera_system.display_dashboard_qr_api()

    @app.post("/api/timeout/reset")
    def api_reset_timeout():
        """Reset the timeout timer (extend the timeout period)."""
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")

        camera_system.update_activity()
        return {"status": "success", "message": "Timeout timer reset"}

    @app.get("/api/timeout/status")
    def api_get_timeout_status():
        """Get current timeout status and remaining time."""
        global camera_system
        if camera_system is None:
            raise HTTPException(status_code=503, detail="Camera system not initialized")

        timeout_enabled = camera_system.camera_manager.is_timeout_enabled()
        timeout_minutes = camera_system.camera_manager.get_timeout_minutes()

        if timeout_enabled:
            elapsed = camera_system.camera_manager.get_inactivity_seconds()
            remaining_seconds = max(0, (timeout_minutes * 60) - elapsed)
            remaining_minutes = remaining_seconds / 60
        else:
            remaining_seconds = None
            remaining_minutes = None

        return {
            "timeout_enabled": timeout_enabled,
            "timeout_minutes": timeout_minutes,
            "remaining_seconds": remaining_seconds,
            "remaining_minutes": remaining_minutes,
            "last_activity": time.time() - camera_system.camera_manager.get_inactivity_seconds()
        }

    return app


def _start_api_server_in_background(host: str = "127.0.0.1", port: int = 8077):

    app = _create_fastapi_routes()
    if app is None:
        logging.warning("FastAPI/uvicorn not available; hardware API will not be started")
        return
    def _run():
        try:
            uvicorn.run(app, host=host, port=port, log_level="warning")
        except Exception as e:
            logging.error(f"Failed to start API server: {e}")
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


def main():
    global camera_system

    startup_display = EInkDisplay()
    startup_settings_path = os.path.join(BASE_PATH, "settings.json")
    if _auto_display_enabled(startup_settings_path):
        startup_display.prepare_async()

    # Python/Picamera2 imports happen before this point, overlapping the cold
    # boot wait for the camera subdevice. HDR is still applied before the
    # Picamera2 constructor opens the camera.
    _enable_camera_hdr()
    camera_system = CameraSystem(eink_display=startup_display)
    logging.info("Camera system initialized")
    logging.info("Taking startup photo...")
    startup_status = "Camera initialized; startup capture failed"
    try:
        with _operation_lock:
            result = camera_system.capture_photo_api(fast_mode=True)

        if result.get("success"):
            logging.info("Startup photo captured: %s", result.get("photo_id", "unknown"))
            if camera_system.camera_manager.settings.get("display", {}).get("auto_display", True):
                logging.info("Startup photo sent to display")
            logging.info("System ready")
            startup_status = "Startup photo dispatched"

            # Ensure activity time is updated before starting timeout monitor
            camera_system.update_activity()
            camera_system.start_timeout_monitor_deferred()
        else:
            logging.warning("Failed to capture startup photo: %s", result.get("message", "unknown error"))
    except Exception as e:
        logging.error("Error taking startup photo: %s", e)
    finally:
        # reframe.service is Type=notify. Dashboard startup waits for this, but
        # does not wait for the e-ink panel's long physical refresh.
        _notify_systemd_ready(startup_status)

    # ═══════════════════════════════════════════════════════════════
    # HARDWARE: Button — PiSugar 3 via I2C
    # The PiSugar 3 exposes a button register at I2C address 0x57.
    # This is a direct hardware read (no PiSugar library needed).
    # To use a different button/trigger, replace is_power_button_pressed().
    # Long press (≥2s) is ignored here — PiSugar handles shutdown natively.
    # See: https://github.com/PiSugar/PiSugar/wiki/PiSugar-3-I2C-Datasheet
    # ═══════════════════════════════════════════════════════════════
    I2C_ADDRESS = 0x57
    BUTTON_REGISTER = 0x02
    import smbus2
    bus = smbus2.SMBus(1)

    def is_power_button_pressed():
        try:
            reg_val = bus.read_byte_data(I2C_ADDRESS, BUTTON_REGISTER)
            return bool(reg_val & 0x01)  # Check the least significant bit
        except Exception as e:
            logging.error("Failed to read I2C: %s", e)
            return False

    prev_state = False
    button_press_start_time = None
    LONG_PRESS_THRESHOLD = 2.0  # 2 seconds threshold for long press

    # Start API server in background
    try:
        port = int(os.environ.get("REFRAME_API_PORT", "8077"))
    except Exception:
        port = 8077
    _start_api_server_in_background(host="127.0.0.1", port=port)
    camera_system.start_dashboard_qr_monitor()

    logging.info("System initialized. API server running. Waiting for button press to capture photo...")
    logging.info(f"Button protection: Long press (>={LONG_PRESS_THRESHOLD}s) will not trigger photo capture")

    try:
        while True:
            current_state = is_power_button_pressed()

            # Button press started
            if current_state and not prev_state:
                button_press_start_time = time.monotonic()
                logging.info("Button pressed - monitoring for long press protection...")

            # Button released
            elif not current_state and prev_state:
                if button_press_start_time is not None:
                    press_duration = time.monotonic() - button_press_start_time

                    if press_duration < LONG_PRESS_THRESHOLD:
                        camera_system.stop_carousel()
                        # Block captures while display is mid-refresh to avoid
                        # invisible captures with no visual feedback
                        display_settings = camera_system.camera_manager.settings.get("display", {})
                        if (
                            camera_system.eink_display.is_busy()
                            and not display_settings.get("interrupt_refresh_on_capture", False)
                        ):
                            logging.info(f"Short press detected ({press_duration:.1f}s) - display busy, ignoring")
                        else:
                            logging.info(f"Short press detected ({press_duration:.1f}s) - capturing photo...")
                            with _operation_lock:
                                result = camera_system.capture_photo_api()
                            if result.get("success"):
                                logging.info("Photo captured%s.", " and sent to display" if camera_system.camera_manager.settings.get("display", {}).get("auto_display", True) else "")
                            else:
                                logging.error("Capture failed: %s", result.get("message", "unknown error"))
                    else:
                        logging.info(f"Long press detected ({press_duration:.1f}s) - ignoring for photo capture")

                    button_press_start_time = None

            prev_state = current_state
            sleep(BUTTON_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logging.info("Program interrupted by user. Exiting...")
    finally:
        bus.close()
        try:
            # Stop timeout monitor and put display to sleep if initialized inside CameraSystem
            if camera_system:
                camera_system.stop_timeout_monitor()
                camera_system.stop_dashboard_qr_monitor()
                camera_system.camera_manager.stop_preview()
                if camera_system.eink_display:
                    camera_system.eink_display.sleep()
        except Exception:
            pass


def demo_api_usage():
    """Demonstrate the new API-style functionality."""
    print("Initializing Camera System...")
    camera_system = CameraSystem()

    print("\n=== System Status ===")
    status = camera_system.get_system_status_api()
    print(f"Storage: {status['storage']['free_gb']}GB free")
    print(f"Photos: {status['photos']['total_count']} total")

    print("\n=== Capturing Photo ===")
    capture_result = camera_system.capture_photo_api()
    if capture_result["success"]:
        photo_id = capture_result["photo_id"]
        print(f"Captured photo: {photo_id}")

        print("\n=== Reprocessing with Different Settings ===")
        new_settings = {
            "dithering_method": "ordered",
            "bayer_size": 8,
            "saturation": 0.8
        }
        reprocess_result = camera_system.reprocess_photo_api(photo_id, new_settings)
        print(f"Reprocessing result: {reprocess_result['success']}")

        print("\n=== Displaying Photo ===")
        display_result = camera_system.display_photo_api(photo_id)
        print(f"Display result: {display_result['success']}")

    print("\n=== Listing All Photos ===")
    photos = camera_system.list_photos_api()
    print(f"Found {len(photos)} photos")
    for photo in photos[:3]:  # Show first 3
        print(f"  {photo['id']}: {photo['filename']} ({'dithered' if photo['has_dithered'] else 'original only'})")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        # Run the API demo
        try:
            demo_api_usage()
        except KeyboardInterrupt:
            print("\nDemo interrupted. Exiting...")
        except Exception as e:
            print(f"Demo error: {e}")
    else:
        # Run the original main loop
        try:
            main()
        except KeyboardInterrupt:
            logging.info("Program interrupted. Exiting...")
            epd4in0e.epdconfig.module_exit(cleanup=True)
