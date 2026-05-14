FROM python:3.13-slim AS base

# System hardening
RUN useradd -m -u 10001 app \
    && mkdir -p /app && chown -R app:app /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# --- builder stage installs into a venv we copy into the final image ---
FROM base AS builder

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install the package + runtime deps only (no dev deps).
RUN pip install --upgrade pip && pip install .

# --- runtime stage: minimal surface ---
FROM base AS runtime

ENV PATH="/opt/venv/bin:${PATH}"
COPY --from=builder /opt/venv /opt/venv

USER app
EXPOSE 8088
ENV PORT=8088 HOST=0.0.0.0

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/healthz', timeout=2)" || exit 1

CMD ["python", "-m", "procurement_decision_api"]
