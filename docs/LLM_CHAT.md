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

## 提供済みTXTの全件索引

大きな提供済みエクスポートには `scripts/llm_corpus_import.py SOURCE --output NEW.sqlite3 --expected-count COUNT` を使う。
原本のUTF-8、日時順、ID重複、件数、本文とヘッダーの復元一致を検証する。出力は新規ファイルに限定する。
全件の本文・原ヘッダー・添付注記・日時・サーバー・チャンネル・返信先IDをSQLiteへ保存する。
原本に著者ID欄がない場合は、運用者が指定した本人IDを来歴として記録する。返信先の他者本文は補完しない。
JSON資料形式も引き続き利用できる。

本番の索引は `/home/ubuntu/ichiyon-shared/secrets/llm_persona_corpus_v2.sqlite3`。
`llm_config.json` の `persona_path` を `/app/secrets/llm_persona_corpus_v2.sqlite3` に設定すると読み込む。
既存索引を更新する場合は別名で新規作成・検証し、設定を原子的に切り替える。直前の索引を復旧用に保持する。
インポート時の `.report.json` には発言例が含まれるため、索引・原本と同じ非公開領域に保存する。

索引は日本語bigram・単語のSQLite FTS5とBM25を使う。明示的なゲーム名を話題フィルターへ分離し、
質問への応答、挨拶、短い反応などの場面と本文の一致を再評価する。分類はヒューリスティックで、
引用・コピペや質問への回答であることを断定するものではない。
空本文やURL・コマンド等も保存し、Few-shotでは通常会話を優先する。
通常検索候補はFTS上位48件と小さな分類別候補に限定する。全件のPythonオブジェクト常駐や毎回の全件走査は行わない。
SQLiteの進捗ハンドラーとランキングの期限は500ms、非同期側の待機上限は750ms。
超過時は口調ガイドで生成する。SQLiteページキャッシュは接続当たり2MiB、接続は検索後に閉じる。
検索・索引ロードはワーカースレッドで実行する。

口調ガイドは非公開SQLiteの `metadata` テーブルの `guide` に最大6000文字で登録する。
ガイドをsystem instructionへ、検索した本人発言をFew-shotデータとして渡す。
現在の会話は本文合計6000文字、Few-shotは1件300文字、初期設定4件に制限し、実トークン数を記録する。
過去のゲーム環境や人名を現在の事実として扱うことを避ける。
検証には `python scripts/check_llm_corpus.py` も使用する。

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
