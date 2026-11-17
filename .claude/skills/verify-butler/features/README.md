# butler verification map

This directory is the maintained source for verifying butler's user-facing behavior. Read the index before driving, then use the matching feature file as the recipe.

## Baseline preconditions

- A fresh run from `$VB launch`, with `source "$RUN/env"` done and `$VB doctor "$RUN"` all `ok`.
- `VB=.claude/skills/verify-butler/scripts/vb`, run from the repo root.
- You are inside herdr. The operator's live butler (agent `butler`) and its `state/` are never touched.

## Driving conventions

- Every `bin/task` call goes through `$VB run "$RUN" <artifact-name> -- task ...` so it is recorded.
- Every conversation with the run's butler goes through `$VB say`, and waits on background wakes through `$VB settle`.
- Task ids in a fresh run start at `1`. `bin/task` prints them zero-padded (`0001`) and accepts either form.
- Peek before pressing keys. Press only what the step names.
- Run `$VB cleanup "$RUN"` at the end of every run, passing or failing.

## Proof and skip reporting

- CLI proof is the `<name>.log` from `vb run`: command, stdout, stderr and exit code.
- A task is proven by its `task wait` outcome, plus `task show` with the report, plus the side effect in the fixture.
- Agent proof is a `<name>.screen.txt`, plus a second view of what it did (`task list --all`, the fixture).
- Record which feature ID each artifact proves.
- Report an unreachable step with the command you tried and the unmet precondition. Do not report it as verified through a different entry point.

## Feature entry contract

Each feature file starts with an H1 and one paragraph, then four H2s in this order: `Sub-features`, `How to get to it (user POV)`, `Driving it with vb`, `Gotchas`.

## Features

- [Task lifecycle](./task-lifecycle.md) covers starting a worker in a plain dir or a worktree, waiting for its report, showing, listing and closing.
- [Blocked worker](./blocked-worker.md) covers a worker stuck at a dialog: detection, peek, refused text, answering with keys, and closing.
- [Follow-up](./follow-up.md) covers sending a finished worker another instruction and waiting for its new report.
- [Butler agent](./butler-agent.md) covers launching, focusing and resuming the butler, its role, delegating a request end to end, and the SessionStart hook.
