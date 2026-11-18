"""Where the butler's state lives: one directory, BUTLER_HOME, outside any install or checkout.

An install is replaced on every upgrade, so nothing it holds may be state. BUTLER_HOME holds the
task DB (in db/) and the butler's pinned session id, and is the butler's working directory, which
is what Claude Code keys the butler's conversation on."""

import os
import shlex
import shutil
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


def home() -> Path:
    if explicit := os.environ.get("BUTLER_HOME"):
        return Path(explicit).expanduser().resolve()
    state = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return (Path(state).expanduser() / "butler").resolve()


def db_path(state_home: Path) -> Path:
    """The task DB. It has a directory of its own because that directory is all a Codex worker may
    write: BUTLER_HOME is the butler's Claude Code project, so a worker able to write there could
    leave a .claude/settings.json whose hooks run, outside any sandbox, when the butler starts."""
    return state_home / "db" / "butler.db"


def make_db_dir(db: Path) -> None:
    """Create the DB's directory, and BUTLER_HOME above it, readable by the owner only: briefs,
    reports and screens are not for the machine's other users. db/ is the butler's own, so its mode
    is set even when it exists; an existing BUTLER_HOME keeps whatever mode the operator gave it."""
    db.parent.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    db.parent.mkdir(mode=0o700, exist_ok=True)
    os.chmod(db.parent, 0o700)


def task_exe() -> str:
    """The task CLI as an absolute path, for commands that run outside the butler's PATH.

    The launcher exports BUTLER_TASK_EXE; without it, the task CLI is the running program itself
    (when it is that CLI) or whatever `butler-task` the PATH finds."""
    if exe := os.environ.get("BUTLER_TASK_EXE"):
        return exe
    argv0 = Path(sys.argv[0])
    if argv0.name == "butler-task" and argv0.is_file():
        return str(argv0.absolute())  # not resolve(): an installed shim's symlink outlives upgrades
    return shutil.which("butler-task") or "butler-task"


def task_cmd(state_home: Path) -> str:
    """The task CLI as a worker must run it: absolute, and pointed at this butler's own DB, since a
    worker's environment comes from herdr, not from the butler."""
    return f"{shlex.quote(task_exe())} --home {shlex.quote(str(state_home))}"
