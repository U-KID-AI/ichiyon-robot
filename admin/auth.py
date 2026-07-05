import os
import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from bot.db import get_connection
from bot.repositories import PermissionRepository


load_dotenv()

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_USER_URL = "https://discord.com/api/users/@me"
SESSION_USER_KEY = "discord_user"
SESSION_STATE_KEY = "discord_oauth_state"
DEV_SESSION_SECRET = "development-only-admin-session-secret"

router = APIRouter()


def get_admin_base_url() -> str:
    return os.getenv("ADMIN_BASE_URL", "http://localhost:8000").rstrip("/")


def get_oauth_redirect_uri() -> str:
    return os.getenv(
        "DISCORD_OAUTH_REDIRECT_URI",
        get_admin_base_url() + "/auth/discord/callback",
    )


def get_session_secret() -> str:
    value = os.getenv("ADMIN_SESSION_SECRET")
    if value:
        return value

    app_env = os.getenv("APP_ENV", "development").strip().lower()
    message = "ADMIN_SESSION_SECRET is not set"
    if app_env == "production":
        raise RuntimeError(message)

    print("[WARN] {0}; using development-only session secret".format(message))
    return DEV_SESSION_SECRET


def get_oauth_config_status() -> Dict[str, bool]:
    return {
        "client_id": bool(os.getenv("DISCORD_OAUTH_CLIENT_ID")),
        "client_secret": bool(os.getenv("DISCORD_OAUTH_CLIENT_SECRET")),
        "redirect_uri": bool(get_oauth_redirect_uri()),
        "session_secret": bool(os.getenv("ADMIN_SESSION_SECRET")),
    }


def oauth_is_configured() -> bool:
    status_map = get_oauth_config_status()
    return status_map["client_id"] and status_map["client_secret"] and status_map["redirect_uri"]


def build_avatar_url(user_id: str, avatar_hash: Optional[str]) -> Optional[str]:
    if not avatar_hash:
        return None

    extension = "gif" if avatar_hash.startswith("a_") else "png"
    return "https://cdn.discordapp.com/avatars/{0}/{1}.{2}".format(
        user_id,
        avatar_hash,
        extension,
    )


def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    user = request.session.get(SESSION_USER_KEY)
    if isinstance(user, dict):
        return user
    return None


def require_login(request: Request) -> Dict[str, Any]:
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    return user


def register_auth_routes(templates: Jinja2Templates) -> None:
    @router.get("/login")
    async def login_page(request: Request, error: Optional[str] = None):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "user": get_current_user(request),
                "error": error,
                "oauth_configured": oauth_is_configured(),
                "oauth_status": get_oauth_config_status(),
            },
        )

    @router.get("/auth/discord")
    async def discord_auth(request: Request):
        if not oauth_is_configured():
            return RedirectResponse(url="/login?error=oauth_not_configured", status_code=303)

        state = secrets.token_urlsafe(32)
        request.session[SESSION_STATE_KEY] = state
        params = {
            "client_id": os.getenv("DISCORD_OAUTH_CLIENT_ID"),
            "redirect_uri": get_oauth_redirect_uri(),
            "response_type": "code",
            "scope": "identify",
            "state": state,
            "prompt": "none",
        }
        return RedirectResponse(
            url="{0}?{1}".format(DISCORD_AUTHORIZE_URL, urlencode(params)),
            status_code=303,
        )

    @router.get("/auth/discord/callback")
    async def discord_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None):
        expected_state = request.session.pop(SESSION_STATE_KEY, None)
        if not code or not state or not expected_state or state != expected_state:
            return RedirectResponse(url="/login?error=invalid_state", status_code=303)

        try:
            user_data = await fetch_discord_user(code)
        except Exception as exc:
            print("[WARN] Discord OAuth failed: {0}".format(exc))
            return RedirectResponse(url="/login?error=oauth_failed", status_code=303)

        user_id = str(user_data.get("id", ""))
        if not user_id:
            return RedirectResponse(url="/login?error=oauth_failed", status_code=303)

        request.session[SESSION_USER_KEY] = {
            "user_id": user_id,
            "username": user_data.get("username", ""),
            "global_name": user_data.get("global_name"),
            "avatar_url": build_avatar_url(user_id, user_data.get("avatar")),
        }
        try:
            with get_connection() as connection:
                PermissionRepository(connection).set_last_login(user_id)
                connection.commit()
        except Exception as exc:
            print("[WARN] Failed to update admin last_login_at: {0}".format(exc))
        return RedirectResponse(url="/me", status_code=303)

    @router.api_route("/logout", methods=["GET", "POST"])
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    @router.get("/me")
    async def me_page(request: Request, user: Dict[str, Any] = Depends(require_login)):
        return templates.TemplateResponse(
            request,
            "me.html",
            {"user": user},
        )


async def fetch_discord_user(code: str) -> Dict[str, Any]:
    client_id = os.getenv("DISCORD_OAUTH_CLIENT_ID")
    client_secret = os.getenv("DISCORD_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("Discord OAuth is not configured")

    async with httpx.AsyncClient(timeout=10.0) as client:
        token_response = await client.post(
            DISCORD_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": get_oauth_redirect_uri(),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError("Discord OAuth token response did not include access_token")

        user_response = await client.get(
            DISCORD_USER_URL,
            headers={"Authorization": "Bearer {0}".format(access_token)},
        )
        user_response.raise_for_status()
        return user_response.json()
