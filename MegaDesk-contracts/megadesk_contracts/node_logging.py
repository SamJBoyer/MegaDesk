"""Shared BE/FE logging setup for MegaDesk nodes."""

from __future__ import annotations

import logging
import os
from typing import Optional

ENV_UNIQUE_ID = "MEGADESK_UNIQUE_ID"
ENV_NODE = "MEGADESK_NODE"
ENV_LOG_PATH = "MEGADESK_LOG_PATH"


def configure_node_logging(name: Optional[str] = None) -> logging.Logger:
    """Configure stderr logging for a node process.

    When launched by Supervisor, stdout/stderr are redirected to the session
    transcript (``Logs/{session}/{node}.md``) and ``MEGADESK_*`` env vars
    identify the instance. Calling this from a BE ``__main__`` makes structured
    logs land in that file.

    Returns a logger named after ``MEGADESK_NODE`` or ``name``.
    """
    node = (name or os.environ.get(ENV_NODE) or "megadesk_contracts.node").strip() or "megadesk_contracts.node"
    unique_id = (os.environ.get(ENV_UNIQUE_ID) or "").strip()
    log_path = (os.environ.get(ENV_LOG_PATH) or "").strip()

    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=os.environ.get("LOG_LEVEL", "INFO"),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    logger = logging.getLogger(node)
    if unique_id or log_path:
        logger.debug(
            "node logging configured unique_id=%s log_path=%s",
            unique_id or "(none)",
            log_path or "(none)",
        )
    return logger
