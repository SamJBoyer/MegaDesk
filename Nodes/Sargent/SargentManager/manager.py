"""Consume SARGENT:ASK and publish one SARGENT:ANSWER per prompt.

One consumer group, one OpenAI call per ask. Every ask is acked, and every ask
that cannot be rewritten gets an error answer first — a person waiting on the
chat should never be left with only a log line on the BE.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any, Callable, Optional

from megadesk_contracts import redis_connect, resolve_ephemeral_db, resolve_redis_url
from megadesk_contracts.wire import sargent as wire

from SargentManager.openai_client import OpenAIError, rewrite_prompt

log = logging.getLogger("sargent.manager")

POLL_INTERVAL_SEC = 1.0
ASK_BATCH = 16
RECONNECT_WAIT_SEC = 3.0

RewriteFn = Callable[[str], str]


class SargentManager:
    """Consume SARGENT:ASK, publish SARGENT:ANSWER."""

    def __init__(
        self,
        *,
        redis_url: Optional[str] = None,
        ephemeral: Any = None,
        rewrite: Optional[RewriteFn] = None,
        group: str = wire.ASK_GROUP,
        consumer: Optional[str] = None,
        poll_interval: float = POLL_INTERVAL_SEC,
    ) -> None:
        self.redis_url = resolve_redis_url(redis_url)
        self.group = group
        self.consumer = consumer or f"{socket.gethostname()}-{os.getpid()}"
        self.poll_interval = float(poll_interval)
        self.rewrite: RewriteFn = rewrite or rewrite_prompt
        self._ephemeral = ephemeral
        self._group_ready = False

    @property
    def ephemeral(self) -> Any:
        if self._ephemeral is None:
            self._ephemeral = redis_connect(
                self.redis_url, db=resolve_ephemeral_db(self.redis_url)
            )
        return self._ephemeral

    def ensure_group(self) -> None:
        if self._group_ready:
            return
        from redis.exceptions import ResponseError

        try:
            self.ephemeral.xgroup_create(
                wire.ASK_STREAM, self.group, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    def poll_once(self) -> int:
        """Drain pending then new asks. Returns how many were handled."""
        self.ensure_group()
        handled = 0
        for stream_id in ("0", ">"):
            results = self.ephemeral.xreadgroup(
                groupname=self.group,
                consumername=self.consumer,
                streams={wire.ASK_STREAM: stream_id},
                count=ASK_BATCH,
            )
            for _stream, messages in results or []:
                for entry_id, fields in messages:
                    self._process(entry_id, fields)
                    handled += 1
        return handled

    def run_forever(self) -> None:
        log.info(
            "Sargent manager up: %s group=%s consumer=%s",
            wire.ASK_STREAM,
            self.group,
            self.consumer,
        )
        from megadesk_contracts import node_should_stop

        while not node_should_stop():
            try:
                self.poll_once()
                time.sleep(self.poll_interval)
            except KeyboardInterrupt:
                log.info("Interrupted; shutting down")
                return
            except Exception:  # noqa: BLE001 - a long-lived BE outlives Redis restarts
                log.exception("Poll failed; retrying in %.1fs", RECONNECT_WAIT_SEC)
                self._group_ready = False
                time.sleep(RECONNECT_WAIT_SEC)

    def _process(self, entry_id: str, fields: dict[str, Any]) -> None:
        try:
            self.handle_ask(entry_id, fields)
        except Exception:  # noqa: BLE001
            log.exception("Unhandled error handling SARGENT:ASK %s", entry_id)
        finally:
            self.ephemeral.xack(wire.ASK_STREAM, self.group, entry_id)

    def handle_ask(self, entry_id: str, fields: dict[str, Any]) -> None:
        try:
            ask = wire.parse_ask(fields)
        except ValueError as exc:
            log.error("Unusable SARGENT:ASK %s: %s", entry_id, exc)
            return

        log.info("Rewrite session=%s prompt=%s", ask["session_id"], ask["prompt_id"])
        try:
            rewritten = self.rewrite(ask["prompt"])
        except OpenAIError as exc:
            log.error("Rewrite failed: %s", exc)
            self._publish_error(ask, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("Rewrite raised")
            self._publish_error(ask, f"Rewrite failed: {exc}")
            return

        text = str(rewritten or "").strip()
        if not text:
            self._publish_error(ask, "Rewrite returned no text")
            return
        self.ephemeral.xadd(
            wire.ANSWER_STREAM,
            wire.answer_fields(
                session_id=ask["session_id"],
                prompt_id=ask["prompt_id"],
                rewrite=text,
            ),
        )

    def _publish_error(self, ask: dict[str, str], message: str) -> None:
        self.ephemeral.xadd(
            wire.ANSWER_STREAM,
            wire.answer_fields(
                session_id=ask["session_id"],
                prompt_id=ask["prompt_id"],
                rewrite=message,
                status=wire.STATUS_ERROR,
            ),
        )


def main() -> None:
    SargentManager().run_forever()


if __name__ == "__main__":
    main()
