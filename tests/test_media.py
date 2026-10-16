"""Media validation against LinkedIn's limits, and preview generation."""

import shutil
import subprocess

import pytest

from linkedin_agent.media import MediaError, build_previews, validate_media

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20
JPG = b"\xff\xd8\xff\xe0" + b"\0" * 20


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_valid_photos(tmp_path):
    items = validate_media([write(tmp_path, "a.png", PNG), write(tmp_path, "b.jpg", JPG)], ["alt a"])
    assert [i.kind for i in items] == ["image", "image"]
    assert items[0].alt_text == "alt a" and items[1].alt_text == ""


def test_rejects_renamed_file(tmp_path):
    with pytest.raises(MediaError, match="does not match"):
        validate_media([write(tmp_path, "fake.png", JPG)])


def test_rejects_unsupported_type(tmp_path):
    with pytest.raises(MediaError, match="unsupported"):
        validate_media([write(tmp_path, "doc.pdf", b"%PDF")])


def test_rejects_missing_file(tmp_path):
    with pytest.raises(MediaError, match="not found"):
        validate_media([tmp_path / "nope.jpg"])


def test_rejects_mixed_photo_and_video(tmp_path):
    video = write(tmp_path, "v.mp4", b"\0\0\0\x18ftypmp42" + b"\0" * 80_000)
    with pytest.raises(MediaError, match="not both"):
        validate_media([write(tmp_path, "a.png", PNG), video])


def test_rejects_too_small_video(tmp_path):
    with pytest.raises(MediaError, match="75 KB"):
        validate_media([write(tmp_path, "v.mp4", b"\0\0\0\x18ftypmp42")])


def test_rejects_more_than_20_photos(tmp_path):
    files = [write(tmp_path, f"{i}.png", PNG) for i in range(21)]
    with pytest.raises(MediaError, match="At most 20"):
        validate_media(files)


def test_small_photo_preview_is_base64(tmp_path):
    previews = build_previews(validate_media([write(tmp_path, "a.png", PNG)]))
    assert len(previews) == 1 and previews[0].media_type == "image/png"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_video_frames_extracted(tmp_path):
    video = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=duration=6:size=640x360:rate=25",
         "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )
    items = validate_media([video]) if video.stat().st_size >= 75 * 1024 else None
    if items is None:
        pytest.skip("generated clip smaller than LinkedIn's 75 KB minimum")
    previews = build_previews(items)
    assert 1 <= len(previews) <= 3 and all(p.media_type == "image/jpeg" for p in previews)
