"""agentview: every Claude Code session from today, read-only, in one terminal window."""
from __future__ import annotations

import datetime as dt
import math
import os
import time
from pathlib import Path

# The whole point of this app is color; don't let a NO_COLOR inherited from the
# launching shell (Claude Code sets one) turn it grey.
os.environ.pop("NO_COLOR", None)
from typing import Dict, List, Optional

from rich.align import Align
from rich.console import Group
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import ContentSwitcher, Footer, Label, ListItem, ListView, RichLog, Static

import render
import room
from sessions import Event, Session, active_subagents, apply_registry, discover_today, open_session, read_new, read_registry

POLL_SECONDS = 2.0
KITCHEN_FRAME_SECONDS = 0.15
# Sidebar order: the sessions that need attention first.
STATUS_ORDER = {"working": 0, "waiting": 1, "idle": 2}


class SessionItem(ListItem):
    """Sidebar entry for one session: colored dot, name, and when it last did something."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.name_label = Label()
        self.meta_label = Label(classes="meta")
        super().__init__(Vertical(self.name_label, self.meta_label), id=f"item-{session.session_id}")
        self.refresh_label()

    def refresh_label(self, now: Optional[float] = None) -> None:
        status = self.session.status(now)
        color = render.display_color(self.session.session_id, status)
        status_color, status_text = render.STATUS[status]
        self.name_label.update(Text.assemble(render.status_dot(status), (self.session.label, f"bold {color}")))
        last = render.local_time(self.session.events[-1].timestamp) if self.session.events else "--:--"
        self.meta_label.update(
            Text.assemble(("  " + status_text, status_color), (f" · {len(self.session.events)} msgs · {last}", render.MUTED))
        )


class AgentView(App):
    TITLE = "agentview"
    CSS = """
    Screen { background: $background; }

    #topbar {
        height: 1;
        padding: 0 2;
        background: $panel;
        color: $text-muted;
    }

    #sidebar-wrap {
        width: 40;
        background: $surface;
        border-right: tall $panel;
    }
    .section {
        padding: 1 2 0 2;
        color: $text-muted;
        text-style: bold;
    }
    #sidebar, #sessions {
        height: auto;
        background: $surface;
    }
    #sessions { height: 1fr; }
    ListView > ListItem {
        padding: 0 2;
        background: $surface;
    }
    ListView > ListItem.-highlight {
        background: $panel;
    }
    ListView:focus > ListItem.-highlight {
        background: $primary 25%;
    }
    ListItem .meta { color: $text-muted; }
    ListItem Vertical { height: auto; }

    #main { width: 1fr; padding: 0 1; }
    #merged { padding: 1 2; }
    #grid { grid-gutter: 1 1; padding: 1 0 0 0; }
    #kitchen { padding: 1 2; }
    .pane {
        border: round $panel;
        border-title-style: bold;
        padding: 0 1;
    }
    RichLog {
        overflow-x: hidden;
        background: $background;
        scrollbar-size-vertical: 1;
        scrollbar-background: $background;
        scrollbar-color: $panel;
    }
    """
    BINDINGS = [
        Binding("m", "show('merged')", "Merged"),
        Binding("g", "show('grid')", "Grid"),
        Binding("k", "show('kitchen')", "Kitchen"),
        Binding("n", "toggle_names", "Names"),
        Binding("s", "toggle_sound", "Sound"),
        Binding("t", "next_chef", "Next chef"),
        Binding("v", "view_selected", "Transcript"),
        Binding("f", "toggle_freeze", "Freeze"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.theme = "tokyo-night"
        self.sessions: Dict[Path, Session] = {}
        self.panes: Dict[str, RichLog] = {}
        self.items: Dict[str, SessionItem] = {}
        self.focused_session: Optional[str] = None
        self.last_merged_session: Optional[str] = None
        self.frozen = False
        self.room = room.Room()  # resized to the window on first draw
        self.sound = True
        self.kitchen_info: Dict[str, dict] = {}
        self._waiting_polls: Dict[str, int] = {}
        self._polls = 0

    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with Horizontal():
            with Vertical(id="sidebar-wrap"):
                yield Label("AGENTVIEW", classes="section")
                yield ListView(
                    ListItem(Label("≡  All (merged)"), id="nav-merged"),
                    ListItem(Label("⊞  Grid (split)"), id="nav-grid"),
                    ListItem(Label("♨  Kitchen"), id="nav-kitchen"),
                    id="sidebar",
                )
                yield Label("SESSIONS TODAY", classes="section")
                yield ListView(id="sessions")
            with ContentSwitcher(initial="merged", id="main"):
                yield RichLog(id="merged", wrap=True, markup=False)
                yield Grid(id="grid")
                yield RichLog(id="single", wrap=True, markup=False, classes="pane")
                with VerticalScroll(id="kitchen"):
                    yield Static(id="kitchen-body")
        yield Footer()

    def on_mount(self) -> None:
        self.poll()
        if not self.sessions:
            self.query_one("#merged", RichLog).write(Text("No Claude Code sessions today yet. Waiting…", style=render.MUTED))
        self.set_interval(POLL_SECONDS, self.poll)
        self.set_interval(KITCHEN_FRAME_SECONDS, self.tick_kitchen)

    # ---- polling -------------------------------------------------------

    def poll(self) -> None:
        for path in discover_today():
            if path not in self.sessions:
                self._add_session(open_session(path))

        batch: List[Event] = []
        for session in self.sessions.values():
            old_label = session.label
            batch.extend(read_new(session))
            if session.label != old_label:
                self._relabel(session)

        batch.sort(key=lambda e: e.timestamp)
        merged = self.query_one("#merged", RichLog)
        single = self.query_one("#single", RichLog)
        by_id = {s.session_id: s for s in self.sessions.values()}
        for event in batch:
            if event.session_id != self.last_merged_session:
                if self.last_merged_session is not None:
                    merged.write(Text(""))
                merged.write(render.merged_header(event, by_id[event.session_id].label))
                self.last_merged_session = event.session_id
            merged.write(render.merged_line(event))
            self.panes[event.session_id].write(render.pane_line(event))
            if event.session_id == self.focused_session:
                single.write(render.pane_line(event))

        apply_registry(self.sessions.values(), read_registry())

        # Status can change with no new events (a session going quiet), so refresh every entry.
        stamp = time.time()
        counts = {"working": 0, "waiting": 0, "idle": 0}
        for session in self.sessions.values():
            status = session.status(stamp)
            counts[status] += 1
            self._note_status(session, status, stamp)
            self.items[session.session_id].refresh_label(stamp)
            self._set_pane_title(self.panes[session.session_id], session, status)
            if session.session_id == self.focused_session:
                self._set_pane_title(self.query_one("#single", RichLog), session, status)
        self._sort_sidebar(stamp)
        self._polls += 1
        self.render_kitchen()

        now = dt.datetime.now().strftime("%H:%M:%S")
        view = {"merged": "All sessions", "grid": "Grid", "single": "Session", "kitchen": "Kitchen"}[self.query_one("#main", ContentSwitcher).current]
        frozen = "   ❚❚ frozen" if self.frozen else ""
        self.query_one("#topbar", Static).update(
            Text.assemble(
                ("agentview", f"bold {render.PALETTE[0]}"),
                (f"   {view}   ", "bold"),
                render.status_dot("working"), (f"{counts['working']} working   ", render.STATUS["working"][0]),
                render.status_dot("waiting"), (f"{counts['waiting']} need you   ", render.STATUS["waiting"][0]),
                render.status_dot("idle"), (f"{counts['idle']} inactive", render.MUTED),
                (f"   · updated {now}", render.MUTED),
                (frozen, render.PALETTE[6]),
            )
        )

    @staticmethod
    def _set_pane_title(pane: RichLog, session: Session, status: str) -> None:
        color = render.display_color(session.session_id, status)
        pane.border_title = f"{session.label}"
        pane.border_subtitle = render.STATUS[status][1]
        pane.styles.border = ("round", color)
        pane.styles.border_title_color = color
        pane.styles.border_subtitle_color = render.STATUS[status][0]

    def _sort_sidebar(self, now: float) -> None:
        """Working, then needs you, then inactive; stable within each group."""
        listview = self.query_one("#sessions", ListView)
        current = [child for child in listview.children if isinstance(child, SessionItem)]
        wanted = sorted(current, key=lambda item: STATUS_ORDER[item.session.status(now)])
        if wanted == current:
            return
        highlighted = listview.highlighted_child
        for i, item in enumerate(wanted):
            if listview.children[i] is not item:
                listview.move_child(item, before=i)
        if highlighted in wanted:
            listview.index = wanted.index(highlighted)

    # ---- kitchen -------------------------------------------------------

    def tick_kitchen(self) -> None:
        self.render_kitchen(step=True)

    def _note_status(self, session: Session, status: str, now: float) -> None:
        """Collect what the kitchen shows about a session, and chime when one newly needs you."""
        try:
            idle = max(0.0, now - session.path.stat().st_mtime)
        except OSError:
            idle = 0.0
        self.kitchen_info[session.session_id] = {
            "tool": session.current_tool if status == "working" else None,
            "detail": session.current_detail,
            "context": session.context_tokens,
            "subs": active_subagents(session, now) if status == "working" else 0,
            "idle": idle,
        }
        sid = session.session_id
        polls = self._waiting_polls.get(sid, 0) + 1 if status == "waiting" else 0
        self._waiting_polls[sid] = polls
        if polls == 2 and self._polls > 2 and self.sound:  # waiting for two polls in a row = really needs you
            self._chime()

    def _chime(self) -> None:
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            self.bell()

    def render_kitchen(self, step: bool = False) -> None:
        if self.query_one("#main", ContentSwitcher).current != "kitchen":
            return
        body = self.query_one("#kitchen-body", Static)
        if not self.sessions:
            body.update(Text("The kitchen is closed: no Claude Code sessions today yet.", style=render.MUTED))
            return
        now = time.time()
        # The world grows and shrinks with the window; rebuild it when the fitting size changes.
        cols, rows = room.fit(body.size.width, self.query_one("#kitchen").size.height - 11)
        if (cols, rows) != (self.room.cols, self.room.rows):
            old = self.room
            self.room = room.Room(cols, rows)
            self.room.name_mode, self.room.selected = old.name_mode, old.selected
        self.room.sync([(s.session_id, s.label, s.status(now), self.kitchen_info.get(s.session_id)) for s in self.sessions.values()])
        if step:
            self.room.step()
        header = self.room.header()
        header.append(f"   ♪ sound {'on' if self.sound else 'off'}", style=render.MUTED)
        if body.size.width and body.size.width < room.MIN_COLS * room.CELL:
            header.append(f"   ⚠ window too narrow: needs {room.MIN_COLS * room.CELL} columns", style=render.PALETTE[6])
        body.update(Group(header, Text(""), Align.center(self.room.render()), Text(""), self.room.details(body.size.width), Text(""), self.room.legend()))

    def _add_session(self, session: Session) -> None:
        if not self.sessions:
            self.query_one("#merged", RichLog).clear()
        self.sessions[session.path] = session
        color = render.session_color(session.session_id)

        pane = RichLog(wrap=True, markup=False, classes="pane")
        pane.border_title = session.label
        pane.styles.border = ("round", color)
        pane.styles.border_title_color = color
        self.panes[session.session_id] = pane
        grid = self.query_one("#grid", Grid)
        grid.mount(pane)
        grid.styles.grid_size_columns = max(1, math.ceil(math.sqrt(len(self.panes))))

        item = SessionItem(session)
        self.items[session.session_id] = item
        self.query_one("#sessions", ListView).append(item)

    def _relabel(self, session: Session) -> None:
        self.panes[session.session_id].border_title = session.label
        if session.session_id == self.focused_session:
            self.query_one("#single", RichLog).border_title = session.label

    # ---- navigation ----------------------------------------------------

    def on_list_view_selected(self, message: ListView.Selected) -> None:
        item = message.item
        if item.id == "nav-merged":
            self.action_show("merged")
        elif item.id == "nav-grid":
            self.action_show("grid")
        elif item.id == "nav-kitchen":
            self.action_show("kitchen")
        elif isinstance(item, SessionItem):
            self._show_single(item.session)

    def _show_single(self, session: Session) -> None:
        self.focused_session = session.session_id
        color = render.display_color(session.session_id, session.status())
        single = self.query_one("#single", RichLog)
        single.clear()
        single.border_title = session.label
        single.styles.border = ("round", color)
        single.styles.border_title_color = color
        for event in session.events:
            single.write(render.pane_line(event))
        self.query_one("#main", ContentSwitcher).current = "single"
        self.poll()

    def action_show(self, view: str) -> None:
        self.focused_session = None
        self.query_one("#main", ContentSwitcher).current = view
        self.query_one("#sidebar", ListView).index = {"merged": 0, "grid": 1, "kitchen": 2}[view]
        self.poll()

    def action_toggle_names(self) -> None:
        self.room.cycle_names()
        self.render_kitchen()

    def action_toggle_sound(self) -> None:
        self.sound = not self.sound
        if self.sound:
            self._chime()
        self.render_kitchen()

    def action_next_chef(self) -> None:
        self.room.cycle_selection()
        self.render_kitchen()

    def action_view_selected(self) -> None:
        sid = self.room.selected
        if not sid:
            return
        for session in self.sessions.values():
            if session.session_id == sid:
                self._show_single(session)
                return

    def on_click(self, event: events.Click) -> None:
        """Clicking a chef in the kitchen selects it; clicking empty floor deselects."""
        if self.query_one("#main", ContentSwitcher).current != "kitchen":
            return
        region = self.query_one("#kitchen-body", Static).region
        if not region.contains(event.screen_x, event.screen_y):
            return
        x = event.screen_x - region.x - max(0, (region.width - self.room.W) // 2)
        y_line = event.screen_y - region.y - 2  # header + blank line sit above the room
        if 0 <= x < self.room.W and 0 <= y_line < self.room.H // 2:
            self.room.select(self.room.chef_at(x, y_line * 2 + 1))
            self.render_kitchen()

    def action_toggle_freeze(self) -> None:
        self.frozen = not self.frozen
        for log in [self.query_one("#merged", RichLog), self.query_one("#single", RichLog), *self.panes.values()]:
            log.auto_scroll = not self.frozen
        self.poll()


if __name__ == "__main__":
    AgentView().run()
