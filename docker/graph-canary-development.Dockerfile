# Reconcile Graph source and its independent lock on the existing canary runtime.
ARG GRAPH_RUNTIME_BASE=ghcr.io/yamonco/fractalops-project-7bdcad0a841cbf9cb68f7b3976cb96aa2c8acf5443df4d67b6c624af8edf504b-graph@sha256:e7944d82305a9ec5656c7f70ec1eb8fb6123f490c238983e9203651475fc9d86
FROM ${GRAPH_RUNTIME_BASE} AS development
USER root
WORKDIR /app/src/graph
COPY src/sdk/vendor /app/src/sdk/vendor
COPY --chown=1000:1000 src/shared/py /app/src/shared/py
COPY --chown=1000:1000 src/graph /app/src/graph
RUN --mount=type=cache,id=langboard-graph-python-packages,target=/root/.cache/uv \
    uv sync --locked --no-dev
RUN /app/src/graph/.venv/bin/python -m compileall -q langboard_graph /app/src/shared/py/langboard_shared
ENV UV_NO_SYNC=1
USER 1000:1000
CMD ["uv", "run", "--no-sync", "graph", "run", "--workers", "1"]
