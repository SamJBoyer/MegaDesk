"""Allow `python -m MergeManager` from the parent directory, or `python __main__.py`."""

from __future__ import annotations

if __name__ == "__main__":
    try:
        from .app import main
    except ImportError:
        from app import main
    main()
