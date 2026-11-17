# Blocked worker

A worker that stops at a dialog (folder trust, a permission request, a question) is `blocked`. `task wait` returns `blocked` at once so the butler can tell the human. Text prompts are refused while a dialog is open, and `task keys` relays the human's answer as key presses.

## Sub-features

- `blocked-detect` makes `task start` warn `blocked at startup` and makes `task wait` print `blocked`.
- `blocked-peek` shows the dialog text with `task peek`.
- `blocked-send-refused` makes `task send` exit non-zero with a hint to use `task keys`.
- `blocked-answer` uses `task keys <id> down enter` to accept, after which the worker runs its original kickoff and reports.
- `blocked-decline` uses `task keys <id> enter` on the default "No, exit" choice to quit the worker. `task wait` then prints `stopped-without-report` or `gone`.

## How to get to it (user POV)

- The butler is woken with `blocked`, peeks, tells the human what is asked, and relays the answer with `task keys`.
- The human switches to the worker with `task focus <id>` and answers there.

## Driving it with vb

Preconditions:

- Baseline from the README.
- An untrusted plain dir: `UNTRUSTED=$(mktemp -d /tmp/vb-untrusted.XXXXXX)`. `/tmp` is not under any trusted parent.

- **Start blocked.** Run `$VB run "$RUN" bl-start -- task start --title "Untrusted" --cwd "$UNTRUSTED" --no-worktree` with stdin `Goal: create ok.txt containing "ok".`. It exits 0, prints the id, and stderr says `is blocked at startup`.
- **Wait sees it.** Run `$VB run "$RUN" bl-wait -- task wait <id> --timeout 60`. It prints `blocked` immediately.
- **Peek.** Run `$VB run "$RUN" bl-peek -- task peek <id>`. The output contains the folder-trust question and the choices `No, exit` / `Yes, I trust this folder`.
- **Text refused.** Run `$VB run "$RUN" bl-send -- task send <id> "hello"`. It exits non-zero, and stderr names `bin/task keys`.
- **Decline (default).** Run `$VB run "$RUN" bl-decline -- task keys <id> enter`, then `$VB run "$RUN" bl-wait2 -- task wait <id> --timeout 60`. It prints `stopped-without-report` or `gone`, `ok.txt` does not exist, and no trust entry is added.
- **Close.** Run `$VB run "$RUN" bl-close -- task close <id>`. It exits 0 and the task disappears from `task list`.
- **Answer instead (optional).** Repeat with a new `UNTRUSTED` dir and `task keys <id> down enter`. `task wait` prints `reported` and `ok.txt` contains `ok`. Only do this when the journey needs it, because it leaves a trust entry in `~/.claude.json`.

## Gotchas

- The trust dialog defaults to "No, exit". A bare `enter` declines it.
- Re-arming `task wait` on a still-blocked task returns `blocked` again at once. Wait only after answering.
- `task keys` waits up to 5s for the worker to leave `blocked`, then prints `now <state>`. If it still says `blocked`, the keys did not reach the dialog, so peek again.
- Remove the `/tmp/vb-untrusted.*` dirs yourself, because `vb cleanup` only removes what is inside the run folder.
