# AI task worktree inspection

`scripts/inspect_ai_task_worktree.py` は通常のcheckoutとtask専用linked worktreeを読み取り専用で確認するCLIです。Python 3.11とPATH上のGitが必要です。runner設定、DB接続、外部サービスは不要です。

```text
python scripts/inspect_ai_task_worktree.py --repo "C:\path\to\ai-task-<UUID>"
python scripts/inspect_ai_task_worktree.py --repo .
```

成功時は終了コード0でJSONをstdoutへ出力します。

```json
{
  "branch": "ai/task/<UUID>",
  "head": "0123456789012345678901234567890123456789",
  "changes": [
    {"status": " M", "path": "Dockerfile"},
    {"status": "??", "path": "scripts/new.py"},
    {"status": " D", "path": "docs/obsolete.md"},
    {"status": "R ", "path": "docs/new.md", "original_path": "docs/old.md"}
  ]
}
```

`status` はGit porcelain v1の2文字（index、working treeの順）です。パスはrepository rootからの相対パスで、rename/copyでは`path`が移動先、`original_path`が移動元です。NUL区切りで取得するため空白や改行を含む名前も扱います。untrackedはファイル単位で表示し、ignoredは表示しません。rename判定にはGitの検出結果を使います。

detached HEADでは`branch`がnull、初回commit前は`head`がnullになります。変更がなければ`changes`は空配列です。検査失敗時は終了コード1で`{"error": "..."}`をstderrへ出力します。引数の誤りはargparseの終了コード2です。出力値と操作エラーには既存の`redact_secrets`を適用するため、秘密値に一致したbranch名やパスも伏せ字になります。

Gitの任意のlock/index更新は`--no-optional-locks`で無効にします。stage、commit、checkout、公開、migration実行は行いません。複数のGit読み取りの間に他のprocessが変更した場合、結果は単一時点のsnapshotを保証しません。

実Gitの一時repositoryを使う検証（実運用データは使用しません）:

```text
python scripts/check_ai_task_worktree_inspection.py
```
