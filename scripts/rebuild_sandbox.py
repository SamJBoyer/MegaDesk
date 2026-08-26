#!/usr/bin/env python3
"""Rebuild the MachineFactory AgentHandler Docker sandbox image.

Wraps ``python -m MachineFactoryManager build`` so the image picks up the
current MegaDesk-Contracts wire package and AgentHandler sources.

    conda activate MEGADESK
    python scripts/rebuild_sandbox.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ENV_NAME = "MEGADESK"
REPO_ROOT = Path(__file__).resolve().parent.parent
MACHINE_FACTORY = REPO_ROOT / "Nodes" / "Factory" / "MachineFactory"


def megadesk_python() -> Path:
    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    if conda_prefix and Path(conda_prefix).name == ENV_NAME:
        candidate = Path(conda_prefix) / ("python.exe" if os.name == "nt" else "bin/python")
        if candidate.is_file():
            return candidate
    home = Path.home()
    for base in (
        home / "anaconda3",
        home / "miniconda3",
        Path(r"C:\ProgramData\anaconda3"),
        Path(r"C:\ProgramData\miniconda3"),
        Path("/opt/conda"),
        Path("/usr/local/anaconda3"),
    ):
        candidate = base / "envs" / ENV_NAME / (
            "python.exe" if os.name == "nt" else "bin/python"
        )
        if candidate.is_file():
            return candidate
    raise SystemExit(
        f"error: {ENV_NAME} interpreter not found. "
        f"create it with: conda create -y -n {ENV_NAME} python=3.13"
    )


def main() -> int:
    py = megadesk_python()
    print(f"==> env {ENV_NAME} ({py})")
    if not (MACHINE_FACTORY / "Dockerfile").is_file():
        print(f"error: MachineFactory Dockerfile missing at {MACHINE_FACTORY}", file=sys.stderr)
        return 1

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(MACHINE_FACTORY)
        if not existing
        else f"{MACHINE_FACTORY}{os.pathsep}{existing}"
    )

    print("==> building machine-factory-agent:latest")
    result = subprocess.run(
        [str(py), "-m", "MachineFactoryManager", "build"],
        cwd=str(MACHINE_FACTORY),
        env=env,
        check=False,
    )
    if result.returncode != 0:
        print("error: sandbox image build failed", file=sys.stderr)
        return result.returncode
    print("==> rebuild_sandbox complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
