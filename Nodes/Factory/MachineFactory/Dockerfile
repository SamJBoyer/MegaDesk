FROM python:3.12-slim-bookworm

# Built with the worktree root as context so megadesk-contracts can be installed;
# see MachineFactoryManager.pool.build_image.

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/home/megadesk/.local/bin:${PATH}" \
    WORKSPACE=/workspace \
    HOME=/home/megadesk

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

# GitHub CLI for opening PRs from the sandbox
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY Nodes/Factory/MachineFactory/requirements-sandbox.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# The wire contract, installed rather than copied: the sandbox reads the hash the
# manager wrote, so both must mean the same thing by every field name.
COPY MegaDesk-Contracts /app/MegaDesk-Contracts
RUN pip install --no-cache-dir /app/MegaDesk-Contracts

COPY Nodes/Factory/MachineFactory/AgentHandler /app/AgentHandler

RUN useradd --create-home --uid 1000 --shell /bin/bash megadesk \
    && mkdir -p /workspace /home/megadesk/.local \
    && if [ -d /root/.local ]; then cp -a /root/.local/. /home/megadesk/.local/; fi \
    && chmod +x /app/AgentHandler/git_askpass.py \
    && chown -R megadesk:megadesk /home/megadesk /app /workspace

USER megadesk
WORKDIR /workspace

# AgentHandler clones REPO_URL into /workspace, then runs the work graph.
CMD ["python", "-m", "AgentHandler"]
