FROM python:3.11-slim

WORKDIR /workspace
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ffmpeg && rm -rf /var/lib/apt/lists/*

# The media engine is a local editable package dependency of the orchestrator.
COPY apps/media-engine ./apps/media-engine
COPY apps/orchestrator ./apps/orchestrator

WORKDIR /workspace/apps/orchestrator
RUN pip install --no-cache-dir -e ".[dev]" -e "../media-engine"

EXPOSE 8000
CMD ["uvicorn", "muse_orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]
