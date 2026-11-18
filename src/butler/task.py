#!/usr/bin/env python3
"""butler-task, the butler's task CLI: start, watch and close herdr-hosted Claude or Codex workers."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

try:
    from butler import paths, version
except ImportError:  # run as a plain script from a checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from butler import paths, version

# Set again by main() once --home is parsed. TASK_CMD is shell-quoted at the source: it only ever
# goes into instructions a worker runs as a command.
DB_PATH = paths.db_path(paths.home())
TASK_CMD = paths.task_cmd(paths.home())
POLL_SECONDS = 3.0
IDLE_POLLS_WITHOUT_WORKING = 5
START_TIMEOUT_MS = "10000"
# herdr agent names are global, so each butler instance namespaces its agents with this.
NAME = os.environ.get("BUTLER_NAME", "butler")

AGENTS = ("claude", "codex")

LiveStatus = Literal["working", "blocked", "idle", "done", "unknown", "gone"]
State = Literal["closed", "reported", "working", "blocked", "idle", "done", "unknown", "gone"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  cwd TEXT NOT NULL,
  repo_root TEXT,
  worktree_path TEXT,
  branch TEXT,
  workspace_id TEXT,
  tab_id TEXT,
  pane_id TEXT,
  brief TEXT NOT NULL,
  report TEXT,
  created_at TEXT NOT NULL,
  reported_at TEXT,
  closed_at TEXT,
  agent TEXT NOT NULL DEFAULT 'claude'
);
-- What happened to each task, appended and never updated. `tasks` holds only the present (a
-- follow-up clears the report, live states are not stored); this keeps the history a timeline
-- needs. Live states (blocked, unblocked, stopped, gone) are only seen by a process polling herdr,
-- so they are recorded when `task wait` or `task keys` sees them.
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS events_task ON events(task_id, id);
"""
EVENT_KINDS = ("started", "followup", "reported", "blocked", "unblocked", "stopped", "gone", "closed")
# Columns added after the first release, for DBs created before them.
MIGRATIONS = {"agent": "ALTER TABLE tasks ADD COLUMN agent TEXT NOT NULL DEFAULT 'claude'"}


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    cwd: str
    repo_root: str | None
    worktree_path: str | None
    branch: str | None
    workspace_id: str | None
    tab_id: str | None
    pane_id: str | None
    brief: str
    report: str | None
    created_at: str
    reported_at: str | None
    closed_at: str | None
    agent: str = "claude"

    @property
    def label(self) -> str:
        return f"{self.id:04d}"


def task_from_row(row: sqlite3.Row) -> Task:
    """The one way a `tasks` row becomes a Task, dropping columns this code does not know.

    MIGRATIONS covers an old DB read by new code; this covers the mirror image, a new DB read by
    old code. An upgrade migrates the DB under the processes already running on it, so a column
    added for a later release must not kill the `task wait` watching a worker right now."""
    known = {f.name for f in fields(Task)}
    return Task(**{name: value for name, value in dict(row).items() if name in known})


class HerdrError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"herdr {code}: {message}")
        self.code = code


