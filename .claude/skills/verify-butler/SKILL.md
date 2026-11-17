---
name: verify-butler
description: Drive a disposable, isolated copy of butler (the bin/task CLI and the bin/butler agent, both on herdr + Claude Code) the way the operator does, and capture proof. Use after changing bin/task, bin/butler, butler/prompt.md or butler/settings.json, or whenever you must show a butler behavior works for real (workers start, report, block, take follow-ups; the butler delegates and resumes).
---

# Verify butler

butler has two surfaces, both terminal-based:

- **CLI:** `bin/task`, which starts Claude Code workers in herdr tabs or worktrees and tracks them in SQLite.
- **Agent:** `bin/butler`, which starts the long-lived butler Claude Code agent in a herdr tab. The operator talks to it.

There is no server and no build. The harness is herdr itself, plus the helper `scripts/vb` in this folder.

The operator's own butler is probably running right now, in the same herdr session. Never drive it, and never touch `/home/satoshi/src/butler/state/`. Every run gets its own copy of the app, DB and agent-name namespace.

## How isolation works

- **State:** `bin/task` keeps state in `<its checkout>/state/butler.db`, so a copy of `bin/` + `butler/` has its own DB.
- **Agent names:** herdr names are global. `BUTLER_NAME` namespaces them: the butler agent is `$BUTLER_NAME`, and workers are `$BUTLER_NAME-0001`, and so on. The live instance uses the default, `butler`.
- **Run folder:** each run lives in `/home/satoshi/src/.butler-verify/<run-id>/`. It is under a Claude-trusted parent (`/home/satoshi/src`) and outside any git repo. Its contents are `app/` (a copy of the working tree, so uncommitted edits are verified), `git-<id>/` (fixture git repo), `plain-<id>/` (fixture plain dir), `artifacts/` (evidence) and `env`.
- **Concurrency:** two runs can go side by side. Fixture repo names carry the run id because herdr puts worktrees at `~/.herdr/worktrees/<repo-name>/<branch>`.

## Launch

Run inside herdr (`HERDR_ENV` must be set). From the repo root:

```bash
VB=.claude/skills/verify-butler/scripts/vb
RUN=$($VB launch)          # prints /home/satoshi/src/.butler-verify/<run-id>
source "$RUN/env"          # RUN_ID, BUTLER_NAME, SRC, FIXTURE_GIT, FIXTURE_PLAIN
```

The run is ready once `launch` prints the path. There is no process to wait for. Workers start per task. The run's butler agent starts only when a journey calls `$VB butler "$RUN"`.

## Doctor

```bash
$VB doctor "$RUN"
```

Read-only. Every line must say `ok`:

- **inside herdr**
- **herdr server running**
- **app copy matches working tree:** fails once you edit the source after `launch`. Launch a new run, or re-copy `bin/` and `butler/` into `$RUN/app/`.
- **task CLI is the run copy**
- **fixture-git has a commit**
- **agent namespace is not the live butler**

It also prints the herdr and claude versions, the run's DB path, and the run's live agents. Run it first, and again whenever anything looks off.

## Drive

Read `features/README.md`, then the feature file for what you changed. Drive the CLI through `vb run`, which runs the command in `$RUN/app` with the run's env and records it:

```bash
$VB run "$RUN" <artifact-name> -- task <subcommand> ...       # stdin passes through
```

Drive the agent through:

```bash
$VB butler "$RUN"                      # bin/butler for this run; puts focus back on your pane
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

- **Use the real path.** Real herdr, real `claude` workers, the real `bin/task` subcommands the butler itself calls. No mocked herdr and no direct DB writes. The one exception is reading the DB read-only to confirm a side effect.
- **Capture action and result.** For a task, that is the `task start` log, the `task wait` outcome, and a second view: `task show` with the report, plus the side effect in the fixture (`cat` the file, `git -C "$FIXTURE_GIT" log --all`).
- **Check the agent's side effects too.** For the butler, check what it did, not only what it said: `task list --all` shows the task it created, and the fixture shows the worker's change.

## Cleanup

```bash
$VB cleanup "$RUN"
```

Cleanup does the following:

- Closes every task in the run's DB with `--remove-worktree`.
- Closes the run's butler tab.
- Closes workspaces whose every pane is inside the run, such as the repo-root workspace that `herdr worktree create` opens. It never closes a workspace that also holds another pane.
- Deletes `app/` and the fixtures. It keeps `artifacts/` and `env`.
- Fails loudly if any `$BUTLER_NAME*` agent is still alive.

It kills nothing by process name. Run it after every attempt, failed ones included. Evidence stays in `/home/satoshi/src/.butler-verify/<run-id>/artifacts/`. Delete old run folders by hand when they are no longer needed.

## Gotchas

- **Trust dialog on worktree tasks.** Claude asks for folder trust in a worktree unless the main repo itself is trusted. A fresh `git-<id>` fixture is not trusted, so a worktree task starts `blocked`. Answer with `task keys <id> down enter`, after peeking to confirm it is the trust dialog. That writes a trust entry for the fixture path into `~/.claude.json`, which outlives the run. Do not edit `~/.claude.json` while Claude is running.
- **Plain non-git directories under a trusted parent never prompt.** `plain-<id>` starts straight away. A directory under `/tmp` is untrusted and always prompts, which makes it the blocked journey's fixture.
- **`$VB butler` steals focus.** `bin/butler` focuses the new tab, and `vb butler` puts focus back on the caller's pane. Raw `bin/butler` does not.
- **Workers run in the operator's default permission mode,** auto mode on this machine. A journey that needs a permission prompt cannot rely on one appearing.
