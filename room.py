"""The kitchen tab: two little rooms side by side, one shared pixel world.

Pure eye candy, Pokémon-overworld style: tiny chefs wander a tile grid. Working sessions
are in the KITCHEN (stove, cutting board, fridge, island); sessions that need you or are
inactive are in the WAITING ROOM (chairs and tables, plus a reception bell where chefs that
need you wave for attention). A doorway joins the two rooms.

The world is drawn on a pixel canvas printed two pixel rows per terminal line with ▀
(foreground = top pixel, background = bottom pixel): plain Rich Text, no images, no extra
dependencies. The canvas size follows the window (see `fit`), so a bigger window = bigger rooms.
Movement runs on a grid of 6px cells with BFS pathfinding.
"""
from __future__ import annotations

import datetime as dt
import math
import random
import zlib
from collections import deque
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from rich.style import Style
from rich.text import Text

import render

CELL = 6
MIN_COLS, MAX_COLS = 14, 40
MIN_ROWS, MAX_ROWS = 11, 20
WALL_ROWS = 3  # rows 0-2 are the back wall; row 3 is where appliances stand
DOOR_ROWS = (5, 6)
DIV_W = 2  # the wall between the rooms is 2 tiles thick (room for a big clock)
Cell = Tuple[int, int]
Pixels = List[List[str]]

# ---- colors --------------------------------------------------------------------------
WALL, WALL_LOW, TRIM = "#3d4468", "#343a5c", "#565f89"
TILE_A, TILE_B = "#6b7ba8", "#6273a0"          # kitchen floor
WOOD_A, WOOD_B, WOOD_SEAM = "#7a6250", "#705a49", "#5e4a3c"  # waiting-room floor
WOOD_TOP, WOOD_HI, WOOD, WOOD_DK = "#c49a5e", "#e0af68", "#8b5a2b", "#6b4423"
STEEL, STEEL_HI, STEEL_DK, IRON = "#a9b1d6", "#c0caf5", "#787c99", "#565f89"
INK = "#1a1b26"
RED, ORANGE, BRASS, GREEN, MINT = "#f7768e", "#ff9e64", "#e0af68", "#9ece6a", "#73daca"
STEAM = "#dfe6ff"
CHAIR_BACK, CHAIR_SEAT = "#8a4b5a", "#a85f70"
LABEL_BG = "#1a1b26"
TABLE_CLOTHS = ("#c0626b", "#5a8fb0")
TV_COLORS = ("#f7768e", "#e0af68", "#9ece6a", "#7dcfff", "#bb9af7")

CHEF_COLORS = {
    "W": "#f4f6ff", "S": "#f5c7a0", "k": INK, "H": "#4a3b32", "B": "#8b5a2b", "G": "#c0caf5",
}
CAT_COLORS = {"o": "#ff9e64", "k": INK}

# ---- sprites: "." = transparent, "A" = the session's own color -----------------------
STAND = {
    "down": (
        ".TTT.",
        "TTTTT",
        "SkSkS",
        ".SSS.",
        "WAAAW",
        "SAAAS",
        ".WWW.",
    ),
    "up": (
        ".TTT.",
        "TTTTT",
        "HHHHH",
        ".HHH.",
        "WWAWW",
        "SWAWS",
        ".WWW.",
    ),
    "right": (
        ".TTT.",
        "TTTTT",
        ".SSkS",
        ".SSS.",
        ".WAAW",
        ".WAAS",
        ".WWW.",
    ),
}
SOUS = (".T.", "TTT", "SkS", "AAA", "k.k")  # tiny helper chef for a sub-agent
LEGS = (".k.k.", "..k..")
SKIN_TONES = ("#f5c7a0", "#e3a878", "#c68642", "#8d5524", "#ffdbac")
HAIR_COLORS = ("#4a3b32", "#1f1a17", "#c9a227", "#a0522d", "#7a7a7a", "#8b3a3a")

# Which kitchen station each tool sends a working chef to, and what the tag under the name says.
TOOL_STATION = {
    "Edit": "board", "Write": "board", "MultiEdit": "board", "NotebookEdit": "board",
    "Bash": "stove", "BashOutput": "stove", "KillShell": "stove", "Monitor": "stove",
    "Read": "fridge", "Grep": "fridge", "Glob": "fridge", "LSP": "fridge",
    "WebSearch": "window", "WebFetch": "window",
    "Skill": "sink", "ToolSearch": "sink",
    "Agent": "island", "Task": "island", "SendMessage": "island", "TaskStop": "island",
}
TOOL_VERB = {
    "Edit": "editing", "Write": "writing", "MultiEdit": "editing", "NotebookEdit": "editing",
    "Bash": "running", "BashOutput": "running", "KillShell": "running", "Monitor": "watching",
    "Read": "reading", "Grep": "searching", "Glob": "searching", "LSP": "reading",
    "WebSearch": "googling", "WebFetch": "browsing", "Skill": "skilling", "ToolSearch": "looking up",
    "Agent": "delegating", "Task": "delegating", "SendMessage": "messaging", "TaskStop": "stopping",
}
ACTIONS = {"board": "chop", "stove": "stir", "fridge": "peek", "sink": "wash", "island": "knead", "window": None}


def traits(sid: str) -> Tuple[str, str, bool]:
    """(skin, hair, cap) picked from the session id so every chef looks a little different."""
    h = zlib.crc32(sid.encode())
    return SKIN_TONES[h % len(SKIN_TONES)], HAIR_COLORS[(h >> 3) % len(HAIR_COLORS)], bool((h >> 7) % 2)


def verb_for(tool: Optional[str]) -> str:
    if not tool:
        return "thinking"
    return TOOL_VERB.get(tool, "using mcp" if tool.startswith("mcp__") else "working")
SIT_ROWS = 6  # torso only; the table hides the rest

BUBBLE = (
    ".kkkkk.",
    "kWWRWWk",
    "kWWRWWk",
    "kWWRWWk",
    "kWWWWWk",
    "kWWRWWk",
    ".kkkkk.",
    "...k...",
)

CAT_WALK = (("..o.o", "oooo.", "o.o.o"), ("..o.o", ".ooo.", ".o.o."))
CAT_SIT = ("..o.o", "oooo.", "ooooo")

STEP = 3  # pixels per tick
SLEEP_SECS = 600  # an inactive session this long nods off in its chair
MAX_SOUS = 4
BAR_BACK = "#2a2e45"
ZEE = ("CC.", ".C.", ".CC")
ARROW = ("YYYYY", ".YYY.", "..Y..")


def _phase(hour: int) -> str:
    if 8 <= hour < 17:
        return "day"
    if 17 <= hour < 20 or 6 <= hour < 8:
        return "dusk"
    return "night"


def context_window(tokens: int) -> int:
    # Logs don't record the window size: assume 200k, and anything past that must be a 1M session.
    return 200_000 if tokens <= 200_000 else 1_000_000


