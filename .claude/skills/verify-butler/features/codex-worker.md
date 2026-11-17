# Codex worker

`task start --agent codex` runs the worker on Codex CLI (`herdr agent start --kind codex`) instead of Claude Code. The kickoff rides in Codex's argv like Claude's, and Codex starts with `--sandbox workspace-write --add-dir <app>/state`, so its `task report` can write the butler's DB without an approval. Every other subcommand is agent-agnostic.

## Sub-features

- `start-codex-plain` starts a Codex worker in a new tab of the run's workspace for a plain dir.
- `start-codex-worktree` starts a Codex worker in a worktree and it commits on its branch.
- `wait-codex-reported` returns `reported` once the Codex worker's report is in the DB.
- `agent-column` shows the worker's agent in `task list`, `task show` and the SessionStart hook block.
- `send-codex` sends a follow-up to an idle Codex worker and waits for its new report.
- `butler-picks-codex` makes the butler pass `--agent codex` when the human asks for Codex.
- `migration` opens a DB made before the `agent` column, and its tasks show as `claude`.

## How to get to it (user POV)

- The operator tells the butler "Codex でやって" (or asks for a second opinion), and the butler runs `bin/task start --agent codex ...`.
- A developer runs `bin/task start --agent codex --title T --cwd DIR < brief` directly.

## Driving it with vb

Preconditions:

- Baseline from the README. `codex --version` works and `codex login status` says logged in, on the machine that runs herdr. `vb doctor` prints the Codex version.

- **Start in a plain dir.** Run `$VB run "$RUN" cx-start-plain -- task start --agent codex --title "Codex marker" --cwd "$FIXTURE_PLAIN" --no-worktree` with stdin `Goal: create codex.txt in your working directory containing exactly the line "verified by codex".`. It prints `0001`. `herdr agent list` shows `$BUTLER_NAME-0001` with kind codex in the run's workspace. If stderr says `blocked at startup`, peek and answer the dialog as in Gotchas.
- **Wait.** Run `$VB run "$RUN" cx-wait-plain -- task wait 1 --timeout 600`. It prints `reported`.
- **Second view.** `task show 1` has `agent: codex` and a report, and `cat "$FIXTURE_PLAIN/codex.txt"` prints `verified by codex`.
- **Follow-up.** Run `task send 1 "Append a second line: round 2"`, then `task wait 1 --timeout 600`. It prints `reported` and the file has two lines.
- **Worktree.** Run `task start --agent codex --title "Codex hello" --cwd "$FIXTURE_GIT"` with stdin `Goal: add hello.txt containing "hello" and commit it with message "codex hello".`, then `task wait 2 --timeout 600`. Branch `butler/0002-codex-hello` has the commit.
- **List.** `task list` shows both tasks with `codex` in the agent column.
- **Butler.** Start the run's butler, `$VB say "$RUN" cx-ask "Codex で $FIXTURE_PLAIN に butler.txt を作って、中身は ok だけ"`, then `$VB settle`. `task list --all` shows a new task with agent `codex`.
- **Close.** `task close <id> --remove-worktree` for each.

## Gotchas

- Codex shows its own folder-trust screen for a directory it has not trusted, so a fresh fixture may start `blocked`. Peek first, then answer with `task keys <id> ...` only what the screen offers. Trust is recorded in `~/.codex/config.toml` and outlives the run.
- Codex in a worktree writes the commit into the main repo's `.git/worktrees/...`, which is outside its cwd. If the sandbox refuses the commit, Codex asks for approval and the task goes `blocked`. Record what you saw.
- Codex cannot run inside the cloud container (OpenAI hosts are blocked there). Run this feature on the operator's PC.
