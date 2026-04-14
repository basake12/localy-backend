"""
app/middleware/cors.py

CORS is configured directly in app/main.py via:

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        ...
    )

This file is intentionally kept as a reference stub.
Do NOT call add_cors_middleware() — doing so would register CORSMiddleware
a second time, which causes duplicate CORS headers and broken preflight
responses in some browsers.

Allowed origins are controlled by ALLOWED_ORIGINS in .env:
    ALLOWED_ORIGINS=https://localy.ng,https://admin.localy.ng
"""