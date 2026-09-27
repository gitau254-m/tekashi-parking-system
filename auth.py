"""
auth.py - Admin login for the control room (/admin) and the admin API routes.

How it works:
  1. The manager types the password from .env (ADMIN_PASSWORD) on /admin/login.
  2. If it matches, we create a random SESSION TOKEN, remember it on the
     server, and give it to the browser in a cookie.
  3. Every admin request sends the cookie back; we check the token is one we
     issued and has not expired.

Why a random token and not "logged_in=true" in the cookie? The browser owns
its cookies - anyone can type logged_in=true. Nobody can guess a random
32-byte token, and it only counts if the SERVER issued it.

Sessions live in memory, so restarting the server logs everyone out.
"""

import hmac
import os
import secrets
import threading
import time

from dotenv import load_dotenv
from fastapi import HTTPException, Request

from config import BASE_DIR

load_dotenv(BASE_DIR / ".env")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SESSION_COOKIE = "tekashi_admin"
SESSION_SECONDS = 8 * 60 * 60            # one working shift

_sessions: dict[str, float] = {}         # token -> time it expires
_sessions_lock = threading.Lock()


def password_is_set() -> bool:
    return bool(ADMIN_PASSWORD)


def check_password(attempt: str) -> bool:
    """
    hmac.compare_digest compares in CONSTANT time. A normal == stops at the
    first wrong character, so an attacker timing many guesses could learn the
    password letter by letter (a "timing attack").
    """
    if not ADMIN_PASSWORD:
        return False
    return hmac.compare_digest(attempt.encode(), ADMIN_PASSWORD.encode())


def create_session() -> str:
    """A new unguessable token, valid for SESSION_SECONDS."""
    token = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[token] = time.time() + SESSION_SECONDS
    return token


def end_session(token: str | None) -> None:
    with _sessions_lock:
        _sessions.pop(token or "", None)     # pop with a default never raises


def is_admin(request: Request) -> bool:
    """True if the request carries a valid, unexpired session cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    with _sessions_lock:
        expires_at = _sessions.get(token)
        if expires_at is None:
            return False
        if time.time() > expires_at:
            del _sessions[token]
            return False
        return True


def require_admin(request: Request) -> None:
    """
    FastAPI DEPENDENCY: add  dependencies=[Depends(require_admin)]  to a route
    and FastAPI runs this first. If it raises, the route never runs.
    """
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Admin login required.")
