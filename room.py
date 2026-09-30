"""The kitchen tab: one shared pixel room where every session is a chef.

Pure eye candy. Working chefs are up and about (stove, cutting board, fridge, window);
chefs that need you stand at the order bell and wave; inactive chefs sit at the tables
with a coffee. The room is a 96x56 pixel canvas printed two pixel rows per terminal line
with ▀ (foreground = top pixel, background = bottom pixel), so it's plain Rich Text: no
images, no extra dependencies, works in any truecolor terminal.

Movement runs on a coarse 24x14 grid of 4px cells (BFS pathfinding, chefs wait for each other).
"""
from __future__ import annotations

import datetime as dt
import random
from collections import deque
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from rich.style import Style
from rich.text import Text

import render

W, H = 96, 56
CELL = 4
COLS, ROWS = W // CELL, H // CELL
Cell = Tuple[int, int]

# ---- colors --------------------------------------------------------------------------
WALL, WALL_LOW, TRIM = "#3d4468", "#343a5c", "#565f89"
FLOOR_A, FLOOR_B, FLOOR_SEAM = "#7a6250", "#705a49", "#5e4a3c"
WOOD_TOP, WOOD_HI, WOOD, WOOD_DK = "#c49a5e", "#e0af68", "#8b5a2b", "#6b4423"
STEEL, STEEL_HI, STEEL_DK, IRON = "#a9b1d6", "#c0caf5", "#787c99", "#565f89"
INK = "#1a1b26"
RED, ORANGE, BRASS, GREEN, MINT = "#f7768e", "#ff9e64", "#e0af68", "#9ece6a", "#73daca"
STEAM = "#dfe6ff"
CHAIR_BACK, CHAIR_SEAT = "#8a4b5a", "#a85f70"
LABEL_BG = "#1a1b26"

CHEF_COLORS = {
    "W": "#f4f6ff", "w": "#aab2d8", "S": "#f5c7a0", "k": INK, "H": "#4a3b32",
    "B": "#8b5a2b", "G": "#9aa5ce", "R": RED,
}
CAT_COLORS = {"o": "#ff9e64", "k": INK}

# ---- sprites: "." = transparent, "A" = the session's own color -----------------------
_HAT = (
    "...kkkk...",
    "..kWWWWk..",
    ".kWWWWWWk.",
    ".kWWWWWWk.",
    ".kwwwwwwk.",
)
FRONT = _HAT + (
    ".kSSSSSSk.",
    ".kSkSSkSk.",
    ".kSSSSSSk.",
    "..kkSSkk..",
    ".kWAAAAWk.",
    "kWWAAAAWWk",
    "kSWAAAAWSk",
    ".kWAAAAWk.",
)
BACK = _HAT + (
    ".kHHHHHHk.",
    ".kHHHHHHk.",
    ".kHHHHHHk.",
    "..kkHHkk..",
    ".kWWAAWWk.",
    "kWWWAAWWWk",
    "kSWWAAWWSk",
    ".kWWAAWWk.",
)
SIDE = _HAT + (
    ".kSSSSSSk.",
    ".kSSSSkSSk",
    ".kSSSSSSk.",
    "..kkSSkk..",
    ".kWAAAWk..",
    ".kWAAAWSk.",
    ".kWAAAWk..",
    ".kWAAAWk..",
)
SPRITES = {"down": FRONT, "up": BACK, "right": SIDE}
SIT_FRONT = FRONT[:12]  # torso only: the table hides the rest
LEGS = (("..kwk.kwk.", "..kkk.kkk."), ("...kwwk...", "...kkkk..."))

BUBBLE = (
    ".kkkkkkk.",
    "kWWWRWWWk",
    "kWWWRWWWk",
    "kWWWRWWWk",
    "kWWWWWWWk",
    "kWWWRWWWk",
    ".kkkkkkk.",
    "...kWk...",
    "....k....",
)

