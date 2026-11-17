"""Where the butler's state lives: one directory, BUTLER_HOME, outside any install or checkout.

An install is replaced on every upgrade, so nothing it holds may be state. BUTLER_HOME holds the
task DB and the butler's pinned session id, and is the butler's working directory, which is what
Claude Code keys the butler's conversation on."""

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
