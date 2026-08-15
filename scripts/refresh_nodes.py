#!/usr/bin/env python3
"""Uninstall and editable-reinstall every Nodes/<Name> into the MEGADESK env.

Run this after changing a node package so the conda env does not keep a stale
install. Always use the MEGADESK interpreter — never the system Python.

    conda activate MEGADESK
    python scripts/refresh_nodes.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ENV_NAME = "MEGADESK"
SKIP_EXTRAS = {"dev", "test", "tests", "docs", "lint", "sandbox"}
REPO_ROOT = Path(__file__).resolve().parent.parent


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


def node_specs(nodes_root: Path) -> list[tuple[Path, str, str, list[str]]]:
    specs: list[tuple[Path, str, str, list[str]]] = []
    for pyproject in sorted(nodes_root.glob("*/pyproject.toml")):
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
        name = project.get("name")
        if not name:
            continue
        extras = sorted(
            e
            for e in project.get("optional-dependencies", {})
            if e.lower() not in SKIP_EXTRAS
        )
        endpoints = sorted(
            project.get("entry-points", {}).get("MegaDesk.nodes", {})
        )
        extra = ",".join(extras)
        specs.append((pyproject.parent, name, extra, endpoints))
    return specs


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
    probe = run(py, "-c", "import megadesk_contracts", check=False)
    if probe.returncode != 0:
        print(
            "error: megadesk-contracts is not installed in "
            f"{ENV_NAME}; run: {py} -m pip install -e {REPO_ROOT / 'MegaDesk-contracts'}",
            file=sys.stderr,
        )
        return 1

    specs = node_specs(REPO_ROOT / "Nodes")
    if not specs:
        print(f"error: no installable nodes found under {REPO_ROOT / 'Nodes'}", file=sys.stderr)
        return 1

    for _dir, dist, _extras, _eps in specs:
        print(f"==> uninstalling {dist}")
        run(py, "-m", "pip", "uninstall", "-y", dist, check=False)

    for directory, _dist, _extras, _eps in specs:
        for leftover in directory.glob("*.egg-info"):
            shutil.rmtree(leftover, ignore_errors=True)
        shutil.rmtree(directory / "build", ignore_errors=True)

    for directory, dist, extras, _eps in specs:
        target = f"{directory}[{extras}]" if extras else str(directory)
        print(f"==> installing {dist}" + (f" [{extras}]" if extras else ""))
        result = run(py, "-m", "pip", "install", "-e", target, check=False)
        if result.returncode != 0:
            sys.stderr.write(result.stdout)
            sys.stderr.write(result.stderr)
            print(f"error: install failed for {target}", file=sys.stderr)
            return 1

    verify = run(
        py,
        "-c",
        "import megadesk_contracts as mc; "
        "fe=sorted(mc.discover_frontends()); be=sorted(mc.discover_backends()); "
        "print('frontends: ' + ', '.join(fe)); print('backends:  ' + ', '.join(be))",
    )
    print(verify.stdout, end="")
    print("==> refresh complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
