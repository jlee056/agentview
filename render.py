"""Turn events into Rich renderables for the merged feed and the per-session panes."""
from __future__ import annotations

import datetime as dt
import zlib

from rich.console import Console, ConsoleOptions, RenderResult
from rich.segment import Segment
from rich.style import Style
from rich.text import Text

from sessions import Event

# Tokyo Night accents: distinct from each other, all readable on the dark background.
# No red or green: those are reserved for the status dots (needs you / working).
PALETTE = (
    "#7aa2f7",  # blue
    "#bb9af7",  # purple
    "#ff9e64",  # orange
    "#7dcfff",  # cyan
    "#e0af68",  # yellow
    "#73daca",  # teal
    "#ff79c6",  # pink
)
FOREGROUND = "#c0caf5"
MUTED = "#565f89"

# Session status → (dot color, label). Separate from the session's own identity color.
STATUS = {
    "working": ("#9ece6a", "working"),
    "waiting": ("#f7768e", "needs you"),
    "idle": ("#565f89", "inactive"),
}


def status_dot(status: str) -> Text:
    color, _ = STATUS[status]
    return Text("● ", style=color)


def session_color(session_id: str) -> str:
    return PALETTE[zlib.crc32(session_id.encode()) % len(PALETTE)]


WAITING_COLOR = "#e6e9f5"


def display_color(session_id: str, status: str) -> str:
    """Only working sessions wear their own color; needs-you is white, inactive is grey."""
    if status == "working":
        return session_color(session_id)
    return WAITING_COLOR if status == "waiting" else MUTED


def blend(color: str, other: str, amount: float) -> str:
    """Mix `amount` of `color` into `other` (both #rrggbb)."""
    a = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * amount + y * (1 - amount)):02x}" for x, y in zip(a, b))


def reply_tint(session_id: str) -> str:
    """Session color softened toward the normal text color so long replies stay easy to read."""
    return blend(session_color(session_id), FOREGROUND, 0.35)


def local_time(timestamp: str) -> str:
    try:
        stamp = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return stamp.astimezone().strftime("%H:%M")
    except ValueError:
        return "--:--"


def _body(event: Event) -> Text:
    color = session_color(event.session_id)
    if event.kind == "prompt":
        return Text.assemble(("you › ", f"bold {color}"), (event.text, f"bold {FOREGROUND}"))
    if event.kind == "tool":
        name, _, rest = event.text.partition(" ")
        return Text.assemble(("⏺ ", color), (name, f"bold {color}"), (" " + rest if rest else "", MUTED))
    if event.kind == "error":
        return Text("✗ tool error", style="#f7768e")
    return Text(event.text, style=reply_tint(event.session_id))


class Bar:
    """Draws a colored bar down the left edge of every wrapped line of `body`."""

    def __init__(self, body: Text, color: str) -> None:
        self.body = body
        self.color = color

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = max(10, options.max_width - 2)
        bar = Segment("▎ ", Style(color=self.color))
        for line in console.render_lines(self.body, options.update_width(width), pad=False):
            yield bar
            yield from line
            yield Segment.line()


def pane_line(event: Event) -> Text:
    return Text.assemble((local_time(event.timestamp) + "  ", MUTED), _body(event))


def merged_header(event: Event, label: str) -> Text:
    color = session_color(event.session_id)
    return Text.assemble(("● ", color), (label, f"bold {color}"), ("  " + local_time(event.timestamp), MUTED))


def merged_line(event: Event) -> Bar:
    return Bar(pane_line(event), session_color(event.session_id))
