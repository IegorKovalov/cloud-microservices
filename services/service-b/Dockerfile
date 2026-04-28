# syntax=docker/dockerfile:1.7
# Multi-stage Dockerfile for service-b (Python storage service).
#
# Stage 1 (builder): install dependencies into a virtualenv on top of a
# slim base image. Stage 2 (runtime): copy the venv + application code
# into a fresh slim image so the final layer is small and contains no
# build toolchain.

FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY services/service-b/requirements.txt /build/requirements.txt
RUN pip install --upgrade pip && pip install -r /build/requirements.txt


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY shared/ /app/shared/
COPY services/service-b/app/ /app/app/

EXPOSE 8002

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${SERVICE_PORT:-8002}"]
