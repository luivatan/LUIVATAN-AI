"""Mount and launch the custom chat-first Apex AI web interface."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from apex_ai.web.landing import render_landing_html

_WEB_ROOT = Path(__file__).resolve().parent
_STATIC = _WEB_ROOT / "static"
_INDEX = _WEB_ROOT / "templates" / "index.html"
_LOGIN = _WEB_ROOT / "templates" / "login.html"


class BrowserSecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        # The product UI intentionally has no inline scripts, CDNs, or remote assets.
        # FastAPI's optional Swagger page loads its stock bundle from jsDelivr, so it
        # receives a narrowly scoped exception rather than weakening the chat UI.
        if request.url.path == "/api/docs":
            policy = (
                "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "connect-src 'self'; object-src 'none'; base-uri 'self'"
            )
        else:
            policy = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
                "object-src 'none'; base-uri 'self'; form-action 'self'"
            )
        response.headers.setdefault("Content-Security-Policy", policy)
        return response


def mount_web_ui(app: FastAPI) -> None:
    """Attach static assets and the single-page shell to an API application."""
    app.add_middleware(BrowserSecurityHeaders)
    app.mount("/assets", StaticFiles(directory=str(_STATIC)), name="assets")

    @app.get("/", include_in_schema=False)
    def web_index():
        return FileResponse(
            _INDEX,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/login", include_in_schema=False)
    def web_login():
        return FileResponse(
            _LOGIN,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/welcome", include_in_schema=False)
    def web_landing():
        # Phase 96: the public marketing page, separate from the authenticated
        # app shell at "/". Rendered from real plan data on every request
        # rather than cached as a static file, so pricing can never drift
        # from what apex_ai.billing.plans actually defines.
        return HTMLResponse(render_landing_html())


def launch(services=None, **uvicorn_options) -> None:
    """Run the web app on the configured Apex AI host/port."""
    import uvicorn

    from apex_ai.api.server import create_api
    from apex_ai.runtime import build_services

    services = services or build_services()
    app = create_api(services, include_web=True)
    uvicorn.run(
        app,
        host=services.settings.server_name,
        port=services.settings.server_port,
        log_level=uvicorn_options.pop("log_level", "info"),
        **uvicorn_options,
    )
