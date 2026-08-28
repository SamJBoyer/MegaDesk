#!/usr/bin/env python3
"""Run the full MegaDesk env refresh sequence.

Order: down_nodes → refresh_contracts → refresh_nodes → rebuild_sandbox.
Stops on the first failure.

    conda activate MEGADESK
    python scripts/master_refresh.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ENV_NAME = "MEGADESK"
SCRIPTS = Path(__file__).resolve().parent

STEPS = (
    "down_nodes.py",
    "refresh_contracts.py",
    "refresh_nodes.py",
    "rebuild_sandbox.py",
)


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
    print(f"==> master_refresh via {ENV_NAME} ({py})")
    for name in STEPS:
        script = SCRIPTS / name
        if not script.is_file():
            print(f"error: missing {script}", file=sys.stderr)
            return 1
        print(f"\n==== {name} ====")
        result = subprocess.run([str(py), str(script)], check=False)
        if result.returncode != 0:
            print(f"error: {name} failed with code {result.returncode}", file=sys.stderr)
            return result.returncode
    print("\n==> master_refresh complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
