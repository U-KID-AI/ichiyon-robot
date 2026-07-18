# 音声機能: VC入退室とローカル音声再生

Botへのメンション付きメッセージで、Discord VCへの入退室と `assets/audio` 配下のローカル音声ファイル再生を行います。

## 簡略音楽再生

BotをVCに入れた状態なら、`歌え` などのキーワードなしでリンクだけでも音楽再生できます。

- `@Bot <YouTubeまたはSpotifyリンク>`
- `@Bot https://youtu.be/...`
- `@Bot https://www.youtube.com/watch?v=...`
- `@Bot https://open.spotify.com/track/...`
- `@Bot https://open.spotify.com/album/...`
- `@Bot spotify:track:...`
- `@Bot spotify:album:...`

メンションとリンクの間には空白や短い文章を入れられます。例: `@Bot これ流して https://youtu.be/...`。従来の `@Bot 歌え <URL>` 形式も引き続き使えます。BotがVCにいない場合は自動参加せず、先に `@Bot もしもししよ` で呼んでください。

## 入室

- `@Bot もしもししよ`

実行者がVCにいない場合は、先にVCへ入るよう案内します。Botが別VCにいる場合は、実行者のVCへ移動します。

## 退出

- `@Bot 二度と来るな`

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

## URL音楽再生

- `@Bot 歌え <URL>`
- `@Bot 流して <URL>`
- `@Bot 音楽 <URL>`
- `@Bot play <URL>`

実行者がVCにいる場合、Botが未接続ならそのVCへ接続して再生します。再生中の場合はguild単位のキューに追加します。URL情報の取得には `yt-dlp` を使います。

YouTube側の確認要求で取得できない場合は、サーバー上にcookiesファイルを配置し、`.env` に `YTDLP_COOKIES_FILE=/app/secrets/youtube-cookies.txt` のように設定します。cookieファイルはGit管理しません。実行時は読み取り専用の `/app/secrets` から `/tmp` へコピーした一時ファイルを `yt-dlp` に渡します。playlist付きURLは1曲再生のため展開しません。

## Spotifyリンク再生

同じURL音楽再生コマンドでSpotifyの曲/アルバムリンクを受け付けます。

- `@Bot 歌え https://open.spotify.com/track/...`
- `@Bot 歌え https://open.spotify.com/album/...`
- `@Bot 歌え spotify:track:...`
- `@Bot 歌え spotify:album:...`

Spotifyは曲名、アーティスト名、アルバム名、ISRC、曲の長さなどのメタデータ取得にだけ使います。Spotify上の音源やプレビュー音源を直接再生することはありません。実際の再生元は、取得した曲情報をもとに `yt-dlp` のYouTube検索で見つけたYouTube音源です。

誤った曲を流さないため、曲名、アーティスト名、再生時間、official audio / Topic / VEVOなどの情報を使って候補を採点します。アーティストが確認できない候補は採用せず、cover、karaoke、instrumental、live、remix、sped up、slowed、nightcoreなど、Spotify側の曲名にない語がYouTube候補側だけにある場合は強く減点します。Spotify側がLive版やRemix版の場合は、YouTube候補側にも同じ版表記があるものを優先します。最低スコアを下回る場合や、1位と2位のスコア差が小さい場合は、その曲をスキップして「一致する音源が見つからない」扱いにします。

Spotify曲から解決したYouTube URLはプロセス内メモリに一定時間キャッシュします。キャッシュ済みURLが削除、非公開、地域制限などで取得できなくなった場合は、該当曲だけキャッシュを無効化し、最大1回だけ再検索します。通常の一時的なネットワークエラーでは、不要な再検索を避けます。

対応URL:

- Spotify曲URL
- SpotifyアルバムURL
- `open.spotify.com/intl-ja/track/...` のようなロケール付きURL
- `?si=...` などの共有パラメータ付きURL
- `spotify:track:...`
- `spotify:album:...`

非対応URL:

- Spotifyプレイリスト
- Spotifyエピソード
- Spotifyポッドキャスト/番組
- Spotifyアーティストページ
- Spotifyユーザー認証が必要な非公開データ

プレイリストは、現在のSpotify API仕様では一般プレイリストから曲一覧を取得できないため、曲またはアルバムのリンクを送るよう案内します。将来、ユーザー認証やAPI仕様が変わった場合に拡張する想定です。

アルバムリンクは全トラックをページネーションで取得し、曲ごとにYouTube音源を解決して既存のguild単位キューへ追加します。負荷を抑えるため、同時解決数はデフォルト1、曲数上限はデフォルト100です。ローカルトラック、利用不能トラック、null項目、一致スコア不足の曲はスキップします。

必要な環境変数:

- `SPOTIFY_CLIENT_ID`
- `SPOTIFY_CLIENT_SECRET`
- `SPOTIFY_MARKET=JP`
- `SPOTIFY_MAX_ALBUM_TRACKS=100`
- `SPOTIFY_RESOLVE_CONCURRENCY=1`
- `SPOTIFY_YOUTUBE_CANDIDATES_PER_QUERY=10`
- `SPOTIFY_RESOLVE_CACHE_TTL_SECONDS=86400`
- `SPOTIFY_RESOLVE_CACHE_MAX_ENTRIES=1000`
- `SPOTIFY_MATCH_MIN_SCORE=`
- `SPOTIFY_MATCH_MIN_MARGIN=10`

