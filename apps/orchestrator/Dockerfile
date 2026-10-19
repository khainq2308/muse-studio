# Build from the repository root (see docker-compose.yml): the image needs apps/media-engine too.
FROM python:3.11-slim

# ffmpeg provides ffprobe as well. The orchestrator needs both: timeline export and boundary-frame
# extraction run them, and without them those features report "unavailable" instead of working.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY apps/media-engine /workspace/apps/media-engine
COPY apps/orchestrator/pyproject.toml /workspace/apps/orchestrator/pyproject.toml
WORKDIR /workspace/apps/orchestrator
RUN pip install --no-cache-dir -e /workspace/apps/media-engine \
    && pip install --no-cache-dir -e ".[dev]"

COPY apps/orchestrator/ ./
EXPOSE 8000
CMD ["uvicorn", "muse_orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]
