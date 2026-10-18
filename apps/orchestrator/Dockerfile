# Build context is the repository root (see docker-compose.yml), because the orchestrator
# depends on the sibling apps/media-engine package.
FROM python:3.11-slim
WORKDIR /workspace/apps/orchestrator
# ffmpeg also provides ffprobe. Timeline export and boundary-frame extraction need both.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ffmpeg && rm -rf /var/lib/apt/lists/*
COPY apps/media-engine /workspace/apps/media-engine
RUN pip install --no-cache-dir -e /workspace/apps/media-engine
COPY apps/orchestrator/pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"
COPY apps/orchestrator .
EXPOSE 8000
CMD ["uvicorn", "muse_orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]
