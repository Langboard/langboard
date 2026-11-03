FROM ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa AS uv
FROM python:3.12@sha256:7ad6d21a25a94b2c00e685e82c2fd298de814353d9ee0e3f7f2cd4fca063df60 AS dependencies

WORKDIR /app/src/graph

ENV PIP_DISABLE_PIP_VERSION_CHECK=on \
    UV_HTTP_TIMEOUT=120

COPY --from=uv /uv /uvx /bin/

RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

COPY ./sdk/vendor /app/src/sdk/vendor
COPY ./shared/py/pyproject.toml /app/src/shared/py/pyproject.toml
COPY ./graph/pyproject.toml ./graph/uv.lock ./
RUN --mount=type=cache,id=langboard-graph-python-packages,target=/root/.cache/uv \
    uv sync --locked --no-dev \
    --no-install-project --no-install-package langboard-shared

FROM dependencies AS runtime

COPY ./graph /app/src/graph
COPY ./shared/py /app/src/shared/py
RUN --mount=type=cache,id=langboard-graph-python-packages,target=/root/.cache/uv \
    uv sync --locked --no-dev \
    && groupadd --gid 10001 langboard \
    && useradd --uid 10001 --gid 10001 --create-home langboard \
    && mkdir -p /app/local \
    && chown langboard:langboard /app/local

ENV PATH="/app/src/graph/.venv/bin:$PATH" \
    HOME=/home/langboard \
    UV_NO_SYNC=1 \
    GRAPH_HOST=0.0.0.0

USER 10001:10001

EXPOSE 5020

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --start-interval=5s --retries=4 \
    CMD python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:5020/health", timeout=2)'

CMD ["graph", "run", "--workers", "1"]

FROM runtime AS validation

# Share the native scheduler/storage fixture without adding API runtime dependencies.
COPY ./api/tests/bots/test_bot_schedule_recovery.py ./tests/test_bot_schedule_recovery.py

USER root
RUN --mount=type=cache,id=langboard-graph-python-packages,target=/root/.cache/uv \
    UV_CACHE_DIR=/root/.cache/uv uv sync --locked \
    && mkdir -p /result/artifacts \
    && chown -R langboard:langboard /result

USER 10001:10001
ENV PROJECT_NAME=langboard
RUN python - <<'PY'
import json
import pathlib
import subprocess

root = pathlib.Path("/result")
with (root / "artifacts/graph-validation.log").open("w") as log:
    result = subprocess.run([
        "python", "-m", "pytest", "-p", "no:tach", "-q", "--junitxml=/result/graph.xml",
        "tests/test_graph_service_auth.py", "tests/test_bot_status_map.py",
        "tests/test_comment_compaction_draft.py",
        "tests/test_app_lifespan.py", "tests/test_graph_schedule_admission.py",
    ], stdout=log, stderr=subprocess.STDOUT)
(root / "result.json").write_text(json.dumps({
    "schemaVersion": 1,
    "exitCode": result.returncode,
    "reports": {
        "junit": ["graph.xml"] if (root / "graph.xml").is_file() else [],
        "artifacts": ["artifacts/graph-validation.log"],
    },
}) + "\n")
PY

FROM scratch AS validation-results
COPY --from=validation /result/ /result/
