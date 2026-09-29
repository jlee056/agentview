"""Discover today's Claude Code session logs and tail them into display events."""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional

PROJECTS_ROOT = Path.home() / ".claude" / "projects"
# Claude Code keeps one <pid>.json per open terminal here, with a live busy/idle status.
REGISTRY_ROOT = Path.home() / ".claude" / "sessions"
# Registry statuses that mean Claude has the turn; anything else from a live process means it's your turn.
BUSY_STATUSES = ("busy", "shell", "running")
# A subagent log touched this recently counts as a sous-chef still at work.
SUBAGENT_ACTIVE_SECONDS = 30

# A session mid-turn with no log writes for this long is probably closed or stuck.
WORKING_TIMEOUT = 10 * 60
# A reply nobody has answered for this long counts as inactive rather than waiting.
WAITING_TIMEOUT = 30 * 60

# Wrappers Claude Code puts around non-prompt "user" messages.
NOISE_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<local-command-stdout>",
    "<local-command-caveat>",
    "<system-reminder>",
    "<task-notification>",
    "[Request interrupted",
)

# Tool input keys, in order of how well they summarise the call.
SUMMARY_KEYS = ("file_path", "command", "pattern", "query", "url", "description", "prompt", "skill")


@dataclass
class Event:
    session_id: str
    kind: str  # "prompt" | "reply" | "tool" | "error"
    text: str
    timestamp: str


@dataclass
class Session:
    path: Path
    session_id: str
    label: str
    offset: int = 0
    partial: bytes = b""
    title: Optional[str] = None
    events: List[Event] = field(default_factory=list)
    # "working" while Claude has the turn, "waiting" once it has replied and handed back to you.
    turn: str = "working"
    last_modified: float = 0.0
    # Status from the live-process registry; None when the registry folder doesn't exist.
    live_status: Optional[str] = None
    # Name of the most recent tool call, e.g. "Edit" (drives the kitchen station).
    current_tool: Optional[str] = None
    current_detail: str = ""
    # Tokens in the context window as of Claude's last reply.
    context_tokens: int = 0

    def status(self, now: Optional[float] = None) -> str:
        """working | waiting | idle. Prefers the live registry; falls back to guessing from the log."""
        if self.live_status is not None:
            return self.live_status
        idle_for = (now or time.time()) - self.last_modified
        if self.turn == "working" and idle_for < WORKING_TIMEOUT:
            return "working"
        if self.turn == "waiting" and idle_for < WAITING_TIMEOUT:
            return "waiting"
        return "idle"


def _fallback_label(path: Path) -> str:
    project = path.parent.name.strip("-").split("-")[-1] or "session"
    return f"{project}:{path.stem[:4]}"


def discover_today(root: Path = PROJECTS_ROOT) -> List[Path]:
    """Top-level session logs modified today (local time). Subagent logs live deeper and are skipped."""
    if not root.is_dir():
        return []
    today = dt.date.today()
    found = []
    for path in root.glob("*/*.jsonl"):
        try:
            if dt.date.fromtimestamp(path.stat().st_mtime) == today:
                found.append(path)
        except OSError:
            continue
    return sorted(found)


def _one_line(text: str, limit: int = 100) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tool_summary(block: dict) -> str:
    name = block.get("name", "tool")
    inputs = block.get("input") or {}
    for key in SUMMARY_KEYS:
        value = inputs.get(key)
        if isinstance(value, str) and value.strip():
            if key == "file_path":
                value = Path(value).name
            return f"{name} {_one_line(value)}"
    return name


def parse_record(record: dict, session: Session) -> Iterator[Event]:
    """Turn one JSONL record into zero or more display events."""
    rtype = record.get("type")
    if rtype == "ai-title":
        title = record.get("aiTitle")
        if title:
            session.title = title
        return
    if rtype not in ("user", "assistant") or record.get("isSidechain"):
        return

    ts = record.get("timestamp", "")
    content = (record.get("message") or {}).get("content")

    if rtype == "user":
        if isinstance(content, str):
            text = content.strip()
            if text.startswith("[Request interrupted"):
                session.turn = "waiting"
            elif text and not text.startswith(NOISE_PREFIXES):
                session.turn = "working"
                yield Event(session.session_id, "prompt", text, ts)
        elif isinstance(content, list):
            for block in content:
                btype = block.get("type")
                text = block.get("text", "").strip() if btype == "text" else ""
                if text.startswith("[Request interrupted"):
                    session.turn = "waiting"
                    continue
                if btype == "tool_result":
                    session.turn = "working"
                    if block.get("is_error"):
                        yield Event(session.session_id, "error", "tool error", ts)
                elif text and not text.startswith(NOISE_PREFIXES):
                    session.turn = "working"
                    yield Event(session.session_id, "prompt", text, ts)
        return

    usage = (record.get("message") or {}).get("usage") or {}
    tokens = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    if tokens:
        session.context_tokens = tokens

    if isinstance(content, list):
        for block in content:
            btype = block.get("type")
            if btype == "text" and block.get("text", "").strip():
                # Text last in the log means Claude finished and handed the turn back.
                session.turn = "waiting"
                yield Event(session.session_id, "reply", block["text"].strip(), ts)
            elif btype == "tool_use":
                session.turn = "working"
                session.current_tool = block.get("name", "tool")
                session.current_detail = _tool_summary(block).partition(" ")[2]
                yield Event(session.session_id, "tool", _tool_summary(block), ts)


def read_new(session: Session) -> List[Event]:
    """Read bytes appended since the last call; keep any half-written last line for next time."""
    try:
        session.last_modified = session.path.stat().st_mtime
        with session.path.open("rb") as fh:
            fh.seek(session.offset)
            chunk = fh.read()
    except OSError:
        return []
    if not chunk:
        return []
    session.offset += len(chunk)
    data = session.partial + chunk
    lines = data.split(b"\n")
    session.partial = lines.pop()

    events = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        events.extend(parse_record(record, session))
    if session.title:
        session.label = session.title
    session.events.extend(events)
    return events


def open_session(path: Path) -> Session:
    return Session(path=path, session_id=path.stem, label=_fallback_label(path))


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        # os.kill on Windows terminates the process, so ask the kernel instead.
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_registry(root: Path = REGISTRY_ROOT) -> Optional[Dict[str, str]]:
    """session id → working | waiting for every session with a live terminal. None if there's no registry."""
    if not root.is_dir():
        return None
    live: Dict[str, str] = {}
    for path in root.glob("*.json"):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            pid, session_id = int(entry["pid"]), entry["sessionId"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if _pid_alive(pid):
            live[session_id] = "working" if entry.get("status") in BUSY_STATUSES else "waiting"
    return live


def apply_registry(sessions: Iterable[Session], live: Optional[Dict[str, str]]) -> None:
    """No live process = inactive, whatever the log says."""
    for session in sessions:
        session.live_status = None if live is None else live.get(session.session_id, "idle")


def active_subagents(session: Session, now: Optional[float] = None) -> int:
    """How many of this session's subagent logs were written to in the last few seconds."""
    folder = session.path.with_suffix("") / "subagents"
    if not folder.is_dir():
        return 0
    cutoff = (now or time.time()) - SUBAGENT_ACTIVE_SECONDS
    count = 0
    for path in folder.glob("*.jsonl"):
        try:
            if path.stat().st_mtime >= cutoff:
                count += 1
        except OSError:
            continue
    return count
