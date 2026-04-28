# syntax=docker/dockerfile:1.7
# Multi-stage Dockerfile for the fault-injector service.
#
# Mounted with /var/run/docker.sock at runtime so it can drive
# container lifecycle on the host docker daemon.

FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY services/fault-injector/requirements.txt /build/requirements.txt
RUN pip install --upgrade pip && pip install -r /build/requirements.txt


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY shared/ /app/shared/
COPY services/fault-injector/app/ /app/app/

EXPOSE 8004

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${SERVICE_PORT:-8004}"]
