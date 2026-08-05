"""Allow `python -m MergeManager` from the parent directory, or `python __main__.py`."""

from __future__ import annotations

if __name__ == "__main__":
    try:
        from .merge_manager_app import main
    except ImportError:
        from merge_manager_app import main
    main()