CAT_WALK = (
    ("....o.o", "oo.oooo", ".ooooo.", ".o.o.o."),
    ("....o.o", ".o.oooo", ".ooooo.", "..o.o.o"),
)
CAT_SIT = ("....o.o", "o..oooo", "oo.oooo", ".ooooo.")

# ---- room layout ---------------------------------------------------------------------
WINDOW = (47, 3, 58, 12)  # frame x0, y0, x1, y1
TABLES = ((8, 55, "#c0626b"), (64, 87, "#5a8fb0"))  # x0, x1, cloth color
TABLE_TOP, TABLE_BASE = 32, 43
NORTH_SEAT_X = (3, 6, 9, 12, 17, 20)  # cell columns; chefs sit behind the table (cell row 7)
SOUTH_SEAT_X = (3, 6, 9, 12, 17, 20)  # ...or in front of it (cell row 12)
SEATS: List[Cell] = [(cx, 7) for cx in NORTH_SEAT_X] + [(cx, 12) for cx in SOUTH_SEAT_X]
SEAT_SET: Set[Cell] = set(SEATS)
WAIT_SPOTS: List[Cell] = [(21, 5), (23, 5), (20, 6), (22, 6), (19, 6), (18, 5)]
DOOR: Cell = (22, 13)

# (cell, facing, action): where a working chef likes to stand.
POIS: List[Tuple[Cell, str, Optional[str]]] = [
    ((17, 5), "up", "stir"),      # stove
    ((7, 5), "up", "chop"),       # cutting board
    ((2, 5), "up", None),         # fridge
    ((12, 4), "up", None),        # window
    ((14, 4), "up", None),        # window
    ((10, 6), "down", None),
    ((13, 6), "down", None),
    ((0, 9), "right", None),
    ((14, 9), "down", None),      # aisle between the tables
    ((14, 12), "down", None),
    ((9, 13), "down", None),
]
CAT_SPOTS: List[Cell] = [(6, 13), (9, 13), (12, 13), (15, 13), (14, 9), (14, 11)]


def _blocked_cells() -> Set[Cell]:
    blocked: Set[Cell] = {(cx, cy) for cx in range(COLS) for cy in range(4)}  # back wall
    blocked |= {(cx, 4) for cx in range(0, 11)}            # fridge + counter
    blocked |= {(cx, 4) for cx in range(15, 24)}           # stove + order counter
    blocked |= {(cx, cy) for cx in range(2, 14) for cy in (8, 9, 10)}   # table 1
    blocked |= {(cx, cy) for cx in range(16, 22) for cy in (8, 9, 10)}  # table 2
    blocked |= {(cx, cy) for cx in (0, 1) for cy in (11, 12, 13)}       # plant
    return blocked


BLOCKED = _blocked_cells()
# Chairs are only enterable as a destination; nobody strolls through them.
WALK_BLOCKED = BLOCKED | SEAT_SET
FREE_CELLS: List[Cell] = [(cx, cy) for cx in range(COLS) for cy in range(ROWS) if (cx, cy) not in WALK_BLOCKED]


def _center(cell: Cell) -> Tuple[int, int]:
    """Where a chef's feet go when standing in `cell` (bottom-center pixel)."""
    return cell[0] * CELL + CELL // 2, cell[1] * CELL + CELL - 1


# ---- pixel helpers -------------------------------------------------------------------
Pixels = List[List[str]]


def _rect(px: Pixels, x0: int, y0: int, x1: int, y1: int, color: str) -> None:
    for y in range(max(0, y0), min(H - 1, y1) + 1):
        row = px[y]
        for x in range(max(0, x0), min(W - 1, x1) + 1):
            row[x] = color


def _dot(px: Pixels, x: int, y: int, color: str) -> None:
    if 0 <= x < W and 0 <= y < H:
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


def _shade(px: Pixels, x0: int, x1: int, y: int, amount: float = 0.55) -> None:
    if 0 <= y < H:
        for x in range(max(0, x0), min(W - 1, x1) + 1):
            px[y][x] = render.blend(px[y][x], INK, amount)


