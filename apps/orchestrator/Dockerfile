FROM python:3.11-slim

WORKDIR /workspace
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ffmpeg && rm -rf /var/lib/apt/lists/*

# Keep the virtual environment outside the source mount used by docker-compose.
# The same resolved dependency graph is used by CI and this development image.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY apps/media-engine ./apps/media-engine
COPY apps/orchestrator ./apps/orchestrator

WORKDIR /workspace/apps/orchestrator
RUN pip install --no-cache-dir "uv==0.10.0" \
    && uv sync --locked --project /workspace/apps/orchestrator --extra dev

# Run as a non-root user. /data/media is pre-created with the app user's owner
# so the named media volume inherits it on first use; bind-mounted sources in
# dev compose stay readable through their default world-readable permissions.
RUN groupadd -r appuser && useradd -r -g appuser -m -d /home/appuser appuser \
    && mkdir -p /data/media \
    && chown -R appuser:appuser /workspace /opt/venv /data/media /home/appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "muse_orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]
