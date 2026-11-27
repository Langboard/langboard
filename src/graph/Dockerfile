FROM python:3.12

WORKDIR /app/src/graph

ENV PIP_DISABLE_PIP_VERSION_CHECK=on
ENV UV_HTTP_TIMEOUT=120

RUN apt-get update && apt-get install -y
RUN apt update && apt install -y
RUN apt install libuv1-dev libssl-dev systemd build-essential curl ca-certificates tar -y
RUN rm -rf /var/lib/apt/lists/*

ADD https://astral.sh/uv/install.sh /uv-installer.sh

RUN sh /uv-installer.sh && rm /uv-installer.sh

ENV PATH="/root/.local/bin/:$PATH"

RUN uv --version

COPY ./graph /app/src/graph
COPY ./shared/py /app/src/shared/py

RUN cd /app/src/shared/py && uv venv && uv sync
RUN cd /app/src/graph && uv venv && uv sync
