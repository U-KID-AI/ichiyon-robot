# 読み上げと音声ミキサー

この機能は、Discord VCに接続中のBotが、紐づけられたテキストチャンネルの通常投稿をVOICEVOX Engineで読み上げるためのものです。

## 起動構成

現在、本番の読み上げは一時停止中です。Bot実行時は `TTS_RUNTIME_ENABLED=true` を明示した場合だけ読み上げを受け付けます。VOICEVOX EngineはComposeの `voicevox` profileで起動します。

```powershell
docker compose --profile bot --profile voicevox up -d --build voicevox-engine bot
```

イルシアも同時に使う場合は `irsia` profileも併用します。

```powershell
docker compose --profile bot --profile irsia --profile voicevox up -d --build voicevox-engine bot bot-irsia
```

`voicevox-engine` はComposeネットワーク内だけで使い、ホストへ50021を公開しません。

## 環境変数

- `VOICEVOX_ENGINE_IMAGE`: VOICEVOX EngineのDocker image。既定は `voicevox/voicevox_engine:cpu-ubuntu24.04-0.25.0`
- `VOICEVOX_ENGINE_URL`: Botから見たEngine URL。既定は `http://voicevox-engine:50021`
- `VOICEVOX_TIMEOUT_SECONDS`: `/audio_query` と `/synthesis` のtimeout秒数。既定は30秒
- `TTS_RUNTIME_ENABLED`: 読み上げランタイムの有効化フラグ。未設定または `false` では、管理画面設定がONでもBotは読み上げません。

## Discord操作

- `@Bot もしもししよ`: VCへ参加し、そのメッセージのチャンネルを読み上げ対象にします。
- `@Bot 読み上げ停止`: 読み上げだけ停止し、読み上げキューを破棄します。音楽やVC接続は止めません。
- `@Bot 読み上げ開始`: 現在のチャンネルを読み上げ対象にして、以後の通常投稿だけ読み上げます。
- `@Bot 二度と来るな`: VC退出し、読み上げセッションも破棄します。

音楽URLやYouTube N連でBotがVCへ自動参加した場合も、参加を起こしたテキストチャンネルを読み上げ対象にします。すでにVC接続中の別チャンネルから音楽を追加しても、読み上げ対象チャンネルは変更しません。

## 読み上げ対象

読み上げるのは、対象チャンネルの人間による通常投稿だけです。

読み上げないもの:

- Bot、Webhook、システム投稿
- Botへのメンションコマンド
- VC操作、音楽URLだけの投稿、音楽操作コマンド
- 空投稿、コードブロックだけの投稿

本文内のURLは `URL`、メンションは `メンション`、カスタム絵文字は `絵文字` に短縮します。添付だけの場合、画像は `画像`、その他は `ファイル` と読み上げます。

## 管理画面設定

管理画面のサーバー機能一覧に「読み上げ」を追加しています。設定は `bot_id + guild_id` 単位です。

- 読み上げON/OFF
- VC参加時に読み上げを初期ONにするか
- speaker ID / 表示名
- 読み上げ音量
- 読み上げ速度
- ユーザー別ピッチ差
- 最大文字数
- キュー上限
- ダッキングON/OFF
- ダッキング時の音楽ゲイン、attack/release
- クレジット表記メモ

## 音声ミキサー

Discordへの出力は1つのMixer AudioSourceにまとめます。

- Music Bus: YouTube、Spotify解決後YouTube、YouTube N連など既存音楽
- TTS Bus: VOICEVOX読み上げ

読み上げ失敗はその読み上げだけをスキップし、音楽キューや再生位置には影響しません。音楽スキップも読み上げキューを消しません。

## ダッキング

初期状態ではOFFです。ONにすると、TTS出力中だけ音楽音量へ設定倍率を掛け、attack/releaseで滑らかに戻します。倍率は設定済みの音楽音量に対する相対値です。

## クレジット

VOICEVOXで生成した音声を利用する場合、VOICEVOXを利用したことが分かるクレジット表記が必要です。利用する話者ごとの利用規約も確認してください。
