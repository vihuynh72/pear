"""Shared FastAPI dependencies and request helpers."""

from __future__ import annotations

import json

from pydantic import ValidationError

from core.errors import PearError
from models.contracts import Household

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # the app downsizes to ~300 KB; this is a safety cap
MAX_AUDIO_BYTES = 10 * 1024 * 1024


def household_from_form(raw: str | None) -> Household:
    """Multipart endpoints send the household as a JSON string field named `household`."""
    if not raw:
        raise PearError("bad_request", "Missing `household` form field (JSON string).")
    try:
        return Household.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as e:
        raise PearError("bad_request", "`household` must be household JSON (see contracts/).",
                        detail={"why": str(e)[:1000]}) from e


async def read_upload(upload, *, limit: int, what: str) -> bytes:
    data = await upload.read()
    if not data:
        raise PearError("bad_request", f"`{what}` was empty.")
    if len(data) > limit:
        raise PearError("bad_request", f"`{what}` is too large ({len(data)} bytes, limit {limit}).")
    return data
