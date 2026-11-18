# butler

人間と話してタスクを管理するだけの、常駐 Claude Code エージェント「butler（執事）」です。butler 自身は作業をしません。実作業はすべて、butler が [herdr](https://herdr.dev) 経由で起動する新しい worker（既定は Claude Code、指定すれば OpenAI Codex）が行います。

## インストール

前提は [herdr](https://herdr.dev) 0.9.5 以上、Claude Code（`claude`）、Python 3.10 以上、[uv](https://docs.astral.sh/uv/) です。Codex worker を使うなら Codex CLI も入れてログインしておきます。

レビュー済みのコミットに固定して入れます（タグやブランチは動くので、SHA を推奨します）。

```bash
uv tool install git+https://github.com/sohtsuka/butler@<コミット SHA>
butler doctor   # herdr / claude / codex の有無と版、BUTLER_HOME と信頼状態を確認
```

`butler`、`butler-task`、`butler-board` が `~/.local/bin` に入ります。更新は差分を確認してから、新しい SHA で `uv tool install --force git+https://github.com/sohtsuka/butler@<新しい SHA>` を実行します。自動更新はしません。アンインストールは `uv tool uninstall butler` です（状態は `BUTLER_HOME` に残ります）。

### バージョンの確認

```
$ butler --version
butler 0.1.0 (4871fd6)
```

`butler-task --version` と `butler-board --version` も同じ形式です。括弧内はインストール元のコミットで、`uv tool install git+…@<SHA>` で入れた場合は uv が記録した SHA、チェックアウトの `bin/` から動かした場合はその `HEAD` です（未コミットの変更があれば `-dirty` が付きます）。`butler doctor` は SHA を省略せずに表示するので、同じものを入れ直すときに使えます。

バージョン番号は `src/butler/__init__.py` の `__version__` だけで管理しています（`pyproject.toml` はそこから読みます）。

## 状態の置き場所（BUTLER_HOME）

タスクの DB（`db/butler.db`）と butler のセッション ID（`butler-session-id`）は `BUTLER_HOME` に置きます。既定は `${XDG_STATE_HOME:-~/.local/state}/butler` です。インストール先やチェックアウトには何も書きません。

- DB は専用の `db/` に置き、所有者だけが読めるようにします（`db/` は 0700。新しく作る `BUTLER_HOME` も 0700）。以前の版が `BUTLER_HOME` 直下に置いていた `butler.db` は、次に `butler-task` を実行したときに `db/` へ移ります。移す瞬間に動いている worker がいない時に更新すると安全です。

- `BUTLER_HOME` は butler の作業ディレクトリでもあります。Claude Code は会話をこのディレクトリに紐づけて保存するので、変えると butler の会話は新しくなります（タスクは DB に残り、起動時に一覧が注入されます）。
- 初回起動時、Claude Code がこのディレクトリのフォルダ信頼を一度だけ聞きます。
- worker には `butler-task --home <BUTLER_HOME>` の形でコマンドを渡すので、worker の環境に `BUTLER_HOME` がなくても同じ DB に報告します。

## 起動

```bash
butler
```

- herdr の中で実行します。butler がまだいなければ、`butler` を実行した「いまいるペイン」でそのまま起動します（`butler` 自身が claude に置き換わります）。新しいタブは増えません。すでにいれば、何も起動せずにその場所（pane とタブ）を表示します。
- そのペインを渡せないとき（herdr の外、出力をパイプした、そのペインに別のエージェントがいる = Ctrl+Z で中断中のものも含む、別のエージェントの下から実行した = そのペインの前景が自分でない）は、従来どおり新しいタブで起動します。どちらの経路を通ったかは出力メッセージに出ます。タブで起動した場合、`butler` は画面を切り替えないので、butler のタブへは手で移動してください。
- herdr のエージェント名（`$BUTLER_NAME`、既定は `butler`）は、ペインで起動したときは claude が立ち上がってから 1 秒ほど遅れて付きます（`butler` が切り離した `herdr agent rename` の再試行ループが付けます。herdr は、まだ見えていない agent に名前を予約できません）。
- セッション ID は `$BUTLER_HOME/butler-session-id` に固定されています。2 回目以降の起動は `--resume` で同じ会話を再開します。
- butler の役割は起動フラグ（`--append-system-prompt-file`、`--settings` でパッケージ内の `data/prompt.md` と `data/settings.json` を渡す）だけで与えます。普通に Claude Code を開いても butler にはなりません。
- チェックアウトから直接動かすときは `bin/butler` と `bin/butler-task` を使います（インストール不要、中身は `src/butler/`）。

## タスクの流れ

1. 人間が butler に頼みます。
2. butler がブリーフを書いて `butler-task start` を実行します。cwd が git リポジトリ内なら worktree（ブランチ `butler/<id>-<slug>`）を作り、そうでなければ新しいタブで worker を起動します。
3. butler は `butler-task wait <id>` をバックグラウンドで実行して待ちます。
4. worker は `butler-task brief <id>` でブリーフを読んで作業し、終わったら `butler-task report <id>` に報告を渡します。
5. wait が終わると butler が起こされ、報告を要約して人間に伝えます。worker がダイアログで止まった（`blocked`）場合は、何を聞かれているかを伝えます。

タスクの状態は `$BUTLER_HOME/db/butler.db`（SQLite）にあります。状態は保存せず、`closed_at`、報告の有無、herdr 上の pane の状態から毎回導出します。

現在の状態とは別に、起きたこと（起動、追加指示、報告、blocked とその解消、停止、close）を `events` 表に追記していきます。追加指示で消える前回の報告も、ここには残ります。blocked / 解消 / 停止は herdr を見ている `butler-task wait` と `butler-task keys` が記録するので、誰も wait していない間の変化は、次に wait したときの時刻で記録されます。

## コマンド

| コマンド | 内容 |
| --- | --- |
| `butler-task start --title T --cwd DIR [--no-worktree] [--agent claude\|codex] < brief` | worker を起動し、タスク ID を表示 |
| `butler-task wait <id> [--timeout S]` | `reported` / `blocked` / `stopped-without-report` / `gone` まで待つ |
| `butler-task list [--all] [--for-hook]` | タスク一覧（`--all` で close 済みも表示） |
| `butler-task show <id>` | 記録、ブリーフ、報告を表示（worktree の git の参照先が書き換えられていれば警告） |
| `butler-task timeline [<id>] [--all] [--limit N]` | 起動、追加指示、各回の報告、blocked / 解消、close の履歴を時刻順に表示（`--all` で close 済みも） |
| `butler-task peek <id> [--lines N]` | worker の画面の末尾を表示 |
| `butler-task send <id> TEXT` | 待機中の worker に追加の指示を送る（前回の報告はクリアされる） |
| `butler-task keys <id> KEY...` | worker のダイアログにキーを送る（例: `down enter`、`esc`） |
| `butler-task focus <id>` | worker のペインに画面を切り替える |
| `butler-task close <id> [--remove-worktree]` | タブまたは workspace を閉じる。worktree は指定時のみ削除し、ブランチは残す |
| `butler-task brief <id>` / `butler-task report <id>` | worker 用。ブリーフを読む / 報告を標準入力から保存する |

## タスク状態の画面

```bash
butler-board [--host 127.0.0.1] [--port 8765] [--allow-host NAME]... [--home DIR]
```

checkout から直接動かすときは `bin/board` です。読む DB は `BUTLER_HOME`（`--home` で指定も可）の `db/butler.db` です。

起動時に表示される `http://127.0.0.1:8765/?token=…` をブラウザで開くと、タスクの状態を一覧できます。3 秒ごとに更新されます。トークンは初回に `$BUTLER_HOME/board-token`（0600）に作られ、再起動しても変わらないのでブックマークできます。開くとトークンは Cookie に置き換わり、アドレスバーからは消えます。作り直すときはこのファイルを消してください。

- 上部のバーに、open なタスクのうち作業中、blocked、報告済み、停止・不明の件数を出します。blocked があるとタブのタイトルにも件数が出ます。
- タイムラインは 1 タスク 1 行で、作業中（青）、blocked（赤）、報告後（緑）の区間と、追加指示（紫）や報告（緑）の印を時間軸に並べます。範囲は 1 時間から 7 日まで選べます。
- 下の 3 列は「この 15 分 / 1 時間 / 4 時間」に起きたことです。「確認した」を押した時刻はブラウザに保存され、「前回からの変化」でそれ以降の出来事を強調します。
- 行や出来事を選ぶと、ブリーフ、各回の報告、追加指示の全文が右に出ます。「読み込む」で worker の画面の末尾（`butler-task peek` 相当）も見られます。

画面は読むだけです。DB は読み取り専用で開き、herdr には pane の状態と画面の末尾を問い合わせるだけで、herdr の表示は切り替えません。既定では 127.0.0.1 でだけ待ち受け、Host ヘッダが `127.0.0.1` か `localhost` 以外のリクエストは断ります。127.0.0.1 には同じマシンの他のユーザーも届くので、トークン（または Cookie）のないリクエストも断ります。

WSL などで別の場所から開きたいときは `--host 0.0.0.0` で全インターフェースに出せます。Windows のブラウザで `http://localhost:8765/` を開くなら、それだけで通ります。WSL の IP アドレスやホスト名で開くなら、その名前を `--allow-host` で足してください（例: `--allow-host 172.20.1.2`）。トークンは平文の HTTP で流れるので、通信を見られる人にはブリーフ、報告、worker の画面の末尾が読めます。信頼できないネットワークでは使わないでください。

## 注意

- 信頼済みでないフォルダでは、worker が起動時のフォルダ信頼ダイアログで `blocked` になります。`butler-task focus` で答えるか、butler に `butler-task keys` で代わりに答えてもらいます。答えたあとは最初の指示がそのまま実行されます。
- worktree の worker は、元のリポジトリ自体が Claude Code で信頼済みなら確認なしで起動します。親ディレクトリが信頼済みなだけでは足りません。
- butler には `Bash(butler-task:*)` と `Bash(herdr:*)` を事前許可しています。herdr のコマンドは承認なしで何でも実行できるので、butler に読ませる内容（worker の報告や画面）に悪意のある指示が混ざるプロンプトインジェクションには注意してください。worker 側は各エージェント自身の権限設定に従います。

## Codex worker

`--agent codex` を付けると worker を Codex CLI で起動します（herdr の `agent start --kind codex`）。butler には「Codex でやって」と頼むか、Claude worker の成果に別の目でレビューがほしいときに使わせます。それ以外のコマンドは Claude worker と同じです。

- Codex CLI がインストール済みでログイン済み（ChatGPT サブスクリプションか API キー）であることが前提です。
- Codex は `--sandbox workspace-write` で起動し、`--add-dir $BUTLER_HOME/db` で butler の DB も書き込み可能にします。`BUTLER_HOME` 自体は butler の Claude Code の作業ディレクトリなので渡しません（`.claude/settings.json` を置かれると、butler の起動時にフックがサンドボックスの外で動くため）。これで `butler-task report` を承認なしで実行できます。それ以外の操作で承認を求められると worker は `blocked` になり、Claude worker と同じく `butler-task peek` / `keys` / `focus` で対応します。
- worktree では、コミットに必要な `.git` 内のディレクトリ（worktree 自身の gitdir と `objects`、`refs`、`logs`）も `--add-dir` で渡すので、コミットも承認なしで通ります。`hooks` と `config` は読み取り専用のままです。push など、それ以外への書き込みは承認待ちになります。
- worktree の gitdir は書き込めるので、worker が `.git` や `commondir` を別の場所に向けたり `config.worktree` を足したりすると、操作者がその worktree で次に実行する `git` が、仕込まれた `core.fsmonitor` などを動かしえます。`butler-task show` と `close` はこれらをファイルとして読んで確かめ（git は実行しません）、書き換えがあれば `WARNING: do not run git in …` と表示します。その worktree では git を実行しないでください。どこまでを想定済みのリスクとしているかは [DESIGN.md](DESIGN.md) にあります。
- 初めてのリポジトリでは Codex 自身のフォルダ信頼画面で `blocked` になります。Claude と違い `enter` だけで「Trust and continue」になります。信頼は `~/.codex/config.toml` に残ります。
- 承認ポリシー、モデルなどは `~/.codex/config.toml` の設定に従います。

## テスト

`butler-task` の回帰テスト。herdr も操作者の DB も触らないので、そのまま走らせて構いません。

```
python3 -m unittest discover -s tests
```

`butler-task` 自体の回帰テストのほか、`BUTLER_HOME` の解決（`tests/test_paths.py`）も含みます。実機での確認は `.claude/skills/verify-butler` の方です。こちらは単体テストだけです。

## 以前の構成（チェックアウト内の state/）からの移行

以前は状態をチェックアウトの `state/` に置いていました。新しい `BUTLER_HOME` へファイルを移せば、タスクはそのまま引き継がれます。

```bash
mkdir -p ~/.local/state/butler
mv ~/src/butler/state/butler.db* ~/.local/state/butler/
```

`butler-session-id` は移しません。butler の作業ディレクトリが変わるので前の会話は再開できず、新しい会話で始まります。開いているタスクは起動時の一覧注入で butler に伝わります。

## ライセンス

[Apache License 2.0](LICENSE)
