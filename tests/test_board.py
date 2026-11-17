"""bin/board: read-only snapshots of the task DB, served to local hosts only."""

import http.client
import importlib.machinery
import importlib.util
from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "board"
loader = importlib.machinery.SourceFileLoader("board_under_test", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
board = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = board
loader.exec_module(board)
task = board.task


class BoardCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(task, "DB_PATH", self.root / "state" / "butler.db"))
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
        self.server = board.ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("GET", path, headers={"Host": host} if host else {})
        res = conn.getresponse()
        body = res.read()
        conn.close()
        return res.status, body

    def test_serves_page_and_api(self):
        self.seed()
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"butler board", body)
        status, body = self.get("/api/tasks")
        self.assertEqual(json.loads(body)["tasks"][0]["title"], "title")

    def test_refuses_foreign_host(self):
        status, _ = self.get("/api/tasks", host="attacker.example:8765")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
