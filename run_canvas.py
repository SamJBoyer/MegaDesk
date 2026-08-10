"""Launch the MegaDesk canvas from the repo root.

Usage:
  python run_canvas.py
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_CANVAS_DIR = _ROOT / "MegaDesk-Canvas"
_MAIN = _CANVAS_DIR / "main.py"


def main() -> None:
    if not _MAIN.is_file():
        raise SystemExit(f"Canvas entry point not found: {_MAIN}")
    # main.py imports `engine.*` relative to MegaDesk-Canvas/
    sys.path.insert(0, str(_CANVAS_DIR))
    runpy.run_path(str(_MAIN), run_name="__main__")


if __name__ == "__main__":
    main()
