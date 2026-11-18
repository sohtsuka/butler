"""A read-only web screen of the butler's tasks: a timeline per task, what happened lately, details.

It only reads: the DB is opened read-only, herdr is only asked for pane states and screen tails,
and nothing here moves the herdr view. It listens on 127.0.0.1 unless told otherwise (--host), and
serves only a browser that has opened the URL with its token: loopback is open to every local user."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# The task CLI is the one place that knows the schema and how states are derived; reuse it.
from butler import paths, task, version

PAGE = paths.DATA / "board.html"
# Derived states hit herdr once per open task; several open browser tabs share one answer.
CACHE_SECONDS = 2.0


def connect_ro() -> sqlite3.Connection | None:
    path = task.DB_PATH
    if not path.exists():
        # Not moved into db/ yet: the next `butler-task` call does that, and the board only reads.
        path = path.parent.parent / path.name
        if not path.exists():
            return None
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    return db


def events_of(db: sqlite3.Connection, t) -> list:
    try:
        return task.task_events(db, t)
    except sqlite3.OperationalError:  # a DB no `butler-task` with the events table has opened yet
        stand_ins = [("started", t.created_at, t.agent), ("reported", t.reported_at, t.report),
                     ("closed", t.closed_at, None)]
        return [task.Event(t.id, at, kind, detail) for kind, at, detail in stand_ins if at]


def safe_state(t) -> str:
    try:
        return task.derive_state(t)
    except (task.HerdrError, OSError):  # herdr down or missing: still show what the DB knows
        return "unknown"


def snapshot(include_closed: bool) -> dict:
    """Every task with its derived state and its events, details cut to one line."""
    db = connect_ro()
    tasks = []
    if db is not None:
        with db:
            for t in task.all_tasks(db, include_closed=include_closed):
                tasks.append({
                    "id": t.id, "label": t.label, "title": t.title, "agent": t.agent,
                    "where": task.where(t), "state": safe_state(t),
                    "created_at": t.created_at, "closed_at": t.closed_at,
                    "events": [{"at": e.at, "kind": e.kind, "detail": task.first_line(e.detail, 120)}
                               for e in events_of(db, t)],
                })
        db.close()
    return {"now": task.now(), "tasks": tasks}


def detail(task_id: int) -> dict | None:
    db = connect_ro()
    if db is None:
        return None
    with db:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        t = task.task_from_row(row)
        out = {
            "id": t.id, "label": t.label, "title": t.title, "agent": t.agent, "state": safe_state(t),
            "cwd": t.cwd, "branch": t.branch, "worktree_path": t.worktree_path,
            "created_at": t.created_at, "closed_at": t.closed_at, "brief": t.brief,
            "events": [{"at": e.at, "kind": e.kind, "detail": e.detail} for e in events_of(db, t)],
        }
    db.close()
    return out


def peek(task_id: int, lines: int) -> str | None:
    db = connect_ro()
    if db is None:
        return None
    with db:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    if row is None:
        return None
    t = task.task_from_row(row)
    if t.closed_at or t.pane_id is None:
        return "(pane is gone)"
    try:
        return task.herdr_text("pane", "read", t.pane_id, "--source", "recent-unwrapped",
                               "--lines", str(lines)).rstrip()
    except (task.HerdrError, OSError) as e:
        return f"({e})"


class Cache:
    def __init__(self):
        self.lock = threading.Lock()
        self.entries: dict[bool, tuple[float, dict]] = {}

    def get(self, include_closed: bool) -> dict:
        with self.lock:
            hit = self.entries.get(include_closed)
            if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
                return hit[1]
            value = snapshot(include_closed)
            self.entries[include_closed] = (time.monotonic(), value)
            return value


CACHE = Cache()
# Host header names served; --allow-host adds more (e.g. a WSL address reached from Windows).
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
# What a browser must show, as ?token= once and then as a cookie. Set by main().
TOKEN = ""


def load_token(state_home: Path) -> str:
    """The board's token, kept in BUTLER_HOME (owner-only) so a bookmarked URL survives restarts.
    Delete the file to make a new one."""
    path = state_home / "board-token"
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        pass
    state_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # another board made it first
        return path.read_text().strip()
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    return token


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # A web page could still reach a local listener by rebinding its own DNS name to this
        # address. Such a request carries that name in Host, so only known names are served.
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS:
            return self.send(403, b"forbidden host", "text/plain")
        path, _, query = self.path.partition("?")
        params = query.split("&")
        # Cookies are shared by every port of a host, so the name carries the port.
        cookie = f"butler_board_{self.server.server_port}"
        given = next((p.removeprefix("token=") for p in params if p.startswith("token=")), None)
        if given is not None and hmac.compare_digest(given, TOKEN):
            # Trade the token in the URL for a cookie, and drop it from the address bar. Always to
            # the page itself: echoing the request path would make `//host` an open redirect.
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", f"{cookie}={TOKEN}; Path=/; HttpOnly; SameSite=Strict")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        jar = SimpleCookie(self.headers.get("Cookie", ""))
        if cookie not in jar or not hmac.compare_digest(jar[cookie].value, TOKEN):
            return self.send(401, b"open the URL butler-board printed (it carries a token)",
                             "text/plain")
        if path == "/":
            return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/tasks":
            return self.json(CACHE.get(include_closed="all=1" in params))
        if m := re.fullmatch(r"/api/tasks/(\d+)", path):
            found = detail(int(m.group(1)))
            return self.json(found) if found else self.send(404, b"not found", "text/plain")
        if m := re.fullmatch(r"/api/tasks/(\d+)/peek", path):
            text = peek(int(m.group(1)), 60)
            if text is None:
                return self.send(404, b"not found", "text/plain")
            return self.send(200, text.encode(), "text/plain; charset=utf-8")
        self.send(404, b"not found", "text/plain")

    def json(self, value: dict):
        self.send(200, json.dumps(value, ensure_ascii=False).encode(), "application/json")

    def send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # quiet: this runs next to the operator's terminal
        pass


def main() -> None:
    p = argparse.ArgumentParser(prog="butler-board", description=__doc__)
    version.add_flag(p, "butler-board")
    p.add_argument("--host", default="127.0.0.1",
                   help="address to listen on (default 127.0.0.1; 0.0.0.0 for every interface)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="also serve requests whose Host header is NAME (repeatable)")
    p.add_argument("--home", help="the butler's state directory (default: BUTLER_HOME)")
    args = p.parse_args()
    if args.home:
        os.environ["BUTLER_HOME"] = args.home
        task.DB_PATH = paths.db_path(paths.home())
    if ":" in args.host:
        p.error("--host takes an IPv4 address or a host name; IPv6 is not supported")
    ALLOWED_HOSTS.update(args.allow_host)
    if args.host not in ("127.0.0.1", "localhost", "0.0.0.0"):
        ALLOWED_HOSTS.add(args.host)
    global TOKEN
    TOKEN = load_token(paths.home())
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    if args.host not in ("127.0.0.1", "localhost"):
        print(f"butler board: listening on {args.host}, not only this machine. The token is the only "
              "lock, and it travels in plain HTTP: briefs, reports and worker screens are readable "
              "by anyone who sees this traffic.", file=sys.stderr, flush=True)
    shown = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    print(f"butler board: http://{shown}:{args.port}/?token={TOKEN}  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
