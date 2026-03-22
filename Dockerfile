# proxy-agent: OpenAI-compatible HTTP proxy + Cursor Agent CLI
FROM python:3.12-slim-bookworm

# Official Cursor Agent CLI installer needs bash, curl, tar (see https://cursor.com/install )
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    tar \
    && rm -rf /var/lib/apt/lists/*

ENV HOME=/root
ENV PATH="/root/.local/bin:${PATH}"

RUN curl -fsSL https://cursor.com/install | bash

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY pyproject.toml README.md ./
COPY src/proxy_agent ./src/proxy_agent/

RUN pip install --upgrade pip \
    && pip install .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import socket; s=socket.create_connection(('127.0.0.1',8000),2); s.close()"

CMD ["uvicorn", "proxy_agent.app:app", "--host", "0.0.0.0", "--port", "8000"]
