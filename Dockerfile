FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.local/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        tar \
        curl \
        gnupg \
        ca-certificates \
        libpq-dev \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL https://cursor.com/install | bash \
    && corepack enable \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

RUN uv pip install --system -e .
