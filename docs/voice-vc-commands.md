# 音声機能: VC入退室とローカル音声再生

Botへのメンション付きメッセージで、Discord VCへの入退室と `assets/audio` 配下のローカル音声ファイル再生を行います。

## 入室

- `@Bot 入って`
- `@Bot 来て`
- `@Bot 参加`
- `@Bot vc入って`
- `@Bot ボイス入って`

実行者がVCにいない場合は、先にVCへ入るよう案内します。Botが別VCにいる場合は、実行者のVCへ移動します。

## 退出

- `@Bot 出て`
- `@Bot 抜けて`
- `@Bot 退出`
- `@Bot vc出て`
- `@Bot ボイス出て`

BotがVC未接続の場合は、その旨を返します。

## 音声一覧

- `@Bot 音声一覧`
- `@Bot ボイス一覧`
- `@Bot sound list`

`assets/audio` 直下の `.mp3` / `.wav` / `.ogg` を一覧表示します。実音声ファイルはGit管理しません。

## ローカル音声再生

- `@Bot 鳴らして <name>`
- `@Bot 再生 <name>`
- `@Bot ボイス <name>`
- `@Bot sound <name>`

`<name>` は拡張子あり/なしの両方に対応します。例: `assets/audio/test.mp3` は `test` または `test.mp3` で指定できます。

BotがVC未接続の場合は、先にVCへ呼ぶよう案内します。再生中の場合、Phase 2ではキューに入れず「現在再生中です。」を返します。

## 反応への音声再生

Phase 3では、自動反応とメンション反応に音声ファイルを紐づけられます。Botが対象サーバーのVCに接続中で、かつ再生中でない場合だけ、既存のテキスト/画像反応に加えて `assets/audio` 配下の音声を再生します。

自動反応は `reactions.audio_config_json`、メンション反応は既存の `mention_reactions.config_json` に以下のどちらかの形で設定します。管理画面の本格UIは後続Phaseで追加します。

```json
{"audio_file": "test.mp3"}
```

```json
{"voice": {"audio_file": "test.mp3"}}
```

VC未接続、再生中、ファイル未存在、再生開始失敗の場合、音声だけスキップします。ユーザー向けには毎回通知せず、既存のテキスト/画像反応は通常通り動きます。

## 停止

- `@Bot 止めて`
- `@Bot 停止`
- `@Bot stop`

再生中の音声だけ停止します。VC退出はしません。

## 依存関係

Discord VC接続のため `PyNaCl` が必要です。ローカル音声再生は `discord.FFmpegPCMAudio` を使うため、Dockerイメージには `ffmpeg` を入れます。

## 手動確認

1. `assets/audio/test.mp3` などの短い音声ファイルをローカルまたはサーバー上に配置します。
2. BotをDiscordサーバーに参加させます。
3. 実行者がVCに入ります。
4. テキストチャンネルで `@Bot 入って` を送ります。
5. `@Bot 音声一覧` でファイルが見えることを確認します。
6. `@Bot 鳴らして test` で再生されることを確認します。
7. `@Bot 止めて` で停止できることを確認します。
8. `@Bot 抜けて` でBotが退出することを確認します。

ログには `guild_id`、`channel_id`、`bot_instance_id`、`filename` が出ます。Tokenなどの秘密値は出しません。
