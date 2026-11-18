# Blocked worker

A worker that stops at a dialog (folder trust, a permission request, a question) is `blocked`. `butler-task wait` returns `blocked` at once so the butler can tell the human. Text prompts are refused while a dialog is open, and `butler-task keys` relays the human's answer as key presses.

## Sub-features

- `blocked-detect` makes `butler-task start` warn `blocked at startup` and makes `butler-task wait` print `blocked`.
- `blocked-peek` shows the dialog text with `butler-task peek`.
- `blocked-send-refused` makes `butler-task send` exit non-zero with a hint to use `butler-task keys`.
- `blocked-answer` uses `butler-task keys <id> down enter` to accept, after which the worker runs its original kickoff and reports.
- `blocked-decline` uses `butler-task keys <id> enter` on the default "No, exit" choice to quit the worker. `butler-task wait` then prints `stopped-without-report` or `gone`.

## How to get to it (user POV)

- The butler is woken with `blocked`, peeks, tells the human what is asked, and relays the answer with `butler-task keys`.
- The human switches to the worker with `butler-task focus <id>` and answers there.

## Driving it with vb

Preconditions:

- Baseline from the README.
- An untrusted plain dir: `UNTRUSTED=$(mktemp -d /tmp/vb-untrusted.XXXXXX)`. `/tmp` is not under any trusted parent.

- **Start blocked.** Run `$VB run "$RUN" bl-start -- butler-task start --title "Untrusted" --cwd "$UNTRUSTED" --no-worktree` with stdin `Goal: create ok.txt containing "ok".`. It exits 0, prints the id, and stderr says `is blocked at startup`.
- **Wait sees it.** Run `$VB run "$RUN" bl-wait -- butler-task wait <id> --timeout 60`. It prints `blocked` immediately.
- **Peek.** Run `$VB run "$RUN" bl-peek -- butler-task peek <id>`. The output contains the folder-trust question and the choices `No, exit` / `Yes, I trust this folder`.
- **Text refused.** Run `$VB run "$RUN" bl-send -- butler-task send <id> "hello"`. It exits non-zero, and stderr names `butler-task keys`.
- **Decline (default).** Run `$VB run "$RUN" bl-decline -- butler-task keys <id> enter`, then `$VB run "$RUN" bl-wait2 -- butler-task wait <id> --timeout 60`. It prints `stopped-without-report` or `gone`, `ok.txt` does not exist, and no trust entry is added.
- **Close.** Run `$VB run "$RUN" bl-close -- butler-task close <id>`. It exits 0 and the task disappears from `butler-task list`.
- **Answer instead (optional).** Repeat with a new `UNTRUSTED` dir and `butler-task keys <id> down enter`. `butler-task wait` prints `reported` and `ok.txt` contains `ok`. Only do this when the journey needs it, because it leaves a trust entry in `~/.claude.json`.

## Gotchas

- The trust dialog defaults to "No, exit". A bare `enter` declines it.
- Re-arming `butler-task wait` on a still-blocked task returns `blocked` again at once. Wait only after answering.
- `butler-task keys` waits up to 5s for the worker to leave `blocked`, then prints `now <state>`. If it still says `blocked`, the keys did not reach the dialog, so peek again.
- Remove the `/tmp/vb-untrusted.*` dirs yourself, because `vb cleanup` only removes what is inside the run folder.
