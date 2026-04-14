# ──────────────────────────────────────────────────────────────
# Localy Backend — Dockerfile
# ──────────────────────────────────────────────────────────────
# Stage 1: dependency builder (keeps final image lean)
FROM python:3.11-slim AS builder

WORKDIR /app

# System deps needed to compile psycopg2 / geoalchemy2
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# FIX: install into a local prefix so we can copy to final stage
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ──────────────────────────────────────────────────────────────
# Stage 2: runtime image
FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime system deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# FIX: run as a non-root user for security
RUN addgroup --system localy && adduser --system --ingroup localy localy

# Copy application code — exclude secrets and local env files
COPY --chown=localy:localy . .

# Make sure .env files are never baked into the image
# (they should be injected via env_file: in docker-compose or a secrets manager)
RUN rm -f .env .env.development .env.production .env.local

USER localy

EXPOSE 8000

# Healthcheck so Docker and orchestrators can detect a bad container
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command — override in docker-compose for dev (add --reload)
# Production: add --workers $(nproc) for multi-core utilisation
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]