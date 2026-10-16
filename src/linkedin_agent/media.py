"""Validation and preparation of user-supplied photos and videos.

Two jobs:
1. Reject files LinkedIn will not accept *before* any upload starts
   (type, size, count, mixing photos with video).
2. Build small JPEG previews that Claude can look at. Claude reads images
   directly; for videos we extract a few still frames with ffmpeg, because
   the Claude API does not accept video input.
"""

from __future__ import annotations

import base64
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .store import MediaItem

log = logging.getLogger(__name__)

# LinkedIn limits (Images / Videos / MultiImage API docs, checked 2026-10).
IMAGE_EXTENSIONS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif"}
VIDEO_EXTENSIONS = {".mp4": "video/mp4"}
MAX_IMAGES = 20
MAX_VIDEO_BYTES = 500 * 1024 * 1024
MIN_VIDEO_BYTES = 75 * 1024
# Claude accepts images up to about 5 MB each; larger photos get a preview.
CLAUDE_MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Leading "magic" bytes, so a renamed file is caught early.
_SIGNATURES = {
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/gif": [b"GIF87a", b"GIF89a"],
}


class MediaError(ValueError):
    """Raised when supplied media cannot be posted to LinkedIn."""


def _check_signature(path: Path, mime: str) -> None:
    """Verify the file content matches its extension (images and MP4)."""
    with path.open("rb") as fh:
        head = fh.read(12)
    if mime == "video/mp4":
        # MP4 files carry an "ftyp" box at byte offset 4.
        if head[4:8] != b"ftyp":
            raise MediaError(f"{path.name} does not look like a real MP4 file")
        return
    if not any(head.startswith(sig) for sig in _SIGNATURES[mime]):
        raise MediaError(f"{path.name} content does not match its {path.suffix} extension")


def validate_media(paths: list[str | Path], alt_texts: list[str] | None = None) -> list[MediaItem]:
    """Validate files and convert them to MediaItem records.

    Rules (from LinkedIn's API): 1–20 JPG/PNG/GIF images, OR exactly one
    MP4 video between 75 KB and 500 MB. Photos and video cannot be mixed.

    Args:
        paths: Files given by the user.
        alt_texts: Optional accessibility text per file (same order).

    Returns:
        MediaItem list with absolute paths.

    Raises:
        MediaError: describing the first problem found.
    """
    if not paths:
        raise MediaError("No media files given")
    alt_texts = alt_texts or []
    items: list[MediaItem] = []
    for index, raw in enumerate(paths):
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise MediaError(f"File not found: {raw}")
        ext = path.suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            _check_signature(path, IMAGE_EXTENSIONS[ext])
            kind = "image"
        elif ext in VIDEO_EXTENSIONS:
            size = path.stat().st_size
            if not MIN_VIDEO_BYTES <= size <= MAX_VIDEO_BYTES:
                raise MediaError(f"{path.name}: LinkedIn videos must be 75 KB to 500 MB (got {size:,} bytes)")
            _check_signature(path, "video/mp4")
            kind = "video"
        else:
            raise MediaError(
                f"{path.name}: unsupported type. Use JPG, PNG or GIF photos, or an MP4 video."
            )
        alt = alt_texts[index] if index < len(alt_texts) else ""
        items.append(MediaItem(path=str(path), kind=kind, alt_text=alt))

    kinds = {m.kind for m in items}
    if kinds == {"image", "video"}:
        raise MediaError("A LinkedIn post can contain photos or one video, not both")
    if "video" in kinds and len(items) > 1:
        raise MediaError("Only one video per post is supported")
    if len(items) > MAX_IMAGES:
        raise MediaError(f"At most {MAX_IMAGES} photos per post (got {len(items)})")
    return items


@dataclass
class Preview:
    """An image Claude can see: base64 data plus its MIME type."""

    media_type: str
    data_b64: str
    label: str


def _ffmpeg() -> str | None:
    """Path to ffmpeg if installed, else None."""
    return shutil.which("ffmpeg")


def _ffmpeg_jpeg(args: list[str], out: Path) -> bytes | None:
    """Run ffmpeg to produce one JPEG; returns its bytes or None on failure."""
    try:
        subprocess.run(
            [_ffmpeg(), "-loglevel", "error", "-y", *args, str(out)],
            check=True,
            timeout=120,
            capture_output=True,
        )
        return out.read_bytes() if out.exists() else None
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("ffmpeg failed: %s", exc)
        return None


def build_previews(items: list[MediaItem], max_video_frames: int = 3) -> list[Preview]:
    """Create Claude-readable previews for photos and video frames.

    Photos under 5 MB are sent as-is. Larger photos and video frames are
    converted to 1280px-wide JPEGs with ffmpeg when it is installed; when it
    is not, those items are skipped and Claude writes from your notes alone.
    """
    previews: list[Preview] = []
    with tempfile.TemporaryDirectory(prefix="li-agent-") as tmp:
        tmp_dir = Path(tmp)
        for n, item in enumerate(items, start=1):
            path = Path(item.path)
            if item.kind == "image":
                mime = IMAGE_EXTENSIONS[path.suffix.lower()]
                if path.stat().st_size <= CLAUDE_MAX_IMAGE_BYTES:
                    previews.append(Preview(mime, base64.standard_b64encode(path.read_bytes()).decode(), f"Photo {n}"))
                elif _ffmpeg():
                    data = _ffmpeg_jpeg(["-i", str(path), "-vf", "scale='min(1280,iw)':-2", "-frames:v", "1"], tmp_dir / f"p{n}.jpg")
                    if data:
                        previews.append(Preview("image/jpeg", base64.standard_b64encode(data).decode(), f"Photo {n}"))
                else:
                    log.warning("%s is over 5 MB and ffmpeg is missing; Claude will rely on your notes.", path.name)
            else:
                if not _ffmpeg():
                    log.warning("ffmpeg not installed; Claude cannot see the video and will use your notes only.")
                    continue
                # Grab frames at 1s, 5s, 9s... A seek past the end of a short
                # clip yields no frame and is simply skipped.
                for f in range(max_video_frames):
                    seconds = f * 4 + 1
                    data = _ffmpeg_jpeg(
                        ["-ss", str(seconds), "-i", str(path), "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2"],
                        tmp_dir / f"v{f}.jpg",
                    )
                    if data:
                        previews.append(Preview("image/jpeg", base64.standard_b64encode(data).decode(), f"Video frame at {seconds}s"))
    return previews
