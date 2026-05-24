FROM python:3.12-slim

WORKDIR /app

# Install system deps for trafilatura
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libxml2-dev libxslt1-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy and install packages
COPY packages/core /app/packages/core
COPY packages/cli /app/packages/cli

RUN pip install --no-cache-dir /app/packages/core && \
    pip install --no-cache-dir /app/packages/cli && \
    pip install --no-cache-dir trafilatura python-dotenv uvicorn fastapi httpx

# Expose port (Railway sets PORT env var)
EXPOSE 8000

# Start the agent server
CMD ["python", "-m", "ai_prophet.forecast.ensemble_agent"]
