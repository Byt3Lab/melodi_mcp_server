# ==============================================================================
# Multi-stage Dockerfile for MELODI MCP Server
# ==============================================================================

# --- Stage 1: Build stage ---
FROM python:3.12-slim AS builder

WORKDIR /build

# Build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy package definition and project files for installation
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

# Install the application and dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# --- Stage 2: Runtime stage ---
FROM python:3.12-slim AS runner

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/bash -m appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appuser /app/data

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set runtime environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_TRANSPORT=http \
    MCP_SERVER_HOST=0.0.0.0 \
    MCP_SERVER_PORT=8765

# Switch to non-root user
USER appuser

# Expose MCP HTTP & Admin port
EXPOSE 8765

# Container healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -s http://localhost:8765/health | grep -q 'melodi_mcp_server' || exit 1

ENTRYPOINT ["melodi-mcp-server"]
