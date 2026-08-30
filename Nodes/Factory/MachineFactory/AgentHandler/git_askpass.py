#!/usr/bin/env python3
"""GIT_ASKPASS helper: username or token from MEGADESK_GITHUB_TOKEN_FILE.

Git invokes this with a prompt. The token never appears in argv, remotes, or
clone URLs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    prompt = " ".join(argv if argv is not None else sys.argv[1:])
    if "username" in prompt.lower():
        print("x-access-token")
        return 0
    path = (os.environ.get("MEGADESK_GITHUB_TOKEN_FILE") or "").strip()
    if not path:
        return 0
    try:
        token = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return 0
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
