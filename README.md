# agentview

**Every Claude Code session you're running, in one terminal window, and a little pixel kitchen where every one of them is a chef wandering around.**

![The kitchen and the waiting room: one tiny chef per session, plus a cat](docs/kitchen-room.png)

<sub>Working chefs cook in the kitchen (left), going to the station for the tool they're using, with tiny sous-chefs for sub-agents. Chefs that need you wave from the reception bell in the waiting room (right); inactive chefs sit at its tables and nod off if left alone. Real-time clock, day/night windows, fish tank, TV, and a cat.</sub>

If you run several Claude Code agents in parallel, each one lives in its own terminal. agentview reads them all in one place without touching them. It tails the conversation logs Claude Code already writes to disk and shows your prompts, Claude's replies and a one-line summary of every tool call.

It is **read-only**: it never sends anything to your agents, never calls an API and never leaves your machine. You still reply in each agent's own terminal.

## Features

- **Merged feed:** every session in one scrolling feed, each tagged with its name in its own color.
- **Grid:** one pane per session, side by side.
- **Single session:** click a session in the sidebar to see it full-screen.
- **Kitchen:** two tiny Pokémon-style rooms, a kitchen and a waiting room, where each session is a chef in its own color with a name tag. Working chefs roam the kitchen, the rest wait next door. Pure eye candy.
- **Live status:** each session shows 🟢 working, ⚪ needs you or ⚫ inactive, and the sidebar is sorted so the sessions that need you are at the top.
- **Zero setup:** Python plus one dependency ([Textual](https://github.com/Textualize/textual)). No server, no background service, no config.
- **Mac, Linux and Windows.**

## Install

Requires Python 3.9+ and [Claude Code](https://claude.com/claude-code).

```bash
git clone https://github.com/jlee056/agentview.git
cd agentview
pip install -r requirements.txt
python agentview.py
```

It picks up every session from today automatically, including ones you start after it's open.

## Using it

Click in the left sidebar, or use the shortcuts:

| Key | View |
|---|---|
| `m` | All sessions, merged into one feed |
| `g` | Grid, one pane per session |
| `k` | Kitchen |
| `n` | Cycle the chef labels: names + activity, names only, none (kitchen) |
| `s` | Turn the "needs you" chime on or off |
| `t` | Select the next chef (or click one) |
| `v` | Open the selected chef's transcript |
| `f` | Freeze or unfreeze auto-scroll |
| `q` | Quit |

Clicking a session name shows that session alone.

### The kitchen

Purely for fun. The world is two small rooms joined by a doorway, and it grows and shrinks to fit your window (it needs at least ~84 columns; a warning shows if the window is narrower).

| Session status | Where the chef is and what it does |
|---|---|
| Working | In the **kitchen**, at the station for its current tool (see below) |
| Needs you | In the **waiting room**: waves from the reception bell under a red `!` bubble; the bell rings and its lamp flashes |
| Inactive (terminal closed) | In the **waiting room**: sits at a table with a coffee, and falls asleep (`z z`) after 10 minutes |

| Tool | Station |
|---|---|
| `Edit`, `Write` | Chops at the cutting board |
| `Bash` | Stirs the pot on the stove |
| `Read`, `Grep`, `Glob` | Rummages in the fridge |
| `WebSearch`, `WebFetch`, MCP tools | Looks out the window |
| `Skill`, `ToolSearch` | Washes at the sink |
| `Agent`, `Task` | Kneads dough at the island |

More details:

- **Sous-chefs:** every sub-agent that's active (log written in the last 30 s) is a tiny helper chef that follows its parent around, up to four.
- **Context bar:** a little bar over each chef's hat fills green, yellow, then red as the context window fills (200k assumed, 1M past that).
- **Labels:** each chef carries its session name and what it's doing ("editing", "idle 12m", "needs you!"). Press `n` to cycle or hide them.
- **Inspect:** click a chef, or press `t`, to see its tool, the file or command, context usage and idle time; `v` opens its transcript.
- **Chime:** a sound plays when a session has needed you for a couple of polls in a row. `s` turns it off.
- **Individuals:** each session gets its own skin tone, hair, and a white toque or a cap in its color.
- **The house:** a wall clock showing the real time, windows with day, dusk and night skies (the whole house is tinted at night), a stove that flickers, a TV, a fish tank (in wider windows), and a cat roaming both rooms.

The world is drawn with half-block characters (`▀`), two pixels per character cell, so it works in any modern terminal with truecolor support. Chefs walk on a 6-pixel tile grid using BFS pathfinding and sidestep each other in the doorway. No images, no extra dependencies.

## How it works

| Data | Where it comes from |
|---|---|
| Conversations | `~/.claude/projects/<project>/<session>.jsonl`, polled every 2 s. Each file is read by byte offset, so only new lines are parsed. |
| Status | `~/.claude/sessions/<pid>.json`, which Claude Code writes for every open terminal. A live process reporting `busy` is working, any other live process needs you, and a session with no live process is inactive. If that folder doesn't exist, status is guessed from the log instead. |
| Current tool, context | The newest `tool_use` and token usage in the conversation log, used for the kitchen stations and the context bar. |
| Sub-agents | `<session>/subagents/*.jsonl` files written in the last 30 s, shown as sous-chefs. |
| Idle time | The session log's last modified time. |

Hidden on purpose: thinking, tool output and subagent transcripts. Only the conversation and one-line tool summaries are shown.

On Windows, `~` is `C:\Users\<you>`.

## Files

| File | What it does |
|---|---|
| `agentview.py` | The Textual app: sidebar, views, polling |
| `sessions.py` | Finds today's logs, tails them, parses records into events, reads live status |
| `render.py` | Colors and line formatting |
| `room.py` | The kitchen: pixel room, sprites, chef wandering and drawing |

## Privacy

agentview only reads files already on your machine and makes no network requests. Your session logs can contain anything you've typed into Claude Code, so be careful sharing screenshots of it.

## Credits

The half-block pixel approach was inspired by [recon](https://github.com/gavraz/recon)'s Tamagotchi view. The colors are from [Tokyo Night](https://github.com/tokyo-night/tokyo-night-vscode-theme).

## License

[MIT](LICENSE)