def _build_background() -> Pixels:
    px: Pixels = [[FLOOR_A] * W for _ in range(H)]
    for y in range(16, H):  # wood floor planks, 4px tall, staggered seams
        plank = (y - 16) // 4
        base = FLOOR_A if plank % 2 == 0 else FLOOR_B
        for x in range(W):
            px[y][x] = FLOOR_SEAM if (x + plank * 11) % 24 == 0 else base
    _rect(px, 0, 0, W - 1, 14, WALL)
    _rect(px, 0, 9, W - 1, 14, WALL_LOW)
    _rect(px, 0, 0, W - 1, 0, TRIM)
    _rect(px, 0, 15, W - 1, 15, TRIM)  # baseboard

    # Fridge
    _rect(px, 2, 1, 13, 19, STEEL)
    _rect(px, 2, 1, 13, 1, STEEL_HI)
    _rect(px, 2, 9, 13, 9, IRON)
    _rect(px, 11, 4, 11, 7, INK)
    _rect(px, 11, 11, 11, 16, INK)
    _rect(px, 2, 19, 13, 19, STEEL_DK)

    # Shelf with jars, over the counter
    _rect(px, 18, 6, 41, 6, WOOD_DK)
    for x, c in ((20, RED), (25, ORANGE), (30, GREEN), (35, BRASS)):
        _rect(px, x, 3, x + 3, 5, c)
        _rect(px, x, 2, x + 3, 2, STEEL_HI)

    # Counter with a cutting board
    _rect(px, 16, 10, 43, 11, WOOD_TOP)
    _rect(px, 16, 10, 43, 10, WOOD_HI)
    _rect(px, 16, 12, 43, 19, WOOD)
    _rect(px, 16, 13, 43, 13, WOOD_DK)
    for x in (22, 28, 34, 40):
        _rect(px, x, 14, x, 19, WOOD_DK)
    for x in (19, 25, 31, 37, 42):
        _dot(px, x, 15, BRASS)
    _rect(px, 26, 10, 37, 11, "#f2d79b")
    for x, c in ((28, RED), (31, GREEN), (34, GREEN), (36, ORANGE)):
        _rect(px, x, 9, x + 1, 10, c)

    # Window (sky is painted per frame, see _paint_sky)
    x0, y0, x1, y1 = WINDOW
    _rect(px, x0, y0, x1, y1, STEEL_HI)
    _rect(px, x0 - 1, y1 + 1, x1 + 1, y1 + 1, STEEL)
    _rect(px, 52, y0 + 1, 53, y1 - 1, STEEL_HI)
    _rect(px, x0 + 1, 8, x1 - 1, 8, STEEL_HI)

    # Pan rail, stove, pot
    _rect(px, 60, 2, 79, 2, STEEL_DK)
    for x in (60, 77):
        _rect(px, x, 3, x + 2, 7, IRON)
        _rect(px, x, 3, x + 2, 3, STEEL_DK)
    _rect(px, 62, 9, 77, 10, STEEL_DK)
    _rect(px, 62, 11, 77, 19, IRON)
    for x in (65, 69, 73):
        _dot(px, x, 12, BRASS)
    _rect(px, 65, 13, 74, 13, STEEL)
    _rect(px, 65, 14, 74, 18, INK)
    _rect(px, 64, 4, 75, 9, "#9aa5ce")
    _rect(px, 74, 5, 75, 9, STEEL_DK)
    _rect(px, 63, 4, 76, 4, STEEL_HI)
    _rect(px, 65, 4, 74, 4, ORANGE)
    _rect(px, 62, 6, 62, 7, INK)
    _rect(px, 77, 6, 77, 7, INK)

    # Order counter (the bell is animated per frame)
    _rect(px, 80, 10, 95, 11, WOOD_TOP)
    _rect(px, 80, 10, 95, 10, WOOD_HI)
    _rect(px, 80, 12, 95, 19, WOOD)
    _rect(px, 80, 13, 95, 13, WOOD_DK)
    for x in (84, 88, 92):
        _rect(px, x, 14, x, 19, WOOD_DK)

    # Chairs behind the tables (backrest + seat) and stools in front
    for cx in NORTH_SEAT_X:
        sx = cx * CELL + 2
        _rect(px, sx - 6, 19, sx + 5, 27, CHAIR_BACK)
        _rect(px, sx - 6, 19, sx + 5, 19, CHAIR_SEAT)
        _rect(px, sx - 6, 28, sx + 5, 31, CHAIR_SEAT)
    for cx in SOUTH_SEAT_X:
        sx = cx * CELL + 2
        _rect(px, sx - 5, 49, sx + 4, 51, CHAIR_SEAT)
        _rect(px, sx - 5, 49, sx + 4, 49, CHAIR_BACK)

    # Plant, runner rug, door mat
    _rect(px, 1, 52, 6, 55, "#c0684a")
    _rect(px, 3, 45, 4, 51, "#4b8f4b")
    _rect(px, 1, 40, 6, 46, GREEN)
    _rect(px, 0, 42, 7, 44, GREEN)
    for x, y in ((2, 41), (5, 43), (1, 45), (4, 40)):
        _dot(px, x, y, MINT)
    _rect(px, 20, 53, 76, 55, "#a54a5a")
    for x in range(20, 77, 3):
        _dot(px, x, 53, BRASS)
        _dot(px, x, 55, BRASS)
    _rect(px, 84, 52, 95, 55, WOOD)
    _rect(px, 84, 52, 95, 52, WOOD_TOP)
    return px


