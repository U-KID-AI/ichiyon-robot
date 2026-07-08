# 共通基盤命名棚卸し

作業日: 2026-07-08

## 目的

いちよんロボ単体から、いちよんロボ + イルシアを同じソースとDBで動かす複数Bot基盤へ移行したため、共通処理・共通設定・共通ドキュメントに残る `ichiyon` / `ICHIYON` / `いちよんロボ` 系の名称を棚卸しする。

今回の作業では一括置換は行わず、変更候補とリスクを分類する。

## 検索対象

`git grep` で以下を確認した。

- `ichiyon`
- `ICHIYON`
- `いちよんロボ`
- `ichiyon_robot`
- `ichiyon-robot`

`tools/` は未追跡のため対象外。

## 分類1: 変えるべきもの

共通基盤なのに、いちよんロボ単体に見える名称。

| 現在の名称 | 主な場所 | 推奨候補 | 理由 |
| --- | --- | --- | --- |
| `ICHIYON_DATA_BACKEND` | `.env.example`, `.env.stg.example`, `bot/config.py`, `docker-compose.yml`, docs | `BOT_DATA_BACKEND` / `APP_DATA_BACKEND` | DB/JSON backendの切替はBot共通設定であり、いちよんロボ個別ではない。 |
| `FastAPI(title="いちよんロボ 管理画面")` | `admin/main.py` | `Bot Platform 管理画面` / `管理画面` | 管理画面は複数Botを扱うため、アプリ全体名としてはいちよんロボ固定に見える。 |
| テンプレートtitle内の `いちよんロボ管理画面` | `admin/templates/*.html` | `Bot管理画面` / `管理画面` | 画面はBot選択で複数Botを扱う。個別Bot名は選択中Bot表示に寄せるのが自然。 |
| `admin/templates/guild_top.html` などの `- いちよんロボ` | `admin/templates/*.html` | `- 管理画面` / `- {{ current_bot_instance.display_name }}` | ページタイトルが選択中Botとずれる可能性がある。 |
| `docker-compose.yml` の共通サービスcontainer名 `ichiyon-robot-admin`, `ichiyon-robot-db` | `docker-compose.yml` | 将来 `bot-platform-admin`, `bot-platform-db` | admin/dbは共通基盤だが、名前だけ見るといちよんロボ専用に見える。現時点では後方互換リスクがあるため即変更は非推奨。 |
| docsの「いちよんロボ前提」説明 | `docs/v2-*`, `docs/development.md`, `docs/v3-foundation-design.md` | v2資料は履歴扱い、v3以降は「Bot基盤」「複数Bot基盤」 | 現行運用手順と過去設計資料が混ざり、読む人が混乱しやすい。 |
| `bot-ichiyon` 例だけが先に出るCompose例 | `docs/v3-foundation-design.md` | `bot-ichiyon` と `bot-irsia` を対で記載 | v3設計としては複数Bot前提を明確にする。 |

## 分類2: 残すべきもの

いちよんロボ個別の識別子・表示名・互換データとして正しい名称。

| 名称 | 主な場所 | 残す理由 |
| --- | --- | --- |
| `bot_id='ichiyon'` | `bot/config.py`, migrations, repositories, checks | BotインスタンスIDとして明確にいちよんロボを指す。 |
| 表示名 `いちよんロボ` | `bot/config.py`, `bot_instances` seed, tests | いちよんロボ個別の表示名。 |
| `ICHIYON_DISCORD_TOKEN` | `.env.example`, `bot/config.py`, Compose | いちよんロボ個別Token。`IRSIA_DISCORD_TOKEN` と対になる。 |
| `BOT_INSTANCE_ID=ichiyon` | env例, tests, docs | デフォルトBotインスタンスとしての互換値。 |
| `mini_ichiyon` | `runtime_db.py`, docs, seed | 既存特殊効果テンプレート互換名。現在は `hankaku` 相当として残す意味がある。 |
| `SHIKOCCHI_RECOVERY_MESSAGE if BOT_INSTANCE_ID == "ichiyon"` | `bot/services/runtime_db.py` | いちよんロボだけに既存復活文言をfallbackする個別仕様。 |
| `NORMAL_BOT_NICKNAME=いちよんロボ-*` | env例 | いちよんロボ側の通常表示名例。イルシア側は別envを持つ。 |
| `migrations/025` 以降の `DEFAULT 'ichiyon'` | migrations | 既存データをいちよんロボ扱いで保つ後方互換。適用済みmigrationは編集しない。 |

## 分類3: 後方互換のため当面残すもの

変えると既存環境・本番運用・Docker volume・DB接続に影響が大きいもの。

