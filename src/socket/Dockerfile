FROM hexpm/elixir:1.19.5-erlang-28.5.0.5-debian-bookworm-20260824-slim@sha256:6eed5992d974265841fd43857e21236d3c5a700de70caaff234b74f6682921fd AS tooling

WORKDIR /app/socket

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

RUN mix local.hex --force && mix local.rebar --force

FROM tooling AS build

ENV MIX_ENV=prod

COPY socket/mix.exs socket/mix.lock ./
RUN mix deps.get --only prod && mix deps.compile

COPY socket/config ./config
COPY socket/lib ./lib
COPY socket/rel ./rel
COPY shared/realtime /app/shared/realtime

RUN mix compile --warnings-as-errors && mix release

FROM tooling AS validation-dependencies

ENV MIX_ENV=test \
    HEX_HTTP_CONCURRENCY=4 \
    HEX_HTTP_TIMEOUT=120

COPY socket/mix.exs socket/mix.lock ./
RUN mix deps.get && mix deps.compile

FROM validation-dependencies AS validation-source

COPY socket/config ./config
COPY socket/lib ./lib
COPY socket/test ./test
COPY socket/scripts ./scripts
COPY socket/.formatter.exs ./
COPY shared/realtime /app/shared/realtime

FROM validation-source AS validation

# Export diagnostics even on a failed gate. The central executor must inspect
# result.json before promoting an image; a successful export is not a test pass.
RUN mkdir -p /result/artifacts \
    && status=0 \
    && (mix format --check-formatted \
        && mix compile --warnings-as-errors \
        && mix test \
        && mix deps.unlock --check-unused \
        && mix credo --strict \
        && mix dialyzer --format github --format dialyxir \
        && elixir --sname editor_cluster_probe -S mix run test/integration/editor_cluster_probe.exs) \
        > /result/artifacts/phoenix-validation.log 2>&1 || status=$?; \
    printf '{"schemaVersion":1,"exitCode":%s,"reports":{"junit":[],"artifacts":["artifacts/phoenix-validation.log"]}}\n' \
        "$status" > /result/result.json

FROM scratch AS validation-results
COPY --from=validation /result /result

FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS runtime

ARG PROJECT_NAME=langboard

LABEL com.docker.compose.project=${PROJECT_NAME}

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libncurses6 libstdc++6 openssl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system langboard \
    && useradd --system --gid langboard --home-dir /app langboard

WORKDIR /app

COPY --from=build --chown=langboard:langboard /app/socket/_build/prod/rel/langboard_socket ./

ENV HOME=/app \
    LANG=C.UTF-8 \
    OTEL_SERVICE_NAME=langboard-socket \
    PHX_SERVER=true \
    PORT=5690

USER langboard

EXPOSE 5690

HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=12 \
    CMD curl --fail --silent --show-error --max-time 2 http://127.0.0.1:5690/health || exit 1

ENTRYPOINT ["/app/bin/langboard_socket"]
CMD ["start"]
