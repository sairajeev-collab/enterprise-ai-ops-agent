# Single image for both the API and the worker; the process is chosen by the
# container command (see docker-compose.yml / fly.toml). Slim base, no compiler:
# every pinned dependency ships a manylinux wheel, so the image stays small.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first for better layer caching. Copy only what the build
# backend needs to resolve the package metadata.
COPY pyproject.toml README.md ./
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./

RUN pip install .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Default to the API. The worker service overrides this command.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
