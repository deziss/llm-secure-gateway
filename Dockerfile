# syntax=docker/dockerfile:1
#
# Multi-stage build for the LLM Secure Gateway.
#
#   docker build --target runtime -t llm-gw .     # production image (published)
#   docker build --target ci -t llm-gw:ci .       # pytest-capable image (CI, dev)
#
# `ci` is the LAST stage, so a bare `docker build .` produces the CI image. Every
# consumer (docker-publish.yml, ci.yml, each docker-compose*.yml) therefore states
# `target:` explicitly — never rely on stage order.
#
# Design notes
#   * Dependencies come from the committed `requirements.base.txt` lock and are
#     installed with `uv` into a self-contained venv at /opt/venv, so the whole
#     dependency layer is a single `COPY` into the final stage: editing `src/`
#     never reinstalls a package.
#   * `requirements.base.txt` intentionally excludes the `[test]` and `[evals]`
#     extras: the production image ships neither pytest nor pandas/numpy.
#   * `.dockerignore` keeps `.git`, `.env*`, `tests/` and local tool state out of
#     the build context, so nothing sensitive can be COPYed by accident.
#   * No pip, setuptools, wheel or build tooling survives in the final image.

ARG PYTHON_IMAGE=python:3.14.7-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.6

# ─────────────────────────────────────────────────────────────────────────────
# uv binary (pinned, so builds don't depend on whatever pip resolves today)
# ─────────────────────────────────────────────────────────────────────────────
FROM ${UV_IMAGE} AS uv

# ─────────────────────────────────────────────────────────────────────────────
# base — shared runtime configuration
# ─────────────────────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH \
    PYTHONFAULTHANDLER=1 \
    # Never let uv fetch a different interpreter than the base image's.
    UV_PYTHON_DOWNLOADS=never \
    # Precompile .py -> .pyc at install time: faster start-up, ~+2 MB for -O0.
    UV_COMPILE_BYTECODE=1 \
    # Cross-layer copies of the venv must not use hardlinks.
    UV_LINK_MODE=copy
WORKDIR /app

# ─────────────────────────────────────────────────────────────────────────────
# builder — resolve + install production dependencies only
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS builder
COPY --from=uv /uv /usr/local/bin/uv
# Lock first: this layer is cached until a dependency genuinely changes.
COPY requirements.base.txt pyproject.toml README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv \
 && uv pip install --python /opt/venv/bin/python --no-deps -r requirements.base.txt \
 # The project itself, with --no-deps so uv does not re-resolve: deps above win.
 && uv pip install --python /opt/venv/bin/python --no-deps .

# ─────────────────────────────────────────────────────────────────────────────
# test-builder — builder + the `[test]` extra (CI only, never published)
# ─────────────────────────────────────────────────────────────────────────────
FROM builder AS test-builder
COPY requirements.test.txt ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv/bin/python --no-deps -r requirements.test.txt

# ─────────────────────────────────────────────────────────────────────────────
# runtime — the published image
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS runtime
LABEL org.opencontainers.image.title="llm-secure-gateway" \
      org.opencontainers.image.description="Secure LLM Gateway for Ollama/vLLM with API keys, SPIFFE identity and observability." \
      org.opencontainers.image.source="https://github.com/deziss/llm-secure-gateway" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

# Unprivileged runtime user (uid 1000, unchanged from the previous image).
RUN useradd --create-home --uid 1000 --user-group --shell /usr/sbin/nologin gateway

COPY --from=builder /opt/venv /opt/venv
# App code. Templates and static assets live inside the package, so `src` covers
# them. `alembic*` and `scripts` stay because `deploy/docker-compose.yml` and the
# dev stack run `python scripts/migrate.py` from this image with no bind mounts.
# `pyproject.toml` is only metadata (the dev stack bind-mounts over it).
COPY --chown=gateway:gateway pyproject.toml README.md ./
COPY --chown=gateway:gateway src ./src
COPY --chown=gateway:gateway alembic.ini ./alembic.ini
COPY --chown=gateway:gateway alembic ./alembic
COPY --chown=gateway:gateway scripts ./scripts

USER gateway
EXPOSE 8000

# The slim base has no curl/wget, so probe /health with the stdlib. Same pattern
# as agent/Dockerfile. /health runs `SELECT 1`, i.e. it verifies DB connectivity
# too, which is what load balancers and `depends_on: service_healthy` need.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

# Deliberately free of `--loop` / `--workers`: uvicorn's CLI honours
# UVICORN_LOOP / UVICORN_WORKERS (click auto_envvar_prefix=UVICORN), and an
# explicit flag here would silently override deployment-level settings.
# uvloop is still used by default — uvicorn's loop="auto" picks it up because
# uvicorn[standard] installs it.
CMD ["uvicorn", "llm_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ─────────────────────────────────────────────────────────────────────────────
# ci — runtime layout plus test deps, tests/ and the SDK used by the suite
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS ci
COPY --from=test-builder /opt/venv /opt/venv
# pyproject.toml is pytest's config file here ([tool.pytest.ini_options] sets
# asyncio_mode=auto, testpaths and pythonpath) — without it the suite silently
# stops recognising async fixtures.
COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY scripts ./scripts
COPY tests ./tests
COPY sdk ./sdk
CMD ["python", "-m", "pytest", "tests/", "-v", "--tb=short"]
