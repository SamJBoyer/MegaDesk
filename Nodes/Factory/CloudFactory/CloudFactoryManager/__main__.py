"""CLI entry for CloudFactoryManager: launch and follow Cursor cloud agents."""

from __future__ import annotations

import argparse
import logging
import os


def cmd_run(_args: argparse.Namespace) -> None:
    from CloudFactoryManager.manager import main as manager_main

    manager_main()


def cmd_models(_args: argparse.Namespace) -> None:
    """Print the model ids this account can use — the first auth check to run."""
    from CloudFactoryManager.runtime import CursorCloudFactory

    models = CursorCloudFactory().models()
    if not models:
        print("No models returned. Check CURSOR_API_KEY and that cursor-sdk is installed.")
        return
    for name in models:
        print(name)


def cmd_runs(_args: argparse.Namespace) -> None:
    """Show the run registry, which survives restarts of this process."""
    from CloudFactoryManager.manager import CloudFactoryManager

    manager = CloudFactoryManager()
    live = manager.live_runs()
    if not live:
        print("No live runs.")
        return
    for agent_id, run in live:
        print(f"{agent_id}  {run['status']:<9} {run['title']}  {run['pr_url']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="CloudFactoryManager",
        description=(
            "CloudFactoryManager: listen for CLOUDORDER signals, run Cursor "
            "cloud agents, and hand a GitHub PR off without publishing success "
            "CLOUDFINISHED."
        ),
    )
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="Start the CLOUDORDER poller (default)")
    run.set_defaults(func=cmd_run)
    models = sub.add_parser("models", help="List available model ids and exit")
    models.set_defaults(func=cmd_models)
    runs = sub.add_parser("runs", help="List live runs from the registry and exit")
    runs.set_defaults(func=cmd_runs)
    return parser


def main(argv: list[str] | None = None) -> None:
    try:
        from megadesk_contracts import NodeRuntime, configure_node_logging

        configure_node_logging("cloud_factory")
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
    if NodeRuntime is not None and getattr(args, "func", None) is cmd_run:
        with NodeRuntime.from_env("cloud_factory"):
            args.func(args)
        return
    if args.command is None:
        cmd_run(args)
        return
    args.func(args)


if __name__ == "__main__":
    main()
