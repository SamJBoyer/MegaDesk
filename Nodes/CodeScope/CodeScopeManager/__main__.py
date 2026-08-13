"""CLI entry for CodeScopeManager: poll CODEQ:ASK and answer from a clone."""

from __future__ import annotations

import argparse
import logging
import os


def cmd_run(_args: argparse.Namespace) -> None:
    from CodeScopeManager.manager import main as manager_main

    manager_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="CodeScopeManager",
        description=(
            "CodeScopeManager: consume the CODEQ:ASK stream and answer questions "
            "about a cloned repository with a warm local Cursor agent."
        ),
    )
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="Start the CODEQ:ASK poller (default)")
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> None:
    try:
        from megadesk_contracts import configure_node_logging

        configure_node_logging("code_scope")
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
