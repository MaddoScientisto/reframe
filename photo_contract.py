"""Shared photo identity, timestamp, and download filename rules."""

import hashlib
import os
import re
from datetime import datetime, timezone


CANONICAL_FILENAME_PATTERN = re.compile(
    r"^(?P<sequence>\d+)_(?P<date>\d{8}_\d{6})_(?P<photo_id>[0-9a-f]{8,64})$"
)


def short_content_hash(content, length=8):
    """Return the lowercase content hash used as the stable photo identity."""
    return hashlib.sha256(bytes(content)).hexdigest()[:length]


def safe_dither_method_token(method):
    """Convert a dither label into a lowercase, path-safe download token."""
    value = str(method or "").encode("ascii", "ignore").decode("ascii").lower()
    token = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return token or "unknown"


def resolve_capture_timestamp(metadata=None, fallback=None):
    """Resolve one plausible capture timestamp, retaining a known offset."""
    metadata = metadata if isinstance(metadata, dict) else {}
    candidate = metadata.get("CaptureTimestamp") or metadata.get("capture_time")
    if candidate is None:
        candidate = metadata.get("DateTimeOriginal") or metadata.get("datetime_original")

    resolved = None
    if isinstance(candidate, datetime):
        resolved = candidate
    elif isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
        try:
            resolved = datetime.fromtimestamp(candidate, timezone.utc)
        except (OverflowError, OSError, ValueError):
            resolved = None
    elif isinstance(candidate, str):
        text = candidate.strip()
        try:
            resolved = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for format_string in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    resolved = datetime.strptime(text, format_string)
                    break
                except ValueError:
                    continue

    if resolved is None:
        resolved = fallback or datetime.now().astimezone()
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=datetime.now().astimezone().tzinfo)
    if resolved.year < 2000 or resolved.year > 2100:
        raise ValueError("Capture timestamp is outside the supported range")
    return resolved


def canonical_original_filename(sequence, capture_time, content, extension="jpg", hash_length=8):
    """Build the canonical original filename and stable photo ID."""
    resolved = resolve_capture_timestamp({}, capture_time)
    photo_id = short_content_hash(content, hash_length)
    timestamp = resolved.astimezone().strftime("%Y%m%d_%H%M%S")
    filename = f"{int(sequence):05d}_{timestamp}_{photo_id}.{extension.lstrip('.').lower()}"
    return filename, photo_id


def dithered_download_filename(original_filename, method, upscale_2x=False):
    """Build a download-only filename from the original basename and method."""
    original_base = os.path.splitext(os.path.basename(original_filename))[0]
    safe_base = re.sub(r"[^a-z0-9]+", "_", original_base.lower()).strip("_") or "photo"
    suffix = "_x2" if upscale_2x else ""
    return f"{safe_base}_{safe_dither_method_token(method)}{suffix}.png"
