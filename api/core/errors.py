"""One error type for the whole API, rendered as the 7.6 error body.

    raise PearError("not_found", "We couldn't find this barcode.", next="take_photo")

-> HTTP 404  {"error": {"code": "not_found", "message": "...", "next": "take_photo",
                        "request_id": "..."}}
"""

from __future__ import annotations

from typing import Any

from models.contracts import ErrorCode, ErrorNext

STATUS: dict[str, int] = {
    "bad_request": 400,
    "unauthorized": 401,
    "not_found": 404,
    "unreadable": 422,
    "too_blurry": 422,
    "rate_limited": 429,
    "internal": 500,
    "unavailable": 503,
}


class PearError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        next: ErrorNext | None = None,
        status: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next = next
        self.status = status or STATUS.get(code, 500)
        # Never sent to the client; logged with the request and shown in ?debug=1.
        self.detail = detail or {}

    def __repr__(self) -> str:
        return f"PearError({self.code!r}, {self.message!r}, next={self.next!r})"


def gemini_unavailable(why: str) -> PearError:
    return PearError(
        "unavailable",
        "Pear's AI reader isn't reachable right now. Barcodes still work.",
        next="retry",
        detail={"why": why, "fix": "local: gcloud auth application-default login; "
                                    "Cloud Run: service account needs roles/aiplatform.user"},
    )
