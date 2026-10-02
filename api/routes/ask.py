"""POST /v1/ask: one question -> AskResponse (addendum 6). Both body types on one path:

    application/json      AskRequest {"text", "language", "last_product_key", "household", "asker_id"}
                          Typed or on-device dictated text. The primary path.
    multipart/form-data   audio (m4a, required unless `text` is sent), household (JSON string),
                          language, last_product_key, asker_id. Fallback for a language the phone
                          can't transcribe: ONE Gemini call transcribes + understands the audio.

The Content-Type header picks the parser. Both end in pipeline.ask (ADK Workflow or the
direct engine, see settings.ask_engine). AskResponse.transcript = the text (JSON) or the
Gemini transcript (audio).

Debugging: ?debug=1 adds debug.ask_engine (adk | direct | fallback), debug.ask (understood
fields) and debug.gemini.question_reader. Examples:
    curl -X POST 'localhost:8080/v1/ask?debug=1' -H 'content-type: application/json' \
         -d '{"text": "Can Mia eat this?", "last_product_key": "off:...", "household": {...}}'
    curl -F audio=@q.m4a -F household="$(cat ../contracts/household.example.json)" \
         -F last_product_key=off:... 'localhost:8080/v1/ask?debug=1'
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from core.auth import User, current_user
from core.clients import Clients, get_clients
from core.deps import MAX_AUDIO_BYTES, household_from_form, read_upload
from core.errors import PearError
from models.contracts import AskRequest, AskResponse
from pipeline import ask

router = APIRouter(tags=["ask"])


def _form_str(form, name: str) -> str | None:
    v = form.get(name)
    if v is None or isinstance(v, UploadFile):
        return None
    v = str(v).strip()
    return v or None


async def _from_json(request: Request) -> AskRequest:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
        raise PearError("bad_request", "Body must be AskRequest JSON (or multipart with audio).") from e
    try:
        return AskRequest.model_validate(payload)
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []))
        raise PearError("bad_request", f"Invalid request: {where}: {first.get('msg', 'invalid')}",
                        detail={"errors": [{"loc": x.get("loc"), "msg": x.get("msg")} for x in e.errors()[:20]]}) from e


async def _from_form(request: Request, clients: Clients, user: User) -> AskResponse:
    form = await request.form()
    household = household_from_form(_form_str(form, "household"))
    language = _form_str(form, "language")
    last_key = _form_str(form, "last_product_key")
    asker_id = _form_str(form, "asker_id")
    audio = form.get("audio")
    if isinstance(audio, UploadFile):
        data = await read_upload(audio, limit=MAX_AUDIO_BYTES, what="audio")
        return await ask.answer_audio(clients, audio=data, mime=ask.audio_mime(audio.content_type, audio.filename),
                                      household=household, language=language, last_product_key=last_key,
                                      asker_id=asker_id, uid=user.uid)
    text = _form_str(form, "text")
    if not text:
        raise PearError("bad_request", "Multipart /v1/ask needs an `audio` file (m4a) or a `text` field.")
    req = AskRequest(text=text, language=language, last_product_key=last_key, household=household,
                     asker_id=asker_id)
    return await ask.answer(clients, req, user.uid)


@router.post("/v1/ask", response_model=AskResponse)
async def post_ask(request: Request, clients: Clients = Depends(get_clients),
                   user: User = Depends(current_user)) -> AskResponse:
    ctype = request.headers.get("content-type", "").lower()
    if ctype.startswith("multipart/form-data") or ctype.startswith("application/x-www-form-urlencoded"):
        return await _from_form(request, clients, user)
    return await ask.answer(clients, await _from_json(request), user.uid)
