# RemoteLLM Broker Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy source code
COPY src/ ./src/

# Install the package
RUN uv pip install --no-deps -e .

# Create non-root user
RUN useradd -r -s /bin/false remotellm
USER remotellm

# Expose ports: API (8443), WebSocket tunnel (8444), Health (8080)
EXPOSE 8443 8444 8080

# Default command runs the broker
ENTRYPOINT ["uv", "run", "python", "-m", "remotellm.broker"]