def herdr_text(*args: str) -> str:
    """The one boundary to herdr: run it, raise its JSON error (stderr, exit 1), return stdout."""
    proc = subprocess.run(["herdr", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        try:
            error = json.loads(proc.stderr)["error"]
        except (json.JSONDecodeError, KeyError):
            raise HerdrError("bad_output", (proc.stdout + proc.stderr).strip()) from None
        raise HerdrError(error["code"], error["message"])
    return proc.stdout


def herdr(*args: str) -> dict:
    return json.loads(herdr_text(*args))["result"]


def move_legacy_db() -> None:
    """Move a DB from BUTLER_HOME itself, where releases before db/ kept it, into db/. The WAL and
    shared-memory files go first, so the DB never appears at its new path without them; a racing
    process finds each file already moved."""
    legacy = DB_PATH.parent.parent / DB_PATH.name
    if DB_PATH.exists() or not legacy.exists():
        return
    for suffix in ("-wal", "-shm", ""):
        try:
            os.rename(f"{legacy}{suffix}", f"{DB_PATH}{suffix}")
        except FileNotFoundError:
            pass


def connect() -> sqlite3.Connection:
    paths.make_db_dir(DB_PATH)
    move_legacy_db()
    db = sqlite3.connect(DB_PATH, timeout=5.0, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    # Switching to WAL ignores busy_timeout, so racing first opens of a new DB retry here.
    for _ in range(50):
        try:
            db.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError:
            time.sleep(0.1)
    db.executescript(SCHEMA)
    have = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
    for column, sql in MIGRATIONS.items():
        if column not in have:
            try:
                db.execute(sql)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e):  # a racing first open already added it
                    raise
    return db


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(db: sqlite3.Connection, task_id: int) -> Task:
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        sys.exit(f"task {task_id:04d} not found")
    return task_from_row(row)


def record(db: sqlite3.Connection, task_id: int, kind: str, detail: str | None = None) -> int:
    assert kind in EVENT_KINDS, kind
    return db.execute(
        "INSERT INTO events (task_id, at, kind, detail) VALUES (?, ?, ?, ?)",
        (task_id, now(), kind, detail),
    ).lastrowid


def record_seen(db: sqlite3.Connection, task_id: int, kind: str) -> None:
    """Record a live state a poller saw, once: several waiters (a re-armed one, a stale one) can
    see the same state, so skip it when it is already the task's latest event. `unblocked` only
    means something right after `blocked`."""
    row = db.execute(
        "SELECT kind FROM events WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,)
    ).fetchone()
    latest = row["kind"] if row else None
    if latest == kind or (kind == "unblocked" and latest != "blocked"):
        return
    record(db, task_id, kind)


def live_status(pane_id: str | None) -> LiveStatus:
    if pane_id is None:
        return "gone"
    try:
        return herdr("pane", "get", pane_id)["pane"]["agent_status"]
    except HerdrError as e:
        if e.code == "pane_not_found":
            return "gone"
        raise


def derive_state(task: Task) -> State:
    if task.closed_at:
        return "closed"
    if task.report is not None:
        return "reported"
    return live_status(task.pane_id)


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:30].strip("-")
    return slug or "task"


def git_toplevel(cwd: Path) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def kickoff(task_id: int) -> str:
    return (
        f"You are a worker for task {task_id:04d}. "
        f"Run `{TASK_CMD} brief {task_id}` to read your brief, then do it. "
        f"When you finish, whether it worked or not, pipe a report into "
        f"`{TASK_CMD} report {task_id}` (stdin, e.g. a heredoc) covering: outcome; "
        f"what changed (files, branch, commits); how you verified it; open questions."
    )


def agent_argv(agent: str, name: str, task_id: int, git_dirs: list[str]) -> list[str]:
    """What follows `--` in `herdr agent start`: the agent CLI's own flags, then the kickoff.

    The kickoff rides in argv for both, so it survives a startup dialog (folder trust)."""
    if agent == "claude":
        return ["--name", name, kickoff(task_id)]
    # Codex's workspace-write sandbox only lets it write inside its cwd, but `task report` writes
    # the butler's DB, and a commit in a worktree writes the main repo's .git. --add-dir makes
    # both writable, so neither needs an approval. Only the DB's own directory, never BUTLER_HOME:
    # see paths.db_path.
    dirs = [str(DB_PATH.parent), *git_dirs]
    return ["--sandbox", "workspace-write", *(a for d in dirs for a in ("--add-dir", d)),
            kickoff(task_id)]


def commit_dirs(worktree: str) -> list[str]:
    """What a commit in `worktree` writes outside it: its own gitdir (index, HEAD) and the main
    repo's objects, refs and reflogs. Codex keeps a cwd's gitdir read-only even under a writable
    .git, but honors it as an explicit root. The rest of .git (hooks, config) stays read-only."""
    out = subprocess.run(
        ["git", "-C", worktree, "rev-parse", "--path-format=absolute", "--git-dir",
         "--git-common-dir"], capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    common = Path(out[1])
    dirs = [out[0], *(str(common / d) for d in ("objects", "refs", "logs"))]
    return [d for d in dirs if Path(d).is_dir()]


def worktree_git_problem(task: Task) -> str | None:
    """Why running git in a task's worktree is unsafe, or None when its links are as git made them.

    A worker can write its worktree and its gitdir (commit_dirs), so it could point the worktree at
    a gitdir or common dir whose config it wrote, or add a config.worktree; core.fsmonitor or
    core.hooksPath there would run with the operator's next git command in the worktree, outside
    any sandbox. butler-task itself only touches a worktree from the main repo, with
    `worktree remove --force`, which reads none of them. The links are read as files, never by git.
    """
    if not task.worktree_path or not task.repo_root or not Path(task.worktree_path).exists():
        return None
    worktree = Path(task.worktree_path)
    common = Path(subprocess.run(
        ["git", "-C", task.repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()).resolve()
    try:
        link = (worktree / ".git").read_text()
        if not link.startswith("gitdir: "):
            return f"{worktree}/.git is not a link to a gitdir"
        gitdir = (worktree / link.removeprefix("gitdir: ").strip()).resolve()
        if gitdir.parent != common / "worktrees":
            return f"{worktree}/.git points at {gitdir}, outside {common}/worktrees"
        if (gitdir / (gitdir / "commondir").read_text().strip()).resolve() != common:
            return f"{gitdir}/commondir does not point at {common}"
        if (gitdir / "config.worktree").exists():
            return f"{gitdir}/config.worktree exists"
    except OSError as e:
        return f"cannot read the worktree's git links: {e}"
    return None


def git_warning(task: Task) -> str:
    problem = worktree_git_problem(task)
    return f"WARNING: do not run git in {task.worktree_path}: {problem}" if problem else ""


def cmd_start(args: argparse.Namespace) -> None:
    brief = sys.stdin.read()
    if not brief.strip():
        sys.exit("task start: brief on stdin is empty")
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.is_dir():
        sys.exit(f"task start: {cwd} is not a directory")
    repo_root = None if args.no_worktree else git_toplevel(cwd)

    db = connect()
    task_id = db.execute(
        "INSERT INTO tasks (title, cwd, repo_root, brief, created_at, agent) VALUES (?, ?, ?, ?, ?, ?)",
        (args.title, str(cwd), repo_root, brief, now(), args.agent),
    ).lastrowid
    record(db, task_id, "started", args.agent)
    label = f"{task_id:04d}"
    name = f"{NAME}-{label}"
    try:
        if repo_root:
            branch = f"butler/{label}-{slugify(args.title)}"
            made = herdr("worktree", "create", "--cwd", str(cwd), "--branch", branch, "--no-focus")
            pane = made["root_pane"]
            ids = (made["worktree"]["path"], branch, made["workspace"]["workspace_id"])
        else:
            pane = herdr(
                "tab", "create", "--cwd", str(cwd), "--label", name, "--no-focus",
                *(["--workspace", ws] if (ws := os.environ.get("HERDR_WORKSPACE_ID")) else []),
            )["root_pane"]
            ids = (None, None, None)
        db.execute(
            "UPDATE tasks SET worktree_path = ?, branch = ?, workspace_id = ?, tab_id = ?, pane_id = ?"
            " WHERE id = ?",
            (*ids, pane["tab_id"], pane["pane_id"], task_id),
        )
        # The kickoff rides in the agent's argv, so it survives a startup dialog (folder trust):
        # the agent runs it once the dialog is answered, while `agent prompt` is refused when
        # blocked. With a prompt in argv, `agent start` only reports ready after the first turn
        # ends, and on timeout it drops the name while the agent keeps working. Rename to reclaim
        # it; that fails, and so fails the start, only if no agent came up in the pane.
        try:
            git_dirs = commit_dirs(ids[0]) if ids[0] else []
            herdr("agent", "start", name, "--kind", args.agent, "--pane", pane["pane_id"],
                  "--timeout", START_TIMEOUT_MS, "--",
                  *agent_argv(args.agent, name, task_id, git_dirs))
        except HerdrError as e:
            if e.code == "timeout":
                herdr("agent", "rename", pane["pane_id"], name)
            elif e.code == "agent_not_ready":
                print(f"{label} is blocked at startup; see `butler-task peek {label}`", file=sys.stderr)
            else:
                raise
    except HerdrError as e:
        # A readiness timeout or failed rename can follow real work, including commits.
        # Keep the worktree and branch for inspection, just as a normal close does.
        task = load(db, task_id)
        close_herdr(task, remove_worktree=False)
        db.execute("UPDATE tasks SET closed_at = ? WHERE id = ?", (now(), task_id))
        record(db, task_id, "closed", f"start failed: {e}")
        kept = f"; worktree kept: {task.worktree_path}" if task.worktree_path else ""
        sys.exit(f"task start: {e}; task {label} closed{kept}")
    print(label)


def cmd_brief(args: argparse.Namespace) -> None:
    sys.stdout.write(load(connect(), args.id).brief)


def cmd_report(args: argparse.Namespace) -> None:
    report = sys.stdin.read()
    if not report.strip():
        sys.exit("task report: report on stdin is empty")
    db = connect()
    task = load(db, args.id)
    if task.closed_at:
        sys.exit(f"task report: task {task.label} is closed")
    db.execute(
        "UPDATE tasks SET report = ?, reported_at = ? WHERE id = ?", (report, now(), task.id)
    )
    record(db, task.id, "reported", report)
    print(f"report saved for task {task.label}")


def cmd_wait(args: argparse.Namespace) -> None:
    db = connect()
    deadline = time.monotonic() + args.timeout if args.timeout else None
    saw_working = False
    stopped_polls = 0
    while True:
        state = derive_state(load(db, args.id))
        if state == "working":
            saw_working = True
            record_seen(db, args.id, "unblocked")
        elif state == "blocked":
            record_seen(db, args.id, "blocked")
        stopped_polls = stopped_polls + 1 if state in ("idle", "done", "unknown") else 0
        outcome = _wait_outcome(state, saw_working, stopped_polls)
        if outcome:
            break
        if deadline and time.monotonic() >= deadline:
            print("timeout")
            return
        time.sleep(POLL_SECONDS)
    if outcome == "stopped-without-report":
        record_seen(db, args.id, "stopped")
    elif outcome == "gone":
        record_seen(db, args.id, "gone")
    print(outcome)
    sound = "request" if outcome == "blocked" else "done"
    try:
        herdr("notification", "show", f"task {args.id:04d}: {outcome}", "--sound", sound)
    except HerdrError:
        pass


def _wait_outcome(state: State, saw_working: bool, stopped_polls: int) -> str | None:
    if state in ("reported", "blocked", "gone", "closed"):
        return state
    if stopped_polls and (saw_working or stopped_polls >= IDLE_POLLS_WITHOUT_WORKING):
        return "stopped-without-report"
    return None


def all_tasks(db: sqlite3.Connection, include_closed: bool) -> list[Task]:
    sql = "SELECT * FROM tasks" + ("" if include_closed else " WHERE closed_at IS NULL") + " ORDER BY id"
    return [task_from_row(r) for r in db.execute(sql)]


def where(task: Task) -> str:
    return task.branch or task.cwd


def cmd_list(args: argparse.Namespace) -> None:
    db = connect()
    rows = [(t, derive_state(t)) for t in all_tasks(db, include_closed=args.all and not args.for_hook)]
    if args.for_hook:
        print(hook_block(rows))
        return
    if not rows:
        print("no tasks")
        return
    width = max(len(t.title) for t, _ in rows)
    for t, state in rows:
        print(f"{t.label}  {state:<9}  {t.agent:<6}  {t.title:<{width}}  {where(t)}")


def hook_block(rows: list[tuple[Task, State]]) -> str:
    if not rows:
        return "[butler] No open tasks. State lives in butler-task; trust `butler-task list` over memory."
    lines = ["[butler] Open tasks (from butler-task; trust this over memory):"]
    lines += [f"- {t.label} {state} [{t.agent}]: {t.title} ({where(t)})" for t, state in rows]
    rearm = [t.label for t, state in rows if state not in ("reported", "gone", "blocked")]
    blocked = [t.label for t, state in rows if state == "blocked"]
    if rearm:
        lines.append(
            "Re-arm a waiter now for each of these, one Bash call each with run_in_background: true: "
            + ", ".join(f"`butler-task wait {i}`" for i in rearm)
        )
    if blocked:
        lines.append(
            "Blocked on a question: " + ", ".join(blocked) + ". Tell the human (see `butler-task peek <id>`);"
            " re-arm `butler-task wait <id>` only after it has been answered."
        )
    return "\n".join(lines)


def cmd_show(args: argparse.Namespace) -> None:
    t = load(connect(), args.id)
    print(f"id:        {t.label}")
    print(f"state:     {derive_state(t)}")
    print(f"title:     {t.title}")
    print(f"agent:     {t.agent}")
    for name in ("cwd", "repo_root", "worktree_path", "branch", "workspace_id", "tab_id",
                 "pane_id", "created_at", "reported_at", "closed_at"):
        if (value := getattr(t, name)) is not None:
            print(f"{name + ':':<10} {value}")
    if warning := git_warning(t):
        print(warning)
    print("\n--- brief ---\n" + t.brief.rstrip())
    if t.report is not None:
        print("\n--- report ---\n" + t.report.rstrip())


def live_pane(t: Task) -> str:
    if t.closed_at or live_status(t.pane_id) == "gone":
        sys.exit(f"task {t.label}: pane is gone")
    return t.pane_id


def cmd_peek(args: argparse.Namespace) -> None:
    pane = live_pane(load(connect(), args.id))
    print(herdr_text("pane", "read", pane, "--source", "recent-unwrapped", "--lines", str(args.lines)).rstrip())


def cmd_send(args: argparse.Namespace) -> None:
    """A follow-up opens a new round: the old report is cleared so `task wait` waits for the next."""
    db = connect()
    task = load(db, args.id)
    text = f"{args.text}\n\nWhen you finish, pipe an updated report into `{TASK_CMD} report {task.id}`."
    pane = live_pane(task)
    # Clear before prompting: the worker can report while `agent prompt` is in flight, and a
    # clear afterwards would erase that report with the worker none the wiser.
    db.execute("UPDATE tasks SET report = NULL, reported_at = NULL WHERE id = ?", (task.id,))
    # Recorded before the prompt for the same reason, so it sorts before any report it causes.
    event = record(db, task.id, "followup", args.text)
    try:
        herdr("agent", "prompt", pane, text)
    except HerdrError as e:
        # The prompt never landed, so put the old round back -- unless a report has since arrived.
        db.execute(
            "UPDATE tasks SET report = ?, reported_at = ? WHERE id = ? AND report IS NULL",
            (task.report, task.reported_at, task.id),
        )
        db.execute("DELETE FROM events WHERE id = ?", (event,))
        if e.code != "agent_blocked":
            raise
        sys.exit(f"task {task.label} is at a dialog, so text is refused. Answer it with "
                 f"`butler-task keys {task.label} <key>...` (e.g. down enter, esc) or `butler-task focus`.")
    print(f"sent to task {task.label}")


def cmd_keys(args: argparse.Namespace) -> None:
    """Prints the worker's state once it leaves `blocked` (or after 5s), so a waiter armed next is fresh."""
    db = connect()
    pane = live_pane(load(db, args.id))
    herdr("agent", "send-keys", pane, *args.keys)
    deadline = time.monotonic() + 5
    while (state := live_status(pane)) == "blocked" and time.monotonic() < deadline:
        time.sleep(0.5)
    if state != "blocked":
        record_seen(db, args.id, "unblocked")
    print(f"sent keys to task {args.id:04d}; now {state}")


def cmd_focus(args: argparse.Namespace) -> None:
    herdr("agent", "focus", live_pane(load(connect(), args.id)))


def close_herdr(task: Task, remove_worktree: bool) -> None:
    target = ("workspace", task.workspace_id) if task.workspace_id else ("tab", task.tab_id)
    if target[1]:
        try:
            herdr(target[0], "close", target[1])
        except HerdrError as e:
            if not e.code.endswith("not_found"):
                raise
    if remove_worktree and task.worktree_path and Path(task.worktree_path).exists():
        subprocess.run(
            ["git", "-C", task.repo_root, "worktree", "remove", "--force", task.worktree_path],
            check=True,
        )


def cmd_close(args: argparse.Namespace) -> None:
    db = connect()
    task = load(db, args.id)
    close_herdr(task, args.remove_worktree)
    if not task.closed_at:
        db.execute("UPDATE tasks SET closed_at = ? WHERE id = ?", (now(), task.id))
        record(db, task.id, "closed")
    kept = task.worktree_path and Path(task.worktree_path).exists()
    print(f"closed task {task.label}" + (f" (worktree kept: {task.worktree_path})" if kept else ""))
    if kept and (warning := git_warning(task)):
        print(warning, file=sys.stderr)


@dataclass(frozen=True)
class Event:
    task_id: int
    at: str
    kind: str
    detail: str | None


def task_events(db: sqlite3.Connection, t: Task) -> list[Event]:
    """A task's events. A task from before the events table has none, so its timestamps stand in."""
    rows = db.execute(
        "SELECT task_id, at, kind, detail FROM events WHERE task_id = ? ORDER BY id", (t.id,)
    ).fetchall()
    if rows:
        return [Event(**dict(r)) for r in rows]
    stand_ins = [("started", t.created_at, t.agent), ("reported", t.reported_at, t.report),
                 ("closed", t.closed_at, None)]
    return [Event(t.id, at, kind, detail) for kind, at, detail in stand_ins if at]


def local_time(at: str) -> str:
    return datetime.fromisoformat(at).astimezone().strftime("%m-%d %H:%M:%S")


def first_line(text: str | None, width: int = 60) -> str:
    line = (text or "").strip().split("\n", 1)[0]
    return line if len(line) <= width else line[: width - 1] + "…"


def cmd_timeline(args: argparse.Namespace) -> None:
    db = connect()
    tasks = [load(db, args.id)] if args.id is not None else all_tasks(db, include_closed=args.all)
    titles = {t.id: t.title for t in tasks}
    events = sorted((e for t in tasks for e in task_events(db, t)), key=lambda e: e.at)
    if args.limit:
        events = events[-args.limit:]
    if not events:
        print("no events")
        return
    for e in events:
        detail = first_line(e.detail)
        print(f"{local_time(e.at)}  {e.task_id:04d}  {e.kind:<9}  {titles[e.task_id]}"
              + (f"  | {detail}" if detail else ""))


def main() -> None:
    p = argparse.ArgumentParser(prog="butler-task")
    p.add_argument("--home", help="the butler's state directory (default: $BUTLER_HOME, else "
                   "$XDG_STATE_HOME/butler)")
    version.add_flag(p, "butler-task")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="start a worker; brief on stdin")
    s.add_argument("--title", required=True)
    s.add_argument("--cwd", required=True)
    s.add_argument("--no-worktree", action="store_true")
    s.add_argument("--agent", choices=AGENTS, default="claude", help="worker CLI (default: claude)")
    s.set_defaults(fn=cmd_start)

    for name, fn in (("brief", cmd_brief), ("report", cmd_report), ("show", cmd_show),
                     ("focus", cmd_focus)):
        s = sub.add_parser(name)
        s.add_argument("id", type=int)
        s.set_defaults(fn=fn)

    s = sub.add_parser("wait")
    s.add_argument("id", type=int)
    s.add_argument("--timeout", type=float)
    s.set_defaults(fn=cmd_wait)

    s = sub.add_parser("list")
    s.add_argument("--all", action="store_true")
    s.add_argument("--for-hook", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("timeline", help="what happened to tasks, oldest first")
    s.add_argument("id", type=int, nargs="?")
    s.add_argument("--all", action="store_true", help="include closed tasks")
    s.add_argument("--limit", type=int, help="only the last N events")
    s.set_defaults(fn=cmd_timeline)

    s = sub.add_parser("peek")
    s.add_argument("id", type=int)
    s.add_argument("--lines", type=int, default=40)
    s.set_defaults(fn=cmd_peek)

    s = sub.add_parser("send")
    s.add_argument("id", type=int)
    s.add_argument("text")
    s.set_defaults(fn=cmd_send)

    s = sub.add_parser("keys", help="press keys in the worker's UI, e.g. at a dialog")
    s.add_argument("id", type=int)
    s.add_argument("keys", nargs="+")
    s.set_defaults(fn=cmd_keys)

    s = sub.add_parser("close")
    s.add_argument("id", type=int)
    s.add_argument("--remove-worktree", action="store_true")
    s.set_defaults(fn=cmd_close)

    args = p.parse_args()
    if args.home:
        global DB_PATH, TASK_CMD
        os.environ["BUTLER_HOME"] = args.home
        DB_PATH = paths.db_path(paths.home())
        TASK_CMD = paths.task_cmd(paths.home())
    try:
        args.fn(args)
    except HerdrError as e:
        sys.exit(f"task {args.cmd}: {e}")


if __name__ == "__main__":
    main()
