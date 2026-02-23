# =============================================================================
# Dockerfile - Azure OpenAI Model Migration Evaluation Framework
# Multi-stage build: slim Python image with Flask web server
# =============================================================================

FROM python:3.13-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install minimal system dependencies (curl is needed for HEALTHCHECK)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Dependencies layer (cached unless requirements.txt changes) ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application layer ──
COPY . .

# Create required directories that may not exist in a fresh clone
RUN mkdir -p logs data/results data/synthetic/classification \
    data/synthetic/dialog data/synthetic/general \
    data/synthetic/topics \
    prompts/gpt4 prompts/gpt5 prompts/history \
    prompts/topics

# Expose the Flask port
EXPOSE 5000

# Health check — calls the /api/health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

# Run the web server bound to 0.0.0.0 so it is reachable from outside the container
CMD ["python", "app.py", "web", "--host", "0.0.0.0", "--port", "5000"]
