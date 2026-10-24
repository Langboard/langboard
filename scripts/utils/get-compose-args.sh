#!/bin/bash

# Get Docker Compose arguments based on environment variables
# Usage: source scripts/utils/get-compose-args.sh or bash scripts/utils/get-compose-args.sh

# Source utility functions
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/utils.sh"

# Keep an explicitly supplied trace sink ahead of an empty .env default.
trace_endpoint_from_environment="${SOCKET_PHOENIX_OTEL_TRACES_ENDPOINT:-}"

# Load .env file if it exists
if [ -f .env ]; then
    source .env
fi

if [ -n "${trace_endpoint_from_environment}" ]; then
    SOCKET_PHOENIX_OTEL_TRACES_ENDPOINT="${trace_endpoint_from_environment}"
fi

# Validate external postgres URLs if they are set
if [ -n "$POSTGRES_EXTERNAL_MAIN_URL" ]; then
    validate_postgres_url "$POSTGRES_EXTERNAL_MAIN_URL" "POSTGRES_EXTERNAL_MAIN_URL"

    if [ -n "$POSTGRES_EXTERNAL_REPLICA_URL" ]; then
        validate_postgres_url "$POSTGRES_EXTERNAL_REPLICA_URL" "POSTGRES_EXTERNAL_REPLICA_URL"
    fi
fi

COMPOSE_PREFIX="./docker/docker-compose"

# Base compose args (include the main compose file)
COMPOSE_ARGS="-f ${COMPOSE_PREFIX}.yaml -f ${COMPOSE_PREFIX}.kafka.yaml"

# Check if we should use external postgres (skip postgres compose files)
if [ -z "${POSTGRES_EXTERNAL_MAIN_URL}" ]; then
    COMPOSE_ARGS+=" -f ${COMPOSE_PREFIX}.pg.yaml"
fi

# Backup service is enabled by default
WITH_DB_BACKUP="${WITH_DB_BACKUP:-true}"
WITH_DB_BACKUP="$(printf '%s' "$WITH_DB_BACKUP" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
if [ "${WITH_DB_BACKUP}" = "true" ]; then
    COMPOSE_ARGS+=" -f ${COMPOSE_PREFIX}.backup.yaml"
fi

COMPOSE_ARGS+=" -f ${COMPOSE_PREFIX}.redis.yaml -f ${COMPOSE_PREFIX}.server.yaml"

if [ -n "${REDIS_EXTERNAL_CACHE_URL:-}${REDIS_EXTERNAL_BROKER_URL:-}" ]; then
    : "${REDIS_EXTERNAL_CACHE_URL:?REDIS_EXTERNAL_CACHE_URL is required for external Redis}"
    : "${REDIS_EXTERNAL_BROKER_URL:?REDIS_EXTERNAL_BROKER_URL is required for external Redis}"
    : "${CACHE_KEY_PREFIX:?CACHE_KEY_PREFIX is required for external Redis}"
    : "${BROKER_KEY_PREFIX:?BROKER_KEY_PREFIX is required for external Redis}"
    COMPOSE_ARGS+=" -f ${COMPOSE_PREFIX}.redis-external.yaml"
fi
# External realtime is an explicit deployment choice, never an endpoint fallback.
socket_mode="${SOCKET_DEPLOYMENT_MODE:-bundled}"
socket_mode="$(printf '%s' "$socket_mode" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
case "$socket_mode" in
    bundled) ;;
    external)
        for name in SOCKET_URL SOCKET_INTERNAL_URL SOCKET_EDITOR_INTERNAL_URL; do
            value="${!name}"
            schemes="https?"
            [ "$name" = "SOCKET_URL" ] && schemes="https?|wss?"
            pattern="^($schemes)://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._-]+)(:[0-9]+)?(/[^[:space:]?#\\]*)?$"
            if [[ ! "$value" =~ $pattern ]]; then
                echo "$name must be an explicit compatible service URL without credentials, query or fragment" >&2
                exit 1
            fi
        done
        COMPOSE_ARGS+=" -f ${COMPOSE_PREFIX}.phoenix-external.yaml"
        ;;
    *) echo "SOCKET_DEPLOYMENT_MODE must be bundled or external" >&2; exit 1 ;;
esac
COMPOSE_ARGS+=" --env-file ./.env"

# Optional compose args
VAULT_COMPOSE_ARGS="-f ${COMPOSE_PREFIX}.vault.yaml"
DOCS_COMPOSE_ARGS="-f ${COMPOSE_PREFIX}.docs.yaml"
UI_WATCHER_COMPOSE_ARGS="-f ${COMPOSE_PREFIX}.ui-watcher.yaml"
OLLAMA_SHARED_COMPOSE_ARGS="-f ${COMPOSE_PREFIX}.ollama.shared.yaml"
OLLAMA_CPU_COMPOSE_ARGS="-f ${COMPOSE_PREFIX}.ollama.cpu.yaml ${OLLAMA_SHARED_COMPOSE_ARGS}"
OLLAMA_GPU_COMPOSE_ARGS="-f ${COMPOSE_PREFIX}.ollama.gpu.yaml ${OLLAMA_SHARED_COMPOSE_ARGS}"
OTEL_COMPOSE_ARGS="-f ${COMPOSE_PREFIX}.otel.yaml --profile socket-phoenix --profile socket-phoenix-otel"

# Append optional compose args based on environment variables
if [ "${WITH_DOCS}" = "true" ]; then
    COMPOSE_ARGS+=" ${DOCS_COMPOSE_ARGS}"
fi

if [ "${WITH_UI_WATCHER}" = "true" ]; then
    COMPOSE_ARGS+=" ${UI_WATCHER_COMPOSE_ARGS}"
fi

if [ "${WITH_OLLAMA_CPU}" = "true" ]; then
    COMPOSE_ARGS+=" ${OLLAMA_CPU_COMPOSE_ARGS}"
fi

if [ "${WITH_OLLAMA_GPU}" = "true" ]; then
    COMPOSE_ARGS+=" ${OLLAMA_GPU_COMPOSE_ARGS}"
fi

if [ "${WITH_OTEL}" = "true" ] && [ "$socket_mode" = "bundled" ]; then
    COMPOSE_ARGS+=" ${OTEL_COMPOSE_ARGS}"
fi

if [ "${WITH_OTEL}" = "true" ] && [ "$socket_mode" = "bundled" ] && [ -n "${SOCKET_PHOENIX_OTEL_TRACES_ENDPOINT:-}" ]; then
    COMPOSE_ARGS+=" -f ${COMPOSE_PREFIX}.phoenix-otel-traces.yaml"
fi

# Match the runtime default and compare the resolved value, not a file prefix.
vault_provider="${KEY_PROVIDER_TYPE:-openbao-local}"
vault_provider="$(printf '%s' "$vault_provider" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
if [ "$vault_provider" = "openbao-local" ]; then
    COMPOSE_ARGS+=" ${VAULT_COMPOSE_ARGS}"
fi

# Output the compose args
echo "${COMPOSE_ARGS}"
