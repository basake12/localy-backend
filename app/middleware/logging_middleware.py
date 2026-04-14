"""
app/middleware/logging_middleware.py

Request / response logging.

Registered in main.py as:
    from app.middleware.logging_middleware import LoggingMiddleware
    app.add_middleware(LoggingMiddleware)

FIX APPLIED — ASGI lifespan unsupported warning:
    The class was previously a plain dispatch callable with __call__(request, call_next).
    When registered via app.add_middleware(), Starlette expects a proper ASGI middleware
    class whose __call__ accepts (scope, receive, send). The old shape caused lifespan
    events (startup/shutdown) to be swallowed, producing:
        "ASGI 'lifespan' protocol appears unsupported."

    Fix: subclass BaseHTTPMiddleware and move logic into dispatch(). The base class
    handles the raw ASGI __call__, correctly forwards lifespan and WebSocket scopes,
    and delegates HTTP requests to dispatch().

FIX APPLIED — WebSocket 403:
    Same root cause: BaseHTTPMiddleware intercepts WebSocket upgrade connections
    before they reach the router. Without a scope-type bypass, call_next() on a
    WebSocket scope fails and the connection is rejected with 403.

    Fix: check scope["type"] at the top of dispatch(). WebSocket connections pass
    through to the next ASGI layer with no logging side-effects. WebSocket
    connection/disconnection events are logged inside the ws_manager
    (websocket_manager.py) and the chat endpoint itself.
"""
import logging
import time

from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs method, path, status code, and duration for every HTTP request.
    Skips noisy /health polling and passes WebSocket upgrades straight through.
    """

    _SKIP_PATHS = {"/health", "/"}

    async def dispatch(self, request: Request, call_next) -> Response:
        # ─────────────────────────────────────────────────────────────────
        # [FIX] WEBSOCKET BYPASS
        # WebSocket upgrade connections must not be intercepted here.
        # Logging for WS connect/disconnect lives in websocket_manager.py.
        # ─────────────────────────────────────────────────────────────────
        if request.scope.get("type") == "websocket":
            return await call_next(request)

        start = time.perf_counter()

        if request.url.path not in self._SKIP_PATHS:
            logger.info(f"→ {request.method} {request.url.path}")

        response = await call_next(request)

        duration = time.perf_counter() - start
        response.headers["X-Process-Time"] = f"{duration:.4f}"

        if request.url.path not in self._SKIP_PATHS:
            logger.info(
                f"← {request.method} {request.url.path} "
                f"[{response.status_code}] {duration * 1000:.1f}ms"
            )

        return response