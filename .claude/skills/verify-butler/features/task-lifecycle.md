# Task lifecycle

A task is one unit of delegated work. `task start` takes a brief on stdin and starts a Claude Code worker in a new herdr tab (plain dir) or in a new git worktree workspace (git repo). The worker reads its brief with `task brief` and reports through `task report`. `task wait` returns `reported` once that report is stored.

## Sub-features

- `start-plain` starts a worker in a new tab of the current workspace for a non-git dir, or with `--no-worktree`.
- `start-worktree` starts a worker in a new worktree workspace on branch `butler/<id>-<slug>` when `--cwd` is in a git repo.
- `wait-reported` returns `reported` once the worker's report is in the DB.
- `show` prints the record, the brief and the report.
- `list` lists open tasks with their derived state. `--all` includes closed ones.
- `close` closes the tab or workspace and marks the task closed. It is idempotent, and `--remove-worktree` also removes the worktree. The branch is always kept.

## How to get to it (user POV)

- The operator asks the butler, and the butler runs `bin/task start ... <<'EOF'` (see butler-agent).
- A developer runs `bin/task start --title T --cwd DIR [--no-worktree] < brief` directly.

## Driving it with vb

Preconditions:

- Baseline from the README. `$FIXTURE_PLAIN` is empty, and `$FIXTURE_GIT` has exactly one commit, `init`.

- **Start in a plain dir.** Run `$VB run "$RUN" lc-start-plain -- task start --title "Write marker" --cwd "$FIXTURE_PLAIN" --no-worktree` with stdin `Goal: create marker.txt in your working directory containing exactly the line "verified by vb".`. It exits 0 and prints `0001`. `herdr agent list` shows `$BUTLER_NAME-0001` in a new tab of `$HERDR_WORKSPACE_ID`.
- **Wait.** Run `$VB run "$RUN" lc-wait-plain -- task wait 1 --timeout 300`. It prints `reported`, observed in about 10 to 30s.
- **Second view.** Run `$VB run "$RUN" lc-show-plain -- task show 1` and `$VB run "$RUN" lc-marker -- cat "$FIXTURE_PLAIN/marker.txt"`. `show` reports `state: reported` with a `--- report ---` section, and the file prints `verified by vb`.
- **Start in a worktree.** Run `$VB run "$RUN" lc-start-wt -- task start --title "Add hello" --cwd "$FIXTURE_GIT"` with stdin `Goal: add hello.txt containing "hello" and commit it with message "add hello".`. It prints `0002`. If stderr says `blocked at startup`, peek with `task peek 2`. When it shows the folder-trust dialog, answer it with `$VB run "$RUN" lc-trust -- task keys 2 down enter` (see Gotchas).
- **Wait in the worktree.** Run `$VB run "$RUN" lc-wait-wt -- task wait 2 --timeout 300`. It prints `reported`.
- **Side effect in git.** Run `$VB run "$RUN" lc-git -- git -C "$FIXTURE_GIT" log --oneline --all`. Branch `butler/0002-add-hello` has a commit `add hello` and `main`/`master` does not.
- **List.** Run `$VB run "$RUN" lc-list -- task list`. Both tasks show `reported`, and task 2's location column is its branch.
- **Close.** Run `$VB run "$RUN" lc-close -- task close 1`, then run it again, then run `$VB run "$RUN" lc-close-wt -- task close 2 --remove-worktree`. Every call exits 0. `task list` prints `no tasks`, `task list --all` shows both as `closed`, and the worktree path from `task show 2` no longer exists.

## Gotchas

- A fresh `$FIXTURE_GIT` is not trusted by Claude, so the worktree worker usually starts `blocked` at the trust dialog. Answering it writes a lasting trust entry for that path into `~/.claude.json`.
- `task start` returns after about 10s even if the worker is still on its first turn. That is expected. Judge only by `task wait`.
- `herdr worktree create` opens a repo-root workspace for the fixture once. `task close` does not close it, and `vb cleanup` does.
- The branch survives `close --remove-worktree` by design. Assert on the branch, not on the worktree dir, after close.