BACKGROUND = _build_background()

SKY_PIXELS: List[Tuple[int, int]] = [
    (x, y)
    for y in range(WINDOW[1] + 1, WINDOW[3])
    for x in range(WINDOW[0] + 1, WINDOW[2])
    if x not in (52, 53) and y != 8
]
STARS = [(49, 5), (51, 6), (55, 5), (56, 10), (50, 10), (55, 9), (49, 7)]


def _paint_sky(px: Pixels, hour: int, frame: int) -> None:
    if 8 <= hour < 17:
        top, bottom, phase = "#7dcfff", "#a6e3ff", "day"
    elif 17 <= hour < 20 or 6 <= hour < 8:
        top, bottom, phase = "#bb9af7", "#ff9e64", "dusk"
    else:
        top, bottom, phase = "#16161e", "#24283b", "night"
    for x, y in SKY_PIXELS:
        px[y][x] = top if y < 8 else bottom
    if phase == "night":
        for i, (x, y) in enumerate(STARS):
            if (frame // 6 + i) % 4:
                _dot(px, x, y, "#e6e9f5")
        for x, y in ((55, 5), (56, 5), (55, 6)):
            _dot(px, x, y, "#f2e6b8")
    elif phase == "day":
        cloud = (frame // 10) % 4
        for dx in range(4):
            _dot(px, 48 + cloud + dx, 6, "#ffffff")
    else:
        _rect(px, 49, 10, 51, 10, "#ffd166")


def _flicker(px: Pixels, frame: int) -> None:
    f = frame // 2
    for i, x in enumerate((63, 76)):
        _dot(px, x, 9, ORANGE if (f + i) % 2 else RED)
        _dot(px, x, 10, RED if (f + i) % 2 else ORANGE)
    glow = (ORANGE, RED, "#ffb37a")
    for i, x in enumerate(range(66, 74)):
        _dot(px, x, 17, glow[(f + i) % 3])
        _dot(px, x, 18, glow[(f + i + 1) % 3])
    for i, x in enumerate((67, 70, 73)):  # steam off the pot
        rise = (f + i * 2) % 5
        _dot(px, x + (rise % 2), 3 - min(rise, 3), STEAM)


def _bell(px: Pixels, frame: int, ringing: bool) -> None:
    s = ((frame // 2) % 2) if ringing else 0
    _rect(px, 85, 10, 92, 10, STEEL_DK)
    _rect(px, 86 + s, 7, 91 + s, 9, BRASS)
    _rect(px, 87 + s, 6, 90 + s, 6, BRASS)
    _dot(px, 88 + s, 5, INK)
    _dot(px, 89 + s, 5, INK)
    _dot(px, 87 + s, 8, "#f2d79b")
    lamp = (RED if (frame // 3) % 2 else "#ff9aa9") if ringing else "#5a3a48"
    _rect(px, 87, 2, 90, 3, lamp)


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
        self.goal_kind: Optional[str] = None  # poi | random | seat | wait
        self.action: Optional[str] = None
        self.stride = 0
        # chef only
        self.sid = ""
        self.label = ""
        self.color = "#7aa2f7"
        self.status = "working"

    @property
    def stationary(self) -> bool:
        return self.mode != "walk"


def _neighbors(cell: Cell) -> Iterable[Cell]:
    cx, cy = cell
    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
        if 0 <= nx < COLS and 0 <= ny < ROWS:
            yield nx, ny


def find_path(start: Cell, goal: Cell, blocked: Set[Cell]) -> Optional[List[Cell]]:
    """Shortest 4-neighbor path over free cells; excludes `start`, includes `goal`."""
    if start == goal:
        return []
    prev: Dict[Cell, Cell] = {start: start}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for nxt in _neighbors(cur):
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
    def __init__(self, seed: Optional[int] = None) -> None:
        self.rng = random.Random(seed)
        self.chefs: Dict[str, Mover] = {}
        self.cat = Mover("cat", (12, 13), self.rng)
        self.cat.facing = "right"
        self.frame = 0
        self.show_names = True

    # ---- public -----------------------------------------------------------------
    def sync(self, entries: Sequence[Tuple[str, str, str]]) -> None:
        """entries = (session_id, label, status) for every session; status = working|waiting|idle."""
        seen = set()
        for sid, label, status in entries:
            seen.add(sid)
            chef = self.chefs.get(sid)
            if chef is None:
                chef = Mover("chef", self._spawn_cell(), self.rng)
                chef.sid = sid
                chef.color = render.session_color(sid)
                self.chefs[sid] = chef
            chef.label = label
            chef.status = status
        for sid in list(self.chefs):
            if sid not in seen:
                del self.chefs[sid]

    def step(self) -> None:
        self.frame += 1
        for mover in self._movers():
            self._tick(mover)

    def counts(self) -> Dict[str, int]:
        out = {"working": 0, "waiting": 0, "idle": 0}
        for chef in self.chefs.values():
            out[chef.status] += 1
        return out

    def draw(self, hour: Optional[int] = None) -> Pixels:
        hour = dt.datetime.now().hour if hour is None else hour
        px = [row[:] for row in BACKGROUND]
        _paint_sky(px, hour, self.frame)
        _flicker(px, self.frame)
        _bell(px, self.frame, ringing=any(c.status == "waiting" for c in self.chefs.values()))

        items: List[Tuple[float, int, Callable[[], None]]] = [(TABLE_BASE, 0, lambda: self._draw_tables(px))]
        for i, m in enumerate(self._movers(), start=1):
            if m.kind == "cat":
                items.append((m.ay, i, lambda m=m: self._draw_cat(px, m)))
            elif m.mode == "sit":
                items.append((m.ay, i, lambda m=m: self._draw_seated(px, m)))
                if m.facing == "down":  # arms and cup rest on the table, in front of the table's back edge
                    items.append((TABLE_BASE + 0.5, i, lambda m=m: self._draw_table_setting(px, m)))
            else:
                items.append((m.ay, i, lambda m=m: self._draw_standing(px, m)))
        for _, _, fn in sorted(items, key=lambda it: (it[0], it[1])):
            fn()
        for m in self.chefs.values():  # bubbles sit on top of everything
            if m.mode == "alert":
                _blit(px, BUBBLE, m.ax - 4, m.ay - 23, {"k": INK, "W": "#ffffff", "R": RED if (self.frame // 3) % 2 else ORANGE})
        return px

    def labels(self) -> List[Label]:
        if not self.show_names:
            return []
        out: List[Label] = []
        for m in sorted(self.chefs.values(), key=lambda c: c.ay):
            text = m.label.strip()
            text = text if len(text) <= 11 else text[:10] + "…"
            if not text:
                continue
            col = max(0, min(W - len(text), m.ax - len(text) // 2))
            out.append((min(H // 2 - 1, m.ay // 2 + 1), col, text, m.color))
        return out

    def render(self, hour: Optional[int] = None) -> Text:
        return to_text(self.draw(hour), self.labels())

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
            ("THE KITCHEN   ", f"bold {render.PALETTE[2]}"),
            (f"{c['working']} cooking", render.STATUS["working"][0]), ("  ·  ", render.MUTED),
            (f"{c['waiting']} order up", render.STATUS["waiting"][0]), ("  ·  ", render.MUTED),
            (f"{c['idle']} chilling", render.MUTED),
        )

    # ---- internals --------------------------------------------------------------
    def _movers(self) -> List[Mover]:
        return [self.cat, *self.chefs.values()]

    def _spawn_cell(self) -> Cell:
        taken = {m.cell for m in self._movers()} | {m.next for m in self._movers() if m.next}
        queue, seen = deque([DOOR]), {DOOR}
        while queue:
            cell = queue.popleft()
            if cell not in taken and cell not in WALK_BLOCKED:
                return cell
            for nxt in _neighbors(cell):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return DOOR

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
        blocked = set(WALK_BLOCKED)
        for other in self._movers():
            if other is me:
                continue
            if include_moving or other.stationary:
                blocked.add(other.cell)
            if include_moving and other.next:
                blocked.add(other.next)
        return blocked

    def _set_route(self, m: Mover, goal: Cell, face: str, action: Optional[str], kind: str, include_moving: bool = False) -> bool:
        path = find_path(m.cell, goal, self._obstacles(m, include_moving))
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
        taken = self._taken(m)
        if m.kind == "cat":
            spots = [c for c in CAT_SPOTS if c not in taken and c != m.cell]
            pool = spots if spots and self.rng.random() < 0.6 else [c for c in FREE_CELLS if c not in taken and c != m.cell]
            if pool and self._set_route(m, self.rng.choice(pool), m.facing, None, "random"):
                return
        else:
            pois = [p for p in POIS if p[0] not in taken and p[0] != m.cell]
            if pois and self.rng.random() < 0.75:
                cell, face, action = self.rng.choice(pois)
                if self._set_route(m, cell, face, action, "poi"):
                    return
            pool = [c for c in FREE_CELLS if c not in taken and c != m.cell]
            if pool and self._set_route(m, self.rng.choice(pool), "down", None, "random"):
                return
        m.timer = 4

    def _go_seat(self, m: Mover) -> None:
        taken = self._taken(m)
        for seat in SEATS:
            if seat == m.cell or seat not in taken:
                face = "down" if seat[1] < 10 else "up"
                if self._set_route(m, seat, face, None, "seat"):
                    return
        m.mode, m.timer, m.path, m.goal, m.goal_kind = "pause", 10, [], None, "seat"  # chairs taken or path jammed; retry soon

    def _go_wait(self, m: Mover) -> None:
        taken = self._taken(m)
        for spot in WAIT_SPOTS:
            if spot == m.cell or spot not in taken:
                if self._set_route(m, spot, "down", None, "wait"):
                    return
        # No route to the bell right now: wave from here and try again in a few seconds.
        m.mode, m.facing, m.path, m.goal, m.goal_kind, m.timer = "alert", "down", [], None, "wait_here", 25

    def _arrive(self, m: Mover) -> None:
        if m.goal_kind == "seat":
            m.mode, m.facing, m.action, m.goal = "sit", m.goal_face, None, None
            return
        if m.goal_kind == "wait":
            m.mode, m.facing, m.action, m.goal = "alert", "down", None, None
            return
        m.mode = "pause"
        m.timer = self.rng.randint(25, 70) if m.kind == "cat" else self.rng.randint(14, 34)
        m.facing = m.goal_face if m.kind == "chef" else m.facing
        m.action = m.goal_action
        m.goal = None

    def _tick(self, m: Mover) -> None:
        if m.kind == "chef":
            at_rest = m.next is None
            if m.status == "idle":
                if at_rest and m.mode != "sit" and m.goal_kind != "seat":
                    self._go_seat(m)
            elif m.status == "waiting":
                if at_rest and m.goal_kind == "wait_here":
                    m.timer -= 1
                    if m.timer <= 0:
                        self._go_wait(m)
                elif at_rest and m.mode != "alert" and m.goal_kind != "wait":
                    self._go_wait(m)
            elif m.mode in ("sit", "alert") or (at_rest and m.goal_kind in ("seat", "wait", "wait_here")):
                m.mode, m.timer, m.goal, m.goal_kind, m.path, m.action = "pause", 1, None, None, [], None

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
                        m.mode, m.timer, m.path, m.goal = "pause", 4, [], None
                return
            m.wait = 0
            m.next = m.path.pop(0)
            dx, dy = m.next[0] - m.cell[0], m.next[1] - m.cell[1]
            m.facing = "right" if dx > 0 else "left" if dx < 0 else "down" if dy > 0 else "up"
        tx, ty = _center(m.next)
        m.ax += max(-2, min(2, tx - m.ax))
        m.ay += max(-2, min(2, ty - m.ay))
        m.stride += 1
        if (m.ax, m.ay) == (tx, ty):
            m.cell, m.next = m.next, None

    def _taken_now(self, me: Mover) -> Set[Cell]:
        cells: Set[Cell] = set()
        for other in self._movers():
            if other is me:
                continue
            cells.add(other.cell)
            if other.next:
                cells.add(other.next)
        return cells

    # ---- drawing ----------------------------------------------------------------
    @staticmethod
    def _palette(m: Mover) -> Dict[str, str]:
        return dict(CHEF_COLORS, A=m.color)

    def _draw_tables(self, px: Pixels) -> None:
        for x0, x1, cloth in TABLES:
            light = "#f4f6ff"
            _rect(px, x0, TABLE_TOP, x1, TABLE_TOP, WOOD_DK)  # back edge
            for y in range(TABLE_TOP + 1, TABLE_TOP + 7):
                for x in range(x0, x1 + 1):
                    px[y][x] = cloth if ((x - x0) // 3 + (y - TABLE_TOP - 1) // 3) % 2 == 0 else light
            skirt = render.blend(cloth, INK, 0.7)
            _rect(px, x0, TABLE_TOP + 7, x1, TABLE_BASE - 1, skirt)
            for x in range(x0, x1 + 1, 3):
                _dot(px, x, TABLE_BASE - 1, render.blend(cloth, INK, 0.5))
            _shade(px, x0 + 1, x1 + 1, TABLE_BASE, 0.45)
            mid = (x0 + x1) // 2
            _rect(px, mid, TABLE_TOP - 2, mid, TABLE_TOP + 2, "#4b8f4b")  # little vase of flowers
            _dot(px, mid - 1, TABLE_TOP - 3, RED)
            _dot(px, mid + 1, TABLE_TOP - 3, BRASS)
            _dot(px, mid, TABLE_TOP - 3, "#ff9ec5")
            _rect(px, mid - 1, TABLE_TOP + 3, mid + 1, TABLE_TOP + 3, STEEL_HI)

    def _draw_table_setting(self, px: Pixels, m: Mover) -> None:
        x = m.ax
        _rect(px, x - 5, 32, x - 4, 33, CHEF_COLORS["W"])   # sleeves
        _rect(px, x + 3, 32, x + 4, 33, CHEF_COLORS["W"])
        _rect(px, x - 5, 34, x - 4, 34, CHEF_COLORS["S"])   # hands
        _rect(px, x + 3, 34, x + 4, 34, CHEF_COLORS["S"])
        _rect(px, x - 1, 35, x + 1, 37, "#f4f6ff")           # coffee cup
        _rect(px, x - 1, 35, x + 1, 35, CHEF_COLORS["B"])
        _dot(px, x + 2, 36, "#f4f6ff")
        rise = (self.frame // 3) % 3
        _dot(px, x - 1 + (rise % 2), 34 - rise, STEAM)
        _dot(px, x + 1 - (rise % 2), 33 - rise, STEAM)

    def _draw_seated(self, px: Pixels, m: Mover) -> None:
        palette = self._palette(m)
        if m.facing == "down":  # behind the table, facing us
            _blit(px, SIT_FRONT, m.ax - 5, m.ay - 11, palette)
            if (self.frame // 4) % 9 == 0:  # blink
                _dot(px, m.ax - 2, m.ay - 5, CHEF_COLORS["S"])
                _dot(px, m.ax + 1, m.ay - 5, CHEF_COLORS["S"])
        else:  # in front of the table, back to us
            _blit(px, BACK, m.ax - 5, m.ay - 12, palette)

    def _draw_standing(self, px: Pixels, m: Mover) -> None:
        _shade(px, m.ax - 3, m.ax + 2, m.ay + 1, 0.55)
        walking = m.mode == "walk"
        face = m.facing if m.facing != "left" else "right"
        flip = m.facing == "left"
        bob = 1 if walking and m.stride % 2 else 0
        x0 = m.ax - 5
        palette = self._palette(m)
        _blit(px, SPRITES[face], x0, m.ay - 14 - bob, palette, flip)
        _blit(px, LEGS[m.stride % 2 if walking else 0], x0, m.ay - 1, palette, flip)

        f = self.frame // 2
        if m.mode == "pause" and m.action == "stir":
            sx = m.ax + 4 + (f % 2)
            _rect(px, sx, m.ay - 17, sx, m.ay - 6, CHEF_COLORS["B"])
        elif m.mode == "pause" and m.action == "chop":
            y = m.ay - 11 + (f % 2) * 3
            _dot(px, m.ax + 5, y - 1, INK)
            _rect(px, m.ax + 5, y, m.ax + 5, y + 2, CHEF_COLORS["G"])
        elif m.mode == "alert":  # wave for attention
            up = f % 2
            _rect(px, m.ax + 5, m.ay - 8 - up * 3, m.ax + 5, m.ay - 6, CHEF_COLORS["W"])
            _dot(px, m.ax + 5, m.ay - 9 - up * 3, CHEF_COLORS["S"])

    def _draw_cat(self, px: Pixels, m: Mover) -> None:
        _shade(px, m.ax - 3, m.ax + 2, m.ay + 1, 0.6)
        rows = CAT_SIT if m.mode != "walk" else CAT_WALK[m.stride % 2]
        flip = m.facing == "left"
        _blit(px, rows, m.ax - 3, m.ay - 3, CAT_COLORS, flip)
        _dot(px, m.ax - 3 + (1 if flip else 5), m.ay - 2, INK)


def to_text(px: Pixels, labels: Sequence[Label] = ()) -> Text:
    """Two pixel rows per terminal line; identical neighbors merge into one run. Name tags overwrite cells."""
    by_line: Dict[int, List[Label]] = {}
    for label in labels:
        by_line.setdefault(label[0], []).append(label)
    styles: Dict[Tuple[str, str], Style] = {}
    out = Text(no_wrap=True, overflow="crop")
    for line in range(H // 2):
        top_row, bottom_row = px[2 * line], px[2 * line + 1]
        cells = [("▀", top_row[x], bottom_row[x]) for x in range(W)]
        for _, col, text, color in by_line.get(line, ()):
            for i, ch in enumerate(text):
                if 0 <= col + i < W:
                    cells[col + i] = (ch, color, LABEL_BG)
        x = 0
        while x < W:
            cell = cells[x]
            end = x + 1
            while end < W and cells[end] == cell:
                end += 1
            key = (cell[1], cell[2])
            style = styles.get(key)
            if style is None:
                style = styles[key] = Style(color=cell[1], bgcolor=cell[2])
            out.append(cell[0] * (end - x), style)
            x = end
        if line < H // 2 - 1:
            out.append("\n")
    return out
