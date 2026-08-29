"""The notepad itself: titled documents stored as ``.txt`` files.

Kept free of Dear PyGui so create / append / switch and the filename codec
can be exercised without a desktop session. ``app.py`` owns the tabs.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from megadesk_contracts.wire import notepad as wire

ENV_NOTES_ROOT = "NOTEPAD_ROOT"
NOTES_DIRNAME = "notes"

_UNSAFE = re.compile(r"[^\w.-]+")


class PadError(RuntimeError):
    """A note operation failed; the message is safe to show."""


def safe_title(title: str) -> str:
    """Turn a tab name into a ``.txt`` stem."""
    cleaned = _UNSAFE.sub("-", str(title or "").strip()).strip(".-")
    if not cleaned:
        raise PadError("note title is empty")
    return cleaned[:80]


def note_filename(title: str) -> str:
    return f"{safe_title(title)}.txt"


def default_notes_root() -> Path:
    configured = (os.environ.get(ENV_NOTES_ROOT) or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / NOTES_DIRNAME


@dataclass
class Note:
    title: str
    text: str = ""

    def filename(self) -> str:
        return note_filename(self.title)


@dataclass
class Pad:
    """Ordered notes, one current target, and the folder they persist into."""

    notes: dict[str, Note] = field(default_factory=dict)
    current: str = ""
    notes_root: Path = field(default_factory=default_notes_root)

    def titles(self) -> list[str]:
        return list(self.notes)

    def note(self, title: str = "") -> Optional[Note]:
        key = stripped_title(title) or self.current
        return self.notes.get(key) if key else None

    def next_title(self, base: str = "note") -> str:
        stem = safe_title(base)
        if stem not in self.notes:
            return stem
        n = 2
        while f"{stem}-{n}" in self.notes:
            n += 1
        return f"{stem}-{n}"

    def create(self, title: str, text: str = "") -> Note:
        """Open ``title`` as the current document, creating it when missing."""
        name = safe_title(title)
        note = self.notes.get(name)
        if note is None:
            note = Note(title=name, text=text_field(text))
            self.notes[name] = note
        elif text_field(text):
            note.text = _join(note.text, text_field(text))
        self.current = name
        return note

    def append(self, text: str, title: str = "") -> Note:
        """Add ``text`` to ``title``, or to the current target when title is empty."""
        body = text_field(text)
        if not body:
            raise PadError("nothing to add")
        name = stripped_title(title) or self.current
        if not name:
            raise PadError("no document is selected")
        note = self.notes.get(name)
        if note is None:
            note = self.create(name)
        note.text = _join(note.text, body)
        self.current = name
        return note

    def switch(self, title: str) -> Note:
        """Make ``title`` the target, creating an empty document if it is new."""
        return self.create(title)

    def set_text(self, title: str, text: str) -> Note:
        note = self.create(title)
        note.text = text_field(text)
        return note

    def apply(self, command: dict[str, str]) -> Note:
        action = command["action"]
        title = command.get("title") or ""
        text = command.get("text") or ""
        if action == wire.ACTION_CREATE:
            return self.create(title, text)
        if action == wire.ACTION_APPEND:
            return self.append(text, title)
        if action == wire.ACTION_SWITCH:
            return self.switch(title)
        raise PadError(f"unknown action {action!r}")

    def load(self, root: Optional[Path] = None) -> None:
        folder = Path(root) if root is not None else self.notes_root
        self.notes.clear()
        self.current = ""
        if not folder.is_dir():
            return
        for path in sorted(folder.glob("*.txt")):
            try:
                name = safe_title(path.stem)
            except PadError:
                continue
            self.notes[name] = Note(
                title=name,
                text=path.read_text(encoding="utf-8"),
            )
        if self.notes:
            self.current = next(iter(self.notes))

    def save(self, root: Optional[Path] = None) -> list[Path]:
        folder = Path(root) if root is not None else self.notes_root
        folder.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for note in self.notes.values():
            path = folder / note.filename()
            path.write_text(note.text, encoding="utf-8")
            written.append(path)
        return written


def stripped_title(title: str) -> str:
    return str(title or "").strip()


def text_field(value: str) -> str:
    return "" if value is None else str(value)


def _join(existing: str, added: str) -> str:
    if not existing:
        return added
    if existing.endswith("\n"):
        return existing + added
    return f"{existing}\n{added}"
