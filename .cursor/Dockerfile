# Cursor Cloud Agent host for MegaDesk.
#
# Bakes the official host env: conda MEGADESK (Python 3.13), native Redis,
# PortAudio for VoiceDeck [audio], and the X11/OpenGL stack Dear PyGui needs
# so `import dearpygui` works. Editable packages are NOT installed here —
# Cursor clones the commit, then `.cursor/install.sh` runs
# refresh_contracts / refresh_nodes against that worktree.
#
# This is not the MachineFactory sandbox
# (Nodes/Factory/MachineFactory/Dockerfile → machine-factory-agent:latest).
# Do not COPY the repo; Cursor owns /workspace.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    CONDA_DIR=/opt/conda \
    ENV_NAME=MEGADESK \
    DISPLAY=:99

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        sudo \
        build-essential \
        redis-server \
        redis-tools \
        libportaudio2 \
        xvfb \
        x11-utils \
        libgl1 \
        libgl1-mesa-dri \
        libglib2.0-0 \
        libx11-6 \
        libx11-xcb1 \
        libxext6 \
        libxrender1 \
        libxi6 \
        libxrandr2 \
        libxinerama1 \
        libxcursor1 \
        libxxf86vm1 \
        libxkbcommon0 \
        libglu1-mesa \
        fonts-dejavu-core \
        gnupg \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI — WorkDispatcher / PRManager / AutoIntegrate shell out to `gh`.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# ubuntu:24.04 already ships an `ubuntu` user; make it passwordless-sudo.
RUN id -u ubuntu >/dev/null 2>&1 || useradd -m -s /bin/bash ubuntu \
    && usermod -aG sudo ubuntu \
    && echo "ubuntu ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/ubuntu \
    && chmod 0440 /etc/sudoers.d/ubuntu

# Miniforge at /opt/conda so refresh_nodes.py / refresh_contracts.py find
# MEGADESK without depending on $HOME (they already search this path).
RUN curl -fsSL -o /tmp/miniforge.sh \
        "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh" \
    && bash /tmp/miniforge.sh -b -p "${CONDA_DIR}" \
    && rm -f /tmp/miniforge.sh \
    && "${CONDA_DIR}/bin/conda" config --system --set auto_activate false \
    && "${CONDA_DIR}/bin/conda" create -y -n "${ENV_NAME}" python=3.13 \
    && "${CONDA_DIR}/bin/conda" clean -afy \
    && chmod -R a+rX "${CONDA_DIR}"

# Third-party wheels only. Editable MegaDesk packages install after clone.
RUN "${CONDA_DIR}/envs/${ENV_NAME}/bin/python" -m pip install --no-cache-dir \
        --upgrade pip setuptools wheel \
    && "${CONDA_DIR}/envs/${ENV_NAME}/bin/python" -m pip install --no-cache-dir \
        "pytest>=8.0" \
        "dearpygui>=2.0" \
        "redis>=5.0.0" \
        "websockets>=12.0" \
        "sounddevice>=0.4.6" \
        "numpy>=1.24" \
        "cursor-sdk>=0.1.0" \
        "docker>=7.0.0" \
        "langgraph>=1.2.11,<2"

# New shells land in MEGADESK; DISPLAY is the Xvfb screen start.sh raises.
RUN printf '%s\n' \
        '# MegaDesk Cloud Agent' \
        "source ${CONDA_DIR}/etc/profile.d/conda.sh" \
        "conda activate ${ENV_NAME} 2>/dev/null || true" \
        'export DISPLAY="${DISPLAY:-:99}"' \
        > /etc/profile.d/megadesk.sh \
    && chmod 0644 /etc/profile.d/megadesk.sh \
    && for rc in /root/.bashrc /home/ubuntu/.bashrc; do \
        printf '\n# MegaDesk Cloud Agent\nsource /etc/profile.d/megadesk.sh\n' >> "$rc"; \
       done \
    && chown ubuntu:ubuntu /home/ubuntu/.bashrc

ENV PATH="/opt/conda/envs/MEGADESK/bin:/opt/conda/bin:${PATH}" \
    CONDA_PREFIX=/opt/conda/envs/MEGADESK \
    CONDA_DEFAULT_ENV=MEGADESK \
    DISPLAY=:99

USER ubuntu
