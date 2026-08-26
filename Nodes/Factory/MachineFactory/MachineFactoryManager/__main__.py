"""CLI entry for MachineFactoryManager: poll for orders, or build the agent image."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from MachineFactoryManager.pool import IMAGE_NAME, build_image


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def cmd_run(_args: argparse.Namespace) -> None:
    from MachineFactoryManager.manager import main as manager_main

    manager_main()


def cmd_build(_args: argparse.Namespace) -> None:
    build_image(project_root())
    print(f"[MachineFactoryManager] image ready: {IMAGE_NAME}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="MachineFactoryManager",
        description=(
            "MachineFactoryManager: poll the WORKORDER stream and spin one-shot "
            "AgentHandler sandboxes with Redis sidecars (clone in-container, no Floor)."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Start the WORKORDER poller (default)")
    run.set_defaults(func=cmd_run)

    build = sub.add_parser("build", help="Build the AgentHandler Docker image")
    build.set_defaults(func=cmd_build)

    return parser


def main(argv: list[str] | None = None) -> None:
    try:
        from megadesk_contracts import NodeRuntime, configure_node_logging

        configure_node_logging("machine_factory")
    except Exception:
        logging.basicConfig(
            level=os.environ.get("LOG_LEVEL", "INFO"),
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
        NodeRuntime = None  # type: ignore[assignment]
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args.func = cmd_run
    if NodeRuntime is not None and args.func is cmd_run:
        with NodeRuntime.from_env("machine_factory"):
            args.func(args)
        return
    args.func(args)


if __name__ == "__main__":
    main()
