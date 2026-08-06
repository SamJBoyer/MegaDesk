FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/root/.local/bin:${PATH}" \
    WORKSPACE=/workspace \
    REDIS_URL=redis://host.docker.internal:6379/0

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        git \
        build-essential \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Cursor CLI (agent / cursor-agent)
RUN curl https://cursor.com/install -fsS | bash \
    && agent --version || cursor-agent --version || true

WORKDIR /app

COPY requirements-sandbox.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY redis_packets.py /app/redis_packets.py
COPY PlantManager /app/PlantManager
COPY LiveHarness /app/LiveHarness

RUN mkdir -p /workspace /bare
WORKDIR /workspace

# LiveHarness is the container entrypoint.
# Mount Floor worktree at /workspace and Floor/<repo>/.bare at /bare.
CMD ["python", "-m", "LiveHarness"]
