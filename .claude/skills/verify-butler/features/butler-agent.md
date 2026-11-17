# Butler agent

`bin/butler` runs one long-lived Claude Code agent, the butler, in its own herdr tab. Its role comes from `butler/prompt.md` and `butler/settings.json`. It talks with the human, turns requests into `bin/task` tasks, waits on them in the background, and summarizes reports. Its session id is fixed, so a relaunch resumes the same conversation. A SessionStart hook re-injects open tasks after startup, resume or compaction.

## Sub-features

- `launch` makes `bin/butler` start the agent `$BUTLER_NAME` in a new tab with `--session-id <uuid>`, and writes `state/butler-session-id`.
- `running` makes a second `bin/butler` start nothing, print `butler is running in <pane> (<tab>)` and exit 0, without moving focus, so there is still exactly one `$BUTLER_NAME` agent.
- `resume` makes `bin/butler`, after the tab is closed, start again with `--resume <same uuid>` and keep the earlier conversation.
- `role` makes the butler say it delegates through `bin/task` and does not edit files. Edit and Write are denied.
- `delegate` turns a request into a task. The butler arms `task wait` in the background, is woken by it, and summarizes the report.
- `hook` makes `bin/task list --for-hook` print the open-task block, with re-arm lines for working or idle tasks and a separate blocked line.

## How to get to it (user POV)

- The operator runs `bin/butler` in herdr and types to the butler.
- After a restart or compaction, the butler sees the hook's open-task block at the top of its context.

## Driving it with vb

Preconditions:

- Baseline from the README.
- No agent named `$BUTLER_NAME` (`$VB doctor` lists run agents).

- **Launch.** Run `$VB butler "$RUN"`. `artifacts/butler-launch.log` says `butler started in <pane> (<tab>)`, and `herdr agent get "$BUTLER_NAME"` has `agent_status` `idle`. `$RUN/app/state/butler-session-id` holds a uuid.
- **Running, not duplicate.** Run `$VB butler "$RUN"` again. It prints `butler is running in <pane> (<tab>)`, focus stays where it was, and `herdr agent list` shows exactly one `$BUTLER_NAME`.
- **Role.** Run `$VB say "$RUN" ag-role "あなたの役割は？短く答えて。"`. The screen shows a Japanese answer saying it manages tasks, delegates through `bin/task`, and does not edit files.
- **Delegate.** Run `$VB say "$RUN" ag-ask "$FIXTURE_PLAIN に butler.txt を作って中身を 'via butler' にしてください。worktree は不要です。"`, then `$VB settle "$RUN" ag-summary 20`. `task list --all` (via `vb run`) shows a new task whose cwd is `$FIXTURE_PLAIN`. `cat "$FIXTURE_PLAIN/butler.txt"` prints `via butler`. `ag-summary.screen.txt` shows the butler summarizing the report after a background wake.
- **Resume.** Close the butler's tab with `herdr tab close "$(herdr agent get "$BUTLER_NAME" | jq -r .result.agent.tab_id)"`, then run `$VB butler "$RUN"` and `$VB say "$RUN" ag-resume "さっき私が最初に聞いた質問は何でしたか？"`. Then run `ps -o args= -p "$(herdr pane process-info --pane "$(herdr agent get "$BUTLER_NAME" | jq -r .result.agent.pane_id)" | jq -r '.result.process_info.foreground_processes[0].pid')" > "$RUN/artifacts/ag-resume-args.txt"`. The args contain `--resume <the uuid in $RUN/app/state/butler-session-id>`, and the answer quotes the role question.
- **Hook.** Run `$VB run "$RUN" ag-hook -- env CLAUDE_PROJECT_DIR="$RUN/app" "$RUN/app/bin/task" list --for-hook`. It prints `[butler] Open tasks ...` with each open task, or `No open tasks`.

## Gotchas

- A finished butler turn shows `done` (sometimes `idle`), and `vb settle` counts both as quiet. The quiet window must outlast the gap between the butler handing off and the waiter waking it. 20s covered a one-file task: hand-off, wake and summary all came within about 40s. Raise it for longer briefs. `settle` gives up after 600s by default (4th argument).
- After its summary the butler may ask whether to close the task. Claude Code can show a greyed suggested reply in the input box. It is not typed, so do not submit it.
- The butler's tab is in the run's dedicated workspace and opens without focus, so the operator's view never moves. To look at it, switch to the `vb-<run-id>` workspace by hand.
- A session killed before its first turn has no transcript, so the next launch uses `--session-id` again instead of `--resume`. Prompt it once before testing resume.
- Compaction itself cannot be forced from outside. `hook` proves the re-injected text, and `resume` proves the matcher path with `startup|resume`.
