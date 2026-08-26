#!/usr/bin/env python3
"""Uninstall and editable-reinstall MegaDesk-Contracts + MegaDesk-Canvas.

Run this after changing the contracts package or canvas packaging so the
MEGADESK env does not keep a stale install.

    conda activate MEGADESK
    python scripts/refresh_contracts.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ENV_NAME = "MEGADESK"
REPO_ROOT = Path(__file__).resolve().parent.parent

# Install contracts before canvas — canvas depends on megadesk-contracts.
PACKAGES: list[tuple[str, Path]] = [
    ("megadesk-contracts", REPO_ROOT / "MegaDesk-Contracts"),
    ("megadesk-canvas", REPO_ROOT / "MegaDesk-Canvas"),
]


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


def run(py: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(py), *args],
        check=check,
        text=True,
        capture_output=True,
    )


def main() -> int:
    py = megadesk_python()
    print(f"==> env {ENV_NAME} ({py})")

    for dist, directory in PACKAGES:
        if not (directory / "pyproject.toml").is_file():
            print(f"error: missing package at {directory}", file=sys.stderr)
            return 1
        print(f"==> uninstalling {dist}")
        run(py, "-m", "pip", "uninstall", "-y", dist, check=False)

    for _dist, directory in PACKAGES:
        for leftover in directory.glob("*.egg-info"):
            shutil.rmtree(leftover, ignore_errors=True)
        shutil.rmtree(directory / "build", ignore_errors=True)

    for dist, directory in PACKAGES:
        print(f"==> installing {dist}")
        result = run(py, "-m", "pip", "install", "-e", str(directory), check=False)
        if result.returncode != 0:
            sys.stderr.write(result.stdout)
            sys.stderr.write(result.stderr)
            print(f"error: install failed for {directory}", file=sys.stderr)
            return 1

    verify = run(
        py,
        "-c",
        "from importlib.metadata import version; "
        "print('megadesk-contracts', version('megadesk-contracts')); "
        "print('megadesk-canvas', version('megadesk-canvas')); "
        "import megadesk_contracts; import supervisor; "
        "print('imports ok')",
    )
    print(verify.stdout, end="")
    print("==> refresh_contracts complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
