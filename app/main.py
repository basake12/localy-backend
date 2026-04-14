from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.config import settings, is_production
from app.core.database import engine, check_db_connection
from app.core.exceptions import LocalyException
from app.api.v1.router import api_router
from app.middleware.logging_middleware import LoggingMiddleware
from app.middleware.rate_limit import RateLimitMiddleware

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} API v{settings.API_VERSION}")
    logger.info(f"Environment: {settings.APP_ENV}")

    db_healthy = await check_db_connection()
    if not db_healthy:
        logger.error("Database connection failed — check DATABASE_URL in .env")
    else:
        logger.info("Database connection: OK")

    # Schema is managed exclusively by Alembic.
    # Run `alembic upgrade head` before starting the app.
    # Never call create_all_tables() here — it bypasses migration history.

    yield

    # engine.dispose() is synchronous on a sync engine — do NOT await it.
    logger.info("Shutting down application...")
    engine.dispose()


# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────

# Swagger/ReDoc are hidden in production.
_docs_url    = f"{settings.API_PREFIX}/{settings.API_VERSION}/docs"         if not is_production() else None
_redoc_url   = f"{settings.API_PREFIX}/{settings.API_VERSION}/redoc"        if not is_production() else None
_openapi_url = f"{settings.API_PREFIX}/{settings.API_VERSION}/openapi.json" if not is_production() else None

app = FastAPI(
    title=settings.APP_NAME,
    description="Localy Super App API — Connect with local businesses",
    version=settings.API_VERSION,
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
    lifespan=lifespan,
    debug=settings.DEBUG,
)


# ─────────────────────────────────────────────
# MIDDLEWARE
# Starlette applies middleware in reverse registration order (last registered
# runs first on the request). Register outermost layers last.
# Order on request: CORS → Logging → RateLimit → GZip → route handler
#
# FIX: Never wrap custom middleware inside BaseHTTPMiddleware(dispatch=...).
# If your middleware already extends BaseHTTPMiddleware, register it directly.
# Double-wrapping causes requests to hang indefinitely (call_next deadlock).
# ─────────────────────────────────────────────

# 1. GZip — compress responses (innermost)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# 2. Rate limiting — registered directly, NOT via BaseHTTPMiddleware(dispatch=...)
app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.RATE_LIMIT_PER_MINUTE)

# 3. Request logging — registered directly, NOT via BaseHTTPMiddleware(dispatch=...)
app.add_middleware(LoggingMiddleware)

# 4. CORS — must be outermost so preflight OPTIONS requests are handled first
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Process-Time"],
)


# ─────────────────────────────────────────────
# VALIDATION ERROR HELPERS
# ─────────────────────────────────────────────

def _sanitize_validation_errors(raw_errors: list) -> list:
    """
    Pydantic v2 embeds raw Exception objects inside error['ctx']['error'].
    These are NOT JSON-serialisable and cause a 500 crash if passed directly
    to JSONResponse. Convert every ctx value to str before serialising.
    """
    clean = []
    for err in raw_errors:
        safe: dict = {
            "field": " -> ".join(str(loc) for loc in err.get("loc", [])),
            "type":  err.get("type", ""),
        }
        ctx = err.get("ctx", {})
        safe_ctx = {k: str(v) for k, v in ctx.items()}
        safe["message"] = safe_ctx.get("error") or err.get("msg", "Invalid value")
        if safe_ctx:
            safe["context"] = safe_ctx
        clean.append(safe)
    return clean


def _friendly_validation_summary(clean_errors: list) -> str:
    for e in clean_errors:
        msg   = e.get("message", "").lower()
        field = e.get("field",   "").lower()

        if "subcategory" in field or "subcategory" in msg:
            return e["message"]
        if "business_category" in field or "business_category" in msg:
            return (
                "Invalid business category. Must be one of: lodges, food, "
                "services, products, health, property_agent, ticket_sales."
            )
        if "password" in field:
            return (
                "Password must be at least 8 characters and include "
                "uppercase, lowercase, and a digit."
            )
        if "email" in field:
            return "Please provide a valid email address."
        if "phone" in field:
            return "Please provide a valid Nigerian phone number (+234...)."

    if len(clean_errors) == 1:
        return clean_errors[0]["message"]
    return f"{len(clean_errors)} validation error(s). See 'details' for each field."


# ─────────────────────────────────────────────
# EXCEPTION HANDLERS
# ─────────────────────────────────────────────

@app.exception_handler(LocalyException)
async def localy_exception_handler(request: Request, exc: LocalyException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "message": exc.detail,
                "type":    exc.__class__.__name__,
            },
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        clean   = _sanitize_validation_errors(exc.errors())
        summary = _friendly_validation_summary(clean)
    except Exception as parse_err:
        logger.error(f"Failed to parse validation errors: {parse_err}")
        clean   = [{"message": "Invalid request data", "field": "unknown", "type": "parse_error"}]
        summary = "Invalid request data. Please check your input."

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "success": False,
            "error": {
                "message": summary,
                "type":    "ValidationError",
                "details": clean,
            },
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(
        f"Unexpected error on {request.method} {request.url.path}: {exc}",
        exc_info=True,
    )
    if is_production():
        content = {
            "success": False,
            "error": {"message": "Internal server error", "type": "InternalServerError"},
        }
    else:
        content = {
            "success": False,
            "error": {"message": str(exc), "type": exc.__class__.__name__},
        }
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=content)


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@app.get("/", tags=["Root"])
async def root():
    return {
        "success": True,
        "data": {
            "app":         settings.APP_NAME,
            "version":     settings.API_VERSION,
            "environment": settings.APP_ENV,
            "message":     "Welcome to Localy API",
        },
    }


@app.get("/health", tags=["Health"])
async def health_check():
    db_healthy = await check_db_connection()
    return {
        "success": True,
        "data": {
            "status":   "healthy"   if db_healthy else "unhealthy",
            "database": "connected" if db_healthy else "disconnected",
            "version":  settings.API_VERSION,
        },
    }


app.include_router(
    api_router,
    prefix=f"{settings.API_PREFIX}/{settings.API_VERSION}",
)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="debug" if settings.DEBUG else "info",
    )