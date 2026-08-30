"""CLI entry for CodeScopeManager: Redis poller or HTTP service."""

from __future__ import annotations

import argparse
import logging
import os


def cmd_run(_args: argparse.Namespace) -> None:
    from CodeScopeManager.manager import main as manager_main

    manager_main()


def cmd_serve(args: argparse.Namespace) -> None:
    from CodeScopeManager.server import DEFAULT_HOST, DEFAULT_PORT, serve

    serve(host=args.host or DEFAULT_HOST, port=int(args.port or DEFAULT_PORT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="CodeScopeManager",
        description=(
            "CodeScopeManager: consume the CODEQ:ASK stream and answer questions "
            "about a cloned repository with a warm local Cursor agent. "
            "'serve' exposes the same work over HTTP (no Redis)."
        ),
    )
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="Start the CODEQ:ASK poller (default)")
    run.set_defaults(func=cmd_run)
    http = sub.add_parser("serve", help="Start the HTTP service (no Redis)")
    http.add_argument("--host", default="127.0.0.1", help="Bind address")
    http.add_argument("--port", type=int, default=8080, help="Bind port")
    http.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> None:
    try:
        from megadesk_contracts import NodeRuntime, configure_node_logging

        configure_node_logging("code_scope")
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
        with NodeRuntime.from_env("code_scope"):
            args.func(args)
        return
    if args.command is None:
        cmd_run(args)
        return
    args.func(args)


if __name__ == "__main__":
    main()
