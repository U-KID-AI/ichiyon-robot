# AI開発エージェント向けルール

- 変更前に既存実装と関連ドキュメントを調査する。
- 推測でファイル構成、API、本番状態を決めない。確認できない事項は要確認とする。
- AI専用のbranch/worktreeで作業する。
- 無関係な変更を混ぜない。
- 変更後は関連テスト、compile、checkを実行し、結果を確認する。
- `.env`、secrets、SSH鍵、Token、Cookie、その他の認証情報を読まない、表示しない、コミットしない。
- 本番デプロイ、本番再起動、本番DB migrationを勝手に行わない。
- Minecraft本番worldを勝手に変更しない。
- Discordから任意のshellコマンドをそのまま実行する設計にしない。許可された固定操作と入力検証を使う。
- 危険操作や外部副作用のある操作は人間承認を要求する。

調査・編集・検証はAI専用worktreeで行う。commit、push、Draft PRは `docs/AI_RULES.md` で許可された場合のみ行う。mergeとproduction操作は行わない。
