"""POST /v1/scan/photo: label photo -> ScanResult (plan 7.6, addendum 10).

multipart/form-data fields
    image         required  the ingredient panel (JPEG from the app; HEIC/PNG/WebP accepted)
    front_image   optional  front of the package (helps name/brand, used for front-only matches)
    household     required  household JSON as a string
    member_ids    optional  JSON list string '["m_mia"]' (comma-separated also accepted)

Errors (core.errors.PearError -> {"error": {...}}):
    too_blurry 422 next=retake | unreadable 422 next=retake | not_found 404 next=take_photo
    bad_request 400 (missing/empty/oversized/wrong-type upload, bad household JSON)

Debugging: add ?debug=1 to see the label_reader output (debug.gemini.label_reader) and the
rules engine's hits. curl example:
    curl -F image=@label.jpg -F household="$(cat ../contracts/household.example.json)" \
         'http://localhost:8080/v1/scan/photo?debug=1'
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, UploadFile

from core.auth import User, current_user
from core.clients import Clients, get_clients
from core.deps import MAX_IMAGE_BYTES, household_from_form, read_upload
from core.errors import PearError
from models.contracts import ScanResult
from pipeline import photo_gemini

router = APIRouter(tags=["scan"])

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/pjpeg", "image/heic", "image/heif",
                       "image/png", "image/webp", "application/octet-stream", ""}


def parse_member_ids(raw: str | None) -> list[str] | None:
    """'["m_mia","m_leo"]' or 'm_mia,m_leo' -> list; empty -> None (everyone)."""
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("["):
        try:
            val = json.loads(text)
        except json.JSONDecodeError as e:
            raise PearError("bad_request", "`member_ids` must be a JSON list of member ids.") from e
        if not isinstance(val, list):
            raise PearError("bad_request", "`member_ids` must be a JSON list of member ids.")
        ids = [str(x).strip() for x in val if str(x).strip()]
    else:
        ids = [x.strip() for x in text.split(",") if x.strip()]
    return ids or None


async def _read_image(upload: UploadFile | None, what: str, *, required: bool) -> bytes | None:
    if upload is None:
        if required:
            raise PearError("bad_request", f"Missing `{what}` file (multipart field `{what}`).")
        return None
    if not required and upload.size == 0:
        return None  # an empty optional front image is the same as none
    ctype = (upload.content_type or "").split(";")[0].strip().lower()
    if ctype not in ALLOWED_IMAGE_TYPES:
        raise PearError("bad_request", f"`{what}` must be a JPEG, HEIC or PNG image (got {ctype}).")
    return await read_upload(upload, limit=MAX_IMAGE_BYTES, what=what)


@router.post("/v1/scan/photo", response_model=ScanResult)
async def scan_photo(
    image: UploadFile | None = File(None),
    front_image: UploadFile | None = File(None),
    household: str | None = Form(None),
    member_ids: str | None = Form(None),
    clients: Clients = Depends(get_clients),
    user: User = Depends(current_user),
) -> ScanResult:
    hh = household_from_form(household)
    ids = parse_member_ids(member_ids)
    main = await _read_image(image, "image", required=True)
    front = await _read_image(front_image, "front_image", required=False)
    return await photo_gemini.scan_photo(clients, image=main, front=front, household=hh,
                                         member_ids=ids, uid=user.uid)
