FROM ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa AS uv
FROM node:22@sha256:0e5f906573693feaa1e21057ebdcfdb5bd5021f050b2dc7c9deceb629c7da2a8 AS node
FROM hexpm/elixir:1.19.5-erlang-28.5.0.5-debian-bookworm-20260824-slim@sha256:6eed5992d974265841fd43857e21236d3c5a700de70caaff234b74f6682921fd AS integration-test

WORKDIR /work

ENV MIX_ENV=test \
    PROJECT_NAME=langboard \
    PROJECT_SHORT_NAME=lb \
    HEX_HTTP_CONCURRENCY=4 \
    HEX_HTTP_TIMEOUT=120 \
    UV_HTTP_TIMEOUT=120 \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    COREPACK_HOME=/opt/corepack

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake ca-certificates curl git libncurses6 libsctp1 libsqlite3-0 libssl-dev tar unzip xz-utils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=node /usr/local /usr/local

RUN uv python install 3.12 \
    && corepack enable \
    && corepack prepare yarn@1.22.22 --activate \
    && mix local.hex --force \
    && mix local.rebar --force

# Preserve the existing cross-runtime gate, rather than rebuilding its test list.
COPY pyproject.toml uv.lock README.md alembic.ini Makefile tach.toml ./
COPY src ./src
COPY scripts ./scripts
COPY docker ./docker

RUN uv sync --frozen \
    && cd src/socket && mix deps.get \
    && cd ../shared/ts && yarn install --frozen-lockfile --ignore-scripts --network-concurrency 4 --network-timeout 120000 && yarn build \
    && cd ../../ui && yarn install --frozen-lockfile --ignore-scripts --network-concurrency 4 --network-timeout 120000

RUN groupadd --gid 10001 validation \
    && useradd --uid 10001 --gid 10001 --create-home validation \
    && mkdir -p /result \
    && cp -a /root/.mix /home/validation/.mix \
    && cp -a /root/.hex /home/validation/.hex \
    && chown -R validation:validation /work /result /home/validation /opt/uv /opt/corepack

ENV HOME=/home/validation
USER 10001:10001

CMD ["/bin/bash", "scripts/check-phoenix-validation.sh"]
