FROM ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa AS uv
FROM python:3.12@sha256:7ad6d21a25a94b2c00e685e82c2fd298de814353d9ee0e3f7f2cd4fca063df60 AS dependencies

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=on
ENV UV_HTTP_TIMEOUT=120

COPY --from=uv /uv /uvx /bin/

RUN apt-get update \
    && apt-get upgrade -y \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' librsvg2-2)" ge '2.60.0+dfsg-1+deb13u1' \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        libssl-dev \
        libuv1-dev \
        tar \
    && rm -rf /var/lib/apt/lists/*

RUN uv --version

# Only locked metadata/vendor packages affect the external dependency layer.
COPY ./src/shared/py/pyproject.toml ./src/shared/py/pyproject.toml
COPY ./src/sdk/vendor ./src/sdk/vendor
COPY pyproject.toml uv.lock README.md alembic.ini ./
RUN --mount=type=cache,id=langboard-python-packages,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra document-retrieval --extra image-generation \
    --no-install-project --no-install-package langboard-shared

FROM dependencies AS base
COPY ./src/shared/py ./src/shared/py
COPY ./src/api ./src/api
COPY ./scripts/run_*_cron.sh ./scripts/run_*_recovery.sh ./scripts/
RUN --mount=type=cache,id=langboard-python-packages,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra document-retrieval --extra image-generation

# Runtime writes stay in the dedicated application data directory.
RUN groupadd --gid 10001 langboard \
    && useradd --uid 10001 --gid 10001 --create-home langboard \
    && mkdir -p /app/local /app/.fastmcp \
    && chown -R langboard:langboard /app/local /app/.fastmcp

FROM base AS with-aws

RUN cd /app && uv sync --locked --no-dev --extra aws --extra document-retrieval --extra image-generation

FROM base AS with-azure-vault

RUN cd /app && uv sync --locked --no-dev --extra azure-vault --extra document-retrieval --extra image-generation

FROM base AS with-document-processing

# PDF font substitution requires installed CJK glyphs, even with remote inference.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fontconfig fonts-noto-cjk \
    && fc-cache -f \
    && fc-match -f '%{family}\n' ':lang=ko' | grep -q 'Noto.*CJK' \
    && rm -rf /var/lib/apt/lists/*

RUN cd /app && uv sync --locked --no-dev --extra document-processing --extra document-retrieval --extra image-generation
ENV UV_NO_SYNC=1
USER 10001:10001

FROM base AS with-cron

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/* \
    && printf '' | crontab -

ENV UV_NO_SYNC=1
USER 10001:10001

# Validation needs Python application dependencies, not the runtime cron daemon.
FROM dependencies AS validation-dependencies
RUN --mount=type=cache,id=langboard-python-packages,target=/root/.cache/uv \
    uv sync --locked --dev --extra document-retrieval --extra image-generation \
    --no-install-project --no-install-package langboard-shared

FROM validation-dependencies AS validation-source
COPY ./src/shared/py ./src/shared/py
COPY ./src/api ./src/api
COPY ./scripts ./scripts
COPY conftest.py Makefile .dockerignore .env.example ./
COPY ./docker/*.yaml ./docker/
COPY ./docker/envs ./docker/envs
RUN --mount=type=cache,id=langboard-python-packages,target=/root/.cache/uv \
    uv sync --locked --dev --extra document-retrieval --extra image-generation
RUN groupadd --gid 10001 langboard \
    && useradd --uid 10001 --gid 10001 --create-home langboard \
    && mkdir -p /app/local /app/.fastmcp /app/.pytest_cache /result \
    && chown langboard:langboard /app/local /app/.fastmcp /app/.pytest_cache /result
USER 10001:10001

FROM validation-source AS validation-api
ENV PROFILE=api
RUN bash scripts/run-profile-validation.sh

# Reuse locked API dependencies; the central isolated Job supplies a disposable DB.
FROM validation-source AS integration-api
ENV PROFILE=api-postgres \
    UV_NO_SYNC=1 \
    UV_OFFLINE=1
USER 10001:10001
CMD ["bash", "scripts/run-profile-validation.sh"]

# Optional diagnostics run only against the central disposable PostgreSQL fixture.
FROM integration-api AS integration-work-plan-lock-cost
ENV PROFILE=work-plan-lock-cost

FROM scratch AS validation-api-results
COPY --from=validation-api /result/ /result/

# Document validation includes the actual CJK font and remote-VLM dependencies.
FROM with-document-processing AS validation-worker-dependencies
USER root
RUN --mount=type=cache,id=langboard-python-packages,target=/root/.cache/uv \
    uv sync --locked --dev --extra document-processing --extra document-retrieval --extra image-generation
COPY ./scripts/run-profile-validation.sh ./scripts/run-profile-validation.sh
COPY conftest.py ./
RUN mkdir -p /app/.pytest_cache /result && chown langboard:langboard /app/.pytest_cache /result
USER 10001:10001

FROM validation-worker-dependencies AS validation-worker
ENV PROFILE=worker \
    VERIFY_DOCUMENT_FONTS=1
RUN bash scripts/run-profile-validation.sh

FROM scratch AS validation-worker-results
COPY --from=validation-worker /result/ /result/
