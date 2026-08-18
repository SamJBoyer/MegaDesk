FROM python:3.12-slim-bookworm

# Built with the worktree root as context so megadesk-contracts can be installed;
# see MachineFactoryManager.pool.build_image.

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

COPY Nodes/Factory/MachineFactory/requirements-sandbox.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# The wire contract, installed rather than copied: the sandbox reads the hash the
# manager wrote, so both must mean the same thing by every field name.
COPY MegaDesk-contracts /app/MegaDesk-contracts
RUN pip install --no-cache-dir /app/MegaDesk-contracts

COPY Nodes/Factory/MachineFactory/AgentHandler /app/AgentHandler

RUN mkdir -p /workspace /bare
WORKDIR /workspace

# AgentHandler is the container entrypoint.
# Mount Floor worktree at /workspace and Floor/<repo>/.bare at /bare.
CMD ["python", "-m", "AgentHandler"]
