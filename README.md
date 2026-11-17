# butler

人間と話してタスクを管理するだけの、常駐 Claude Code エージェント「butler（執事）」です。butler 自身は作業をしません。実作業はすべて、butler が [herdr](https://herdr.dev) 経由で起動する新しい worker（既定は Claude Code、指定すれば OpenAI Codex）が行います。

## 起動

```bash
bin/butler
```

- herdr の中で実行します。butler がまだいなければ、`bin/butler` を実行した「いまいるペイン」でそのまま起動します（`bin/butler` 自身が claude に置き換わります）。新しいタブは増えません。すでにいれば、何も起動せずにその場所（pane とタブ）を表示します。
- そのペインを渡せないとき（herdr の外、出力をパイプした、そのペインに別のエージェントがいる = Ctrl+Z で中断中のものも含む、別のエージェントの下から実行した = そのペインの前景が自分でない）は、従来どおり新しいタブで起動します。どちらの経路を通ったかは出力メッセージに出ます。タブで起動した場合、`bin/butler` は画面を切り替えないので、butler のタブへは手で移動してください。
- herdr のエージェント名（`$BUTLER_NAME`、既定は `butler`）は、ペインで起動したときは claude が立ち上がってから 1 秒ほど遅れて付きます（`bin/butler` が切り離した `herdr agent rename` の再試行ループが付けます。herdr は、まだ見えていない agent に名前を予約できません）。
- セッション ID は `state/butler-session-id` に固定されています。2 回目以降の起動は `--resume` で同じ会話を再開します。
- butler の役割は起動フラグ（`--append-system-prompt-file butler/prompt.md`、`--settings butler/settings.json`）だけで与えます。このリポジトリで普通に Claude Code を開いても butler にはなりません。

## タスクの流れ

1. 人間が butler に頼みます。
2. butler がブリーフを書いて `bin/task start` を実行します。cwd が git リポジトリ内なら worktree（ブランチ `butler/<id>-<slug>`）を作り、そうでなければ新しいタブで worker を起動します。
3. butler は `bin/task wait <id>` をバックグラウンドで実行して待ちます。
4. worker は `bin/task brief <id>` でブリーフを読んで作業し、終わったら `bin/task report <id>` に報告を渡します。
5. wait が終わると butler が起こされ、報告を要約して人間に伝えます。worker がダイアログで止まった（`blocked`）場合は、何を聞かれているかを伝えます。

タスクの状態は `state/butler.db`（SQLite）にあります。状態は保存せず、`closed_at`、報告の有無、herdr 上の pane の状態から毎回導出します。

現在の状態とは別に、起きたこと（起動、追加指示、報告、blocked とその解消、停止、close）を `events` 表に追記していきます。追加指示で消える前回の報告も、ここには残ります。blocked / 解消 / 停止は herdr を見ている `bin/task wait` と `bin/task keys` が記録するので、誰も wait していない間の変化は、次に wait したときの時刻で記録されます。

## コマンド

| コマンド | 内容 |
| --- | --- |
| `bin/task start --title T --cwd DIR [--no-worktree] [--agent claude\|codex] < brief` | worker を起動し、タスク ID を表示 |
| `bin/task wait <id> [--timeout S]` | `reported` / `blocked` / `stopped-without-report` / `gone` まで待つ |
| `bin/task list [--all] [--for-hook]` | タスク一覧（`--all` で close 済みも表示） |
| `bin/task show <id>` | 記録、ブリーフ、報告を表示 |
| `bin/task timeline [<id>] [--all] [--limit N]` | 起動、追加指示、各回の報告、blocked / 解消、close の履歴を時刻順に表示（`--all` で close 済みも） |
| `bin/task peek <id> [--lines N]` | worker の画面の末尾を表示 |
| `bin/task send <id> TEXT` | 待機中の worker に追加の指示を送る（前回の報告はクリアされる） |
| `bin/task keys <id> KEY...` | worker のダイアログにキーを送る（例: `down enter`、`esc`） |
| `bin/task focus <id>` | worker のペインに画面を切り替える |
| `bin/task close <id> [--remove-worktree]` | タブまたは workspace を閉じる。worktree は指定時のみ削除し、ブランチは残す |
| `bin/task brief <id>` / `bin/task report <id>` | worker 用。ブリーフを読む / 報告を標準入力から保存する |

## タスク状態の画面

```bash
bin/board [--port 8765]
```

ブラウザで http://127.0.0.1:8765/ を開くと、タスクの状態を一覧できます。3 秒ごとに更新されます。

- 上部のバーに、open なタスクのうち作業中、blocked、報告済み、停止・不明の件数を出します。blocked があるとタブのタイトルにも件数が出ます。
- タイムラインは 1 タスク 1 行で、作業中（青）、blocked（赤）、報告後（緑）の区間と、追加指示（紫）や報告（緑）の印を時間軸に並べます。範囲は 1 時間から 7 日まで選べます。
- 下の 3 列は「この 15 分 / 1 時間 / 4 時間」に起きたことです。「確認した」を押した時刻はブラウザに保存され、「前回からの変化」でそれ以降の出来事を強調します。
- 行や出来事を選ぶと、ブリーフ、各回の報告、追加指示の全文が右に出ます。「読み込む」で worker の画面の末尾（`bin/task peek` 相当）も見られます。

画面は読むだけです。DB は読み取り専用で開き、herdr には pane の状態と画面の末尾を問い合わせるだけで、herdr の表示は切り替えません。127.0.0.1 でだけ待ち受け、Host ヘッダが `127.0.0.1` か `localhost` 以外のリクエストは断ります。

## 注意

- 信頼済みでないフォルダでは、worker が起動時のフォルダ信頼ダイアログで `blocked` になります。`bin/task focus` で答えるか、butler に `bin/task keys` で代わりに答えてもらいます。答えたあとは最初の指示がそのまま実行されます。
- worktree の worker は、元のリポジトリ自体が Claude Code で信頼済みなら確認なしで起動します。親ディレクトリが信頼済みなだけでは足りません。

## Codex worker

`--agent codex` を付けると worker を Codex CLI で起動します（herdr の `agent start --kind codex`）。butler には「Codex でやって」と頼むか、Claude worker の成果に別の目でレビューがほしいときに使わせます。それ以外のコマンドは Claude worker と同じです。

- Codex CLI がインストール済みでログイン済み（ChatGPT サブスクリプションか API キー）であることが前提です。
- Codex は `--sandbox workspace-write` で起動し、`--add-dir state/` で butler の DB も書き込み可能にします。これで `bin/task report` を承認なしで実行できます。それ以外の操作で承認を求められると worker は `blocked` になり、Claude worker と同じく `bin/task peek` / `keys` / `focus` で対応します。
- worktree では、コミットに必要な `.git` 内のディレクトリ（worktree 自身の gitdir と `objects`、`refs`、`logs`）も `--add-dir` で渡すので、コミットも承認なしで通ります。`hooks` と `config` は読み取り専用のままです。push など、それ以外への書き込みは承認待ちになります。
- 初めてのリポジトリでは Codex 自身のフォルダ信頼画面で `blocked` になります。Claude と違い `enter` だけで「Trust and continue」になります。信頼は `~/.codex/config.toml` に残ります。
- 承認ポリシー、モデルなどは `~/.codex/config.toml` の設定に従います。

## テスト

`bin/task` の回帰テスト。herdr も操作者の DB も触らないので、そのまま走らせて構いません。

```
python3 -m unittest discover -s tests
```

実機での確認は `.claude/skills/verify-butler` の方です。こちらは単体テストだけです。
