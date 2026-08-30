"""In-process canvas verbs VoiceDeck reaches over CANVAS:CMD.

The integration harness already pilots widgets through ``NodeDriver``. This
module is that same surface on the live engine: list / drop / select nodes,
then get, type, click, or pick a widget by tag suffix. ``sync_members``
drains ``CANVAS:CMD`` so the voice BE (another process) can use it.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import dearpygui.dearpygui as dpg
import redis
from megadesk_contracts import (
    redis_connect,
    resolve_ephemeral_db,
    resolve_redis_url,
)
from megadesk_contracts.testing.driver import CallbackMissing, NodeDriver, WidgetMissing
from megadesk_contracts.wire import canvas as wire
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from engine.display_engine import NODE_EDITOR, DisplayEngine
from engine.megadesk_registry import palette_key

log = logging.getLogger("megadesk.canvas")

CMD_BATCH = 16

CHROME_PREFIXES = {
    "graph_bar": "graph_bar",
    "voice_deck": "voice_deck_panel_window",
    "catalog": "catalog_sidebar",
    "supervisor": "supervisor_panel_window",
}

_LIVE: Optional["CanvasApi"] = None


class CanvasError(LookupError):
    """A canvas verb could not address a node or widget."""


class CanvasApi:
    """NodeDriver-style control of one booted ``DisplayEngine``."""

    def __init__(self, engine: DisplayEngine) -> None:
        self.engine = engine
        self._redis: Optional[redis.Redis] = None
        self._cursor: Optional[str] = None

    def detach(self) -> None:
        global _LIVE
        client = self._redis
        self._redis = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if _LIVE is self:
            _LIVE = None

    def _connect_redis(self) -> Optional[redis.Redis]:
        if self._redis is not None:
            try:
                self._redis.ping()
                return self._redis
            except (RedisConnectionError, RedisTimeoutError, OSError, RedisError):
                self._redis = None
        try:
            url = resolve_redis_url()
            client = redis_connect(
                url,
                db=resolve_ephemeral_db(url),
                socket_connect_timeout=1.5,
                socket_timeout=2.0,
            )
            client.ping()
            self._redis = client
            if self._cursor is None:
                self._cursor = self._last_id(client)
            return client
        except (RedisConnectionError, RedisTimeoutError, OSError, RedisError, ValueError):
            self._redis = None
            return None

    def _last_id(self, client: redis.Redis) -> str:
        try:
            newest = client.xrevrange(wire.CMD_STREAM, count=1)
        except RedisError:
            return "$"
        if not newest:
            return "0-0"
        return str(newest[0][0])

    def drain_commands(self) -> int:
        try:
            client = self._connect_redis()
            if client is None:
                return 0
            reply = client.xread(
                {wire.CMD_STREAM: self._cursor or "0-0"}, count=CMD_BATCH, block=0
            )
        except (RedisError, OSError, ValueError):
            self._redis = None
            return 0
        applied = 0
        for _stream, items in reply or []:
            for entry_id, fields in items:
                self._cursor = str(entry_id)
                try:
                    self._reply_to(client, fields)
                except Exception:
                    log.exception("CANVAS:CMD %s failed", entry_id)
                applied += 1
        return applied

    def _reply_to(self, client: redis.Redis, fields: dict[str, Any]) -> None:
        request_id = str(fields.get("request_id") or "").strip()
        try:
            command = wire.parse_command(fields)
            result = self.apply(command)
            payload = wire.reply_fields(
                request_id=command["request_id"],
                status=wire.STATUS_OK,
                result=result,
            )
        except Exception as exc:
            payload = wire.reply_fields(
                request_id=request_id or "unknown",
                status=wire.STATUS_ERROR,
                result=str(exc),
            )
        try:
            client.xadd(wire.REPLY_STREAM, payload)
        except RedisError:
            self._redis = None

    def apply(self, command: dict[str, str]) -> dict[str, Any]:
        action = command["action"]
        node = command.get("node") or ""
        suffix = command.get("suffix") or ""
        value = command.get("value") or ""
        if action == wire.ACTION_LIST_NODES:
            return {"nodes": self.list_nodes()}
        if action == wire.ACTION_DROP_NODE:
            return self.drop_node(node)
        if action == wire.ACTION_SELECT_NODE:
            return self.select_node(node)
        if action == wire.ACTION_LIST_WIDGETS:
            return {"widgets": self.list_widgets(node)}
        if action == wire.ACTION_GET:
            return {"value": self.get(node, suffix)}
        if action == wire.ACTION_CLICK:
            self.click(node, suffix)
            return {"node": node, "suffix": suffix}
        if action == wire.ACTION_TYPE_INTO:
            self.type_into(node, suffix, value)
            return {"node": node, "suffix": suffix}
        if action == wire.ACTION_SELECT:
            self.select(node, suffix, value)
            return {"node": node, "suffix": suffix, "value": value}
        if action == wire.ACTION_CHECK:
            self.check(node, suffix, value)
            return {"node": node, "suffix": suffix}
        raise CanvasError(f"unknown action {action!r}")

    def list_nodes(self) -> list[dict[str, Any]]:
        selected = getattr(self.engine, "_selected_member_id", None)
        nodes: list[dict[str, Any]] = []
        for member_id, member in (self.engine.model.members or {}).items():
            nodes.append(
                {
                    "kind": "node",
                    "name": member.name,
                    "member_id": member_id,
                    "selected": member_id == selected,
                }
            )
        for name, prefix in CHROME_PREFIXES.items():
            if dpg.does_item_exist(prefix):
                nodes.append(
                    {
                        "kind": "chrome",
                        "name": name,
                        "member_id": "",
                        "selected": False,
                    }
                )
        return nodes

    def drop_node(self, node_name: str) -> dict[str, str]:
        before = set(self.engine.model.members)
        try:
            self.engine.on_graph_drop(NODE_EDITOR, palette_key(node_name), None)
        except KeyError as exc:
            raise CanvasError(str(exc)) from exc
        created = set(self.engine.model.members) - before
        if not created:
            raise CanvasError(
                f"dropping {node_name!r} added no member; is it in the Catalog?"
            )
        member_id = created.pop()
        self._place(member_id, len(before))
        member = self.engine.model.members[member_id]
        return {"member_id": member_id, "name": member.name}

    def _place(self, member_id: str, index: int) -> None:
        column, row = divmod(index, 3)
        x, y = 40.0 + column * 520.0, 40.0 + row * 250.0
        member = self.engine.model.members.get(member_id)
        if member is None:
            return
        member.position = [x, y]
        tag = member.hosted_tag()
        if dpg.does_item_exist(tag):
            dpg.set_item_pos(tag, [x, y])

    def select_node(self, node: str) -> dict[str, str]:
        if node in CHROME_PREFIXES:
            prefix = CHROME_PREFIXES[node]
            if not dpg.does_item_exist(prefix):
                raise CanvasError(f"chrome {node!r} is not on this canvas")
            return {"name": node, "member_id": ""}
        member = self._member(node)
        self.engine.notify_member_clicked(member.member_id)
        return {"name": member.name, "member_id": member.member_id}

    def list_widgets(self, node: str) -> list[str]:
        return self.driver_for(node).suffixes()

    def get(self, node: str, suffix: str) -> Any:
        return self.driver_for(node).get(suffix)

    def click(self, node: str, suffix: str) -> Any:
        return self.driver_for(node).click(suffix)

    def type_into(self, node: str, suffix: str, text: str) -> Any:
        return self.driver_for(node).type_into(suffix, text)

    def select(self, node: str, suffix: str, value: str) -> Any:
        return self.driver_for(node).select(suffix, value)

    def check(self, node: str, suffix: str, value: str) -> Any:
        return self.driver_for(node).check(suffix, value not in {"", "0", "false", "no"})

    def driver_for(self, node: str) -> NodeDriver:
        if node in CHROME_PREFIXES:
            prefix = CHROME_PREFIXES[node]
            if not dpg.does_item_exist(prefix):
                raise CanvasError(f"chrome {node!r} is not on this canvas")
            return NodeDriver(self, member_id=node, node_name=node, tag_prefix=prefix)
        member = self._member(node)
        return NodeDriver(self, member.member_id, member.name)

    def _member(self, node: str):
        members = self.engine.model.members or {}
        if node in members:
            return members[node]
        matches = [member for member in members.values() if member.name == node]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise CanvasError(f"multiple {node!r} nodes; use member_id")
        raise CanvasError(f"no node {node!r} on the canvas")


def attach_canvas_api(engine: DisplayEngine) -> CanvasApi:
    """Bind a ``CanvasApi`` to ``engine``; ``sync_members`` drains CANVAS:CMD."""
    global _LIVE
    if _LIVE is not None:
        _LIVE.detach()
        _LIVE = None
    api = CanvasApi(engine)
    engine.canvas_api = api
    _LIVE = api
    return api


def live_canvas_api() -> Optional[CanvasApi]:
    return _LIVE
