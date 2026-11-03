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
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    YARN_CACHE_FOLDER=/home/validation/.cache/yarn \
    COREPACK_HOME=/opt/corepack

ENV PANEL_PACKAGE_MANAGER=yarn

# Keep the security floors in the package layer so cached builds enforce them.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends build-essential cmake ca-certificates curl git libncurses6 libsctp1 libsqlite3-0 libssl-dev tar unzip xz-utils \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libbrotli1)" ge '1.0.9-2+deb12u1' \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libexpat1)" ge '2.5.0-1+deb12u5' \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libssl-dev)" ge '3.0.22-1~deb12u2' \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libssl3)" ge '3.0.22-1~deb12u2' \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' openssl)" ge '3.0.22-1~deb12u2' \
    && rm -rf /var/lib/apt/lists/*

# Create the validation user before dependency installation.  Dependencies are
# written by that user, avoiding a final recursive ownership copy-up of the
# whole image filesystem.
RUN groupadd --gid 10001 validation \
    && useradd --uid 10001 --gid 10001 --create-home validation \
    && mkdir -p /result /opt/uv /opt/corepack \
    && chown -R validation:validation /work /result /opt/uv /opt/corepack /home/validation

COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=node /usr/local /usr/local

# The copied Node bin directory contains Yarn links into /opt, outside the copied tree.
# Validation uses Corepack/Yarn; remove the unused bundled npm dependency tree.
RUN rm -f /usr/local/bin/yarn /usr/local/bin/yarnpkg \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx \
    && rm -rf /usr/local/lib/node_modules/npm \
    && corepack enable

USER 10001:10001
ENV HOME=/home/validation

# BuildKit creates missing mount parents as root; create them as the build user.
RUN mkdir -p /home/validation/.cache/uv /home/validation/.cache/yarn /home/validation/.hex/packages
RUN --mount=type=cache,id=langboard-phoenix-uv,target=/home/validation/.cache/uv,uid=10001,gid=10001 uv python install 3.12
RUN corepack prepare yarn@1.22.22 --activate
RUN mix local.hex --force && mix local.rebar --force

# Install external dependencies from locked metadata before copying application sources.
COPY --chown=validation:validation pyproject.toml uv.lock README.md alembic.ini ./
COPY --chown=validation:validation src/shared/py/pyproject.toml ./src/shared/py/pyproject.toml
COPY --chown=validation:validation src/sdk/vendor ./src/sdk/vendor
COPY --chown=validation:validation src/graph/pyproject.toml src/graph/uv.lock ./src/graph/
# Reject stale graph metadata before installing the other runtime dependencies.
RUN --mount=type=cache,id=langboard-phoenix-uv,target=/home/validation/.cache/uv,uid=10001,gid=10001 uv lock --project src/graph --check
RUN --mount=type=cache,id=langboard-phoenix-uv,target=/home/validation/.cache/uv,uid=10001,gid=10001 uv sync --locked --no-install-project --no-install-package langboard-shared
COPY --chown=validation:validation src/socket/mix.exs src/socket/mix.lock ./src/socket/
RUN --mount=type=cache,id=langboard-phoenix-hex,target=/home/validation/.hex/packages,uid=10001,gid=10001 cd src/socket && mix deps.get && mix deps.compile
COPY --chown=validation:validation src/shared/ts/package.json src/shared/ts/yarn.lock ./src/shared/ts/
RUN --mount=type=cache,id=langboard-phoenix-yarn,target=/home/validation/.cache/yarn,uid=10001,gid=10001,sharing=locked cd src/shared/ts && yarn install --frozen-lockfile --ignore-scripts --network-concurrency 4 --network-timeout 120000
COPY --chown=validation:validation src/shared/ts ./src/shared/ts
RUN cd src/shared/ts && yarn build
COPY --chown=validation:validation src/ui/package.json src/ui/yarn.lock ./src/ui/
RUN --mount=type=cache,id=langboard-phoenix-yarn,target=/home/validation/.cache/yarn,uid=10001,gid=10001,sharing=locked cd src/ui && yarn install --frozen-lockfile --ignore-scripts --network-concurrency 4 --network-timeout 120000

# Keep internal Python installation and the original cross-runtime gate intact.
COPY --chown=validation:validation src ./src
COPY --chown=validation:validation Makefile tach.toml conftest.py ./
COPY --chown=validation:validation scripts ./scripts
COPY --chown=validation:validation .github/workflows ./.github/workflows
COPY --chown=validation:validation docker ./docker
RUN --mount=type=cache,id=langboard-phoenix-uv,target=/home/validation/.cache/uv,uid=10001,gid=10001 uv sync --locked
RUN --mount=type=cache,id=langboard-phoenix-uv,target=/home/validation/.cache/uv,uid=10001,gid=10001 uv sync --project src/graph --locked

# Test execution must use the locked environments installed above without egress.
ENV UV_NO_SYNC=1 \
    UV_OFFLINE=1

# Leave headroom for native allocations within the 2Gi validation container.
ENV NODE_OPTIONS=--max-old-space-size=1536

CMD ["/bin/bash", "scripts/check-phoenix-validation.sh"]
