#!/usr/bin/env python3
"""Start the butler agent, or say where it runs if it is already up.

Run at a herdr pane's shell prompt, the butler takes that pane over: this process
replaces itself with claude, so no tab appears and the view never moves. A caller
that does not hold a pane's terminal gets a new tab instead, opened without focus."""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

try:
    from butler import paths
except ImportError:  # run as a plain script from a checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from butler import paths

STATE_HOME = paths.home()  # the butler's state, and its working directory
ID_FILE = STATE_HOME / "butler-session-id"
# Resolved now: take_over() chdirs to STATE_HOME, after which a relative argv[0] (`bin/butler`
# run from a checkout) would point into STATE_HOME instead.
LAUNCHER = Path(sys.argv[0]).absolute()
# herdr agent names are global; a second instance (e.g. a verification run) needs its own.
NAME = os.environ.get("BUTLER_NAME", "butler")


def herdr(*args: str) -> tuple[int, dict]:
    proc = subprocess.run(["herdr", *args], capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout if proc.returncode == 0 else proc.stderr)


def claude_args() -> list[str]:
    """claude's argv: the butler's pinned session, plus the flags that make it the butler."""
    if not ID_FILE.is_file() or not ID_FILE.read_text().strip():
        ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = ID_FILE.with_name(f"{ID_FILE.name}.tmp.{os.getpid()}")
        tmp.write_text(f"{uuid.uuid4()}\n")
        tmp.rename(ID_FILE)
    sid = ID_FILE.read_text().strip()
    # A session killed before its first turn has no transcript and cannot be resumed.
    resumable = glob.glob(str(Path.home() / ".claude" / "projects" / "*" / f"{sid}.jsonl"))
    return [
        # Never --continue: it would pick up a developer's session in this repo.
        *(["--resume", sid] if resumable else ["--session-id", sid]),
        "--append-system-prompt-file", str(paths.DATA / "prompt.md"),
        "--settings", str(paths.DATA / "settings.json"),
    ]


def task_exe() -> str:
    """The task CLI that ships with this launcher: its sibling, as installed or in a checkout."""
    sibling = LAUNCHER.parent / "butler-task"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    if found := shutil.which("butler-task"):
        return str(Path(found).absolute())
    sys.exit("butler: cannot find butler-task next to butler or on PATH")


