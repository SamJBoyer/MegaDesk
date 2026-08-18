"""The one place a work-graph run writes its progress.

Nodes call ``node_started`` / ``node_finished`` / ``node_failed`` and never touch
Redis themselves, so every node reports in the same shape and a new node cannot
forget to. Each call updates the ``GRAPHRUN`` hash, appends to ``GRAPHEVENT``,
and writes the audit file.

Every write is best-effort. Progress reporting is for whoever is watching; a run
that is doing real work must not die because the monitor's Redis blinked.
"""

from __future__ import annotations

import logging
from typing import Any

from megadesk_contracts.wire import graph as wire

log = logging.getLogger("agent_handler.graph")


class GraphReporter:
    def __init__(
        self,
        redis: Any,
        *,
        guid: str,
        spec: wire.GraphSpec,
        audit: Any = None,
        ticket_id: str = "",
        ticket_name: str = "",
        repo: str = "",
    ) -> None:
        self.redis = redis
        self.guid = guid
        self.spec = spec
        self.audit = audit
        self.ticket_id = ticket_id
        self.ticket_name = ticket_name
        self.repo = repo
        self.key = wire.graph_run_key(guid)
        self.started = wire.wire_timestamp()
        self._nodes = wire.initial_nodes(spec)
        self._encoded_spec = wire.encode_spec(spec)
        self._current = ""
        self._status = wire.STATUS_QUEUED
        self._error = ""

    # --- run lifecycle -----------------------------------------------------

    def start_run(self) -> None:
        self._status = wire.STATUS_RUNNING
        self._flush()

    def finish_run(self, status: str, error: str = "") -> None:
        self._status = status
        self._error = error
        self._current = ""
        self._flush()

    def clear(self) -> None:
        """Drop the hash so a missing key still means 'no run'."""
        self._safe(lambda: self.redis.delete(self.key), "clear")

    # --- per-node ----------------------------------------------------------

    def node_started(self, name: str) -> None:
        self._current = name
        self._nodes[name] = wire.node_progress(
            status=wire.STATUS_RUNNING,
            started=wire.wire_timestamp(),
        )
        self._flush()
        self._emit(name, wire.STATUS_RUNNING, "")
        self._audit(name, "started", "")

    def node_finished(self, name: str, detail: str = "") -> None:
        self._close_node(name, wire.STATUS_FINISHED, detail)

    def node_failed(self, name: str, detail: str = "") -> None:
        self._close_node(name, wire.STATUS_ERROR, detail)

    def _close_node(self, name: str, status: str, detail: str) -> None:
        previous = self._nodes.get(name) or wire.node_progress()
        self._nodes[name] = wire.node_progress(
            status=status,
            started=previous.get("started", ""),
            ended=wire.wire_timestamp(),
            detail=detail,
        )
        if self._current == name:
            self._current = ""
        self._flush()
        self._emit(name, status, detail)
        self._audit(name, status, detail)

    # --- plumbing ----------------------------------------------------------

    def _flush(self) -> None:
        def write() -> None:
            self.redis.hset(
                self.key,
                mapping=wire.graph_run_fields(
                    guid=self.guid,
                    graph=self.spec.name,
                    spec=self._encoded_spec,
                    nodes=wire.encode_nodes(self._nodes),
                    current=self._current,
                    status=self._status,
                    ticket_id=self.ticket_id,
                    ticket_name=self.ticket_name,
                    repo=self.repo,
                    started=self.started,
                    error=self._error,
                ),
            )

        self._safe(write, "hash update")

    def _emit(self, node: str, status: str, detail: str) -> None:
        def write() -> None:
            self.redis.xadd(
                wire.GRAPHEVENT_STREAM,
                wire.graph_event_fields(
                    guid=self.guid,
                    graph=self.spec.name,
                    node=node,
                    status=status,
                    detail=detail,
                ),
                maxlen=wire.GRAPHEVENT_MAXLEN,
                approximate=True,
            )

        self._safe(write, "event")

    def _audit(self, node: str, status: str, detail: str) -> None:
        if self.audit is None:
            return
        body = f"{node} {status}"
        if detail:
            body += f" — {detail}"
        self.audit.event("graph", body)

    def _safe(self, action: Any, what: str) -> None:
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - reporting must never fail a run
            log.warning("Graph %s failed for %s: %s", what, self.guid, exc)
