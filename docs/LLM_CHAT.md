# いちよんロボのGemini会話

専用チャンネルは `1568969564769747006`、Guildは `1515983621461245972`。
人格資料の対象IDは `748965361486921831`。本人から直接提供された資料を使用する。
資料未登録時は暫定会話となる。過去ログ収集や人格資料への自動追加は実装しない。

## 本番設定

稼働コンテナの `/app/secrets` は `/home/ubuntu/ichiyon-shared/secrets` をread-onlyでマウントする。
ホスト側で `llm_config.json` を編集する。設定は約2秒で再読込し、送信直前にも確認する。
`enabled` を `false` にするとBot再起動なしでLLMだけ停止できる。
設定なし、不正設定、キー読み取り失敗でも通常Botは起動する。

```json
{
  "enabled": true,
  "channel_id": 1568969564769747006,
  "guild_id": 1515983621461245972,
  "model": "gemini-3.1-flash-lite",
  "reply_chars": 400,
  "concurrency": 1,
  "queue_size": 5,
  "history_size": 16,
  "rag_count": 4,
  "timeout": 25,
  "user_interval": 5,
  "requests_per_minute": 5,
  "requests_per_day": 100,
  "max_output_tokens": 600,
  "key_path": "/app/secrets/gemini_api_key",
  "persona_path": "/app/secrets/llm_persona.json"
}
```

E2.1.Microでは同時生成数の許容値を1に制限する。キューは最大5、120秒より古い待機を破棄する。
API試行数はリトライを含めPostgreSQLで予約し、ローリング1分/24時間の上限を再起動後も維持する。
429時は5分間休止し、5xxと通信障害のリトライは1回に制限する。
Geminiプロジェクト側の上限はAI Studioで確認する。上記設定はアプリ側の上限であり、無料課金区分を変更する機能ではない。
モデルの自動切替はない。無料枠で利用するGoogleプロジェクトの課金設定は運用者が管理する。

## 本人資料の登録

本人が提供したJSONをホストの非公開領域に置く。形式は次のとおり。
`guide` は本人作成の口調ガイド、`context` は返答の場面、`reply` は本人の発言。
ガイドのみの登録も可能。以下の文字列は形式を示すプレースホルダー。

```json
{
  "author_id": "748965361486921831",
  "source": "direct_provision",
  "guide": "本人提供の口調ガイド",
  "examples": [
    {"context": "相手の発言または場面", "reply": "本人提供の発言"}
  ]
}
```

稼働releaseのPython環境で `scripts/llm_persona_import.py INPUT --output OUTPUT` を実行する。
コンテナのsecretsは読み取り専用なので、ホスト側Pythonから出力する。
本番OUTPUTは `/home/ubuntu/ichiyon-shared/secrets/llm_persona.json`。
インポートは全件置換。以前の資料を必要に応じて非公開の復旧領域に保持する。
重複を除外し、別人IDや不正な形式を拒否する。最大2000例、各返信1000文字、ガイド6000文字、合計20万文字。
資料を削除・修正した場合も次の生成で反映する。本人発言やキーをGitに追加しない。

検索は日本語の文字bigramと単語の転置索引、質問・冗談・挨拶・感情・雑談の意図加点を使う。
候補を制限し、短すぎる発言を減点する。採用した本人replyと場面をFew-shotとして渡す。
資料が増えた後の口調評価は別途実際の本人資料で行う。

## 会話と障害

直近16件をDiscordから読み、発言者ID、発言順、返信先をモデルへ渡す。
順番待ち中に生成されたBotの返答も含める。別ユーザーの後続発言は先行要求へ混入させない。
本文は各800文字に制限する。Bot/Webhook投稿を起動条件にしない。生成返信のメンションは無効。
会話全文を通常ログやDBへ保存しない。`llm_requests` は重複防止、API試行数、トークン数、遅延、返信IDのみ保持する。
送信結果不明時に再送しない設計のため、クラッシュ時の一部要求は未返信になる。
再起動時に過去の要求を再生しない。

検証は `python scripts/check_llm_chat.py` と既存ルーターcheckを実行する。
本番は既存immutable Compose adapterでmigrationを適用し、既存backupと直前releaseを復旧点とする。
最初の切り戻し手段は `llm_config.json` の `enabled: false`。
