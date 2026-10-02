"""POST /v1/profile/parse: free text -> profile chips to confirm (plan 7.6, 5.2 item 8).

    {"text": "peanuts, mild lactose intolerance, carmine", "language": "en"}
    -> {"chips": [{"group": "allergies", "id": "peanut", "level": "strict", "label": "peanut",
                   "note": null}, ...], "unmatched": [...], "timings_ms": {...}, "request_id": "..."}

Thin route over pipeline.profile_parse.parse_profile (local kb pass first, one Gemini call for
the leftovers only; Gemini down -> local chips + unmatched, never an error).
Debugging: ?debug=1 puts the pass counts in the X-Pear-Debug response header
(ProfileParseResponse has no debug field). Log event: profile.parsed.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Response

from core.auth import User, current_user
from core.clients import Clients, get_clients
from core.timing import debug_payload, request_id, timings
from models.contracts import ProfileParseRequest, ProfileParseResponse
from pipeline import profile_parse

router = APIRouter(tags=["profile"])
_MAX_DEBUG_HEADER = 4000


@router.post("/v1/profile/parse", response_model=ProfileParseResponse)
async def parse(body: ProfileParseRequest, response: Response, clients: Clients = Depends(get_clients),
                user: User = Depends(current_user)) -> ProfileParseResponse:
    if not body.text.strip():
        out = ProfileParseResponse(chips=[], unmatched=[], timings_ms=timings(), request_id=request_id())
    else:
        out = await profile_parse.parse_profile(clients, body.text, body.language)
    payload = debug_payload()
    if payload:
        response.headers["X-Pear-Debug"] = json.dumps(payload, default=str, ensure_ascii=True)[:_MAX_DEBUG_HEADER]
    return out
