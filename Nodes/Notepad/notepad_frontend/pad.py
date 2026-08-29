"""The pad itself: named documents, .txt files, and git include.

Imports no Dear PyGui, so create / append / switch / save can be exercised
without a desktop session. A document is a tab on the FE and a ``.txt`` file
on disk. When the pad is a git clone, save includes those files (``git add``),
commits, and pushes — that is how notes land on GitHub.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from megadesk_contracts.repo import CloneError, ensure_clone, is_clone, repo_name_from_url

ENV_NOTEPAD_ROOT = "NOTEPAD_ROOT"
DEFAULT_PAD_DIRNAME = "Pad"
NOTE_SUFFIX = ".txt"
LOCAL_PAD_NAME = "local"

_GIT_IDENTITY = (
    "-c",
    "user.name=MegaDesk Notepad",
    "-c",
    "user.email=notepad@megadesk.local",
    "-c",
    "commit.gpgsign=false",
)
GIT_TIMEOUT_SEC = 60


class PadError(RuntimeError):
    """A pad operation failed; the message is safe to show on the status line."""


def default_pad_root() -> Path:
    """``NOTEPAD_ROOT`` if set, else ``Pad/`` next to the node package."""
    configured = (os.environ.get(ENV_NOTEPAD_ROOT) or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / DEFAULT_PAD_DIRNAME


def safe_note_name(name: str) -> str:
    """A filesystem-safe stem: letters, digits, dash, underscore, dot."""
    cleaned = re.sub(r"[^\w.-]+", "-", str(name or "").strip(), flags=re.UNICODE)
    cleaned = cleaned.strip(".-")
    if not cleaned or cleaned in {".", ".."}:
        return ""
    if not re.match(r"^[\w.-]+$", cleaned, flags=re.UNICODE):
        return ""
    return cleaned


def _git(args: list[str], *, cwd: Path, identity: bool = False) -> str:
    cmd = ["git"]
    if identity:
        cmd.extend(_GIT_IDENTITY)
    cmd.extend(args)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SEC,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PadError("git not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise PadError(f"git {' '.join(args)} timed out") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PadError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


@dataclass
class Document:
    name: str
    text: str = ""
    included: bool = True

    def filename(self) -> str:
        return f"{self.name}{NOTE_SUFFIX}"


@dataclass
class Pad:
    """In-memory documents plus the directory they persist into."""

    root: Optional[Path] = None
    git_url: str = ""
    documents: dict[str, Document] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    current: str = ""

    def names(self) -> list[str]:
        return list(self.order)

    def current_document(self) -> Optional[Document]:
        if not self.current:
            return None
        return self.documents.get(self.current)

    def _next_name(self) -> str:
        index = 1
        while True:
            candidate = "note" if index == 1 else f"note-{index}"
            if candidate not in self.documents:
                return candidate
            index += 1

    def create(self, name: str = "", text: str = "") -> Document:
        stem = safe_note_name(name) or self._next_name()
        existing = self.documents.get(stem)
        if existing is None:
            existing = Document(name=stem, text=text)
            self.documents[stem] = existing
            self.order.append(stem)
        elif text and not existing.text:
            existing.text = text
        self.current = stem
        return existing

    def append(self, text: str, name: str = "") -> Document:
        payload = "" if text is None else str(text)
        if not payload:
            raise PadError("nothing to add")
        stem = safe_note_name(name) if name else self.current
        if not stem:
            return self.create("note", payload)
        doc = self.documents.get(stem)
        if doc is None:
            return self.create(stem, payload)
        if doc.text and not doc.text.endswith("\n"):
            doc.text = f"{doc.text}\n{payload}"
        else:
            doc.text = f"{doc.text}{payload}"
        return doc

    def switch(self, name: str) -> Document:
        stem = safe_note_name(name)
        if not stem or stem not in self.documents:
            raise PadError(f"{name or 'that document'} is not open")
        self.current = stem
        return self.documents[stem]

    def set_text(self, text: str, name: str = "") -> Document:
        stem = safe_note_name(name) if name else self.current
        if not stem:
            return self.create("note", text)
        doc = self.documents.get(stem)
        if doc is None:
            return self.create(stem, text)
        doc.text = "" if text is None else str(text)
        return doc

    def load_from(self, root: Path) -> None:
        """Replace in-memory documents with ``*.txt`` files under ``root``."""
        root = Path(root)
        self.root = root
        self.documents.clear()
        self.order.clear()
        self.current = ""
        if not root.is_dir():
            return
        for path in sorted(root.glob(f"*{NOTE_SUFFIX}")):
            if not path.is_file():
                continue
            stem = safe_note_name(path.stem)
            if not stem:
                continue
            self.documents[stem] = Document(
                name=stem,
                text=path.read_text(encoding="utf-8"),
            )
            self.order.append(stem)
        if self.order:
            self.current = self.order[0]

    def attach_repo(self, url: str, *, root: Optional[Path] = None) -> Path:
        """Clone ``url`` under the pad root and load its ``.txt`` files."""
        text = str(url or "").strip()
        if not text:
            raise PadError("no repository URL")
        dest = ensure_clone(
            url=text,
            root=Path(root) if root is not None else default_pad_root(),
            name=repo_name_from_url(text),
            depth=None,
        )
        self.git_url = text
        self.load_from(dest)
        return dest

    def _ensure_root(self) -> Path:
        if self.root is not None:
            return Path(self.root)
        folder = LOCAL_PAD_NAME
        if self.git_url:
            try:
                folder = repo_name_from_url(self.git_url)
            except ValueError:
                folder = LOCAL_PAD_NAME
        dest = default_pad_root() / folder
        dest.mkdir(parents=True, exist_ok=True)
        self.root = dest
        return dest

    def write_files(self) -> list[Path]:
        """Persist every document as a ``.txt`` file. Returns written paths."""
        dest = self._ensure_root()
        written: list[Path] = []
        for name in self.order:
            doc = self.documents[name]
            path = dest / doc.filename()
            path.write_text(doc.text, encoding="utf-8")
            written.append(path)
        return written

    def save(self, *, push: bool = True) -> list[Path]:
        """Write ``.txt`` files and, when the pad is a clone, git-include them."""
        written = self.write_files()
        dest = Path(self.root) if self.root is not None else None
        if dest is None or not is_clone(dest):
            return written
        included = [
            self.documents[name].filename()
            for name in self.order
            if self.documents[name].included
        ]
        if not included:
            return written
        _git(["add", "--", *included], cwd=dest)
        status = _git(["status", "--porcelain"], cwd=dest)
        if status.strip():
            _git(
                ["commit", "-m", "notepad: save notes"],
                cwd=dest,
                identity=True,
            )
        if push:
            _git(["push", "origin", "HEAD"], cwd=dest)
        return written


def apply_command(pad: Pad, fields: Mapping[str, str]) -> Document:
    """Apply a parsed ``NOTEPAD:COMMAND`` to ``pad``."""
    from megadesk_contracts.wire.notepad import (
        ACTION_APPEND,
        ACTION_CREATE,
        ACTION_SWITCH,
    )

    action = fields["action"]
    if action == ACTION_CREATE:
        return pad.create(fields["title"], fields.get("text") or "")
    if action == ACTION_APPEND:
        return pad.append(fields["text"], fields.get("title") or "")
    if action == ACTION_SWITCH:
        return pad.switch(fields["title"])
    raise PadError(f"unknown action {action}")
