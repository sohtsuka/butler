# Follow-up

After a worker reports, the butler can send it another instruction in the same session. `task send` clears the previous report, so the next `task wait` waits for the new round instead of returning the old report.

## Sub-features

- `send-accepted` makes `task send <id> TEXT` print `sent to task <id>` and moves the worker to `working`.
- `send-clears-report` gives `reported_at` no value after the send, and `task show` shows no report until the worker reports again.
- `send-rewait` makes `task wait` block until the new report lands, then print `reported`.

## How to get to it (user POV)

- The operator tells the butler "also do X" about a finished task. The butler runs `bin/task send <id> "..."` and re-arms `bin/task wait <id>`.

## Driving it with vb

Preconditions:

- Baseline from the README.
- Task 1 is `reported` from task-lifecycle `start-plain`, and `$FIXTURE_PLAIN/marker.txt` exists.

- **Send.** Run `$VB run "$RUN" fu-send -- task send 1 "Append a second line 'round two' to marker.txt."`. It exits 0 and prints `sent to task 0001`.
- **Old report cleared.** Run `$VB run "$RUN" fu-show-cleared -- task show 1`. There is no `reported_at:` line and no `--- report ---` section.
- **Wait for the new round.** Run `$VB run "$RUN" fu-wait -- task wait 1 --timeout 300`. It prints `reported` only after the worker has worked, which takes seconds and is not instant.
- **Side effect.** Run `$VB run "$RUN" fu-marker -- cat "$FIXTURE_PLAIN/marker.txt"`. The output is two lines, `verified by vb` and `round two`.

## Gotchas

- `task send` is refused while the worker is `blocked`. Answer the dialog first (blocked-worker).
- If the worker never reports again, the earlier report is gone. That is by design, and `task wait` then ends `stopped-without-report`.
