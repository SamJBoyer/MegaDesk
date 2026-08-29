"""CLI entry for SargentManager: poll SARGENT:ASK and rewrite via OpenAI."""

from __future__ import annotations

import argparse
import logging
import os


def cmd_run(_args: argparse.Namespace) -> None:
    from SargentManager.manager import main as manager_main

    manager_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="SargentManager",
        description=(
            "SargentManager: consume the SARGENT:ASK stream and rewrite each "
            "prompt with one OpenAI chat-completions call."
        ),
    )
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="Start the SARGENT:ASK poller (default)")
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> None:
    try:
        from megadesk_contracts import NodeRuntime, configure_node_logging

        configure_node_logging("sargent")
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
        with NodeRuntime.from_env("sargent"):
            args.func(args)
        return
    if args.command is None:
        cmd_run(args)
        return
    args.func(args)


if __name__ == "__main__":
    main()
