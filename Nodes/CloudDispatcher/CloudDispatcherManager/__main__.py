"""CLI entry for CloudDispatcherManager: launch and follow Cursor cloud agents."""

from __future__ import annotations

import argparse
import logging
import os


def cmd_run(_args: argparse.Namespace) -> None:
    from CloudDispatcherManager.dispatcher import main as dispatcher_main

    dispatcher_main()


def cmd_models(_args: argparse.Namespace) -> None:
    """Print the model ids this account can use — the first auth check to run."""
    from CloudDispatcherManager.runtime import CursorCloudRuntime

    models = CursorCloudRuntime().models()
    if not models:
        print("No models returned. Check CURSOR_API_KEY and that cursor-sdk is installed.")
        return
    for name in models:
        print(name)


def cmd_runs(_args: argparse.Namespace) -> None:
    """Show the run registry, which survives restarts of this process."""
    from CloudDispatcherManager.dispatcher import CloudDispatcher

    dispatcher = CloudDispatcher()
    live = dispatcher.live_runs()
    if not live:
        print("No live runs.")
        return
    for agent_id, run in live:
        print(f"{agent_id}  {run['status']:<9} {run['title']}  {run['pr_url']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="CloudDispatcherManager",
        description=(
            "CloudDispatcherManager: consume the CLOUDORDER stream, run Cursor "
            "cloud agents, and publish CLOUDFINISHED with their pull requests."
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
        from megadesk_contracts import configure_node_logging

        configure_node_logging("cloud_dispatcher")
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
