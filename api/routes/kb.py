"""GET /v1/kb/search?q=&lang=en&limit=10&group= -> KbSearchResponse (onboarding chip search).

The app also bundles contracts/kb_index.json for offline instant search; this endpoint is the
same search server-side (diacritic-insensitive: "dau phong" finds peanut, "hypertension" finds
high_blood_pressure). `group` may be one group or a comma list (allergies,conditions,skin,diets,less_of).

Debug: curl 'localhost:8080/v1/kb/search?q=cacahuate&lang=es&debug=1'
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from core.errors import PearError
from core.logging import log_event
from core.timing import debug_put, stage
from models.contracts import KbSearchResponse, base_language
from rules.kb import PROFILE_GROUPS, get_kb

router = APIRouter(tags=["kb"])


@router.get("/v1/kb/search", response_model=KbSearchResponse)
async def kb_search(
    q: str = Query("", max_length=80),
    lang: str = Query("en"),
    limit: int = Query(10, ge=1, le=50),
    group: str | None = Query(None),
) -> KbSearchResponse:
    groups = [g.strip() for g in group.split(",") if g.strip()] if group else None
    if groups:
        bad = [g for g in groups if g not in PROFILE_GROUPS]
        if bad:
            raise PearError("bad_request", f"Unknown group {bad[0]!r}. Use one of: {', '.join(PROFILE_GROUPS)}")
    with stage("kb_search"):
        kb = get_kb()
        suggestions = kb.search(q, language=base_language(lang), limit=limit, groups=groups)
    debug_put("kb_version", kb.version)
    if not suggestions and q.strip():
        log_event("kb.search_miss", q=q[:80], lang=lang)
    return KbSearchResponse(query=q, suggestions=suggestions)
