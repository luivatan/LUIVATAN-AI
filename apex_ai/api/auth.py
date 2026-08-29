"""Phase 51/52 — signup, login, logout, and the current-user dependency.

Session identity travels as an httponly cookie (not a header/token the
frontend has to manage) — the browser sends it automatically, JavaScript
never touches it, which is the standard mitigation for token theft via XSS.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from apex_ai.api.errors import APIError
from apex_ai.api.schemas import UserOut
from apex_ai.auth.service import InvalidCredentialsError
from apex_ai.auth.users import EmailAlreadyRegisteredError
from apex_ai.core.logging import get_logger

log = get_logger("api.auth")


# Plain str, not pydantic's EmailStr: that needs the optional email-validator
# dependency for a much more thorough (DNS-aware) check than this app needs.
# apex_ai.auth.users.normalize_email() already validates format on every
# create/lookup; adding a second, different validator here would be two
# sources of truth for the same rule.
class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field(default="", max_length=100)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


def _set_session_cookie(response: Response, request: Request, services, token: str) -> None:
    response.set_cookie(
        key=services.settings.session_cookie_name,
        value=token,
        max_age=services.settings.session_ttl_days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


def _clear_session_cookie(response: Response, services) -> None:
    response.delete_cookie(key=services.settings.session_cookie_name, path="/")


def get_current_user(request: Request, services):
    """The real authenticated user for this request, or ``None`` — never the
    auto-login fallback (that's ``require_user``'s job, not this lookup's)."""
    if services.auth is None:
        return None
    token = request.cookies.get(services.settings.session_cookie_name)
    if not token:
        return None
    return services.auth.user_for_session(token)


def make_require_user_dependency(services):
    """A per-app FastAPI dependency: the real session user if one is
    presented, else the auto-provisioned default local account when
    ``auto_login_local`` is on, else 401. Other routers (Phase 54) depend on
    this the same way they already depend on ``services`` via closures."""

    def require_user(request: Request):
        if services.auth is None:
            # Distinct from "not signed in": the auth subsystem itself never
            # came up (see runtime.py's startup_error handling), so telling
            # the caller to sign in would be misleading.
            raise APIError(
                503,
                "Accounts are temporarily unavailable. Try again shortly.",
                code="auth_unavailable",
                retryable=True,
            )
        user = get_current_user(request, services)
        if user is not None:
            return user
        if services.settings.auto_login_local and services.default_local_user is not None:
            return services.default_local_user
        raise APIError(401, "Sign in to continue.", code="authentication_required")

    return require_user


def create_auth_router(services) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    require_user = make_require_user_dependency(services)

    def auth_service():
        if services.auth is None:
            raise APIError(
                503,
                "Accounts are temporarily unavailable. Try again shortly.",
                code="auth_unavailable",
                retryable=True,
            )
        return services.auth

    @router.post("/signup", status_code=201, response_model=UserOut)
    def signup(payload: SignupRequest, request: Request, response: Response):
        try:
            result = auth_service().signup(
                str(payload.email), payload.password, display_name=payload.display_name
            )
        except EmailAlreadyRegisteredError as error:
            raise APIError(409, str(error), code="email_already_registered") from error
        except ValueError as error:
            raise APIError(400, str(error), code="invalid_signup") from error
        _set_session_cookie(response, request, services, result.session.token)
        return result.user.to_dict()

    @router.post("/login", response_model=UserOut)
    def login(payload: LoginRequest, request: Request, response: Response):
        try:
            result = auth_service().login(str(payload.email), payload.password)
        except InvalidCredentialsError as error:
            raise APIError(401, str(error), code="invalid_credentials") from error
        _set_session_cookie(response, request, services, result.session.token)
        return result.user.to_dict()

    @router.post("/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(services.settings.session_cookie_name)
        if token and services.auth is not None:
            services.auth.logout(token)
        _clear_session_cookie(response, services)
        return {"logged_out": True}

    @router.get("/me", response_model=UserOut)
    def me(user=Depends(require_user)):
        return user.to_dict()

    return router


__all__ = ["create_auth_router", "get_current_user", "make_require_user_dependency"]
