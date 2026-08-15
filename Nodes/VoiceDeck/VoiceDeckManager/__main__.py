"""CLI entry for VoiceDeckManager: idle on VOICE:CONTROL, talk when told to."""

from __future__ import annotations

import argparse
import logging
import os


def cmd_run(_args: argparse.Namespace) -> None:
    from VoiceDeckManager.session import main as session_main

    session_main()


def cmd_devices(_args: argparse.Namespace) -> None:
    """Print audio devices, which is the first thing to check when voice is silent."""
    import sounddevice as sd

    print(sd.query_devices())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="VoiceDeckManager",
        description=(
            "VoiceDeckManager: hold a speech-to-speech session with the OpenAI "
            "Realtime API and route its tool calls to the other MegaDesk nodes."
        ),
    )
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="Start the control-plane loop (default)")
    run.set_defaults(func=cmd_run)
    devices = sub.add_parser("devices", help="List audio input/output devices")
    devices.set_defaults(func=cmd_devices)
    return parser


def main(argv: list[str] | None = None) -> None:
    try:
        from megadesk_contracts import NodeRuntime, configure_node_logging

        configure_node_logging("voice_deck")
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
        with NodeRuntime.from_env("voice_deck"):
            args.func(args)
        return
    if args.command is None:
        cmd_run(args)
        return
    args.func(args)


if __name__ == "__main__":
    main()