def fit(width: int, lines: int) -> Tuple[int, int]:
    """(cols, rows) of cells that fit a widget `width` terminal columns x `lines` lines."""
    cols = 16 if width <= 0 else max(MIN_COLS, min(MAX_COLS, width // CELL))
    rows = 12 if lines <= 0 else max(MIN_ROWS, min(MAX_ROWS, lines // 3))
    return cols, rows


# ---- pixel helpers -------------------------------------------------------------------
def _rect(px: Pixels, x0: int, y0: int, x1: int, y1: int, color: str) -> None:
    h, w = len(px), len(px[0])
    for y in range(max(0, y0), min(h - 1, y1) + 1):
        row = px[y]
        for x in range(max(0, x0), min(w - 1, x1) + 1):
            row[x] = color


def _dot(px: Pixels, x: int, y: int, color: str) -> None:
    if 0 <= y < len(px) and 0 <= x < len(px[0]):
        px[y][x] = color


def _blit(px: Pixels, rows: Sequence[str], x0: int, y0: int, palette: Dict[str, str], flip: bool = False) -> None:
    for dy, row in enumerate(rows):
        width = len(row)
        for dx, ch in enumerate(row):
            if ch == ".":
                continue
            color = palette.get(ch)
            if color:
                _dot(px, x0 + (width - 1 - dx if flip else dx), y0 + dy, color)


def _shade(px: Pixels, x0: int, x1: int, y: int, amount: float = 0.6) -> None:
    if 0 <= y < len(px):
        for x in range(max(0, x0), min(len(px[0]) - 1, x1) + 1):
            px[y][x] = render.blend(px[y][x], INK, amount)


def _center(cell: Cell) -> Tuple[int, int]:
    """Where a chef's feet go when standing in `cell` (bottom-center pixel)."""
    return cell[0] * CELL + CELL // 2, cell[1] * CELL + CELL - 1


# ---- layout --------------------------------------------------------------------------
Table = Tuple[int, int, int, int, str, str]  # x0, y0, x1, y1, kind ("cloth"|"island"), cloth color


class Layout:
    """The two rooms for a given size: background, blocked cells, seats and animated spots."""

    def __init__(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        self.W, self.H = cols * CELL, rows * CELL
        self.kc = max(7, min(cols - 7, round(cols * 0.6)))  # kitchen columns
        self.lx = self.kc + DIV_W                            # first waiting-room column
        self.lc = cols - self.lx
        self.px: Pixels = [[WOOD_A] * self.W for _ in range(self.H)]
        self.blocked: Set[Cell] = set()
        self.seats: List[Tuple[Cell, str]] = []  # (cell, facing)
        self.wait_spots: List[Cell] = []
        self.pois: List[Tuple[Cell, str, Optional[str]]] = []
        self.tables: List[Table] = []
        self.windows: List[Tuple[int, int, int, int]] = []
        self.flames: List[Tuple[int, int]] = []
        self.steam: Tuple[int, int, int] = (0, 0, 0)  # x0, x1, y
        self.oven: Optional[Tuple[int, int, int]] = None  # x0, x1, y
        self.tv: Optional[Tuple[int, int, int, int]] = None
        self.bell: Optional[Tuple[int, int]] = None  # x, y of the bell's base
        self.lamp: Optional[Tuple[int, int]] = None
        self.stations: Dict[str, Tuple[Cell, str, Optional[str]]] = {}  # kitchen stations by name
        self.tank: Optional[Tuple[int, int, int, int]] = None
        self.clock: Tuple[int, int] = (0, 0)  # center of the wall clock
        self._tint_cache: Dict[str, Pixels] = {}
        self._build()
        self.walk_blocked = self.blocked | {cell for cell, _ in self.seats}
        every = [(cx, cy) for cx in range(cols) for cy in range(rows)]
        self.free = [c for c in every if c not in self.walk_blocked]
        self.kitchen_free = [c for c in self.free if c[0] < self.kc and c[1] >= WALL_ROWS]
        self.lounge_free = [c for c in self.free if c[0] >= self.lx and c[1] >= WALL_ROWS + 1]
        self.door: Cell = (self.cols - 2, self.rows - 1)

    def tinted(self, phase: str) -> Pixels:
        """The background with the light of the current time of day baked in (cached per phase)."""
        if phase == "day":
            return self.px
        cached = self._tint_cache.get(phase)
        if cached is None:
            tint, amount = ("#10143a", 0.42) if phase == "night" else ("#ff9e64", 0.14)
            colors: Dict[str, str] = {}
            cached = []
            for row in self.px:
                out = []
                for c in row:
                    t = colors.get(c)
                    if t is None:
                        t = colors[c] = render.blend(tint, c, amount)
                    out.append(t)
                cached.append(out)
            self._tint_cache[phase] = cached
        return cached

    # -- construction ---------------------------------------------------------------
    def _build(self) -> None:
        px = self.px
        for y in range(self.H):
            for x in range(self.W):
                if y < WALL_ROWS * CELL:
                    px[y][x] = WALL_LOW if y >= 12 else WALL
                elif x < self.kc * CELL:
                    px[y][x] = TILE_A if (x // CELL + y // CELL) % 2 == 0 else TILE_B
                else:
                    plank = (y - 18) // 3
                    base = WOOD_A if plank % 2 == 0 else WOOD_B
                    px[y][x] = WOOD_SEAM if (x + plank * 7) % 18 == 0 else base
        _rect(px, 0, 0, self.W - 1, 0, TRIM)
        _rect(px, 0, WALL_ROWS * CELL - 1, self.W - 1, WALL_ROWS * CELL - 1, TRIM)
        self.blocked |= {(cx, cy) for cx in range(self.cols) for cy in range(WALL_ROWS)}
        self._kitchen()
        self._divider()
        self._lounge()

    def _appliance(self, cx0: int, cw: int) -> None:
        for cx in range(cx0, cx0 + cw):
            self.blocked.add((cx, WALL_ROWS))

    def _window(self, x0: int, x1: int) -> None:
        px = self.px
        _rect(px, x0 + 1, 3, x1 - 1, 12, STEEL_HI)
        _rect(px, x0, 13, x1, 13, STEEL)
        self.windows.append((x0 + 2, 4, x1 - 2, 11))

    def _kitchen(self) -> None:
        px = self.px
        base = (WALL_ROWS + 1) * CELL - 1  # y of the last pixel row of an appliance
        cx = 0
        for name, w in (("fridge", 2), ("counter", 3), ("stove", 2), ("window", 2), ("sink", 3), ("window", 2)):
            if cx + w > self.kc:
                break
            x0, x1 = cx * CELL, (cx + w) * CELL - 1
            if name == "fridge":
                self._appliance(cx, w)
                _rect(px, x0, base - 15, x1, base, STEEL)
                _rect(px, x0, base - 15, x1, base - 15, STEEL_HI)
                _rect(px, x0, base - 7, x1, base - 7, IRON)
                _rect(px, x1 - 2, base - 13, x1 - 2, base - 10, INK)
                _rect(px, x1 - 2, base - 5, x1 - 2, base - 2, INK)
                self.pois.append(((cx, WALL_ROWS + 1), "up", None))
                self.stations["fridge"] = ((cx, WALL_ROWS + 1), "up", "peek")
            elif name == "counter":
                self._appliance(cx, w)
                _rect(px, x0, base - 8, x1, base - 7, WOOD_TOP)
                _rect(px, x0, base - 8, x1, base - 8, WOOD_HI)
                _rect(px, x0, base - 6, x1, base, WOOD)
                _rect(px, x0, base - 5, x1, base - 5, WOOD_DK)
                for x in range(x0 + 5, x1, 6):
                    _rect(px, x, base - 4, x, base, WOOD_DK)
                mid = (x0 + x1) // 2
                _rect(px, mid - 4, base - 8, mid + 4, base - 7, "#f2d79b")
                for dx, c in ((-3, RED), (0, GREEN), (2, ORANGE)):
                    _rect(px, mid + dx, base - 10, mid + dx + 1, base - 9, c)
                self.pois.append(((cx + 1, WALL_ROWS + 1), "up", "chop"))
                self.stations["board"] = ((cx + 1, WALL_ROWS + 1), "up", "chop")
                _rect(px, x0 + 2, 2, x1 - 2, 2, STEEL_DK)  # pan rail with two hanging pans
                for px0 in (x0 + 3, x1 - 5):
                    _rect(px, px0, 3, px0 + 2, 7, IRON)
                    _dot(px, px0 + 1, 3, STEEL_DK)
            elif name == "stove":
                self._appliance(cx, w)
                _rect(px, x0, base - 9, x1, base - 8, STEEL_DK)
                _rect(px, x0, base - 7, x1, base, IRON)
                for x in (x0 + 2, x0 + 5, x0 + 8):
                    _dot(px, x, base - 6, BRASS)
                _rect(px, x0 + 2, base - 4, x1 - 2, base - 1, INK)
                _rect(px, x0 + 1, base - 15, x1 - 1, base - 10, "#9aa5ce")
                _rect(px, x0, base - 15, x1, base - 15, STEEL_HI)
                _rect(px, x0 + 2, base - 15, x1 - 2, base - 15, ORANGE)
                self.flames = [(x0, base - 10), (x1, base - 10)]
                self.oven = (x0 + 2, x1 - 2, base - 2)
                self.steam = (x0 + 3, x1 - 3, base - 16)
                self.pois.append(((cx, WALL_ROWS + 1), "up", "stir"))
                self.stations["stove"] = ((cx, WALL_ROWS + 1), "up", "stir")
            elif name == "window":
                self._window(x0, x1)
                _rect(px, x0 + 5, 4, x0 + 6, 11, STEEL_HI)
                _rect(px, x0 + 2, 7, x1 - 2, 7, STEEL_HI)
                self.pois.append(((cx, WALL_ROWS), "up", None))
                self.pois.append(((cx + 1, WALL_ROWS), "up", None))
                self.stations.setdefault("window", ((cx, WALL_ROWS), "up", None))
            elif name == "sink":
                self._appliance(cx, w)
                _rect(px, x0, base - 8, x1, base - 7, STEEL)
                _rect(px, x0 + 3, base - 8, x1 - 3, base - 7, "#5a8fb0")
                _rect(px, x0, base - 6, x1, base, WOOD)
                _rect(px, x0, base - 5, x1, base - 5, WOOD_DK)
                _rect(px, x0 + 8, base - 12, x0 + 8, base - 9, STEEL_HI)
                _dot(px, x0 + 9, base - 12, STEEL_HI)
                self.pois.append(((cx + 1, WALL_ROWS + 1), "up", None))
                self.stations["sink"] = ((cx + 1, WALL_ROWS + 1), "up", "wash")
            cx += w

        # Island in the middle of the floor
        iw = min(max(3, self.kc - 4), 7)
        icx = (self.kc - iw) // 2
        r0 = 7
        for x in range(icx, icx + iw):
            for y in (r0, r0 + 1):
                self.blocked.add((x, y))
        self.tables.append((icx * CELL, r0 * CELL, (icx + iw) * CELL - 1, (r0 + 2) * CELL - 1, "island", ""))
        self.pois += [
            ((icx - 1, r0), "right", None), ((icx + iw, r0 + 1), "left", None),
            ((icx + 1, r0 - 1), "down", None), ((icx + iw - 2, r0 + 2), "up", None),
        ]
        self.stations["island"] = ((icx + iw // 2, r0 - 1), "down", "knead")
        self._plant(0, self.rows - 1)
        self.pois += [((self.kc - 1, self.rows - 2), "down", None), ((2, self.rows - 1), "down", None)]

    def _plant(self, cx: int, cy: int) -> None:
        self.blocked.add((cx, cy))
        x0, y1 = cx * CELL, (cy + 1) * CELL - 1
        _rect(self.px, x0 + 1, y1 - 2, x0 + 4, y1, "#c0684a")
        _rect(self.px, x0 + 2, y1 - 6, x0 + 3, y1 - 3, "#4b8f4b")
        _rect(self.px, x0, y1 - 9, x0 + 5, y1 - 5, GREEN)
        _dot(self.px, x0 + 1, y1 - 8, MINT)
        _dot(self.px, x0 + 4, y1 - 6, MINT)

    def _divider(self) -> None:
        px, kc = self.px, self.kc
        x0, x1 = kc * CELL, (kc + DIV_W) * CELL - 1
        _rect(px, x0, 0, x1, self.H - 1, WALL)
        _rect(px, x0, 0, x0, self.H - 1, TRIM)
        _rect(px, x1, 0, x1, self.H - 1, TRIM)
        for cx in range(kc, kc + DIV_W):
            for cy in range(self.rows):
                if cy not in DOOR_ROWS:
                    self.blocked.add((cx, cy))
        top, bottom = DOOR_ROWS[0] * CELL, (DOOR_ROWS[-1] + 1) * CELL - 1
        for y in range(top, bottom + 1):  # doorway: the floor shows through
            for x in range(x0 + 1, x1):
                px[y][x] = WOOD_A if (y // 3) % 2 == 0 else WOOD_B
        _rect(px, x0, top - 2, x1, top - 1, WOOD)  # lintel
        _rect(px, x0, top, x0, bottom, WOOD_DK)
        _rect(px, x1, top, x1, bottom, WOOD_DK)
        # Clock face (hands are drawn per frame)
        cx0, cy0 = (x0 + x1 + 1) // 2, 6
        self.clock = (cx0, cy0)
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if dx * dx + dy * dy <= 18:
                    _dot(px, cx0 + dx, cy0 + dy, "#f4f6ff" if dx * dx + dy * dy <= 13 else STEEL)
        for dx, dy in ((0, -3), (3, 0), (0, 3), (-3, 0)):
            _dot(px, cx0 + dx, cy0 + dy, INK)

    def _lounge(self) -> None:
        px = self.px
        base = (WALL_ROWS + 1) * CELL - 1
        cx = self.lx
        for name, w in (("bell", 3), ("window", 2), ("tv", 3), ("shelf", 3), ("tank", 3)):
            if cx + w > self.cols:
                continue
            x0, x1 = cx * CELL, (cx + w) * CELL - 1
            if name == "bell":
                self._appliance(cx, w)
                _rect(px, x0, base - 8, x1, base - 7, WOOD_TOP)
                _rect(px, x0, base - 8, x1, base - 8, WOOD_HI)
                _rect(px, x0, base - 6, x1, base, WOOD)
                _rect(px, x0, base - 5, x1, base - 5, WOOD_DK)
                mid = (x0 + x1) // 2
                self.bell = (mid, base - 9)
                self.lamp = (mid - 1, 5)
                self.wait_spots = sorted(
                    [(c, WALL_ROWS + 1) for c in range(self.lx, self.cols)],
                    key=lambda cell: abs(cell[0] * CELL + CELL // 2 - mid),
                )
            elif name == "window":
                self._window(x0, x1)
                _rect(px, x0 + 5, 4, x0 + 6, 11, STEEL_HI)
                _rect(px, x0 + 2, 7, x1 - 2, 7, STEEL_HI)
            elif name == "shelf":
                self._appliance(cx, w)
                _rect(px, x0, base - 17, x1, base, WOOD)
                for y in (base - 17, base - 11, base - 5):
                    _rect(px, x0, y, x1, y, WOOD_DK)
                for row_y in (base - 16, base - 10, base - 4):
                    for i, x in enumerate(range(x0 + 1, x1 - 1, 2)):
                        _rect(px, x, row_y, x, row_y + 3, TV_COLORS[(i + row_y) % len(TV_COLORS)])
            elif name == "tank":
                self._appliance(cx, w)
                _rect(px, x0, base - 6, x1, base, WOOD)
                _rect(px, x0, base - 6, x1, base - 6, WOOD_HI)
                _rect(px, x0 + 1, base - 17, x1 - 1, base - 7, STEEL_HI)
                _rect(px, x0 + 2, base - 16, x1 - 2, base - 8, "#4a7fb5")
                _rect(px, x0 + 2, base - 9, x1 - 2, base - 8, "#c9b27a")  # gravel
                _rect(px, x0 + 4, base - 13, x0 + 4, base - 10, "#4b8f4b")
                self.tank = (x0 + 2, base - 16, x1 - 2, base - 10)
            elif name == "tv":
                self._appliance(cx, w)
                _rect(px, x0, base - 6, x1, base, WOOD)
                _rect(px, x0, base - 6, x1, base - 6, WOOD_HI)
                _rect(px, x0 + 2, base - 15, x1 - 2, base - 7, INK)
                self.tv = (x0 + 3, base - 14, x1 - 3, base - 8)
            cx += w
        if not self.wait_spots:
            self.wait_spots = [(c, WALL_ROWS + 1) for c in range(self.lx, self.cols)]

        # Rug on the floor (walkable)
        rx0, rx1 = (self.lx + 1) * CELL, (self.cols - 1) * CELL - 1
        ry0, ry1 = (self.rows - 3) * CELL + 1, (self.rows - 1) * CELL - 2
        if rx1 - rx0 > 12:
            _rect(px, rx0, ry0, rx1, ry1, "#5b4a6b")
            _rect(px, rx0 + 2, ry0 + 2, rx1 - 2, ry1 - 2, "#6f5c82")
            for x in range(rx0, rx1 + 1, 3):
                _dot(px, x, ry0, BRASS)
                _dot(px, x, ry1, BRASS)

        # Tables with chairs
        if self.lc >= 5:
            tw = self.lc - 2
            tcx = self.lx + 1
            for n, r0 in enumerate([7] + ([12] if self.rows >= 15 else [])):
                for x in range(tcx, tcx + tw):
                    for y in (r0, r0 + 1):
                        self.blocked.add((x, y))
                self.tables.append((tcx * CELL, r0 * CELL, (tcx + tw) * CELL - 1, (r0 + 2) * CELL - 1,
                                    "cloth", TABLE_CLOTHS[n % 2]))
                for x in range(tcx, tcx + tw, 2):
                    self.seats.append(((x, r0 - 1), "down"))
                    self.seats.append(((x, r0 + 2), "up"))
        for (scx, scy), face in self.seats:
            sx, ay = scx * CELL + CELL // 2, scy * CELL + CELL - 1
            if face == "down":
                _rect(px, sx - 3, ay - 6, sx + 3, ay, CHAIR_BACK)
                _rect(px, sx - 3, ay - 6, sx + 3, ay - 6, CHAIR_SEAT)
                _rect(px, sx - 3, ay - 1, sx + 3, ay, CHAIR_SEAT)
            else:
                _rect(px, sx - 3, ay - 4, sx + 2, ay - 2, CHAIR_SEAT)
        self._plant(self.cols - 1, self.rows - 1)


# ---- movers --------------------------------------------------------------------------
class Mover:
    def __init__(self, kind: str, cell: Cell, rng: random.Random) -> None:
        self.kind = kind  # "chef" | "cat"
        self.rng = rng
        self.cell = cell
        self.ax, self.ay = _center(cell)
        self.next: Optional[Cell] = None
        self.path: List[Cell] = []
        self.mode = "pause"  # pause | walk | sit | alert
        self.timer = 2
        self.wait = 0
        self.facing = "down"
        self.goal: Optional[Cell] = None
        self.goal_face = "down"
        self.goal_action: Optional[str] = None
        self.goal_kind: Optional[str] = None  # poi | random | seat | wait | wait_here
        self.action: Optional[str] = None
        self.stride = 0
        self.holding = False  # deliberately settled at a spot (others must route around); False while jammed
        # chef only
        self.sid = ""
        self.label = ""
        self.color = "#7aa2f7"
        self.status = "working"
        self.tool: Optional[str] = None  # the tool the session is using right now
        self.detail = ""
        self.context = 0  # tokens in the context window
        self.subs = 0  # active sub-agents
        self.idle_secs = 0.0
        self.tool_seen: Optional[str] = None
        self.owner = ""  # sous-chefs: the session they help

    @property
    def stationary(self) -> bool:
        return self.mode != "walk"


def find_path(start: Cell, goal: Cell, blocked: Set[Cell], cols: int, rows: int) -> Optional[List[Cell]]:
    """Shortest 4-neighbor path over free cells; excludes `start`, includes `goal`."""
    if start == goal:
        return []
    prev: Dict[Cell, Cell] = {start: start}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        cx, cy = cur
        for nxt in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if not (0 <= nxt[0] < cols and 0 <= nxt[1] < rows):
                continue
            if nxt in prev or (nxt in blocked and nxt != goal):
                continue
            prev[nxt] = cur
            if nxt == goal:
                path = [nxt]
                while path[-1] != start:
                    path.append(prev[path[-1]])
                path.pop()
                path.reverse()
                return path
            queue.append(nxt)
    return None


Label = Tuple[int, int, str, str]  # terminal line, first column, text, color


class Room:
    def __init__(self, cols: int = 16, rows: int = 12, seed: Optional[int] = None) -> None:
        self.rng = random.Random(seed)
        self.lay = Layout(cols, rows)
        self.cols, self.rows = cols, rows
        self.W, self.H = self.lay.W, self.lay.H
        self.chefs: Dict[str, Mover] = {}
        self.cat = Mover("cat", self.lay.door, self.rng)
        self.cat.facing = "left"
        self.frame = 0
        self.name_mode = 0  # 0 = names + what each chef is doing, 1 = names, 2 = off
        self.sous: List[Mover] = []
        self.selected: Optional[str] = None

    # ---- public -----------------------------------------------------------------
    def sync(self, entries: Sequence[tuple]) -> None:
        """entries = (session_id, label, status[, info]); status = working|waiting|idle.

        `info` is an optional dict: tool, detail, context (tokens), subs (active sub-agents), idle (seconds).
        """
        seen = set()
        for entry in entries:
            sid, label, status = entry[0], entry[1], entry[2]
            info = entry[3] if len(entry) > 3 and entry[3] else {}
            seen.add(sid)
            chef = self.chefs.get(sid)
            if chef is None:
                chef = Mover("chef", self._spawn_cell(), self.rng)
                chef.sid = sid
                chef.color = render.session_color(sid)
                self.chefs[sid] = chef
            chef.label = label
            chef.status = status
            chef.tool = info.get("tool")
            chef.detail = info.get("detail", "")
            chef.context = info.get("context", 0)
            chef.subs = info.get("subs", 0)
            chef.idle_secs = info.get("idle", 0.0)
        for sid in list(self.chefs):
            if sid not in seen:
                del self.chefs[sid]
        if self.selected not in self.chefs:
            self.selected = None
        self._sync_sous()

    def _sync_sous(self) -> None:
        """One tiny sous-chef per active sub-agent, following its parent around."""
        mine: Dict[str, List[Mover]] = {}
        for helper in self.sous:
            mine.setdefault(helper.owner, []).append(helper)
        keep: List[Mover] = []
        for sid, chef in self.chefs.items():
            want = min(MAX_SOUS, chef.subs) if chef.status == "working" and chef.cell not in self.lay.walk_blocked else 0
            have = mine.get(sid, [])[:want]
            while len(have) < want:
                helper = Mover("sous", chef.cell, self.rng)
                helper.owner, helper.sid, helper.color = sid, sid, chef.color
                helper.timer = self.rng.randint(1, 6)
                have.append(helper)
            keep += have
        self.sous = keep

    def step(self) -> None:
        self.frame += 1
        for mover in self._movers() + self.sous:
            self._tick(mover)

    def cycle_names(self) -> str:
        self.name_mode = (self.name_mode + 1) % 3
        return ("names + activity", "names", "no labels")[self.name_mode]

    def cycle_selection(self) -> None:
        ids = sorted(self.chefs, key=lambda k: (self.chefs[k].label, k))
        if not ids:
            self.selected = None
        elif self.selected not in ids:
            self.selected = ids[0]
        else:
            self.selected = ids[(ids.index(self.selected) + 1) % len(ids)]

    def chef_at(self, x: int, y: int) -> Optional[str]:
        """The chef under pixel (x, y), if any."""
        best, best_d = None, 99
        for sid, m in self.chefs.items():
            if m.ax - 5 <= x <= m.ax + 5 and self._head_top(m) - 4 <= y <= m.ay + 3:
                d = abs(x - m.ax) + abs(y - (m.ay - 3))
                if d < best_d:
                    best, best_d = sid, d
        return best

    def select(self, sid: Optional[str]) -> None:
        self.selected = sid if sid in self.chefs else None

    def counts(self) -> Dict[str, int]:
        out = {"working": 0, "waiting": 0, "idle": 0}
        for chef in self.chefs.values():
            out[chef.status] += 1
        return out

    def draw(self, hour: Optional[int] = None) -> Pixels:
        lay = self.lay
        hour = dt.datetime.now().hour if hour is None else hour
        px = [row[:] for row in lay.tinted(_phase(hour))]
        self._animate(px, hour)

        items: List[Tuple[float, int, Callable[[], None]]] = []
        for t in lay.tables:
            items.append((t[3], 0, lambda t=t: self._draw_table(px, t)))
        for i, m in enumerate(self._movers(), start=1):
            if m.kind == "cat":
                items.append((m.ay, i, lambda m=m: self._draw_cat(px, m)))
            elif m.mode == "sit":
                items.append((m.ay, i, lambda m=m: self._draw_seated(px, m)))
                if m.facing == "down":
                    items.append((self._table_below(m) + 0.5, i, lambda m=m: self._draw_table_setting(px, m)))
            else:
                items.append((m.ay, i, lambda m=m: self._draw_standing(px, m)))
        for j, m in enumerate(self.sous):
            items.append((m.ay + 0.1, 200 + j, lambda m=m: self._draw_sous(px, m)))
        for _, _, fn in sorted(items, key=lambda it: (it[0], it[1])):
            fn()
        for m in self.chefs.values():  # bars, bubbles and the selection marker sit on top of everything
            self._draw_bar(px, m)
            if m.mode == "alert":
                _blit(px, BUBBLE, m.ax - 3, m.ay - 17, {"k": INK, "W": "#ffffff", "R": RED if (self.frame // 3) % 2 else ORANGE})
        sel = self.chefs.get(self.selected) if self.selected else None
        if sel:
            _rect(px, sel.ax - 3, sel.ay + 1, sel.ax + 3, sel.ay + 1, "#ffd166")
            bounce = (self.frame // 3) % 2
            top = self._head_top(sel) - (22 if sel.mode == "alert" else 7) - bounce
            _blit(px, ARROW, sel.ax - 2, top, {"Y": "#ffd166"})
        return px

    def _asleep(self, m: Mover) -> bool:
        return m.status == "idle" and m.idle_secs >= SLEEP_SECS

    def _head_top(self, m: Mover) -> int:
        """y of the top of a chef's hat (used to place bars, arrows and click targets)."""
        if m.mode == "sit":
            base = m.ay - 5 if m.facing == "down" else m.ay - 7
            return base + (1 if self._asleep(m) else 0)
        bob = 1 if m.mode == "walk" and m.stride % 2 else 0
        return m.ay - 7 - bob

    def _activity(self, m: Mover) -> str:
        if m.status == "waiting":
            return "needs you!"
        if m.status == "idle":
            if self._asleep(m):
                return "zzz"
            mins = int(m.idle_secs // 60)
            return f"idle {mins}m" if mins >= 1 else "idle"
        return verb_for(m.tool)

    def labels(self) -> List[Label]:
        if self.name_mode == 2:
            return []
        out: List[Label] = []
        for m in sorted(self.chefs.values(), key=lambda c: c.ay):
            text = m.label.strip()
            text = text if len(text) <= 10 else text[:9] + "…"
            if not text:
                continue
            line = min(self.H // 2 - 1, (m.ay + 1) // 2)
            out.append((line, max(0, min(self.W - len(text), m.ax - len(text) // 2)), text, m.color))
            if self.name_mode == 0 and line + 1 < self.H // 2:
                act = self._activity(m)[:10]
                out.append((line + 1, max(0, min(self.W - len(act), m.ax - len(act) // 2)), act, "#9aa5ce"))
        return out

    def details(self, width: int = 80) -> Text:
        """A short panel about the selected chef (or a hint when nothing is selected)."""
        m = self.chefs.get(self.selected) if self.selected else None
        if m is None:
            return Text("click a chef (or press t) to inspect it", style=render.MUTED)
        status = {"working": "working", "waiting": "needs you", "idle": "inactive"}[m.status]
        head = Text.assemble(
            (f"▸ {m.label}", f"bold {m.color}"), ("   ", ""),
            (status, render.STATUS[m.status][0]),
        )
        doing = verb_for(m.tool) if m.status == "working" else self._activity(m)
        detail = f" · {m.detail}" if m.detail and m.status == "working" else ""
        line2 = Text.assemble((doing, f"bold {m.color}"), ((detail[: max(0, width - len(doing) - 4)]), render.MUTED))
        bits = Text()
        if m.context:
            pct = min(1.0, m.context / context_window(m.context))
            filled = round(pct * 12)
            bits.append("context ", style=render.MUTED)
            bits.append("▰" * filled, style=GREEN if pct < 0.6 else BRASS if pct < 0.85 else RED)
            bits.append("▱" * (12 - filled), style=render.MUTED)
            bits.append(f" {round(pct * 100)}% ({m.context // 1000}k)   ", style=render.MUTED)
        if m.subs and m.status == "working":
            bits.append(f"{m.subs} helper{'s' if m.subs > 1 else ''}   ", style=render.MUTED)
        if m.status == "idle" and m.idle_secs >= 60:
            bits.append(f"idle {int(m.idle_secs // 60)}m   ", style=render.MUTED)
        bits.append("v = open transcript", style=render.MUTED)
        return Text("\n").join([head, line2, bits])

    def render(self, hour: Optional[int] = None) -> Text:
        return to_text(self.draw(hour), self.labels(), self.W, self.H)

    def legend(self) -> Text:
        out = Text()
        for chef in sorted(self.chefs.values(), key=lambda c: c.label):
            color = render.display_color(chef.sid, chef.status)
            mark = "!" if chef.status == "waiting" else "☻"
            label = chef.label if len(chef.label) <= 22 else chef.label[:21] + "…"
            out.append(f"{mark} {label}   ", style=color)
        return out

    def header(self) -> Text:
        c = self.counts()
        return Text.assemble(
            ("KITCHEN ", f"bold {render.PALETTE[2]}"),
            (f"{c['working']} cooking", render.STATUS["working"][0]),
            ("   ·   ", render.MUTED),
            ("WAITING ROOM ", f"bold {render.PALETTE[0]}"),
            (f"{c['waiting']} need you", render.STATUS["waiting"][0]), ("  ·  ", render.MUTED),
            (f"{c['idle']} chilling", render.MUTED),
        )

    # ---- internals --------------------------------------------------------------
    def _movers(self) -> List[Mover]:
        return [self.cat, *self.chefs.values()]

    def _spawn_cell(self) -> Cell:
        taken = {m.cell for m in self._movers()} | {m.next for m in self._movers() if m.next}
        start = self.lay.door
        queue, seen = deque([start]), {start}
        while queue:
            cell = queue.popleft()
            if cell not in taken and cell not in self.lay.walk_blocked:
                return cell
            cx, cy = cell
            for nxt in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if 0 <= nxt[0] < self.cols and 0 <= nxt[1] < self.rows and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return start

    def _table_below(self, m: Mover) -> float:
        for t in self.lay.tables:
            if t[4] == "cloth" and t[0] <= m.ax <= t[2] and t[1] - 1 <= m.ay + 1 <= t[1] + 1:
                return t[3]
        return m.ay + 6

    def _taken(self, me: Mover) -> Set[Cell]:
        taken: Set[Cell] = set()
        for other in self._movers():
            if other is me:
                continue
            taken.add(other.cell)
            if other.next:
                taken.add(other.next)
            if other.goal:
                taken.add(other.goal)
        return taken

    def _obstacles(self, me: Mover, include_moving: bool = False) -> Set[Cell]:
        blocked = set(self.lay.walk_blocked)
        if me.kind == "sous":  # helpers are tiny: they only avoid furniture
            return blocked
        for other in self._movers():
            if other is me:
                continue
            if include_moving or other.mode in ("sit", "alert") or (other.stationary and other.holding):
                blocked.add(other.cell)
            if include_moving and other.next:
                blocked.add(other.next)
        return blocked

    def _set_route(self, m: Mover, goal: Cell, face: str, action: Optional[str], kind: str, include_moving: bool = False) -> bool:
        path = find_path(m.cell, goal, self._obstacles(m, include_moving), self.cols, self.rows)
        if path is None:
            return False
        m.goal, m.goal_face, m.goal_action, m.goal_kind = goal, face, action, kind
        m.path = path
        m.wait = 0
        if path:
            m.mode = "walk"
        else:
            self._arrive(m)
        return True

    def _plan(self, m: Mover) -> None:
        lay = self.lay
        taken = self._taken(m)
        if m.kind == "cat":
            pool = [c for c in lay.free if c not in taken and c != m.cell]
            if pool and self._set_route(m, self.rng.choice(pool), m.facing, None, "random"):
                return
        elif m.status == "working":
            station = lay.stations.get(TOOL_STATION.get(m.tool or "", ""))
            if station and self.rng.random() < 0.6:  # stay near the station for what we're doing
                cell, face, action = station
                if cell == m.cell:
                    self._hold(m, face, action)
                    return
                if cell not in taken and self._set_route(m, cell, face, action, "poi"):
                    return
            pois = [p for p in lay.pois if p[0] not in taken and p[0] != m.cell and p[0] not in lay.walk_blocked]
            if pois and self.rng.random() < 0.7:
                cell, face, action = self.rng.choice(pois)
                if self._set_route(m, cell, face, action, "poi"):
                    return
            pool = [c for c in lay.kitchen_free if c not in taken and c != m.cell]
            if pool and self._set_route(m, self.rng.choice(pool), "down", None, "random"):
                return
        else:  # not working: hang around the waiting room
            pool = [c for c in lay.lounge_free if c not in taken and c != m.cell]
            if pool and self._set_route(m, self.rng.choice(pool), "down", None, "random"):
                return
        m.timer = 4

    def _hold(self, m: Mover, face: str, action: Optional[str]) -> None:
        """Stay put at a station for a while, doing its little animation."""
        m.mode, m.timer, m.holding = "pause", self.rng.randint(14, 34), True
        m.path, m.goal, m.facing, m.action = [], None, face, action

    def _go_station(self, m: Mover) -> None:
        """The session just switched tools: walk to the matching station."""
        name = TOOL_STATION.get(m.tool or "") or ("window" if (m.tool or "").startswith("mcp__") else "")
        station = self.lay.stations.get(name)
        if not station:
            return
        cell, face, action = station
        if cell == m.cell:
            self._hold(m, face, action)
        elif cell not in self._taken(m):
            self._set_route(m, cell, face, action, "poi")

    def _go_seat(self, m: Mover) -> None:
        taken = self._taken(m)
        for seat, face in self.lay.seats:
            if seat == m.cell or seat not in taken:
                if self._set_route(m, seat, face, None, "seat"):
                    return
        # No free chair: still make sure we're in the waiting room, then hang around and retry soon.
        lay = self.lay
        if m.mode == "walk" and m.goal is not None and m.goal[0] >= lay.lx:
            return  # already on the way there; don't interrupt the walk
        if m.cell[0] < lay.lx:
            pool = [c for c in lay.lounge_free if c not in taken]
            if pool and self._set_route(m, self.rng.choice(pool), "down", None, "random"):
                return
        m.mode, m.timer, m.path, m.goal, m.goal_kind, m.holding = "pause", 10, [], None, "seat", False

    def _go_wait(self, m: Mover) -> None:
        taken = self._taken(m)
        for spot in self.lay.wait_spots:
            if spot == m.cell or spot not in taken:
                if self._set_route(m, spot, "down", None, "wait"):
                    return
        # Every bell spot is taken: wave from elsewhere in the waiting room and retry in a few seconds.
        lay = self.lay
        if m.cell[0] < lay.lx:
            pool = [c for c in lay.lounge_free if c not in taken]
            if pool and self._set_route(m, self.rng.choice(pool), "down", None, "wait_here"):
                return
        m.mode, m.facing, m.path, m.goal, m.goal_kind, m.timer = "alert", "down", [], None, "wait_here", 25

    def _arrive(self, m: Mover) -> None:
        if m.kind == "sous":
            m.mode, m.timer, m.goal = "pause", self.rng.randint(2, 6), None
            return
        if m.goal_kind == "seat":
            m.mode, m.facing, m.action, m.goal = "sit", m.goal_face, None, None
            return
        if m.goal_kind in ("wait", "wait_here"):
            m.mode, m.facing, m.action, m.goal, m.timer = "alert", "down", None, None, 25
            return
        if m.goal_kind == "sidestep":
            m.mode, m.timer, m.holding, m.goal, m.action = "pause", self.rng.randint(1, 4), False, None, None
            return
        m.mode = "pause"
        m.holding = True
        m.timer = self.rng.randint(25, 70) if m.kind == "cat" else self.rng.randint(14, 34)
        m.facing = m.goal_face if m.kind == "chef" else m.facing
        m.action = m.goal_action
        m.goal = None

    def _tick_sous(self, m: Mover) -> None:
        parent = self.chefs.get(m.owner)
        if parent is None:
            return
        if m.mode == "walk":
            self._walk(m)
            return
        m.timer -= 1
        if m.timer > 0:
            return
        m.timer = self.rng.randint(3, 8)
        px_, py_ = parent.cell
        near = [c for c in self.lay.free if abs(c[0] - px_) <= 2 and abs(c[1] - py_) <= 2 and c != m.cell and abs(c[0] - px_) + abs(c[1] - py_) >= 1]
        if near:
            self._set_route(m, self.rng.choice(near), "down", None, "follow")

    def _tick(self, m: Mover) -> None:
        if m.kind == "sous":
            self._tick_sous(m)
            return
        if m.kind == "chef":
            at_rest = m.next is None
            if m.status == "idle":
                if at_rest and m.mode != "sit" and m.goal_kind != "seat":
                    self._go_seat(m)
            elif m.status == "waiting":
                if at_rest and m.goal_kind == "wait_here":
                    if m.mode == "alert":
                        m.timer -= 1
                        if m.timer <= 0:
                            self._go_wait(m)
                elif at_rest and m.mode != "alert" and m.goal_kind != "wait":
                    self._go_wait(m)
            elif m.mode in ("sit", "alert") or (at_rest and m.goal_kind in ("seat", "wait", "wait_here")):
                m.mode, m.timer, m.goal, m.goal_kind, m.path, m.action = "pause", 1, None, None, [], None
            if m.status == "working" and m.next is None and m.mode in ("pause", "walk") and m.tool != m.tool_seen:
                m.tool_seen = m.tool
                self._go_station(m)

        if m.mode == "pause":
            m.timer -= 1
            if m.timer <= 0:
                self._plan(m)
        elif m.mode == "walk":
            self._walk(m)

    def _walk(self, m: Mover) -> None:
        if m.next is None:
            if not m.path:
                self._arrive(m)
                return
            nxt = m.path[0]
            if nxt in self._taken_now(m):
                m.wait += 1
                if m.wait > 4:  # jammed: route around whoever is in the way
                    goal = m.goal
                    if goal is None or not self._set_route(m, goal, m.goal_face, m.goal_action, m.goal_kind or "random", include_moving=True):
                        self._sidestep(m)
                return
            m.wait = 0
            m.next = m.path.pop(0)
            dx, dy = m.next[0] - m.cell[0], m.next[1] - m.cell[1]
            m.facing = "right" if dx > 0 else "left" if dx < 0 else "down" if dy > 0 else "up"
        tx, ty = _center(m.next)
        m.ax += max(-STEP, min(STEP, tx - m.ax))
        m.ay += max(-STEP, min(STEP, ty - m.ay))
        m.stride += 1
        if (m.ax, m.ay) == (tx, ty):
            m.cell, m.next = m.next, None

    def _sidestep(self, m: Mover) -> None:
        """Jammed with no way round: shuffle one cell sideways so the other chef can get through."""
        taken = self._taken_now(m)
        cx, cy = m.cell
        options = [
            n for n in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1))
            if 0 <= n[0] < self.cols and 0 <= n[1] < self.rows and n not in self.lay.walk_blocked and n not in taken
        ]
        m.wait = 0
        m.holding = False
        if options:
            step = self.rng.choice(options)
            m.path, m.goal, m.goal_kind, m.goal_face, m.goal_action, m.mode = [step], step, "sidestep", "down", None, "walk"
        else:
            m.mode, m.timer, m.path, m.goal = "pause", self.rng.randint(2, 5), [], None

    def _taken_now(self, me: Mover) -> Set[Cell]:
        cells: Set[Cell] = set()
        if me.kind == "sous":
            return cells
        for other in self._movers():
            if other is me:
                continue
            cells.add(other.cell)
            if other.next:
                cells.add(other.next)
        return cells

    # ---- drawing ----------------------------------------------------------------
    def _animate(self, px: Pixels, hour: int) -> None:
        lay, frame = self.lay, self.frame
        f = frame // 2
        # Sky in every window
        if 8 <= hour < 17:
            top, bottom, phase = "#7dcfff", "#a6e3ff", "day"
        elif 17 <= hour < 20 or 6 <= hour < 8:
            top, bottom, phase = "#bb9af7", "#ff9e64", "dusk"
        else:
            top, bottom, phase = "#16161e", "#24283b", "night"
        for n, (x0, y0, x1, y1) in enumerate(lay.windows):
            for y in range(y0, y1 + 1):
                for x in range(x0, x1 + 1):
                    if x in (x0 + 3, x0 + 4) or y == 7:
                        continue  # window bars
                    px[y][x] = top if y < 7 else bottom
            if phase == "night":
                for i, (dx, dy) in enumerate(((1, 1), (5, 2), (2, 5), (6, 6))):
                    if (frame // 6 + i + n) % 4:
                        _dot(px, x0 + dx, y0 + dy, "#e6e9f5")
                _dot(px, x1 - 1, y0 + 1, "#f2e6b8")
            elif phase == "day":
                _dot(px, x0 + (frame // 10 + n) % 4, y0 + 2, "#ffffff")
                _dot(px, x0 + (frame // 10 + n) % 4 + 1, y0 + 2, "#ffffff")
            else:
                _dot(px, x0 + 1, y1, "#ffd166")
        # Stove: flames lick the pot, oven glows, steam rises
        for i, (x, y) in enumerate(lay.flames):
            _dot(px, x, y, ORANGE if (f + i) % 2 else RED)
        if lay.oven:
            ox0, ox1, oy = lay.oven
            glow = (ORANGE, RED, "#ffb37a")
            for i, x in enumerate(range(ox0, ox1 + 1)):
                _dot(px, x, oy, glow[(f + i) % 3])
        if lay.steam[1] > lay.steam[0]:
            sx0, sx1, sy = lay.steam
            for i, x in enumerate(range(sx0, sx1 + 1, 2)):
                rise = (f + i * 2) % 4
                _dot(px, x + (rise % 2), sy - rise, STEAM)
        # TV static
        if lay.tv:
            x0, y0, x1, y1 = lay.tv
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    px[y][x] = TV_COLORS[((x - x0) // 2 + f // 2 + (y - y0) // 3) % len(TV_COLORS)]
        # Wall clock on the divider: real time
        cx0, cy0 = lay.clock
        now = dt.datetime.now()
        for label_a, length, color in (((now.hour % 12 + now.minute / 60) / 12, 2, INK), (now.minute / 60, 3, "#c0404f")):
            ang = label_a * 2 * math.pi
            for t in range(1, length + 1):
                _dot(px, cx0 + round(math.sin(ang) * t), cy0 - round(math.cos(ang) * t), color)
        _dot(px, cx0, cy0, INK)
        # Fish tank
        if lay.tank:
            tx0, ty0, tx1, ty1 = lay.tank
            span = max(2, tx1 - tx0 - 1)
            for i, (speed, color, dy) in enumerate(((2, ORANGE, 1), (3, "#ffd166", 3), (2, RED, 5))):
                fx = tx0 + (frame // speed + i * 5) % span
                fy = min(ty1, ty0 + dy)
                _dot(px, fx, fy, color)
                _dot(px, fx + 1, fy, color)
                _dot(px, fx - 1, fy - ((frame // 4 + i) % 2), color)
        # Reception bell and lamp
        ringing = any(c.status == "waiting" for c in self.chefs.values())
        if lay.bell:
            bx, by = lay.bell
            s = ((frame // 2) % 2) if ringing else 0
            _rect(px, bx - 2 + s, by - 2, bx + 2 + s, by, BRASS)
            _rect(px, bx - 1 + s, by - 3, bx + 1 + s, by - 3, BRASS)
            _dot(px, bx + s, by - 4, INK)
        if lay.lamp:
            lx, ly = lay.lamp
            color = (RED if (frame // 3) % 2 else "#ff9aa9") if ringing else "#5a3a48"
            _rect(px, lx, ly, lx + 2, ly + 1, color)

    @staticmethod
    def _palette(m: Mover) -> Dict[str, str]:
        skin, hair, cap = traits(m.sid)
        return dict(CHEF_COLORS, A=m.color, S=skin, H=hair, T=m.color if cap else CHEF_COLORS["W"])

    def _draw_table(self, px: Pixels, t: Table) -> None:
        x0, y0, x1, y1, kind, cloth = t
        if kind == "island":
            _rect(px, x0, y0, x1, y0 + 5, WOOD_TOP)
            _rect(px, x0, y0, x1, y0, WOOD_HI)
            _rect(px, x0, y0 + 6, x1, y1, WOOD)
            for x in range(x0 + 4, x1, 6):
                _rect(px, x, y0 + 7, x, y1, WOOD_DK)
            _shade(px, x0 + 1, x1 + 1, y1 + 1, 0.5)
            mid = (x0 + x1) // 2
            _rect(px, mid - 1, y0 + 1, mid + 1, y0 + 2, "#f4f6ff")  # a bowl
            _dot(px, mid, y0 + 1, ORANGE)
            return
        light = "#f4f6ff"
        _rect(px, x0, y0, x1, y0, WOOD_DK)
        for y in range(y0 + 1, y0 + 9):
            for x in range(x0, x1 + 1):
                px[y][x] = cloth if ((x - x0) // 2 + (y - y0 - 1) // 2) % 2 == 0 else light
        skirt = render.blend(cloth, INK, 0.7)
        _rect(px, x0, y0 + 9, x1, y1 - 1, skirt)
        _shade(px, x0 + 1, x1 + 1, y1, 0.5)
        mid = (x0 + x1) // 2
        _rect(px, mid, y0 - 2, mid, y0 + 2, "#4b8f4b")  # little vase of flowers
        _dot(px, mid - 1, y0 - 3, RED)
        _dot(px, mid + 1, y0 - 3, BRASS)

    def _draw_table_setting(self, px: Pixels, m: Mover) -> None:
        x, y = m.ax, m.ay + 2
        _dot(px, x - 2, y, CHEF_COLORS["W"])        # sleeves
        _dot(px, x + 2, y, CHEF_COLORS["W"])
        _dot(px, x - 2, y + 1, CHEF_COLORS["S"])    # hands
        _dot(px, x + 2, y + 1, CHEF_COLORS["S"])
        _rect(px, x, y + 1, x + 1, y + 2, "#f4f6ff")  # coffee cup
        _dot(px, x, y + 1, CHEF_COLORS["B"])
        _dot(px, x + (self.frame // 3) % 2, y - (self.frame // 3) % 2, STEAM)

    def _draw_seated(self, px: Pixels, m: Mover) -> None:
        palette = self._palette(m)
        asleep = self._asleep(m)
        drop = 1 if asleep else 0
        skin = palette["S"]
        if m.facing == "down":  # behind the table, facing us
            _blit(px, STAND["down"][:SIT_ROWS], m.ax - 2, m.ay - SIT_ROWS + 1 + drop, palette)
            if asleep or (self.frame // 4) % 9 == 0:  # eyes shut
                _dot(px, m.ax - 1, m.ay - 3 + drop, skin)
                _dot(px, m.ax + 1, m.ay - 3 + drop, skin)
        else:  # in front of the table, back to us
            _blit(px, STAND["up"][:SIT_ROWS], m.ax - 2, m.ay - SIT_ROWS - 1 + drop, palette)
        if asleep:
            for i in range(2):
                _blit(px, ZEE, m.ax + 2 + i * 3, self._head_top(m) - 3 - ((self.frame // 5 + i * 2) % 4), {"C": STEAM})

    def _draw_standing(self, px: Pixels, m: Mover) -> None:
        _shade(px, m.ax - 2, m.ax + 2, m.ay + 1, 0.6)
        walking = m.mode == "walk"
        face = m.facing if m.facing != "left" else "right"
        flip = m.facing == "left"
        bob = 1 if walking and m.stride % 2 else 0
        x0 = m.ax - 2
        palette = self._palette(m)
        _blit(px, STAND[face], x0, m.ay - 7 - bob, palette, flip)
        _blit(px, (LEGS[m.stride % 2 if walking else 0],), x0, m.ay, palette, flip)

        f = self.frame // 2
        if m.mode == "pause" and m.action == "stir":
            _rect(px, m.ax + 3, m.ay - 6 - (f % 2), m.ax + 3, m.ay - 3, CHEF_COLORS["B"])
            _dot(px, m.ax + 3 + (f % 2), m.ay - 7 - (f % 2), STEAM)
        elif m.mode == "pause" and m.action == "chop":
            y = m.ay - 6 + (f % 2) * 2
            _dot(px, m.ax + 3, y, INK)
            _rect(px, m.ax + 3, y + 1, m.ax + 3, y + 2, CHEF_COLORS["G"])
        elif m.mode == "pause" and m.action == "peek":  # rummaging in the fridge: cold mist
            _dot(px, m.ax - 1 + (f % 2) * 2, m.ay - 9 - (f % 2), "#dff6ff")
            _dot(px, m.ax + 2 - (f % 2) * 2, m.ay - 10, "#dff6ff")
        elif m.mode == "pause" and m.action == "wash":  # water drops
            _dot(px, m.ax + 3, m.ay - 5 + (f % 3), "#7dcfff")
            _dot(px, m.ax - 3, m.ay - 4 + ((f + 1) % 3), "#7dcfff")
        elif m.mode == "pause" and m.action == "knead":  # working the dough with both hands
            _dot(px, m.ax - 3, m.ay - 3 - (f % 2), CHEF_COLORS["S"])
            _dot(px, m.ax + 3, m.ay - 3 - ((f + 1) % 2), CHEF_COLORS["S"])
            _dot(px, m.ax + (f % 3) - 1, m.ay - 2, "#f2d79b")
        elif m.mode == "alert":  # wave for attention
            up = f % 2
            _rect(px, m.ax + 3, m.ay - 5 - up * 2, m.ax + 3, m.ay - 3, CHEF_COLORS["W"])
            _dot(px, m.ax + 3, m.ay - 6 - up * 2, CHEF_COLORS["S"])

    def _draw_sous(self, px: Pixels, m: Mover) -> None:
        _shade(px, m.ax - 1, m.ax + 1, m.ay + 1, 0.6)
        bob = 1 if m.mode == "walk" and m.stride % 2 else 0
        _blit(px, SOUS, m.ax - 1, m.ay - 4 - bob, self._palette(m))

    def _draw_bar(self, px: Pixels, m: Mover) -> None:
        """Context-window meter above a chef's hat: green, then yellow, then red as it fills."""
        if not m.context:
            return
        pct = min(1.0, m.context / context_window(m.context))
        y = self._head_top(m) - 2
        _rect(px, m.ax - 2, y, m.ax + 2, y, BAR_BACK)
        filled = max(1, round(pct * 5))
        _rect(px, m.ax - 2, y, m.ax - 3 + filled, y, GREEN if pct < 0.6 else BRASS if pct < 0.85 else RED)

    def _draw_cat(self, px: Pixels, m: Mover) -> None:
        _shade(px, m.ax - 2, m.ax + 2, m.ay + 1, 0.6)
        rows = CAT_SIT if m.mode != "walk" else CAT_WALK[m.stride % 2]
        flip = m.facing == "left"
        _blit(px, rows, m.ax - 2, m.ay - 2, CAT_COLORS, flip)
        _dot(px, m.ax - 2 + (1 if flip else 3), m.ay - 1, INK)


def to_text(px: Pixels, labels: Sequence[Label], width: int, height: int) -> Text:
    """Two pixel rows per terminal line; identical neighbors merge into one run. Name tags overwrite cells."""
    by_line: Dict[int, List[Label]] = {}
    for label in labels:
        by_line.setdefault(label[0], []).append(label)
    styles: Dict[Tuple[str, str], Style] = {}
    out = Text(no_wrap=True, overflow="crop")
    lines = height // 2
    for line in range(lines):
        top_row, bottom_row = px[2 * line], px[2 * line + 1]
        cells = [("▀", top_row[x], bottom_row[x]) for x in range(width)]
        for _, col, text, color in by_line.get(line, ()):
            for i, ch in enumerate(text):
                if 0 <= col + i < width:
                    cells[col + i] = (ch, color, LABEL_BG)
        x = 0
        while x < width:
            cell = cells[x]
            end = x + 1
            while end < width and cells[end] == cell:
                end += 1
            key = (cell[1], cell[2])
            style = styles.get(key)
            if style is None:
                style = styles[key] = Style(color=cell[1], bgcolor=cell[2])
            out.append(cell[0] * (end - x), style)
            x = end
        if line < lines - 1:
            out.append("\n")
    return out
