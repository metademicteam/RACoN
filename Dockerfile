# RACoN Coordinator Dockerfile
# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RACON_HOST=0.0.0.0 \
    RACON_PORT=8765 \
    RACON_DATA_DIR=/data \
    AUTH_TOKEN=racon-local-dev-token \
    OLLAMA_URL=http://host.docker.internal:11434 \
    RACON_CORS_ORIGINS=*

WORKDIR /app

# Install system utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    curl \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY docker/requirements-docker.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy backend application source
COPY backend /app/backend
COPY cli /app/cli
COPY racon /app/racon

# Create persistent data directory
RUN mkdir -p /data

EXPOSE 8765

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8765/health || exit 1

CMD ["python", "-m", "uvicorn", "backend.racon.api.app:app", "--host", "0.0.0.0", "--port", "8765"]
