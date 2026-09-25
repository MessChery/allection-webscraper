# =============================================================================
# Allection scraping microservice — Dockerfile
# Target: Ubuntu VM running Docker (linux/amd64 or linux/arm64)
# Base:   python:3.14-slim  (Debian bookworm, minimal footprint)
# =============================================================================

FROM python:3.14-slim

# ── Metadata ──────────────────────────────────────────────────────────────
LABEL maintainer="Allection Engineering <eng@allection.app>" \
      version="1.0.0" \
      description="Allection web scraping microservice"

# ── System hardening & hygiene ────────────────────────────────────────────
# - Don't run as root inside the container
# - Disable .pyc file generation (saves space, not needed in containers)
# - Force unbuffered stdout/stderr so logs appear immediately in Docker
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create a non-root user for the application process
RUN addgroup --system appgroup && \
    adduser  --system --ingroup appgroup --no-create-home appuser

# ── Working directory ─────────────────────────────────────────────────────
WORKDIR /app

# ── Dependencies (own layer — cached unless requirements.txt changes) ─────
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Application source ────────────────────────────────────────────────────
# Copy only the files the application needs at runtime.
# .dockerignore should exclude .venv, __pycache__, .git, etc.
COPY models.py         .
COPY base_scraper.py   .
COPY client.py         .
COPY router.py         .
COPY main.py           .
COPY strategies/       ./strategies/

# ── Ownership ─────────────────────────────────────────────────────────────
RUN chown -R appuser:appgroup /app
USER appuser

# ── Runtime ───────────────────────────────────────────────────────────────
EXPOSE 8000

# uvicorn[standard] uses uvloop + httptools on Linux for best async throughput
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
