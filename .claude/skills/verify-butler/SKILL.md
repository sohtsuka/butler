---
name: verify-butler
description: Drive a disposable, isolated copy of butler (the butler-task CLI and the butler agent, both on herdr + Claude Code) the way the operator does, and capture proof. Use after changing src/butler/ (task.py, launcher.py, paths.py, data/prompt.md, data/settings.json), or whenever you must show a butler behavior works for real (workers start, report, block, take follow-ups; the butler delegates and resumes).
---

# Verify butler

butler has two surfaces, both terminal-based:

- **CLI:** `butler-task`, which starts Claude Code workers in herdr tabs or worktrees and tracks them in SQLite.
- **Agent:** `bin/butler`, which starts the long-lived butler Claude Code agent in the herdr pane it was run from, or in a new tab when that pane cannot host it. The operator talks to it.

There is no server and no build. The harness is herdr itself, plus the helper `scripts/vb` in this folder.

The operator's own butler is probably running right now, in the same herdr session. Never drive it, and never touch its `BUTLER_HOME` (by default `~/.local/state/butler`). Every run gets its own copy of the app, DB and agent-name namespace.

## How isolation works

- **State:** `butler-task` keeps state in `$BUTLER_HOME/db/butler.db`. `vb` points `BUTLER_HOME` at the run's own `home/`, so each run has its own DB and butler session.
- **Agent names:** herdr names are global. `BUTLER_NAME` namespaces them: the butler agent is `$BUTLER_NAME`, and workers are `$BUTLER_NAME-0001`, and so on. The live instance uses the default, `butler`.
- **Run folder:** each run lives in `<parent of this checkout>/.butler-verify/<run-id>/`, or `$VB_ROOT/<run-id>/` when `VB_ROOT` is set. That parent directory should be trusted in Claude Code (see Gotchas), and the run folder is outside any git repo. Its contents are `app/` (a copy of the working tree's `bin/` and `src/`, so uncommitted edits are verified), `home/` (the run's `BUTLER_HOME`, also its butler's working directory), `git-<id>/` (fixture git repo), `plain-<id>/` (fixture plain dir), `artifacts/` (evidence) and `env`.
- **Workspace:** `launch` creates a dedicated herdr workspace for the run (label `vb-<run-id>`, no focus) and records it in `env` as `RUN_WORKSPACE_ID`. `vb` exports it to its commands as `HERDR_WORKSPACE_ID`, and `butler-task` and `bin/butler` open their tabs there, so plain-dir workers and the run's butler never land next to the operator's own tabs. Worktree workers get their own workspace from `herdr worktree create` as before.
- **Concurrency:** two runs can go side by side. Fixture repo names carry the run id because herdr puts worktrees at `~/.herdr/worktrees/<repo-name>/<branch>`.

## Launch

Run inside herdr (`HERDR_ENV` must be set). From the repo root:

```bash
VB=.claude/skills/verify-butler/scripts/vb
RUN=$($VB launch)          # prints <parent of this checkout>/.butler-verify/<run-id>
source "$RUN/env"          # RUN_ID, BUTLER_NAME, SRC, FIXTURE_GIT, FIXTURE_PLAIN, RUN_WORKSPACE_ID
```

The run is ready once `launch` prints the path. There is no process to wait for. Workers start per task. The run's butler agent starts only when a journey calls `$VB butler "$RUN"`.

## Doctor

```bash
$VB doctor "$RUN"
```

Read-only. Every line must say `ok`:

- **inside herdr**
- **run workspace exists:** fails for a run made before dedicated workspaces. Launch a new run.
- **run workspace is not the caller's**
- **herdr server running**
- **app copy matches working tree:** fails once you edit the source after `launch`. Launch a new run, or re-copy `bin/` and `src/` into `$RUN/app/`.
- **task CLI is the run copy**
- **fixture-git has a commit**
- **agent namespace is not the live butler**

It also prints the herdr and claude versions, the run's DB path, and the run's live agents. Run it first, and again whenever anything looks off.

## Drive

Read `features/README.md`, then the feature file for what you changed. Drive the CLI through `vb run`, which runs the command in `$RUN/app` with the run's env and records it:

```bash
$VB run "$RUN" <artifact-name> -- butler-task <subcommand> ...       # stdin passes through
```

Drive the agent through:

```bash
$VB butler "$RUN"                      # bin/butler from here: piped, so it uses the new-tab fallback
$VB butler-here "$RUN"                 # bin/butler in a shell pane of the run: the pane-takeover path
$VB say    "$RUN" <name> "<text>"      # prompt the run's butler, wait for its turn, save its screen
$VB settle "$RUN" <name> <secs> [max]  # wait until the butler stays idle/done <secs>s (e.g. after a background wake), save screen
```

Raw handles, when you need them:

- `herdr agent get <name>` (`.result.agent.agent_status`, `.pane_id`, `.tab_id`)
- `herdr pane read <pane> --source recent-unwrapped --lines N`
- `herdr agent list`

## Evidence

Everything lands in `$RUN/artifacts/`:

- `<name>.log` holds the command, stdout, stderr and exit code (`vb run`).
- `<name>.screen.txt` is the butler's terminal (`vb say` and `vb settle`).
- `butler-launch.log` is the launch output.

Proof standards:

- **Use the real path.** Real herdr, real `claude` workers, the real `butler-task` subcommands the butler itself calls. No mocked herdr and no direct DB writes. The one exception is reading the DB read-only to confirm a side effect.
- **Capture action and result.** For a task, that is the `butler-task start` log, the `butler-task wait` outcome, and a second view: `butler-task show` with the report, plus the side effect in the fixture (`cat` the file, `git -C "$FIXTURE_GIT" log --all`).
- **Check the agent's side effects too.** For the butler, check what it did, not only what it said: `butler-task list --all` shows the task it created, and the fixture shows the worker's change.

## Cleanup

```bash
$VB cleanup "$RUN"
```

Cleanup does the following:

- Closes every task in the run's DB with `--remove-worktree`.
- Closes the run's butler tab (the shell tab from `butler-here`, or the fallback tab), then the run's dedicated workspace (even if it is empty). It never closes the caller's workspace.
- Closes workspaces whose every pane is inside the run, such as the repo-root workspace that `herdr worktree create` opens. It never closes a workspace that also holds another pane.
- Deletes `app/`, `home/` and the fixtures. It keeps `artifacts/` and `env`.
- Fails loudly if any `$BUTLER_NAME*` agent is still alive.

It kills nothing by process name. Run it after every attempt, failed ones included. Evidence stays in `$RUN/artifacts/`. Delete old run folders by hand when they are no longer needed.

## Gotchas

- **Trust dialog on worktree tasks.** Claude asks for folder trust in a worktree unless the main repo itself is trusted. A fresh `git-<id>` fixture is not trusted, so a worktree task starts `blocked`. Answer with `butler-task keys <id> down enter`, after peeking to confirm it is the trust dialog. That writes a trust entry for the fixture path into `~/.claude.json`, which outlives the run. Do not edit `~/.claude.json` while Claude is running.
- **Plain non-git directories under a trusted parent never prompt.** `plain-<id>` starts straight away. A directory under `/tmp` is untrusted and always prompts, which makes it the blocked journey's fixture.
- **Nothing in a run takes focus.** `bin/butler` takes over the pane it was run from, or opens its fallback tab with `--no-focus`, and when the butler is already running it only prints where it is. `butler-task` always uses `--no-focus`; only `butler-task focus` switches the view, because the human asked for it.
- **Sourcing `env` leaves your own `HERDR_WORKSPACE_ID` alone.** Only commands run through `vb` land in the run workspace. A raw `butler-task` call from your shell opens its tab in your workspace.
- **Workers run in the operator's default permission mode,** auto mode on this machine. A journey that needs a permission prompt cannot rely on one appearing.
