# Pear API container (Cloud Run). Lives at the REPO ROOT so that, from the repo root,
#
#     gcloud run deploy pear-api --source .        (see infra/deploy.sh / `make deploy`)
#
# just works (addendum 13). Layout inside the image mirrors the repo, which is what
# api/core/config.py expects (settings.fixtures_dir -> /app/contracts/fixtures):
#
#     /app/api         the FastAPI app (WORKDIR, `uvicorn main:app`)
#     /app/contracts   fixtures for /mock, demo_products, kb exports
#
# Local check:  make docker-build && make docker-run   then  curl localhost:8080/health
# Debug a broken image:  docker run --rm -it --entrypoint bash pear-api
#
# ONE uvicorn worker on purpose: the picks prefetch started by a scan and the /v1/picks call
# that follows must meet in the same process memory (addendum 13.3). Scale with instances
# (after judging), never with workers.

FROM python:3.13-slim

ARG PEAR_GIT_SHA=dev

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PEAR_GIT_SHA=${PEAR_GIT_SHA} \
    PORT=8080

WORKDIR /app/api

# Dependencies in their own layer: code edits don't reinstall packages.
COPY api/requirements.txt /app/api/requirements.txt
RUN pip install --no-cache-dir -r /app/api/requirements.txt

# App code + contracts (fixtures, demo products, kb exports). .dockerignore keeps the iOS
# app, docs, venvs, caches and secrets out.
COPY api/ /app/api/
COPY contracts/ /app/contracts/

# Non-root runtime user. Code stays root-owned (read-only for the app).
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin pear
USER pear

EXPOSE 8080

# Shell form so ${PORT} (set by Cloud Run) expands; `exec` makes uvicorn PID 1 so it gets
# SIGTERM directly and the lifespan drain (core.background.drain) runs on shutdown.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --timeout-keep-alive 75
