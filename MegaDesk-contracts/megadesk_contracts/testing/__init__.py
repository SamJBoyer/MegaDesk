"""Agent-piloted integration test harness for MegaDesk.

Gives a test (or an agent) the four capabilities needed to exercise the canvas
end to end in-process: address any widget, read and write its state, fire its
real bound callback, and advance frames deliberately — plus a screenshot for
inspecting failures.

Import from here rather than the submodules::

    from megadesk_contracts.testing import CanvasHarness, FakeAgent, FakeGh, GitFloor

``megadesk_contracts.testing`` deliberately imports nothing from any node: the
node-specific pieces (the module to patch, the wire helpers to build payloads
with) are injected by the caller.
"""

from megadesk_contracts.testing.driver import (
    CallbackMissing,
    NodeDriver,
    WidgetMissing,
    invoke_callback,
)
from megadesk_contracts.testing.fakes import (
    EVENT_ASSISTANT_TEXT,
    EVENT_ERROR,
    EVENT_STATE,
    EVENT_TOOL_CALL,
    EVENT_TRANSCRIPT_FINAL,
    EVENT_TRANSCRIPT_PARTIAL,
    AgentRun,
    CloudLaunch,
    CloudStatus,
    CodeAnswer,
    FakeAgent,
    FakeCloudRuntime,
    FakeCodeAgent,
    FakeGh,
    FakeRealtime,
    FakeRunner,
    Issue,
    RealtimeEvent,
    split_sentences,
)
from megadesk_contracts.testing.gitfloor import GitError, GitFloor, git
from megadesk_contracts.testing.harness import (
    DEFAULT_TIMEOUT_SEC,
    OFFSCREEN_VIEWPORT_POS,
    CanvasHarness,
    HarnessTimeout,
    PumpProbe,
)

__all__ = [
    "AgentRun",
    "CallbackMissing",
    "CanvasHarness",
    "CloudLaunch",
    "CloudStatus",
    "CodeAnswer",
    "DEFAULT_TIMEOUT_SEC",
    "EVENT_ASSISTANT_TEXT",
    "EVENT_ERROR",
    "EVENT_STATE",
    "EVENT_TOOL_CALL",
    "EVENT_TRANSCRIPT_FINAL",
    "EVENT_TRANSCRIPT_PARTIAL",
    "FakeAgent",
    "FakeCloudRuntime",
    "FakeCodeAgent",
    "FakeGh",
    "FakeRealtime",
    "FakeRunner",
    "GitError",
    "GitFloor",
    "HarnessTimeout",
    "Issue",
    "NodeDriver",
    "OFFSCREEN_VIEWPORT_POS",
    "PumpProbe",
    "RealtimeEvent",
    "WidgetMissing",
    "git",
    "invoke_callback",
    "split_sentences",
]
