"""CLI entry for PlantManager: poll Redis work queues or build the agent image."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from PlantManager.env import load_plant_env
from PlantManager.pool import IMAGE_NAME, build_image

load_plant_env()


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def cmd_run(_args: argparse.Namespace) -> None:
    from PlantManager.manager import main as manager_main

    manager_main()


def cmd_build(_args: argparse.Namespace) -> None:
    build_image(project_root())
    print(f"[PlantManager] image ready: {IMAGE_NAME}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="PlantManager",
        description=(
            "PlantManager: poll Redis WORKORDER stream, prepare Floor worktrees, "
            "and spin one-shot LiveHarness sandboxes."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Start the Redis WORKORDER poller (default)")
    run.set_defaults(func=cmd_run)

    build = sub.add_parser("build", help="Build the LiveHarness Docker image")
    build.set_defaults(func=cmd_build)

    return parser


def main(argv: list[str] | None = None) -> None:
    try:
        from megadesk_contracts import configure_node_logging

        configure_node_logging("plant")
    except Exception:
        logging.basicConfig(
            level=os.environ.get("LOG_LEVEL", "INFO"),
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        cmd_run(args)
        return
    args.func(args)


if __name__ == "__main__":
    main()
