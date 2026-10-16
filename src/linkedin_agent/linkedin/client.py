"""Client for LinkedIn's versioned REST API (Posts, Images, Videos).

Only official, documented endpoints are used; there is no scraping or
browser automation, which LinkedIn's terms forbid.

Retry policy:
- Uploads and status reads are retried on 429/5xx/network errors with
  exponential backoff (they are safe to repeat).
- Creating the post is retried ONLY on 429 (request rejected, nothing
  created). A 5xx or timeout might have created the post, so it is reported
  as a failure for a human to check, preventing duplicate posts.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import quote

import requests

from ..config import Settings
from .oauth import LinkedInToken

log = logging.getLogger(__name__)

API_BASE = "https://api.linkedin.com/rest"
TIMEOUT_SECONDS = 60
UPLOAD_TIMEOUT_SECONDS = 300
MAX_ATTEMPTS = 4


class LinkedInError(RuntimeError):
    """A LinkedIn API call failed.

    Attributes:
        status: HTTP status code, or None for network errors.
        maybe_created: True when the post may exist despite the error.
    """

    def __init__(self, message: str, status: int | None = None, maybe_created: bool = False):
        super().__init__(message)
        self.status = status
        self.maybe_created = maybe_created


def post_url(post_urn: str) -> str:
    """Public web address for a post URN."""
    return f"https://www.linkedin.com/feed/update/{post_urn}/"


def _error_detail(resp: requests.Response) -> str:
    """Short, human-readable error text from a LinkedIn error response."""
    try:
        data = resp.json()
        return data.get("message") or data.get("code") or resp.text[:300]
    except ValueError:
        return resp.text[:300]


class LinkedInClient:
    """Posts on behalf of the logged-in member."""

    def __init__(
        self,
        settings: Settings,
        token: LinkedInToken,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ):
        """
        Args:
            settings: Provides the LinkedIn API version.
            token: Logged-in member credentials.
            session: Optional HTTP session (injected in tests).
            sleep: Sleep function (injected in tests to skip waiting).
        """
        self.settings = settings
        self.token = token
        self.session = session or requests.Session()
        self.sleep = sleep

    # ------------------------------------------------------------------
    # Low-level HTTP
    # ------------------------------------------------------------------
    def _headers(self, json_body: bool = True) -> dict:
        """Headers required by LinkedIn's versioned REST API."""
        headers = {
            "Authorization": f"Bearer {self.token.access_token}",
            "Linkedin-Version": self.settings.linkedin_api_version,
            "X-Restli-Protocol-Version": "2.0.0",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _call(self, method: str, url: str, *, retry_5xx: bool = True, timeout: int = TIMEOUT_SECONDS, **kwargs) -> requests.Response:
        """Send a request with bounded retries and readable errors.

        Raises:
            LinkedInError: after the final failed attempt.
        """
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self.session.request(method, url, timeout=timeout, **kwargs)
            except requests.RequestException as exc:
                if not retry_5xx or attempt == MAX_ATTEMPTS:
                    raise LinkedInError(f"Network error talking to LinkedIn: {exc}", maybe_created=not retry_5xx) from exc
                self.sleep(2 ** attempt)
                continue

            if resp.status_code < 400:
                return resp
            retryable = resp.status_code == 429 or (retry_5xx and resp.status_code >= 500)
            if retryable and attempt < MAX_ATTEMPTS:
                try:
                    wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                except ValueError:  # Retry-After may be an HTTP date
                    wait = 2 ** attempt
                log.info("LinkedIn returned %s; retrying in %ss", resp.status_code, wait)
                self.sleep(min(wait, 60))
                continue
            raise self._to_error(resp, maybe_created=(not retry_5xx and resp.status_code >= 500))
        raise LinkedInError("LinkedIn request failed after retries")  # pragma: no cover

    @staticmethod
    def _to_error(resp: requests.Response, maybe_created: bool) -> LinkedInError:
        """Translate an HTTP error into an actionable message."""
        detail = _error_detail(resp)
        hints = {
            401: "Your LinkedIn login is invalid or expired. Run: linkedin-agent login",
            403: "LinkedIn refused permission. Check the app has the 'Share on LinkedIn' product (w_member_social).",
            426: "LINKEDIN_API_VERSION is no longer supported. Set it to a recent YYYYMM value.",
            429: "LinkedIn rate limit reached. Try again later.",
        }
        message = f"LinkedIn API error {resp.status_code}: {detail}"
        if resp.status_code in hints:
            message += f" ({hints[resp.status_code]})"
        if maybe_created:
            message += " The post may have been created; check your LinkedIn profile before retrying."
        return LinkedInError(message, status=resp.status_code, maybe_created=maybe_created)

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------
    def upload_image(self, path: str | Path) -> str:
        """Upload one image and return its `urn:li:image:...`."""
        init = self._call(
            "POST",
            f"{API_BASE}/images?action=initializeUpload",
            headers=self._headers(),
            json={"initializeUploadRequest": {"owner": self.token.person_urn}},
        ).json()["value"]
        data = Path(path).read_bytes()
        self._call(
            "PUT",
            init["uploadUrl"],
            headers={"Authorization": f"Bearer {self.token.access_token}"},
            data=data,
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
        log.info("Uploaded image %s as %s", Path(path).name, init["image"])
        return init["image"]

    # ------------------------------------------------------------------
    # Videos
    # ------------------------------------------------------------------
    def upload_video(self, path: str | Path, *, wait_seconds: int = 600) -> str:
        """Upload an MP4 in the parts LinkedIn requests and return `urn:li:video:...`.

        Args:
            wait_seconds: How long to wait for LinkedIn to finish processing.

        Raises:
            LinkedInError: on upload failure or if processing fails.
        """
        path = Path(path)
        size = path.stat().st_size
        init = self._call(
            "POST",
            f"{API_BASE}/videos?action=initializeUpload",
            headers=self._headers(),
            json={
                "initializeUploadRequest": {
                    "owner": self.token.person_urn,
                    "fileSizeBytes": size,
                    "uploadCaptions": False,
                    "uploadThumbnail": False,
                }
            },
        ).json()["value"]

        # Upload each byte range LinkedIn asked for, in order, keeping the ETags.
        etags: list[str] = []
        with path.open("rb") as fh:
            for part in init["uploadInstructions"]:
                fh.seek(part["firstByte"])
                chunk = fh.read(part["lastByte"] - part["firstByte"] + 1)
                resp = self._call(
                    "PUT",
                    part["uploadUrl"],
                    headers={"Content-Type": "application/octet-stream"},
                    data=chunk,
                    timeout=UPLOAD_TIMEOUT_SECONDS,
                )
                etag = resp.headers.get("etag") or resp.headers.get("ETag")
                if not etag:
                    raise LinkedInError("LinkedIn did not return an ETag for an uploaded video part")
                etags.append(etag)

        self._call(
            "POST",
            f"{API_BASE}/videos?action=finalizeUpload",
            headers=self._headers(),
            json={
                "finalizeUploadRequest": {
                    "video": init["video"],
                    "uploadToken": init.get("uploadToken", ""),
                    "uploadedPartIds": etags,
                }
            },
        )
        self._wait_for_video(init["video"], wait_seconds)
        return init["video"]

    def _wait_for_video(self, video_urn: str, wait_seconds: int) -> None:
        """Poll the video until AVAILABLE. If the token may not read video
        status (403), wait a fixed minute and continue; the post call will
        report any remaining processing problem."""
        deadline = time.time() + wait_seconds
        url = f"{API_BASE}/videos/{quote(video_urn, safe='')}"
        while True:
            resp = self.session.get(url, headers=self._headers(json_body=False), timeout=TIMEOUT_SECONDS)
            if resp.status_code == 403:
                log.info("Cannot read video status with this token; waiting 60s for processing.")
                self.sleep(60)
                return
            if resp.status_code < 400:
                status = resp.json().get("status")
                if status == "AVAILABLE":
                    return
                if status == "PROCESSING_FAILED":
                    reason = resp.json().get("processingFailureReason", "unknown reason")
                    raise LinkedInError(f"LinkedIn could not process the video: {reason}")
            if time.time() >= deadline:
                raise LinkedInError("Video is still processing on LinkedIn; retry the draft later.")
            self.sleep(10)

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------
    def create_post(
        self,
        commentary: str,
        *,
        image_urns: list[str] | None = None,
        alt_texts: list[str] | None = None,
        video_urn: str | None = None,
        media_title: str = "",
    ) -> str:
        """Publish a post and return its URN (from the `x-restli-id` header).

        Args:
            commentary: Text already converted to little text format.
            image_urns: 1 image (single photo) or 2-20 (multi-image).
            alt_texts: Accessibility text per image.
            video_urn: A processed video (cannot be combined with images).
            media_title: Optional title for single image/video posts.
        """
        body: dict = {
            "author": self.token.person_urn,
            "commentary": commentary,
            "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        image_urns = image_urns or []
        alt_texts = alt_texts or []
        if video_urn and image_urns:
            raise ValueError("A post can have a video or images, not both")
        if video_urn:
            body["content"] = {"media": {"id": video_urn, "title": media_title}}
        elif len(image_urns) == 1:
            media = {"id": image_urns[0], "title": media_title}
            if alt_texts and alt_texts[0]:
                media["altText"] = alt_texts[0]
            body["content"] = {"media": media}
        elif len(image_urns) > 1:
            images = []
            for i, urn in enumerate(image_urns):
                item = {"id": urn}
                if i < len(alt_texts) and alt_texts[i]:
                    item["altText"] = alt_texts[i]
                images.append(item)
            body["content"] = {"multiImage": {"images": images}}

        resp = self._call("POST", f"{API_BASE}/posts", headers=self._headers(), json=body, retry_5xx=False)
        post_urn = resp.headers.get("x-restli-id") or resp.headers.get("X-RestLi-Id")
        if not post_urn:
            raise LinkedInError("LinkedIn accepted the post but returned no id; check your profile.", maybe_created=True)
        return post_urn
