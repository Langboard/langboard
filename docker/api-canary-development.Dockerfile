# Reuse the existing canary runtime and its native libraries; reconcile Python
# dependencies with the checked-out lock before accepting new application code.
ARG API_RUNTIME_BASE=ghcr.io/yamonco/fractalops-project-7bdcad0a841cbf9cb68f7b3976cb96aa2c8acf5443df4d67b6c624af8edf504b-api@sha256:d1f0e6e4f5b439839694b2536c6a9f86a49184ebeb28d02feffb21ffb0ecd624
FROM ${API_RUNTIME_BASE} AS development
USER root
WORKDIR /app
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src/sdk/vendor ./src/sdk/vendor
COPY --chown=1000:1000 src/shared/py ./src/shared/py
COPY --chown=1000:1000 src/api ./src/api
COPY scripts/run_*_cron.sh scripts/run_*_recovery.sh ./scripts/
RUN --mount=type=cache,id=langboard-python-packages,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra document-retrieval --extra image-generation
RUN /app/.venv/bin/python -m compileall -q src/api/langboard src/shared/py/langboard_shared
ENV UV_NO_SYNC=1
USER 1000:1000
CMD ["uv", "run", "--no-sync", "langboard", "run", "--workers", "1"]