| 名称 | 主な場所 | 当面残す理由 |
| --- | --- | --- |
| `ichiyon_robot` DB名/DBユーザー名 | `.env.example`, `.env.stg.example`, `docker-compose.yml`, docs | 本番/stg/localの接続文字列、Docker volume、既存DB運用に影響する。 |
| `ichiyon-robot` リポジトリ/ディレクトリ名 | docs, 運用手順 | 本番/stgの配置パス、GitHub repo、運用コマンドに影響する。 |
| `ichiyon-robot-*` container名 | `docker-compose*.yml` | 既存Docker Compose運用、監視、ログ確認手順に影響する。 |
| `ichiyon-db-backups` | staging/production docs | 既存バックアップディレクトリ名。変更には運用手順と既存ファイル移行が必要。 |
| `DISCORD_TOKEN` / `DISCORD_BOT_TOKEN` fallbackがichiyon扱い | `bot/config.py`, env docs | 既存本番env互換。新規は個別Token envを使うが、fallbackは残す。 |
| `ICHIYON_DATA_BACKEND` | 現行コード/env | 変えるべき名称だが、本番env互換のため移行期間は残す。新env追加後にfallback扱いへ移す。 |
| systemd unit名 `ichiyon-bot`, `ichiyon-admin` | docs | rollback用の既存運用名。削除・改名は別作業。 |

## 分類4: 将来的には変えたいが今すぐ触らないもの

外部URL・運用・DB・Dockerに強く影響するため、今回の棚卸しでは変更しない。

| 名称 | 候補 | 理由 |
| --- | --- | --- |
| リポジトリ名 `ichiyon-robot` | `bot-platform` / `discord-bot-platform` | Git remote、CI、ローカルパス、運用手順への影響が大きい。 |
| 本番/stgディレクトリ `/home/ubuntu/ichiyon-robot` | `/home/ubuntu/bot-platform` | 本番反映手順、rollback、systemd互換に影響。 |
| DB名/DBユーザー `ichiyon_robot` | `bot_platform` / `discord_bot_platform` | dump/restore、DATABASE_URL、権限、バックアップに影響。 |
| Docker Compose project/container/volume名 | `bot-platform-*` | 既存volume名とcontainer名の移行が必要。 |
| docs/v2系タイトル `いちよんロボ ver2.0 ...` | 原則そのまま | v2時代の履歴資料としては正しい。現行ガイドからリンクする際に「履歴資料」と明示する。 |

## 共通名候補

最終決定はまだしない。候補と印象は以下。

| 候補 | 向いている場所 | 所感 |
| --- | --- | --- |
| `bot_platform` | DB名、サービス名、docs | 汎用的で分かりやすい。Discord以外の連携も含めやすい。 |
| `discord_bot_platform` | docs、管理画面説明 | Discord Bot基盤であることが明確。ただし長い。 |
| `multi_bot` | 内部ヘルパー名、設計メモ | 複数Botの意図は出るが、プロダクト名としてはやや弱い。 |
| `robot_platform` | UI/ドキュメント | 既存の「ロボ」文脈を残せる。Discord以外にも広げやすい。 |
| `app` / `core` | コード内部 | 短いが意味が広すぎる。設定名にはやや不向き。 |

現時点の推奨は、環境変数・DB・サービス系は `BOT_*` または `bot_platform`、画面タイトルは「Bot管理画面」または「Bot Platform 管理画面」。

## 推奨変更順序

1. **低リスクな表示文言から変更**
   - `admin/main.py` のFastAPI title
   - `admin/templates/*.html` のページtitle
   - v3以降docsの共通説明
   - 選択中Bot名は `current_bot_instance.display_name` を表示する。

2. **共通envを追加し、旧envをfallbackにする**
   - 新規: `BOT_DATA_BACKEND` または `APP_DATA_BACKEND`
   - 旧: `ICHIYON_DATA_BACKEND`
   - まず `BOT_DATA_BACKEND` を優先し、未設定なら `ICHIYON_DATA_BACKEND` を読む。
   - 警告やdocsで移行先を案内する。

3. **Compose/env exampleの説明を整理**
   - `ICHIYON_DISCORD_TOKEN` は個別Tokenとして残す。
   - `ICHIYON_DATA_BACKEND` は互換envとして注記する。
   - DB名やcontainer名は変えず、コメントで歴史的名称であることを明示する。

4. **現行docsと履歴docsを分離**
   - `docs/v2-*` は履歴資料として残す。
   - 現行運用は `docs/development.md`, `docs/staging-docker-compose.md`, `docs/production-docker-compose.md`, `docs/v3-foundation-design.md` を中心に整理する。

5. **高リスク名は別Phaseで移行計画を作る**
   - DB名、DBユーザー、Docker container/volume、repo名、本番ディレクトリ名。
   - dump/restore、DNS/URL、systemd rollback、CI、Git remoteへの影響を別途洗う。

## 今回は変更しないもの

- DB名、DBユーザー名、container名、volume名
- `bot_id='ichiyon'`
- `ICHIYON_DISCORD_TOKEN`
- migrations内の既存 `DEFAULT 'ichiyon'`
- v2履歴docsのタイトル
- 本番/stg手順の既存パス

## 次タスク案

1. 管理画面タイトルを「Bot管理画面」に変更する。
2. `BOT_DATA_BACKEND` を追加し、`ICHIYON_DATA_BACKEND` をfallback互換にする。
3. `.env.example` / `.env.stg.example` に互換envの注記を追加する。
4. v3 docsに「ichiyonはBot ID、いちよんロボは個別表示名」という命名ルールを明記する。
5. Docker/DB/repo名の変更は、別途移行計画を作るまで保留する。
