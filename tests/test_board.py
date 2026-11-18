"""butler-board: read-only snapshots of the task DB, served to local hosts only."""

import http.client
from pathlib import Path
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from butler import board, task  # noqa: E402


class BoardCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(task, "DB_PATH", self.root / "state" / "db" / "butler.db"))
        self.enterContext(patch.object(task, "herdr", side_effect=AssertionError("no herdr")))
        self.enterContext(patch.object(board, "CACHE", board.Cache()))

    def seed(self, closed=False):
        db = task.connect()
        task_id = db.execute(
            "INSERT INTO tasks (title, cwd, brief, created_at, pane_id, closed_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("title", str(self.root), "the brief", task.now(), "w1:p1", task.now() if closed else None),
        ).lastrowid
        task.record(db, task_id, "started", "claude")
        task.record(db, task_id, "reported", "line one\nline two")
        db.execute("UPDATE tasks SET report = 'line one' WHERE id = ?", (task_id,))
        db.close()
        return task_id


class SnapshotTests(BoardCase):
    def test_no_db_is_an_empty_board_and_creates_nothing(self):
        self.assertEqual(board.snapshot(include_closed=False)["tasks"], [])
        self.assertFalse(task.DB_PATH.exists())

    def test_snapshot_cuts_details_to_one_line(self):
        self.seed()
        t, = board.snapshot(include_closed=False)["tasks"]
        self.assertEqual(t["state"], "reported")
        self.assertEqual([(e["kind"], e["detail"]) for e in t["events"]],
                         [("started", "claude"), ("reported", "line one")])

    def test_closed_tasks_only_on_request(self):
        self.seed(closed=True)
        self.assertEqual(board.snapshot(include_closed=False)["tasks"], [])
        self.assertEqual(len(board.snapshot(include_closed=True)["tasks"]), 1)

    def test_detail_keeps_full_text(self):
        task_id = self.seed()
        d = board.detail(task_id)
        self.assertEqual(d["brief"], "the brief")
        self.assertEqual(d["events"][-1]["detail"], "line one\nline two")
        self.assertIsNone(board.detail(task_id + 1))

    def test_unreachable_herdr_shows_unknown(self):
        db = task.connect()
        db.execute("INSERT INTO tasks (title, cwd, brief, created_at, pane_id) VALUES ('t', '/', 'b', ?, 'w1:p1')",
                   (task.now(),))
        db.close()
        with patch.object(task, "herdr", side_effect=FileNotFoundError("herdr")):
            self.assertEqual(board.snapshot(include_closed=False)["tasks"][0]["state"], "unknown")

    def test_db_not_yet_moved_into_db_dir_is_read_where_it_is(self):
        self.seed()
        legacy = task.DB_PATH.parent.parent / "butler.db"
        for suffix in ("", "-wal", "-shm"):
            if Path(f"{task.DB_PATH}{suffix}").exists():
                os.rename(f"{task.DB_PATH}{suffix}", f"{legacy}{suffix}")
        self.assertEqual(len(board.snapshot(include_closed=False)["tasks"]), 1)
        self.assertFalse(task.DB_PATH.exists())

    def test_db_from_before_the_events_table(self):
        task.DB_PATH.parent.mkdir(parents=True)
        db = task.sqlite3.connect(task.DB_PATH)
        db.executescript(task.SCHEMA.split("-- What happened")[0])
        db.execute("INSERT INTO tasks (title, cwd, brief, created_at, closed_at) VALUES ('t', '/', 'b', ?, ?)",
                   (task.now(), task.now()))
        db.commit()
        db.close()
        t, = board.snapshot(include_closed=True)["tasks"]
        self.assertEqual([e["kind"] for e in t["events"]], ["started", "closed"])


class ServerTests(BoardCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(board, "TOKEN", "s3cret"))
        self.server = board.ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        self.cookie = f"butler_board_{self.server.server_port}=s3cret"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path, host=None, cookie=True, response=False):
        headers = {"Host": host} if host else {}
        if cookie:
            headers["Cookie"] = self.cookie if cookie is True else cookie
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("GET", path, headers=headers)
        res = conn.getresponse()
        body = res.read()
        conn.close()
        return (res.status, body, res) if response else (res.status, body)

    def test_serves_page_and_api(self):
        self.seed()
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"butler board", body)
        status, body = self.get("/api/tasks")
        self.assertEqual(json.loads(body)["tasks"][0]["title"], "title")

    def test_without_the_token_nothing_is_served(self):
        self.seed()
        for path in ("/", "/api/tasks", "/api/tasks/1", "/api/tasks/1/peek"):
            self.assertEqual(self.get(path, cookie=False)[0], 401, path)
        port = self.server.server_port
        self.assertEqual(self.get("/api/tasks", cookie=f"butler_board_{port}=wrong")[0], 401)
        self.assertEqual(self.get("/api/tasks", cookie="butler_board_1=s3cret")[0], 401)
        self.assertEqual(self.get("/?token=wrong", cookie=False)[0], 401)

    def test_token_in_url_becomes_a_cookie(self):
        status, _, res = self.get("/?token=s3cret", cookie=False, response=True)
        self.assertEqual(status, 303)
        self.assertEqual(res.getheader("Location"), "/")
        set_cookie = res.getheader("Set-Cookie")
        self.assertTrue(set_cookie.startswith(self.cookie + ";"))
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)
        _, _, res = self.get("//attacker.example/?token=s3cret", cookie=False, response=True)
        self.assertEqual(res.getheader("Location"), "/")

    def test_foreign_host_is_refused_even_with_the_token(self):
        self.assertEqual(self.get("/?token=s3cret", host="attacker.example", cookie=False)[0], 403)

    def test_refuses_foreign_host(self):
        status, _ = self.get("/api/tasks", host="attacker.example:8765")
        self.assertEqual(status, 403)

    def test_allowed_host_is_served(self):
        self.enterContext(patch.object(board, "ALLOWED_HOSTS", board.ALLOWED_HOSTS | {"172.20.1.2"}))
        status, _ = self.get("/api/tasks", host="172.20.1.2:8765")
        self.assertEqual(status, 200)
        status, _ = self.get("/api/tasks", host="attacker.example:8765")
        self.assertEqual(status, 403)


class TokenTests(unittest.TestCase):
    def test_token_is_made_once_owner_only_and_kept(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            first = board.load_token(home)
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual((home / "board-token").stat().st_mode & 0o777, 0o600)
            self.assertEqual(home.stat().st_mode & 0o777, 0o700)
            self.assertEqual(board.load_token(home), first)


if __name__ == "__main__":
    unittest.main()
