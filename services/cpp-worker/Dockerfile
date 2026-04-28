# syntax=docker/dockerfile:1.7
# Multi-stage Dockerfile for cpp-worker.
#
# Stage 1 (builder): Debian slim + g++ + cmake; builds the binary with
#   -Wall -Wextra -Werror via the project's CMakeLists.
# Stage 2 (runtime): fresh Debian slim, just the compiled binary plus
#   wget for the docker healthcheck.

FROM debian:bookworm-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    ca-certificates \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY CMakeLists.txt /build/CMakeLists.txt
COPY src/ /build/src/

RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --parallel \
    && strip build/cpp_worker || true


FROM debian:bookworm-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    wget \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 1000 --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder /build/build/cpp_worker /usr/local/bin/cpp_worker

USER appuser

EXPOSE 8003

CMD ["/usr/local/bin/cpp_worker"]