def butler_env() -> dict[str, str]:
    """What the butler's claude, its Bash calls and its hooks need: its state directory, and the
    task CLI as the bare `butler-task` its prompt and permission rules name."""
    exe = task_exe()
    return {
        "BUTLER_NAME": NAME,
        "BUTLER_HOME": str(STATE_HOME),
        "BUTLER_TASK_EXE": exe,
        "PATH": f"{Path(exe).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    }


def own_pane() -> str | None:
    pane = os.environ.get("HERDR_PANE_ID")
    # claude needs the terminal itself, which a pipe or a redirect means we do not hold.
    if not pane or not all(s.isatty() for s in (sys.stdin, sys.stdout, sys.stderr)):
        return None
    # An agent of any kind keeps its name and process while suspended, unseen by the check below.
    if herdr("agent", "get", pane)[0] == 0:
        return None
    code, info = herdr("pane", "process-info", "--pane", pane)
    if code:
        return None
    # exec replaces this process alone, so the pane's foreground must be exactly our group.
    # It is also what stops a claude that runs bin/butler from execing over itself.
    group = info["result"]["process_info"]["foreground_process_group_id"]
    return pane if group == os.getpgrp() else None


def take_over(pane: str) -> None:
    os.chdir(STATE_HOME)  # Claude Code keys the butler's conversation on this directory
    os.environ.update(butler_env())
    # herdr only names an agent it can already see, and `agent start` wants a pane sitting at
    # its shell prompt, so the name has to arrive from outside, once the exec'd claude shows up.
    subprocess.Popen(
        ["sh", "-c", 'for _ in $(seq 240); do kill -0 "$3" 2>/dev/null || exit 0; '
         'herdr agent rename "$1" "$2" && break; sleep 0.5; done',
         "sh", pane, NAME, str(os.getpid())],  # exec keeps this PID; stop if claude exits
        start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"butler is starting in this pane ({pane}); it answers to {NAME} once herdr sees it")
    sys.stdout.flush()
    try:
        os.execvp("claude", ["claude", *claude_args()])
    except OSError as e:
        sys.exit(f"butler: cannot run claude: {e}")


def start_in_new_tab() -> None:
    workspace = os.environ.get("HERDR_WORKSPACE_ID")
    env = [a for k, v in butler_env().items() for a in ("--env", f"{k}={v}")]
    code, made = herdr("tab", "create", "--cwd", str(STATE_HOME), "--label", NAME, "--no-focus", *env,
                       *(["--workspace", workspace] if workspace else []))
    if code:
        sys.exit(f"butler: {made['error']['message']}")
    pane, tab = made["result"]["root_pane"]["pane_id"], made["result"]["tab"]["tab_id"]

    code, started = herdr("agent", "start", NAME, "--kind", "claude", "--pane", pane, "--",
                          *claude_args())
    if code and started["error"]["code"] != "agent_not_ready":
        herdr("tab", "close", tab)
        sys.exit(f"butler: {started['error']['message']}")
    if code:
        print("butler is waiting on a startup dialog in its tab", file=sys.stderr)
    print(f"butler started in {pane} ({tab}), a new tab: this pane could not host it")


def start() -> None:
    STATE_HOME.mkdir(parents=True, exist_ok=True)
    code, found = herdr("agent", "get", NAME)
    if code == 0:
        agent = found["result"]["agent"]
        print(f"butler is running in {agent['pane_id']} ({agent['tab_id']})")
        return
    if pane := own_pane():
        take_over(pane)
    start_in_new_tab()


def version_of(cmd: str) -> tuple[str | None, tuple[int, ...] | None]:
    """`cmd --version`'s first line and the first x.y.z in it, or (None, None) if cmd is missing."""
    if not shutil.which(cmd):
        return None, None
    proc = subprocess.run([cmd, "--version"], capture_output=True, text=True)
    line = next(iter((proc.stdout + proc.stderr).strip().splitlines()), "")
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", line)
    return line, tuple(int(g) for g in m.groups()) if m else None


def trusted(directory: Path) -> bool | None:
    """Has Claude Code's folder-trust dialog been accepted for directory or a parent of it?"""
    try:
        projects = json.loads((Path.home() / ".claude.json").read_text()).get("projects", {})
    except (OSError, json.JSONDecodeError):
        return None
    return any(projects.get(str(d), {}).get("hasTrustDialogAccepted")
               for d in (directory, *directory.parents))


def doctor() -> None:
    """Report what the butler needs on this machine; exit 1 if something required is missing."""
    failed = False

    def line(level: str, what: str, detail: str) -> None:
        nonlocal failed
        failed |= level == "FAIL"
        print(f"{level:<4}  {what:<12} {detail}")

    for cmd, minimum, required in (("herdr", (0, 9, 5), True), ("claude", None, True),
                                   ("codex", None, False)):
        text, version = version_of(cmd)
        if text is None:
            line("FAIL" if required else "info", cmd,
                 "not found" + ("" if required else " (only needed for --agent codex)"))
        elif minimum and (version is None or version < minimum):
            line("FAIL", cmd, f"{text}; need {'.'.join(map(str, minimum))} or later")
        else:
            line("ok", cmd, text)
    line("ok", "butler-task", task_exe())
    line("ok", "BUTLER_HOME", f"{STATE_HOME}" + ("" if STATE_HOME.is_dir() else " (created on first start)"))
    trust = trusted(STATE_HOME)
    if trust is None:
        line("info", "trust", "cannot read ~/.claude.json; answer the folder-trust dialog on first start")
    elif not trust:
        line("warn", "trust", f"{STATE_HOME} is not trusted yet; the first start asks once")
    else:
        line("ok", "trust", "BUTLER_HOME is trusted by Claude Code")
    sys.exit(1 if failed else 0)


def main() -> None:
    p = argparse.ArgumentParser(prog="butler", description="Start the butler, or say where it runs.")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("doctor", help="check this machine for what the butler needs")
    args = p.parse_args()
    doctor() if args.cmd == "doctor" else start()


if __name__ == "__main__":
    main()
