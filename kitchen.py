"""The kitchen tab: every session is a chef at a station, drawn in half-block pixel art.

Pixels are drawn on a small canvas and printed two rows per terminal line with ▀/▄,
so it's plain Rich Text: no images, no extra dependencies, works in any terminal.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from rich.console import Group
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

import render
from sessions import Session

WIDTH, HEIGHT = 24, 16  # pixels; prints as 24 columns x 8 lines
CARD_WIDTH = WIDTH + 4

# Fixed sprite colors (Tokyo Night-ish). "A" is filled with the session's own color.
COLORS = {
    "W": "#e6e9f5",  # chef whites
    "w": "#a9b1d6",  # shading, steel
    "S": "#f2c29b",  # skin
    "k": "#1a1b26",  # eyes, handles
    "R": "#f7768e",  # tomato, flame tips, alarm
    "O": "#ff9e64",  # flame
    "Y": "#e0af68",  # brass bell, light wood
    "B": "#8b5a2b",  # counter wood
    "b": "#6b4423",  # counter grain
    "G": "#787c99",  # pots, knife blade
    "g": "#9ece6a",  # greens
    "C": "#c0caf5",  # steam
    "P": "#bb9af7",  # recipe book cover
    "c": "#7dcfff",  # ticket ink
}

# Which station each tool sends the chef to.
STATIONS = {
    "Edit": "board", "Write": "board", "MultiEdit": "board", "NotebookEdit": "board",
    "Bash": "stove", "BashOutput": "stove", "KillShell": "stove", "Monitor": "stove",
    "Read": "taste", "Grep": "taste", "Glob": "taste", "LSP": "taste",
    "WebSearch": "book", "WebFetch": "book", "Skill": "book", "ToolSearch": "book",
    "Agent": "tickets", "Task": "tickets", "SendMessage": "tickets", "TaskStop": "tickets",
}

ACTIONS = {
    "board": "chopping",
    "stove": "on the stove",
    "taste": "tasting",
    "book": "reading recipes",
    "tickets": "calling sous-chefs",
}

CHEF = (
    "..WWWWWW..",
    ".WWWWWWWW.",
    ".WWWWWWWW.",
    "..wwwwww..",
    "..SSSSSS..",
    "..SkSSkS..",
    "..SSSSSS..",
    "...SSSS...",
    "...AAAA...",
    ".WWWWWWWW.",
    "WWWWWWWWWW",
)


class Canvas:
    def __init__(self) -> None:
        self.px: List[List[Optional[str]]] = [[None] * WIDTH for _ in range(HEIGHT)]

    def set(self, x: int, y: int, c: Optional[str]) -> None:
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            self.px[y][x] = c

    def rect(self, x0: int, y0: int, x1: int, y1: int, c: str) -> None:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.set(x, y, c)

    def sprite(self, rows: Tuple[str, ...], x0: int, y0: int) -> None:
        for dy, row in enumerate(rows):
            for dx, c in enumerate(row):
                if c != ".":
                    self.set(x0 + dx, y0 + dy, c)

    def to_text(self, accent: str, dim: bool) -> Text:
        palette = dict(COLORS, A=accent)
        if dim:
            palette = {k: render.blend(v, "#1a1b26", 0.45) for k, v in palette.items()}
        out = Text()
        for y in range(0, HEIGHT, 2):
            for x in range(WIDTH):
                top, bottom = self.px[y][x], self.px[y + 1][x]
                if top is None and bottom is None:
                    out.append(" ")
                elif bottom is None:
                    out.append("▀", Style(color=palette[top]))
                elif top is None:
                    out.append("▄", Style(color=palette[bottom]))
                else:
                    out.append("▀", Style(color=palette[top], bgcolor=palette[bottom]))
            if y < HEIGHT - 2:
                out.append("\n")
        return out


def _counter(c: Canvas) -> None:
    c.rect(0, 11, WIDTH - 1, 11, "w")
    c.rect(0, 12, WIDTH - 1, 15, "B")
    c.rect(0, 13, WIDTH - 1, 13, "b")


def _chef(c: Canvas, asleep: bool = False) -> None:
    c.sprite(CHEF, 1, 1 if asleep else 0)
    if asleep:
        # Eyes shut, head nodding toward the counter.
        c.set(4, 6, "S")
        c.set(7, 6, "S")
        c.set(4, 7, "w")
        c.set(7, 7, "w")


def _arm_to(c: Canvas, x: int, y: int) -> None:
    """A white sleeve from the shoulder to a hand at (x, y)."""
    sx, sy = 10, 9
    steps = max(abs(x - sx), abs(y - sy), 1)
    for i in range(steps):
        c.set(sx + round((x - sx) * i / steps), sy + round((y - sy) * i / steps), "W")
    c.set(x, y, "S")


def _board(c: Canvas, f: int) -> None:
    c.rect(12, 10, 22, 10, "Y")
    for x in (17, 19, 21):
        c.set(x, 9, "g")
    c.set(18, 9, "R")
    if f % 2:  # a fresh slice falls off
        c.set(15, 9, "g")
    # Knife held blade-down, chopping between frames.
    top = 5 if f % 2 == 0 else 7
    c.set(13, top, "k")
    c.rect(13, top + 1, 14, top + 2, "G")
    _arm_to(c, 12, top)


def _stove(c: Canvas, f: int) -> None:
    c.rect(14, 5, 21, 5, "w")  # rim
    c.rect(14, 6, 21, 9, "G")
    c.set(13, 6, "G")
    c.set(22, 6, "G")
    for x in range(14, 22):
        c.set(x, 10, "O" if (x + f) % 2 else "R")
    for i, x in enumerate((15, 18, 20)):  # steam drifting up
        c.set(x + (f + i) % 2, 4 - (f + i) % 4, "C")
    # Spoon from the hand into the pot, stirring.
    hx = 12 if f % 2 == 0 else 13
    c.set(hx + 2, 4, "B")
    c.set(hx + 3, 5, "B")
    _arm_to(c, hx, 7)


def _taste(c: Canvas, f: int) -> None:
    c.rect(15, 8, 21, 8, "w")  # bowl rim
    c.rect(16, 9, 20, 10, "w")
    c.rect(16, 8, 20, 8, "O")  # soup
    c.set(17 + f % 3, 6 - f % 2, "C")
    if f % 2 == 0:  # spoon at the mouth
        c.set(8, 7, "G")
        c.set(9, 7, "G")
        _arm_to(c, 10, 8)
    else:  # dipping back into the bowl
        c.set(14, 7, "G")
        c.set(15, 7, "G")
        _arm_to(c, 13, 8)


def _book(c: Canvas, f: int) -> None:
    c.rect(13, 6, 22, 10, "P")
    c.rect(14, 6, 17, 9, "W")
    c.rect(18, 6, 21, 9, "W")
    for y in (7, 8):
        c.rect(14, y, 16, y, "w")
        c.rect(19, y, 21, y, "w")
    if f % 3 == 2:  # page mid-flip
        c.rect(18, 3, 19, 6, "W")
    _arm_to(c, 13, 8)


def _tickets(c: Canvas, f: int) -> None:
    c.rect(12, 2, 23, 2, "G")  # ticket rail
    for i, x in enumerate((13, 17, 21)):
        drop = 1 if (f + i) % 3 == 0 else 0
        c.rect(x, 3 + drop, x + 2, 6 + drop, "W")
        c.set(x + 1, 4 + drop, "c")
    _arm_to(c, 12, 4 if f % 2 == 0 else 3)


def _bell(c: Canvas, f: int) -> None:
    shake = f % 2
    c.rect(15, 10, 21, 10, "G")
    c.rect(16 + shake, 8, 20 + shake, 9, "Y")
    c.rect(17 + shake, 7, 19 + shake, 7, "Y")
    c.set(18 + shake, 6, "k")
    c.set(22, 4 + shake, "R")
    c.set(22, 6 + shake, "R")
    _arm_to(c, 11, 3 if f % 2 == 0 else 5)  # waving you over


def _asleep(c: Canvas, f: int) -> None:
    c.rect(16, 10, 21, 10, "w")  # empty plate


DRAW = {
    "board": _board, "stove": _stove, "taste": _taste, "book": _book, "tickets": _tickets,
    "bell": _bell, "asleep": _asleep,
}


def station_for(session: Session, status: str) -> str:
    if status == "waiting":
        return "bell"
    if status == "idle":
        return "asleep"
    tool = session.current_tool or "Bash"
    if tool.startswith("mcp__"):
        return "book"
    return STATIONS.get(tool, "stove")


def scene(session: Session, status: str, frame: int) -> Text:
    station = station_for(session, status)
    c = Canvas()
    _counter(c)
    _chef(c, asleep=station == "asleep")
    DRAW[station](c, frame)
    return c.to_text(render.session_color(session.session_id), dim=station == "asleep")


def _context_window(tokens: int) -> int:
    # Logs don't record the window size: assume 200k, and anything past that must be a 1M session.
    return 200_000 if tokens <= 200_000 else 1_000_000


def pot(tokens: int, color: str) -> Text:
    if not tokens:
        return Text("pot  empty", style=render.MUTED)
    pct = min(1.0, tokens / _context_window(tokens))
    filled = round(pct * 12)
    return Text.assemble(
        ("pot  ", render.MUTED),
        ("▰" * filled, color),
        ("▱" * (12 - filled), render.MUTED),
        (f" {round(pct * 100)}%", render.MUTED),
    )


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


def card(session: Session, status: str, frame: int, sous_chefs: int) -> Panel:
    color = render.display_color(session.session_id, status)
    inner = CARD_WIDTH - 4
    station = station_for(session, status)

    if station == "bell":
        bubble = Text(" ( ! ) order up" if frame % 2 == 0 else " (!!!) order up", style=f"bold {render.STATUS['waiting'][0]}")
        action = Text("needs you", style=f"bold {render.STATUS['waiting'][0]}")
    elif station == "asleep":
        bubble = Text(("  z", "   z Z", "    z Z z")[frame % 3], style=render.MUTED)
        action = Text("asleep", style=render.MUTED)
    else:
        bubble = Text("")
        verb = ACTIONS[station]
        detail = session.current_detail
        action = Text.assemble(
            (verb, f"bold {color}"),
            (_truncate(" · " + detail, inner - len(verb)) if detail else "", render.MUTED),
        )

    crew = Text("")
    if sous_chefs and status == "working":
        plural = "s" if sous_chefs > 1 else ""
        crew = Text.assemble(("crew ", render.MUTED), ("☻" * min(sous_chefs, 8), color), (f" {sous_chefs} sous-chef{plural}", render.MUTED))

    body = Group(bubble, scene(session, status, frame), action, pot(session.context_tokens, color), crew)
    title = Text.assemble(render.status_dot(status), (_truncate(session.label, inner - 4), f"bold {color}"))
    return Panel(body, title=title, title_align="left", width=CARD_WIDTH, border_style=color, padding=(0, 1))


def header(counts: Dict[str, int]) -> Text:
    return Text.assemble(
        ("THE KITCHEN   ", f"bold {render.PALETTE[2]}"),
        (f"{counts['working']} cooking", render.STATUS["working"][0]), ("  ·  ", render.MUTED),
        (f"{counts['waiting']} order up", render.STATUS["waiting"][0]), ("  ·  ", render.MUTED),
        (f"{counts['idle']} asleep", render.MUTED),
    )
