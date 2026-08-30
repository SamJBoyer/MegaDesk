# CodeScope HTTP service. Build from the worktree root:
#   docker build -f Nodes/Cloud/CodeScope/Dockerfile -t codescope-http .
#
# This is not the MegaDesk canvas image and not the MachineFactory sandbox.

FROM python:3.13-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCOPE_ROOT=/data/scope \
    PATH="/root/.local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Local Cursor agents need the CLI/bridge the SDK launches.
RUN curl https://cursor.com/install -fsS | bash \
    && agent --version || cursor-agent --version || true

WORKDIR /src

COPY MegaDesk-Contracts /src/MegaDesk-Contracts
COPY Nodes/Cloud/CodeScope /src/Nodes/Cloud/CodeScope

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        /src/MegaDesk-Contracts \
        /src/Nodes/Cloud/CodeScope

RUN mkdir -p /data/scope
VOLUME ["/data/scope"]
EXPOSE 8080

WORKDIR /src/Nodes/Cloud/CodeScope
CMD ["python", "-m", "CodeScopeManager", "serve", "--host", "0.0.0.0", "--port", "8080"]
