# AI task worktreeの読み取り専用検査

Python 3.11以上とPATH上のGitがあるローカル環境で実行する。runner設定、DB接続、remote接続は不要。

```text
python scripts/inspect_ai_task_worktree.py --repo "C:\path\to\ai-task-<UUID>"
python scripts/inspect_ai_task_worktree.py --repo /path/to/checkout
python scripts/inspect_ai_task_worktree.py --repo /path/to/checkout --pretty
python scripts/inspect_ai_task_worktree.py --help
```

通常のcheckoutとlinked worktree（`.git`がファイルの構成）を扱う。`--repo`は必須で、checkout内のサブディレクトリも指定できる。成功時は終了コード0で標準出力へJSONを返す。

既定の出力は改行を末尾に付けた1行のコンパクトなJSON。`--pretty`を指定すると、同じデータを2スペースのインデントで整形する。キー、値、秘密値のredactionはどちらも同じで、変わるのは空白と改行だけ。`--help`はrepositoryを指定せずに利用できる。

コンパクト形式の例:

```json
{"branch": "ai/task/<UUID>", "head": "0123456789abcdef0123456789abcdef01234567", "changes": []}
```

`--pretty`形式では、変更一覧も以下のように読みやすく表示する:

```json
{
  "branch": "ai/task/<UUID>",
  "head": "0123456789abcdef0123456789abcdef01234567",
  "changes": [
    {
      "status": "??",
      "path": "new.txt"
    },
    {
      "status": " D",
      "path": "deleted.txt"
    },
    {
      "status": "R ",
      "path": "renamed.txt",
      "original_path": "old.txt"
    }
  ]
}
```

`branch`は現在のbranch名（detached HEADでは`null`）、`head`はcommit SHA（初回commit前は`null`）。`changes`はGit porcelain v1の2文字の`status`とrepository rootからの相対`path`を持つ。1文字目はindex、2文字目はworktreeの状態。未追跡ファイルは`??`、追加は`A`、削除は`D`、renameは`R`で表す。rename/copyには`original_path`も付く。rename判定はGitに任せ、未追跡ファイルはディレクトリ単位で省略しない。ignoredファイルは含めない。

NUL区切りで解析するため空白、日本語、改行を含むpathもJSONとして保持する。共通の`redact_secrets`を出力文字列とエラーに適用するため、秘密値と一致するbranch/pathは`[redacted]`等へ置換される。

Git操作失敗、存在しないpath、Git未導入、bare repository等は終了コード1で標準エラーへ`{"error": "診断"}`を返す。引数エラーはargparseのusageと終了コード2。Gitコマンドごとのtimeoutは30秒。

CLIは`git --no-optional-locks`で参照とstatusのみを読み、indexの任意更新を抑止する。ファイル編集、stage、commit、fetch、公開、migration実行は行わない。複数のGit呼び出しの間に別processが変更した場合、結果は単一時点のatomic snapshotではない。アプリのDocker imageにはGitを追加していないため、Gitとcheckoutがある開発環境で利用する。

検証は一時的な実Git repositoryとlinked worktree内で行い、運用データを使用しない。

```text
python scripts/check_ai_task_worktree_inspection.py
```

このcheckerは両形式のデータ一致、出力の整形、`--help`でのオプション説明も検証する。`.github/workflows/checks.yml`のAI task execution checksではcheckerとCLIの`--help` smoke checkを実行する。
