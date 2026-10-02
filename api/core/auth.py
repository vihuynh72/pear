"""Firebase ID token verification (plan 7.1, P1).

PEAR_AUTH modes:
  off       ignore Authorization entirely; everyone is "guest"
  optional  (default) verify the token if one is sent (bad token -> 401), else "guest".
            This is the app's guest switch: guest mode simply sends no token.
  required  no valid token -> 401

The app sends `Authorization: Bearer <Firebase ID token>`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from fastapi import Request

from core.config import get_settings
from core.errors import PearError
from core.timing import current

log = logging.getLogger("pear.auth")
_firebase_app = None


@dataclass(frozen=True)
class User:
    uid: str
    is_guest: bool
    email: str | None = None


GUEST = User(uid="guest", is_guest=True)


def _verify(token: str, project: str) -> dict:
    global _firebase_app
    import firebase_admin
    from firebase_admin import auth

    if _firebase_app is None:
        try:
            _firebase_app = firebase_admin.get_app()
        except ValueError:
            _firebase_app = firebase_admin.initialize_app(options={"projectId": project})
    return auth.verify_id_token(token, app=_firebase_app, check_revoked=False)


async def current_user(request: Request) -> User:
    """FastAPI dependency: `user: User = Depends(current_user)`."""
    s = get_settings()
    if s.auth_mode == "off":
        return GUEST
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        if s.auth_mode == "required":
            raise PearError("unauthorized", "Please sign in.", next="sign_in")
        return GUEST
    try:
        claims = await asyncio.to_thread(_verify, token, s.project)
    except Exception as e:
        log.info("token rejected: %r", e)
        raise PearError("unauthorized", "Your sign-in expired. Please sign in again.",
                        next="sign_in", detail={"why": repr(e)}) from e
    user = User(uid=claims.get("uid") or claims.get("sub", "unknown"), is_guest=False,
                email=claims.get("email"))
    current().uid = user.uid
    return user