Spotify認証はClient Credentials方式です。Client Secret、access token、Cookie、YouTube一時stream URLはログやDiscordメッセージへ出しません。`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` が未設定の場合もBot起動は止めず、Spotifyリンクが送られた時だけ設定不足を案内します。

Cookie状態監視を使う場合は、以下を設定します。`YTDLP_COOKIE_CHECK_URL` が未設定の場合、定期検査は安全にスキップされます。

- `YTDLP_COOKIE_CHECK_ENABLED=true`
- `YTDLP_COOKIE_CHECK_TIME=04:30`
- `YTDLP_COOKIE_CHECK_TIMEZONE=Asia/Tokyo`
- `YTDLP_COOKIE_CHECK_OWNER_BOT_ID=ichiyon`
- `YTDLP_COOKIE_CHECK_URL=<公開されている検査用YouTube URL>`
- `YTDLP_COOKIE_RETRY_COOLDOWN_SECONDS=1800`
- `YTDLP_COOKIE_CHECK_OWNER_BOT_ID` に一致するBotだけが定期チェックを実行します。botとbot-irsiaで同じCookieを共有する場合は、担当Botを1体だけ指定してください。
- `YTDLP_ALERT_CHANNEL_ID=<通知先DiscordチャンネルID>`

現在の自動更新処理は、Cookie検査・分類・排他制御・通知の土台までです。専用Firefoxプロファイルなど安全な更新元が未設定のため、Cookie失効時は「自動更新未設定」として扱い、既存Cookieを変更しません。Cookie内容、Googleアカウント情報、長い例外スタックはログやDiscord通知に出しません。

## 音楽キュー操作

- スキップ: `@Bot スキップ` / `@Bot skip` / `@Bot 次` / `@Bot 次の曲`
- 複数曲スキップ: `@Bot スキップ 5` / `@Bot 5曲スキップ` / `@Bot skip 5`
- 停止: `@Bot 止めて` / `@Bot 停止` / `@Bot stop`
- 一時停止: `@Bot 一時停止` / `@Bot pause`
- 再開: `@Bot 再開` / `@Bot resume`
- キュー表示: `@Bot キュー` / `@Bot queue` / `@Bot 再生予定`
- 現在再生中: `@Bot 今何` / `@Bot now` / `@Bot nowplaying`
- 音量確認/変更: `@Bot 音量` / `@Bot 音量 40`
- ループ確認: `@Bot ループ`
- 1曲ループ: `@Bot 1曲ループ`
- キューループ: `@Bot キューループ`
- ループ解除: `@Bot ループ解除`
- シャッフル: `@Bot シャッフル`
- YouTube Cookie状態: `@Bot YouTube状態`

複数曲スキップは、現在曲を1曲目として指定曲数を音楽キューから除外します。指定数が残り曲数を超えた場合は、現在曲と待機曲をすべて除外して音楽再生を停止します。VCからは退出しません。
停止は音楽キューがある場合、現在曲を停止してキューもクリアします。VCからは退出しません。キューがない場合は従来のローカル音声停止として扱います。

音楽の初期音量は40%、ローカル音声や反応音声などの前景音声は50%です。音楽音量は `bot_id + guild_id` 単位でDBに保存され、再起動後も維持されます。ループとシャッフルは実行中のguild単位ランタイム状態で、停止またはVC退出時に解除されます。

## 停止

- `@Bot 止めて`
- `@Bot 停止`
- `@Bot stop`

再生中の音声だけ停止します。VC退出はしません。

## 依存関係

Discord VC接続のため `PyNaCl` が必要です。ローカル音声再生とURL音楽再生は `discord.FFmpegPCMAudio` を使うため、Dockerイメージには `ffmpeg` を入れます。URL情報の取得には `yt-dlp` を使います。YouTubeのEJS challenge解決用に、DockerイメージへDenoを入れます。

## 手動確認

1. `assets/audio/test.mp3` などの短い音声ファイルをローカルまたはサーバー上に配置します。
2. BotをDiscordサーバーに参加させます。
3. 実行者がVCに入ります。
4. テキストチャンネルで `@Bot もしもししよ` を送ります。
5. `@Bot 音声一覧` でファイルが見えることを確認します。
6. `@Bot 鳴らして test` で再生されることを確認します。
7. `@Bot 止めて` で停止できることを確認します。
8. `@Bot 二度と来るな` でBotが退出することを確認します。

URL音楽再生は、BotをVCに入れた状態で `@Bot 歌え <URL1>`、再生中に `@Bot 歌え <URL2>`、`@Bot キュー`、`@Bot 今何`、`@Bot 一時停止`、`@Bot 再開`、`@Bot スキップ`、`@Bot 停止` を順に確認します。

ログには `guild_id`、`channel_id`、`bot_instance_id`、`filename` が出ます。Tokenなどの秘密値は出しません。
