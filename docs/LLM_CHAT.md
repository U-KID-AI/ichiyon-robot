# レイチェル竈門にぼのWebhook会話

いちよんロボの非AI機能は維持し、AI会話だけを専用Webhookへ移行する。
対象はランセ地方（Guild `1392174489609179327`）の内村家 `1569571831894114354` のみ。
人格資料の著者は `699881916282699876`。人間・Bot・Webhookを別のIDとして扱う。
旧いちよん人格、旧会話履歴、旧設定、旧追加チャンネルは読み込まない。

## 設定と停止

ホスト `/home/ubuntu/ichiyon-shared/secrets/rachel_config.json` がコンテナの
`/app/secrets/rachel_config.json` にread-onlyでマウントされる。
`RACHEL_CONFIG_PATH` 以外の旧 `LLM_CONFIG_PATH` / `llm_config.json` は参照しない。
設定は約2秒で再読込し、送信直前にも確認する。`enabled: false` でAIだけ停止する。
ファイル欠落・不正設定・人格不足時は停止し、旧人格やBot名義の返信へ切り替えない。

```json
{
  "enabled": false,
  "channel_id": "1569571831894114354",
  "guild_id": "1392174489609179327",
  "webhook_id": "CONFIGURED_WEBHOOK_ID",
  "history_after": "0",
  "model": "gemini-3.1-flash-lite",
  "thinking_level": "low",
  "reply_chars": 1600,
  "max_output_tokens": 4096,
  "concurrency": 1,
  "queue_size": 5,
  "history_size": 16,
  "rag_count": 4,
  "timeout": 25,
  "user_interval": 5,
  "requests_per_minute": 14,
  "requests_per_day": 450,
  "tokens_per_minute": 225000,
  "key_path": "/app/secrets/gemini_api_key",
  "persona_path": "/app/secrets/rachel_corpus.sqlite3",
  "webhook_path": "/app/secrets/rachel_webhook.json"
}
```

有効化には実Webhook ID、非空の人格ガイド・資料、正の `history_after` が必要。
`history_after` は切替時刻のDiscord Snowflakeを文字列で設定し、それ以前の会話を読み込まず、遅延受信・待機中の要求も生成前に除外する。
再起動ごとに更新しない。旧AI停止を先に行い、新AIが未準備でも旧AIを復活させない。
利用Tier・課金リスク・実API動作を確認するまで本番自動応答は有効にしない。
2026-11-10に運用者がAI Studioで確認したDefault Gemini Projectの3.1 Flash-Lite Free枠は15 RPM / 500 RPD / 250,000入力TPM。アプリは14 RPM / 450 RPD / 225,000入力TPMで余裕を残す。実際のキーとの対応も有効化前に確認する。
別キー等による同じプロジェクトの使用量は完全には観測できない。

## Gemini呼び出しと利用枠

送信先は `https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent`。
要求モデルは原則 `gemini-3.1-flash-lite`、`thinkingConfig.thinkingLevel=low`。
最新の運用者指定により3.5/3.6から変更した。アプリのモデル自動フォールバックはない。
応答 `modelVersion` が指定の3.1 Flash-Lite以外なら返信せず失敗する。
`temperature`、`topP`、`topK`、`thinkingBudget` は送らない。
出力4096トークン・Discord本文1600文字は上限であり目標長ではない。

API試行はPostgreSQLで原子的に予約し、60秒のRPM/入力TPMと米国太平洋時間0時リセットのRPDを保持する。
入力TPMをJSON UTF-8バイト数+128で保守的に予約し、応答後に実値へ置換する。
429/RESOURCE_EXHAUSTEDは即再試行せず、QuotaFailureとRetryInfo/Retry-Afterから待機する。
判明したGoogle上限があれば95%とアプリ設定の小さい値を使う。
不明な429は60秒から最大1時間の待機、日次上限は次の太平洋時間0時まで待機し、自動復帰する。
5xx/通信障害のみ最大1回再試行する。利用上限時は次の固定文をWebhookで一度だけ送信する。

```text
現在、会話機能の利用上限に達しています。時間をおいてお試しください。
```

