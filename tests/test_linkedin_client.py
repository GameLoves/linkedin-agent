"""LinkedIn REST client: payload shapes, uploads, retries and duplicate safety."""

import pytest
from conftest import FakeResponse, FakeSession

from linkedin_agent.linkedin.client import LinkedInClient, LinkedInError


def make_client(settings, token, routes):
    session = FakeSession(routes)
    return LinkedInClient(settings, token, session=session, sleep=lambda s: None), session


def test_text_post_payload_and_headers(settings, token):
    client, session = make_client(settings, token, [("POST", "/rest/posts", FakeResponse(201, {}, {"x-restli-id": "urn:li:share:9"}))])
    assert client.create_post("hello") == "urn:li:share:9"
    method, url, kw = session.calls[0]
    assert kw["headers"]["Linkedin-Version"] == "202609"
    assert kw["headers"]["X-Restli-Protocol-Version"] == "2.0.0"
    assert kw["json"]["author"] == "urn:li:person:abc"
    assert kw["json"]["lifecycleState"] == "PUBLISHED"
    assert "content" not in kw["json"]


def test_multi_image_payload(settings, token):
    client, session = make_client(settings, token, [("POST", "/rest/posts", FakeResponse(201, {}, {"x-restli-id": "urn:li:share:1"}))])
    client.create_post("x", image_urns=["urn:li:image:1", "urn:li:image:2"], alt_texts=["a", ""])
    content = session.calls[0][2]["json"]["content"]
    assert content == {"multiImage": {"images": [{"id": "urn:li:image:1", "altText": "a"}, {"id": "urn:li:image:2"}]}}


def test_image_upload_flow(settings, token, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 10)
    client, session = make_client(settings, token, [
        ("POST", "images?action=initializeUpload", FakeResponse(200, {"value": {"uploadUrl": "https://up/img", "image": "urn:li:image:X"}})),
        ("PUT", "https://up/img", FakeResponse(201)),
    ])
    assert client.upload_image(img) == "urn:li:image:X"
    assert session.calls[1][2]["data"].startswith(b"\x89PNG")


def test_video_upload_parts_and_finalize(settings, token, tmp_path):
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"A" * 10 + b"B" * 6)
    init = {"value": {"video": "urn:li:video:V", "uploadToken": "t", "uploadInstructions": [
        {"uploadUrl": "https://up/1", "firstByte": 0, "lastByte": 9},
        {"uploadUrl": "https://up/2", "firstByte": 10, "lastByte": 15},
    ]}}
    client, session = make_client(settings, token, [
        ("POST", "videos?action=initializeUpload", FakeResponse(200, init)),
        ("PUT", "https://up/1", FakeResponse(200, None, {"etag": "e1"})),
        ("PUT", "https://up/2", FakeResponse(200, None, {"etag": "e2"})),
        ("POST", "videos?action=finalizeUpload", FakeResponse(200)),
        ("GET", "/rest/videos/urn", FakeResponse(200, {"status": "PROCESSING"})),
        ("GET", "/rest/videos/urn", FakeResponse(200, {"status": "AVAILABLE"})),
    ])
    assert client.upload_video(vid) == "urn:li:video:V"
    assert session.calls[1][2]["data"] == b"A" * 10 and session.calls[2][2]["data"] == b"B" * 6
    final = session.calls[3][2]["json"]["finalizeUploadRequest"]
    assert final == {"video": "urn:li:video:V", "uploadToken": "t", "uploadedPartIds": ["e1", "e2"]}


def test_video_processing_failure(settings, token, tmp_path):
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"A" * 4)
    init = {"value": {"video": "urn:li:video:V", "uploadInstructions": [{"uploadUrl": "https://up/1", "firstByte": 0, "lastByte": 3}]}}
    client, _ = make_client(settings, token, [
        ("POST", "initializeUpload", FakeResponse(200, init)),
        ("PUT", "https://up/1", FakeResponse(200, None, {"etag": "e1"})),
        ("POST", "finalizeUpload", FakeResponse(200)),
        ("GET", "/rest/videos/", FakeResponse(200, {"status": "PROCESSING_FAILED", "processingFailureReason": "codec"})),
    ])
    with pytest.raises(LinkedInError, match="codec"):
        client.upload_video(vid)


def test_create_post_not_retried_on_5xx(settings, token):
    """A 500 may still have created the post, so never retry automatically."""
    client, session = make_client(settings, token, [("POST", "/rest/posts", FakeResponse(500, {"message": "boom"}))])
    with pytest.raises(LinkedInError) as info:
        client.create_post("x")
    assert info.value.maybe_created is True
    assert len(session.calls) == 1


def test_create_post_retried_on_429(settings, token):
    client, session = make_client(settings, token, [
        ("POST", "/rest/posts", FakeResponse(429, {"message": "slow down"}, {"Retry-After": "1"})),
        ("POST", "/rest/posts", FakeResponse(201, {}, {"x-restli-id": "urn:li:share:2"})),
    ])
    assert client.create_post("x") == "urn:li:share:2"
    assert len(session.calls) == 2


def test_upload_retries_5xx_then_succeeds(settings, token, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    client, _ = make_client(settings, token, [
        ("POST", "initializeUpload", FakeResponse(503, {"message": "busy"})),
        ("POST", "initializeUpload", FakeResponse(200, {"value": {"uploadUrl": "https://up/i", "image": "urn:li:image:Y"}})),
        ("PUT", "https://up/i", FakeResponse(201)),
    ])
    assert client.upload_image(img) == "urn:li:image:Y"


def test_401_gives_login_hint(settings, token):
    client, _ = make_client(settings, token, [("POST", "/rest/posts", FakeResponse(401, {"message": "Invalid access token"}))])
    with pytest.raises(LinkedInError, match="linkedin-agent login"):
        client.create_post("x")
