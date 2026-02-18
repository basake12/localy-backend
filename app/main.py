from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
import logging
import time

from app.config import settings
from app.core.database import engine, Base, check_db_connection
from app.core.exceptions import LocalyException
from app.api.v1.router import api_router

logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
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
        logger.error("Database connection failed!")
    else:
        logger.info("Database connection successful")

    if settings.DEBUG and settings.APP_ENV != "production":
        logger.info("Creating database tables...")
        Base.metadata.create_all(bind=engine)

    yield

    logger.info("Shutting down application...")


# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    description="Localy Super App API - Connect with local businesses",
    version=settings.API_VERSION,
    docs_url=f"{settings.API_PREFIX}/{settings.API_VERSION}/docs",
    redoc_url=f"{settings.API_PREFIX}/{settings.API_VERSION}/redoc",
    openapi_url=f"{settings.API_PREFIX}/{settings.API_VERSION}/openapi.json",
    lifespan=lifespan,
    debug=settings.DEBUG,
)


# ─────────────────────────────────────────────
# MIDDLEWARE
# ─────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    logger.info(f"Request: {request.method} {request.url.path}")
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    logger.info(f"Response: {response.status_code} - Time: {process_time:.3f}s")
    return response


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _sanitize_validation_errors(raw_errors: list) -> list:
    """
    Convert Pydantic v2 error dicts into JSON-safe dicts.

    Pydantic v2 puts the original exception object inside
    error['ctx']['error'] — e.g. ValueError("'Hotel' is not a valid
    subcategory for 'food'..."). That object is NOT JSON-serializable,
    which causes the 500 crash. We convert every value in 'ctx' to str.

    We also produce a clean, human-readable 'message' field by preferring
    the ctx error string over the raw Pydantic 'msg'.
    """
    clean = []
    for err in raw_errors:
        # Build a safe copy
        safe: dict = {
            "field":   " → ".join(str(loc) for loc in err.get("loc", [])),
            "type":    err.get("type", ""),
        }

        # ctx may contain non-serializable exception objects (Pydantic v2)
        ctx = err.get("ctx", {})
        safe_ctx = {k: str(v) for k, v in ctx.items()}

        # Prefer the human-readable ctx error string as the message
        # (e.g. "'Hotel' is not a valid subcategory for 'food'.")
        # Fall back to Pydantic's own msg if ctx has no error key.
        if "error" in safe_ctx:
            safe["message"] = safe_ctx["error"]
        else:
            safe["message"] = err.get("msg", "Invalid value")

        if safe_ctx:
            safe["context"] = safe_ctx

        clean.append(safe)

    return clean


def _friendly_validation_summary(clean_errors: list) -> str:
    """
    Return a short top-level summary string for the error response.
    Special-cases common domain errors so the Flutter app can
    surface a useful message without parsing the details array.
    """
    for e in clean_errors:
        msg = e.get("message", "").lower()
        field = e.get("field", "").lower()

        if "subcategory" in field or "subcategory" in msg:
            return e["message"]   # Already human-readable from validator

        if "business_category" in field or "business_category" in msg:
            return (
                "Invalid business category. "
                "Must be one of: lodges, food, services, products, "
                "health, property_agent, ticket_sales."
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

    # Generic fallback
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
    """
    Handles Pydantic v2 validation errors.

    Key fix: exc.errors() in Pydantic v2 embeds raw Exception objects
    inside the 'ctx' dict — those are not JSON-serializable and cause
    a 500 crash if passed straight to JSONResponse. We sanitize them
    with _sanitize_validation_errors() before serializing.
    """
    try:
        raw_errors  = exc.errors()
        clean       = _sanitize_validation_errors(raw_errors)
        summary     = _friendly_validation_summary(clean)
    except Exception as parse_err:
        # Last-resort fallback — should never happen after sanitization
        logger.error(f"Failed to parse validation errors: {parse_err}")
        clean   = [{"message": "Invalid request data", "field": "unknown", "type": "parse_error"}]
        summary = "Invalid request data. Please check your input."

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "success": False,
            "error": {
                "message": summary,   # Clean top-level message for Flutter snackbar
                "type":    "ValidationError",
                "details": clean,     # Per-field breakdown for debug / form highlights
            },
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unexpected error: {exc}", exc_info=True)

    if settings.APP_ENV == "production":
        content = {
            "success": False,
            "error": {
                "message": "Internal server error",
                "type":    "InternalServerError",
            },
        }
    else:
        content = {
            "success": False,
            "error": {
                "message": str(exc),
                "type":    exc.__class__.__name__,
            },
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
            "status":   "healthy" if db_healthy else "unhealthy",
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
        log_level="info",
    )