本文を通常ログやDBへ保存しない。要求モデル、応答modelVersion、推論レベル、入力・出力・思考・合計トークンをログに記録する。
思考トークンが応答にない場合はログ上0として扱う。DBのoutput_tokensはcandidatesTokenCountのみ。
API/返信メタデータは7日を過ぎたものから少量ずつ失効させ、人格原本・索引には触らない。
料金・モデル枠は[Google料金表](https://ai.google.dev/gemini-api/docs/pricing)と対象プロジェクトのAI Studioで確認する。
応答のserviceTier=standardだけでプロジェクトがFreeかPaidかを判定しない。

## 人格資料

提供済みTXT 51,974件を独立した `rachel_corpus.sqlite3` に保存する。原本は変更しない。
`python scripts/llm_corpus_import.py SOURCE --output NEW.sqlite3 --expected-count 51974`
は日時順、重複ID、件数、UTF-8、複数行・空本文・添付注記・返信先と原文復元一致を検証する。
原本に著者ID欄がないため、運用者指定の著者IDを来歴として記録する。
他者の発言を推測で補わず、単独投稿を会話ペアに変換しない。

索引のmetadataに私的なguide・guide_sha256・根拠IDと分類別の少数予備例を持つ。
ガイドはこの著者の資料から作成し、旧人格の設定を流用しない。
全件の本文・日時・サーバー・チャンネル・メッセージID・返信先・原ヘッダーを保存する。
空本文、URL、コマンド、引用等も保持するがFew-shotでは通常会話を優先する。
明らかな物真似の自己紹介は引用として減点する。分類はヒューリスティックであり完全ではない。

日本語bigram/単語のSQLite FTS5+BM25で候補48件と小さな分類別集合を検索し、話題・場面・長さを加点する。
同文や書き出しの重複を抑え、最大4例を渡す。全件常駐・毎回全走査・Embeddingモデルは使わない。
SQLite/ランキング期限500ms、非同期待機750ms、接続キャッシュ2MiB。検索はワーカースレッドで行う。
一致不足・期限超過時は独立したレイチェルの予備例とガイドを使用する。
Bot生成文を人格資料へ取り込む経路はない。

JSON資料も `author_id: "699881916282699876"`, `source: "direct_provision"`, `guide`,
`examples: [{"context": "実際に提供された相手発言", "reply": "本人発言"}]` で登録できる。
旧著者のJSON/SQLiteは拒否する。索引・原本・私的ガイド・評価例をGitへコミットしない。

## 受信・履歴・Webhook

コマンドを優先した後、内村家の通常の人間メッセージをメンションなしで受け付ける。
他チャンネル、DM、スレッド、Bot、自他Webhookは起動条件から除外する。
直近16件・本文合計6000文字を目安に、発言者ID、表示名、順序、返信先を渡す。
このWebhookの送信済み投稿は履歴に含め、別Webhook・旧Bot投稿・切替前の履歴は除外する。
取得結果はメッセージIDで重複除去する。本文を永続記憶・会話要約として別途保存しない。

専用Webhookは明示的に作成・保存したIDで管理し、起動時には作成しない。
秘密JSONはid/channel_id/name/avatar/tokenを持ち、モード600で保管する。
名前は「レイチェル竈門にぼ」、指定JPEGをデフォルトavatarへ登録する。
投稿時username/avatar_urlは上書きせず、allowed_mentionsのparseを空にする。
wait=trueの確定応答から投稿ID・Webhook ID・チャンネルを検証する。
タイムアウト・権限失敗・結果不明時に自動再送やBot名義への代替送信をしない。
確定送信後のメタデータ保存失敗も追加のエラー投稿に変えない。
HTTPライブラリのURLログを抑止し、例外全文を通常ログへ出さない。

## 検証と配備

`check_llm_chat.py`、`check_llm_corpus.py`、`check_llm_limits.py`、`check_llm_rachel.py`、
`check_message_router.py` を使う。DB試験はLLM_TEST_DATABASE_URLの使い捨てDBのみ。
実APIは少数、Webhook試験文は「AI会話テスト」と明示し、内村家に限定する。
既存immutable Compose adapterで配備し、成功receipt、稼働SHA、Bot/admin/DBを確認する。
共有secrets変更は配備ロック内で行い、バックアップと競合させない。
復旧は `rachel_config.json` のenabled=falseを優先し、非AI機能を維持する。
旧コードへの復旧が必要でも旧 `llm_config.json` はdisabledのまま保持する。