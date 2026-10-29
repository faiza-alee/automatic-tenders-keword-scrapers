import logging
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pathlib import Path

from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.api import admin, routes
from app.api.security import RateLimiter
from app.config import Settings, get_settings
from app.logging_config import setup_logging

log = logging.getLogger("api")
OPEN_PATHS = ("/", "/dashboard", "/api/v1/health", "/docs", "/redoc", "/openapi.json")
DASHBOARD = Path(__file__).resolve().parent.parent / "static" / "dashboard.html"

DESCRIPTION = """
Tender data collected from GB-PPRA, enriched with AI-extracted keywords and categories.

**Authentication:** send your key in the `X-API-Key` header (use *Authorize* above).
**Responses:** always JSON. Lists are `{"data": [...], "meta": {page, page_size, total, pages}}`;
single items are `{"data": {...}}`; errors are `{"error": {"code", "message", "details"}}`.
"""


def error(status: int, code: str, message: str, details=None, headers=None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "details": details}}, status_code=status, headers=headers)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    app = FastAPI(title="GB-PPRA Tender API", version="1.0.0", description=DESCRIPTION)
    app.state.settings = settings
    limiter = RateLimiter(settings.rate_limit_per_minute)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        start = time.perf_counter()
        if request.url.path not in OPEN_PATHS:
            identity = request.headers.get("x-api-key") or (request.client.host if request.client else "anon")
            wait = limiter.check(identity)
            if wait:
                return error(429, "rate_limited", f"Rate limit exceeded; retry in {wait}s", headers={"Retry-After": str(wait)})
        response = await call_next(request)
        log.info("request", extra={"ctx": {"method": request.method, "path": request.url.path,
                                           "status": response.status_code, "ms": round((time.perf_counter() - start) * 1000, 1)}})
        return response

    @app.exception_handler(HTTPException)
    async def http_exc(_, exc: HTTPException):
        codes = {401: "unauthorized", 403: "forbidden", 404: "not_found"}
        return error(exc.status_code, codes.get(exc.status_code, "error"), str(exc.detail), headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_exc(_, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return error(422, "validation_error", "Invalid request parameters", details)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"ctx": {"path": request.url.path}})
        return error(500, "internal_error", "Internal server error")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/dashboard")

    @app.get("/dashboard", include_in_schema=False, response_class=HTMLResponse)
    def dashboard():
        """Simple browser page; contains no data itself, it calls the API with the key you paste in."""
        return HTMLResponse(DASHBOARD.read_text(encoding="utf-8"))

    app.include_router(routes.public)
    app.include_router(routes.router)
    app.include_router(admin.router)
    return app

