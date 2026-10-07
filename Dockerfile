FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS base

# System hardening
RUN useradd -m -u 10001 app \
    && mkdir -p /app && chown -R app:app /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# --- builder stage installs locked runtime packages into a venv ---
FROM base AS builder

RUN python -m pip install --no-cache-dir uv==0.12.18
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# The universal lock records package versions and artifact hashes.
RUN uv sync --frozen --no-dev --no-editable --no-python-downloads --reinstall-package procurement-decision-api

# --- runtime stage: minimal surface ---
FROM base AS runtime

ENV PATH="/app/.venv/bin:${PATH}"
COPY --from=builder /app/.venv /app/.venv

USER app
EXPOSE 8088
ENV PORT=8088 HOST=0.0.0.0

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/healthz', timeout=2)" || exit 1

CMD ["python", "-m", "procurement_decision_api"]
