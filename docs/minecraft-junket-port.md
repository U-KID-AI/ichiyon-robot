# JUNKET CRAFT ver1.09 → Bedrock

## 0.8.0 production candidate

2026-10-18時点で、17ゲームすべてに次の本番導入経路を接続した。

- 既存のゲームロジック、共通UI、受付、切断・再戦・人数制御を保持する。
- 原作ZIPの利用規約は二次配布を禁止しているため、原作から変換した会場・モデル・音声・画像は本番成果物へ含めない。`data/junket-private/` はローカル保持のみとする。
- 17会場、30構造物、44操作・表示エンティティ、17ゲーム固有の独自テクスチャとモデルを `build_junket_original_world.py` から生成する。
- 会場は `(20000 + 96 * index, 65, 20000)` からX軸方向へ配置し、各会場を96ブロック間隔で分離する。会場間の重複をオフライン試験で拒否する。
- ワープ制御は既存行 `(10053,65,10077)` からX軸2ブロック間隔で17台を配置する。保護対象 `(10051,65,10076)`, `(10051,65,10077)`, `(10051,66,10077)` は設置対象に含めない。
- 初回ワールドロード時だけ、会場・操作モデル・帰還NPC・17台のワープ設備を一つずつ導入する。各会場の完了マーカーを保存して再起動時は再設置せず、常時intervalや強制チャンク維持を追加しない。
- 手動再開は `/scriptevent junket:installall`、ワープ設備だけの再検証は `/scriptevent junket:warpadmin install` を使用する。

ローカル成果物は `C:\Users\syoub\junket-full17-production\artifacts` に生成する。BPは0.8.0、独立RPは0.8.0で、原作アーカイブを入力にしていない。

本番設定の最大参加人数は、バックアップ `backups/junket-full17-maxplayers-20261018T010503Z` を作成したうえで、`docker-compose.yml` と `server.properties` の15を16へ変更済み。BDS再起動後の実測確認はManaged Release後に別途行う。

## ALL17_IMPLEMENTED

2026-10-18時点で、17ゲームすべてにBedrock Script API上の状態機械、入力UI、参加・退出、手番または入力待ち、入力時間切れ、切断時の扱い、終了処理、受付タグ、ワープ導線を実装した。`scripts/minecraft/junket_games.json` の17件はすべて `readiness=implemented` で、ワープ設備生成側も全件を開放対象として扱う。

この完成表示は**コードとUIの実装完了**を意味する。次の2点は未完了として区別する。

- Bedrock実クライアントでの2〜16人完走、モバイル操作、音・演出、画面サイズの確認は未実施。
- ウラギリスズメ、ブルー・テンパランス、気分屋ルーシー、サウンド・オブ・サイレンス、ジャックポット・ジニー以外の11本は、原作会場の追加変換と原作固有モデルの実装を行っていない。共通UI上でゲーム進行を成立させるBedrockアダプテーションとして実装しており、原作の座標移動・敵AI・physics・演出をそのまま再現した1:1互換版ではない。

### 画面と操作

受付NPCはゲームごとに次の専用タグを使う。共通の `junket:reception` はウラギリスズメ専用のまま維持し、別ゲームのNPCと競合させない。

| ゲーム | 参加・操作画面 | 専用受付タグ | 主な入力 |
| --- | --- | --- | --- |
| ウラギリスズメ | 原作会場の大小箱、受付フォーム | `junket:reception` | 賭け金、隠す箱、選ぶ箱 |
| ブルー・テンパランス | 原作カード、天秤、受付フォーム | `junket:temperance_reception` | 正逆混在3枚、対象者、カード |
| 気分屋ルーシー | 金庫・鍵穴、受付フォーム | `junket:lucy_reception` | 5面の当たり列、鍵穴、回収 |
| サウンド・オブ・サイレンス | ジャケット、レコード、耳栓NPC | `junket:silence_reception` | 0/20/30秒配置、選択、耳栓、返却 |
| ジャックポット・ジニー | カード、レバー、降参、TV | `junket:jinn_reception` | 黄金・盗賊・魔人、精算、降伏 |
| タンブリング・エース | 共通ゲームUI | `junket:ace:reception` | 場代、ダイス、コール、レイズ、フォールド |
| アンハッピー・ホーリーグレイル | 共通ゲームUI | `junket:grail:reception` | 秘匿位置、聖杯、ポーション |
| ザ・ショートホープ | 登録・床選択UI | `junket:hope:reception` | 参加登録、1〜5の床 |
| ライフ・イズ・オークショニア | 共通ゲームUI | `junket:auction:reception` | 1〜4の札 |
| シヴァリング・ファイア | 共通ゲームUI | `junket:fire:reception` | グー、パー、チョキ |
| ピーキー・ピッグ・パレス | 共通ゲームUI | `junket:pig:reception` | 家カード、オオカミの襲撃先 |
| デッドマンズ・キャンドルライト | 共通ゲームUI | `junket:light:reception` | 門番・死者の3枠配置、相手の1枠 |
| デビルズマイン・ツインズ | 共通ゲームUI | `junket:mine:reception` | 岩4枚の秘匿配置、採掘2枠 |
| ヘル・オ・クロック | 共通ゲームUI | `junket:clock:reception` | 時刻位置、短針・長針攻撃 |
| ザ・クレバス・ウォーカー | 共通ゲームUI | `junket:walker:reception` | 1〜5マス移動、爆弾位置 |
| ジュエルシャワー・ハイガーデン | 共通ゲームUI | `junket:garden:reception` | 切替、宝石倍化、開放 |
| ザ・ラビットホール | 共通ゲームUI | `junket:rabbit:reception` | オニの罠、脱出側の移動 |

各画面は次を共通表示する。

- 開始前に人数、ラウンド数、勝敗条件、主な操作を表示する。
- 現在のラウンド、手番、残り入力状態、スコアまたは体力を表示する。
- 手番でない参加者には、待機中であることと現在の公開情報だけを表示する。
- 秘匿選択、相手の配置、未公開カード、罠の選択は公開フェイズまで `view()` から除外する。
- 入力は300秒で時間切れになる。退出、切断、古い画面からの送信を拒否し、終了後にセッションを解放する。
- `/scriptevent junket:<id> menu`、`/scriptevent junket:<id> leave`、`/scriptevent junket:<id> start`（ショートホープ）を利用できる。

ワープ操作列の17ボタンは全件 `implemented` として開放される。既存5本は設定済み会場へ移動し、残り11本はその場で共通ゲームUIを開く。原作物量・映像の権利上、変換済み建築と素材は引き続き公開Gitへ含めない。

## 2026-10-18 引き継ぎ・第一段階

管理画面ブランチ `work/admin-legacy-login-20261015` の未コミット変更は元worktreeに保持したまま、JUNKET専用ブランチ `codex/minecraft-junket-port-20261018` と専用worktreeへ関連差分だけを複製した。元ZIP SHA-256は既存解析記録と同じ `613e58fa8ca59b2bcbacf91a100d652350f8d0e922d8aaf65f6646c8eb413ac0` で、再解凍は行っていない。

原作 `lobby:system/tp`、各 `start.mcfunction`、選択登録処理を照合し、17ゲームの対応を `scripts/minecraft/junket_games.json` に固定した。ウラギリスズメ、ブルー・テンパランスを含む14本は2人、タンブリング・エース、ライフ・イズ・オークショニア、ピーキー・ピッグ・パレスは4人。ザ・ショートホープは原作に `Needplayer` 固定値がなく、1人以上の先着登録後に15秒で開始するため、登録制・人数上限追加解析対象として区別している。

本番スナップショット `data/junket-private/production-survey/snapshot` では、既存の拠点行き設備を次の実ブロックとして確認した。これらは生成対象から明示的に除外し、読み取り以外を行わない。

| 用途 | 座標 | 種類 |
| --- | --- | --- |
| 看板 | `10051 65 10076` | `minecraft:wall_sign` |
| コマンド | `10051 65 10077` | `minecraft:command_block` |
| ボタン | `10051 66 10077` | `minecraft:wooden_button` |

17台の新規装置は `(10053,65,10077)` からX軸へ2ブロック間隔で生成する。JSONを唯一の設定元とし、`python scripts/minecraft/build_junket_warp.py` が実行時カタログ、私有設置計画、確認用mcfunctionを生成する。Script API側は空きブロックと保護設備を全件事前検証してから設置し、押下プレイヤーだけを移動する。`readiness=verified` 以外のゲームはボタン操作を拒否する。現在は実機・会場確認待ちのため全17台が未公開である。

ブルー・テンパランスは既存のロジック・受付・天秤・カード・モニター・会場変換を保持し、原作の6,000 tick、3枚の正逆混在、対象者選択、2R倍率、天秤移動、通気口ダメージ、時間切れの回帰試験を追加した。実機での操作・見た目・2人完走は未確認。

気分屋ルーシーは原作の5面金庫、各面5鍵穴、両者の秘匿パターン、交互試行、開錠成功、失敗時の鍵回収と体力損失、5面開錠・体力切れ・時間切れを状態機械として実装した。Java会場 `(194,55,209)`〜`(208,65,213)` を不足チャンク追加後の新規Chunker出力から `lucy_hall` として変換し、箱・鍵穴・ルーシー人形・モニターの24モデルをローカル専用素材へ変換した。受付UIと会場制御まで接続したが、物理鍵穴50個の完全再現と2人実機完走は未確認。

サウンド・オブ・サイレンスは0/20/30秒ジャケット、隠し配置、0/20/30秒レコード選択、毎tick 190/140ダメージ、共有2,000 tick超過時の6,000/2,000ダメージ、恒久耳栓、体力・時間切れ決着を実装した。31×13×33会場と10モデル、受付・耳栓NPC・再生位置制御を接続した。

ジャックポット・ジニーは黄金4・盗賊1・魔人1の手札、3セット各6ラウンド、9通りの同時公開と原作の整数演算、175/tick精算、第2・第3セットの180秒空腹、降参、最終コイン比較を実装した。28×38×37会場と11モデル、カード・レバー・降参・TV操作を接続した。

タンブリング・エースは4人・初期100枚・場代最低10枚・レイズ上限200枚、5ダイスの役判定、役の辞書順比較、5ゲーム、フォールド、コール、レイズ、勝者総取り、P1+P2対P3+P4のチーム決着をロジックへ実装した。会場・モデル・UI・ランダムダイス操作の接続は次の作業。

ローカル専用の新規成果は `data/junket-private/conversion/bedrock-four-games`、`data/junket-private/assets/junket-local-venue-0.6.0.mcaddon`、`data/junket-private/trial/ichiyon_junket_bp-0.6.0.mcpack`、`data/junket-private/trial/junket-trial-0.6.0.mcworld`。旧0.3.0〜0.5.0成果は保持している。

第一段階の残件は、ウラギリスズメ0.2.0会場の実機確認、ブルー・テンパランスの実機2人完走、ワープ装置設置前の本番バックアップと適用、各会場の帰還NPC実機確認である。第二段階では残り15本の原作関数解析からゲーム固有状態機械と会場変換を順次実装する。

| # | ゲーム | コード・UI | 会場・演出 | 実機 |
| --- | --- | --- | --- | --- |
| 1 | ウラギリスズメ | 実装済み | 会場0.2.0・受付/箱/モニター/帰還NPC | 会場版未確認 |
| 2 | ブルー・テンパランス | 実装済み | 会場・天秤・札・モニター | 2人未確認 |
| 3 | 気分屋ルーシー | 実装済み | 会場・24モデル・受付/金庫制御 | 2人未確認 |
| 4 | サウンド・オブ・サイレンス | 実装済み | 会場・10モデル・音/耳栓制御 | 2人未確認 |
| 5 | ジャックポット・ジニー | 実装済み | 会場・11モデル・カード/レバー | 2人未確認 |
| 6 | タンブリング・エース | 実装済み | 共通UIへ接続、原作会場は未変換 | 4人未確認 |
| 7 | アンハッピー・ホーリーグレイル | 実装済み | 共通UIへ接続、原作会場は未変換 | 2人未確認 |
| 8 | ザ・ショートホープ | 実装済み | 先着1〜16人、15秒開始の共通UI | 複数人未確認 |
| 9 | ライフ・イズ・オークショニア | 実装済み | 共通UIへ接続、原作会場は未変換 | 4人未確認 |
| 10 | シヴァリング・ファイア | 実装済み | 共通UIへ接続、原作会場は未変換 | 2人未確認 |
| 11 | ピーキー・ピッグ・パレス | 実装済み | 共通UIへ接続、原作会場は未変換 | 4人未確認 |
| 12 | デッドマンズ・キャンドルライト | 実装済み | 共通UIへ接続、原作会場は未変換 | 2人未確認 |
| 13 | デビルズマイン・ツインズ | 実装済み | 共通UIへ接続、原作会場は未変換 | 2人未確認 |
| 14 | ヘル・オ・クロック | 実装済み | 共通UIへ接続、原作会場は未変換 | 2人未確認 |
| 15 | ザ・クレバス・ウォーカー | 実装済み | 共通UIへ接続、原作会場は未変換 | 2人未確認 |
| 16 | ジュエルシャワー・ハイガーデン | 実装済み | 共通UIへ接続、原作会場は未変換 | 2人未確認 |
| 17 | ザ・ラビットホール | 実装済み | 共通UIへ接続、原作会場は未変換 | 2人未確認 |

実装本体は `strategy_games.js`（6本）、`board_games.js`（5本）、`complete_runtime.js`（12本共通UI）、`ace.js`（既存4人ロジック）に分かれる。共通 `GameManager` は固定人数、先着登録、開始人数、切断保持、古い画面の拒否を扱う。

### 0.7.0 検証

```powershell
node scripts/check_minecraft_junket.mjs
node scripts/check_minecraft_junket_ace.mjs
node scripts/check_minecraft_junket_temperance.mjs
node scripts/check_minecraft_junket_lucy.mjs
node scripts/check_minecraft_junket_silence.mjs
node scripts/check_minecraft_junket_jinn.mjs
node scripts/check_minecraft_junket_complete.mjs
node scripts/check_minecraft_junket_ui.mjs
node scripts/check_minecraft_junket_warp.mjs
node scripts/check_minecraft_junket_playthrough.mjs
node scripts/check_minecraft_junket_reception.mjs
node scripts/check_minecraft_junket_venue.mjs
python scripts/check_minecraft_junket_warp.py
python scripts/check_minecraft_junket_analysis.py
python scripts/minecraft/build_junket_warp.py --check
```

2026-10-18の実行結果は上記12本のNode確認、Python 6テスト、全JS構文確認、`git diff --check` が成功。0.7.0の独立BPは25ファイル（24 JS + manifest）、試験ワールドは29ファイルでZIP整合性を確認した。これはScript APIを模したオフライン試験であり、Bedrockクライアントの描画・入力・実機完走を証明しない。

0.7.0の独立BPと試験ワールドは `data/junket-private/trial/ichiyon_junket_bp-0.7.0.mcpack`（25 entries、SHA-256 `fed4cf46bd9e61c10cb9553522a55cff46bdb6a81217d2a658ac5f3019e3fd15`）と `data/junket-private/trial/junket-trial-0.7.0.mcworld`（29 entries、SHA-256 `49a4b3b4cf701744f14cd517b928c59701c750e085e9128dd5b42b7199a8f029`）へ生成した。公開用画像・原作素材は含まず、収録するJSは24ファイル、manifestはBP版0.7.0である。ZIP日時と属性は固定し、同じ入力から同じハッシュになることを2回の連続生成で確認した。0.6.0以前の私有成果物は上書きせず保持している。`build_junket_warp.py` は本体の生成JSを更新し、私有plan/mcfunctionを更新できない場合は警告して継続する。これら私有出力まで比較する場合は書込み可能な環境で `--include-private` を付ける。

2026-10-18の追加点検で、オークションの入札、ピッグのカード、ショートホープの床、クロックの攻撃、ガーデンの選択、ラビットホールの罠が入力途中の `view()` から見える問題を修正した。ラビットホールの入力画面と通知は、公開されるまで罠を `非公開` と表示する。ラビットホールのUI回帰、全17ゲームの秘匿選択、independent BPの版・25ファイル構成をCI対象へ追加した。

### 配備前の人数上限

ショートホープのScript API実装上限は16人だが、既存のBDS向け検証スクリプトは `server.properties` の `max-players=10` を期待している。現状の10人のままでは、実クライアントから同時16人は参加できない。16人運用には、`level-name=ichiyon-creative-flat` と既存worldを維持したまま `max-players` 以上の変更を別の本番設定変更として承認・バックアップ・再起動し、検証期待値も同時に更新する必要がある。今回の0.7.0成果物とManaged Release準備では `server.properties` を変更していない。

## 2026-10-18 会場版 0.2.0（実機での配置・見た目確認待ち）

ユーザーにより0.1.2の受付、実プレイヤー2人、賭け金、隠す、選択、精算、6ラウンド、勝敗、再戦時5,000万円へのリセットが確認済み。`sparrow.js` と `game_manager.js` は今回変更していない。

元ZIPは読み取り専用。未コミット変更は `data/junket-private/checkpoints/venue-20261018-051751.zip` に保存。開発用パックも更新前のコピーを同ディレクトリへ保存した。本番・Managed Release・既存ワールドファイルへの書き込みは今回行っていない。

- 原作のゲームホール: Java `(210,54,198)`〜`(236,66,226)`、27×13×29。ウラギリスズメの卓に加えて、同室の装飾・家具・天井・床を含む。西側に接続する別室は含めない。原作の進行制御用barrier 24ブロックは除外。
- CROW HOTEL: 受付カウンターの移植単位 `(360,55,-59)`〜`(373,64,-50)`、14×10×10を抽出。ホテル全館ではない。
- ローカルChunker CLI 1.21.0でJava 1.21.3からBedrock 1.21.130へ変換後、`junket_structure.py` でmcstructure化。通常エンティティ・コマンド・コンテナ内アイテムは持ち込まない。看板・旗・頭・植木鉢など39件の装飾NBTを保持したが、カスタム頭スキンや文字・旗の見た目は未確認。
- 原作RPの17モデルを変換: 大小箱の閉状態＋開封3段階×空/宝石あり、宝石、通常/結果モニター。原作の仕切りdisplayの寸法から板を別途生成。箱10〜11直方体、宝石1直方体、テクスチャアトラス256×128。原作専用音声は見つからず、開封にはBedrock標準音を使用。
- 原作由来: 卓・席の相対位置、左右への箱移動、隠す際の仕切り、3段階開封、宝石公開。Bedrock独自補完: NPC受付、箱への直接インタラクト、所持金のネームタグ/アクションバー、タイトルによる精算・結果表示、受付横の宝石見本。Javaの毎tickタイマー・interaction・item_display・関数マクロは移植せず、既存JS状態遷移から更新する。
- `textField` の第2引数はplaceholderだったため、`{ defaultValue: "1000000" }` を指定。未編集でも実際の初期入力値を送信する。

### ローカル成果物と開発用反映

`data/junket-private/assets/` に素材BP `ichiyon_junket_assets_bp`、RP `ichiyon_junket_assets_rp`、変換記録 `conversion-report.json`、再インポート用 `junket-local-venue-0.2.0.mcaddon` を保存。独自JSのみのパックは `data/junket-private/ichiyon_junket_bp-0.2.0.mcpack`。素材は公開Git・既存Managed archiveへ含めない。

再生成: `python scripts/minecraft/build_junket_venue.py`。入力はローカルの元ZIP、`data/junket-private/conversion/bedrock-output`（Chunker結果）、`data/junket-private/tools/java-1.21.3-client.jar`（欠けているバニラテクスチャの公式配布元）。出力先はprivateディレクトリ内に限定。`junket_java.py` は元Javaチャンクの読み取り専用境界確認用。

Chunker入力コピーは元ZIPの `level.dat` と `region/r.0.0.mca` / `region/r.0.-1.mca` のみ。元のデータパックは実行しない。`conversion/java-input/pruning.chunker.json` で会場周辺と受付周辺に限定。使用コマンド:

```powershell
& 'data/junket-private/tools/chunker/chunker-cli/chunker-cli.exe' -i 'data/junket-private/conversion/java-input' -o 'data/junket-private/conversion/bedrock-output' -f BEDROCK_R21_130
```

開発用の3パックは `%APPDATA%/Minecraft Bedrock/Users/Shared/games/com.mojang/` 以下に配置・バイト照合済み。世界の有効パック設定はMinecraft画面から変更する。

### 既存検証ワールドでの操作

1. 保存終了してMinecraftを再起動。既存「ジャンケットバンクテストワールド」の設定で `Ichiyon JUNKET Games 0.2.0`、BP `Ichiyon JUNKET Local Venue`、RP `Ichiyon JUNKET Local Models` を有効化。旧ゲームBPは必要なら一度無効化して再有効化。
2. `/tp @s 180 -60 40` で草原へ移動し着地、`/scriptevent junket:venue hall`。原点 `(180,-62,40)`、最大 `(206,-50,68)` に配置し、受付近く `(202,-60,61)` へ移動する。空き領域を全ブロック確認し、地表/土以外や未読込部分があれば配置せず停止。実行前の領域を `junket:before_hall_<時刻>` としてワールドに保存する。
3. 2人とも会場NPCへ参加。原作の席へ移動。賭け金はフォーム、隠す/選ぶは大小箱を右クリックまたはタッチ操作。必要なら受付からフォームを再表示。開封中は操作を短時間ロック。試合終了・中止時は参加者を受付へ戻し、一時仕切りを削除。再戦は2人とも再参加。
4. 会場に戻る: `/scriptevent junket:venue goto`。装飾が不足する場合: `/scriptevent junket:venue repair`（試合中不可）。建築を再配置せず、会場の専用エンティティだけを補完する。
5. 任意の受付モジュール設置: 別の空いた草原で `/scriptevent junket:venue hotel`。CROW HOTEL受付NPCから会場へ移動可能。`hotel-goto` で受付モジュールへ移動。このコマンドはホテル全館を配置しない。

常時tick処理・チャンク強制読み込みはない。設置時だけ有限のrunJob、開封時だけ有限のrunTimeoutを使用。待機中は静止した専用エンティティ5体。ルール・所持金・秘密の箱は既存GameManagerで管理し、隠した箱をモデル状態としてクライアントへ送らない。

今回の確認は `node scripts/check_minecraft_junket_venue.mjs` の追加部分に限定（空き領域拒否、退避順序、負Y移設座標、賭け金初期値、直接操作・手番拒否・秘密保持、1回の精算、切断時片付け）と生成物の読込/反映照合。86件の既存テスト・6ラウンド試験は再実行していない。**建築の実機表示、UV/向き、モバイルでの当たり判定、会場での再戦は未確認。ユーザーの画面操作を受けて続行する。**

以下は初回実装時の記録。上記の更新内容を優先する。

## 初回実装（2026-10-17）

移植先は既存の `ichiyon-creative-flat`。今回追加したのは独立したゲームロジックBPと既存managed配備への組み込み。建築配置・本番配備・実クライアントでの確認は未実施。既存ワールド、建築、プレイヤー所持品、他アドオンの実データは変更していない。

## 元データの確認と再調査の入口

- 実在確認：`C:\Users\syoub\Downloads\JUNKET CRAFT ver1.09(要解凍).zip`
- サイズ：60,501,680 bytes
- SHA-256：`613e58fa8ca59b2bcbacf91a100d652350f8d0e922d8aaf65f6646c8eb413ac0`
- ZIP内3,802 entries、2,557 mcfunction、データパック `test` / `dust`。
- `level.dat` の Version.Name は `1.21.3`、対象チャンクの DataVersion は4082。
- 内蔵 `resources.zip` と外部適用用 `ゲーム用テクスチャ1.09.zip` が存在する。
- ZIPをreadモードで開き、解凍・関数実行・元ファイル更新は行っていない。解析前後のSHA一致を確認済み。

ローカル専用の解析結果は `data/junket-private/analysis-ver1.09.json`。元関数を含めず、座標・関数一覧とハッシュ・参照関数・チャンクのブロックパレットを記録している。同一SHAの継続作業ではまずこの結果を読む。

```powershell
python scripts/minecraft/analyze_junket.py --world-details --output data/junket-private/analysis-ver1.09.json
```

別の保存先なら `--zip "<元ZIPの絶対パス>"` を指定する。`--world-details` のNBT確認だけ `nbtlib` が必要（今回の環境には既設）。省略すると標準ライブラリだけで関数・座標を調査できる。

READMEの二次配布を控える旨を確認した。元ワールド・元ソース・元リソース・変換済み建築は公開Gitに入れない。`data/junket-private/` と `minecraft/behavior_packs/ichiyon_junket_assets_bp/` はGit除外、managed archiveの収集対象にも含めない。素材の配布許諾は未確認。独立BPには今回書いたJSとmanifestのみを入れる。

## 会場の特定

以下は**Java元ワールドの座標**。移植先の配置座標ではない。

| 対象 | 座標 x,y,z | 元データの根拠 |
| --- | --- | --- |
| CROW HOTEL 大看板 | 360,111,-69 | `lobby:building/all` |
| CROW HOTEL 入口看板 | 356,61.5,-69 | 同上 |
| シングル受付 | 366,56,-83.9 | 同上 |
| マルチ受付 | 366,56,-54.1 | 同上 |
| 特殊受付 | 369,56,-54.1 | 同上 |
| ウラギリスズメ到着地点 | 232,56,219 | `lobby:system/tp`、受付ID 1 / 101 |
| ウラギリスズメ席1・席2 | 227,56,217 / 223,56,217 | `game:sparrow/start` |
| ゲーム卓・モニタ | 225,57.55,217 / 225,61.6,214 | `game:sparrow/prepare` |

NBT読取りで対応する実チャンクの存在と建材を確認した。会場の位置は特定済みだが、建物全体を欠けなく切り出す直方体の境界と、移植先の空き領域は未確認。`start` 内のbarrier操作範囲は競技中の囲いであり、建築の外周とは扱わない。

受付の `lobby:system/tp` には通常ゲームID 1〜17と対人用101〜117があり、ウラギリスズメは最初のゲーム。今回、他16ゲームは実装対象に含めていない。

## 建築を移す方式

**ローカルChunkerで変換用のコピーをBedrock化 → 対象建築だけを `.mcstructure` に分割して書き出す → 空き領域を確認して既存ワールドへ追加配置**を採用する。Chunkerの現行対応表にはJava 1.21.3を含む範囲があり、Java/Bedrockの変換が可能。ただし通常エンティティは変換対象外なので、display・interaction・Animated Javaの見た目や操作は別実装とする。[Chunker公式README](https://github.com/HiveGamesOSS/Chunker/blob/main/README.md)

次の建築工程：

1. ローカル専用ディレクトリに変換入力コピーを作る。元ZIPは保持する。外部変換サービスへアップロードしない。
2. CROW HOTEL・ウラギリスズメの外壁、床、屋根の範囲を確認。変換後の階段・slab・ドア・看板・旗・head・lightなどを点検する。今回、該当チャンクにこれらのJava固有差分があることを確認済み。
3. ローカル変換結果だけを検査用ワールドで開き、対象建築を構造物として出力。エンティティ・ゲーム制御用command blockは持ち込まず、必要な見た目を別に再構成する。隣の会場や地下を巻き込まない。
4. `.mcstructure`、切り出し元のmin/max、配置先origin、SHA、タイル一覧をローカル素材パックで管理する。構造物の大きさはBedrockの出力制限に合わせて分割する。
5. `ichiyon-creative-flat` の最新バックアップ、配置領域の既存建築との非重複、読み込まれた全対象ブロックを確認して配置。空気を含む貼り付けも既存建築を消し得るため、未確認の座標へ自動loadしない。配置前の領域も退避する。
6. 元ワールドの `level.dat`、server.properties、LevelDBやworld pack参照を移植先へ上書きしない。`level-name` は維持する。

今回は方式決定まで。変換実行、境界確定、素材パックの配備経路、配置用操作は次工程。公開Git経由で元建築を配送する実装は追加していない。

## ウラギリスズメの挙動と実装

主な解析対象は `test/data/game/function/sparrow/` の43関数、`single/function/sparrow/` の19関数、受付・登録処理。Java関数をコピーせず、挙動をJSで再実装した。

| 挙動 | 元実装の根拠 | 今回 |
| --- | --- | --- |
| 2人、各5,000万円、先手は席1 | `start` | 実装 |
| ベット → 同じ人が宝石を隠す → 相手が箱選択 → 公開 | `bet/player-*`, `set/player-*`, `choose/player-*`, `open/player-*` | フォームUIで実装 |
| 的中なら選択者、外れなら隠した人が勝つ | `open/player-*` | 全8分岐を検証 |
| 選ばれた大箱は全額、小箱は半額・整数切捨てを移転 | `win/player-*-big`, `win/player-*-little` | 実装 |
| ラウンドごとに賭ける・隠す側を交代 | `open/player-*` → 次の `bet/player-*` | 実装 |
| 6ラウンドまたは残高100万円未満で終了 | `open/player-*` | 実装、残高比較と同点表示 |
| 参加者切断で中止 | 元README・共通の参加監視 | `playerLeave`で実装 |
| CPUの賭け金・箱選択、見る目による手掛かり | `single:sparrow/*`, `power:eye/*` | 未実装 |
| 箱・仕切り・TV、キャラのAnimated Java演出 | `prepare`, `set`, `open`, 専用RP | 未実装 |
| 受付予約・入場・着席・ロビー帰還・キャリア報酬 | `lobby:*`, `single:sparrow/*` | 未実装 |

賭け金の下限は100万円。元 `bet/check` のメッセージは両者の所持金に言及するが、実際の条件分岐は**賭ける本人の所持金だけ**を上限にする。今回はこの実際の処理を再現し、相手の残高がマイナスになる精算も許容した。実通貨・既存の経済スコアへの接続はない。

共通の `GameManager` はゲーム登録、卓ごとの状態、二重参加拒否、人数制限、手番検証、単調増加tokenによる古い画面・二重応答拒否、終了/退出時の解放を持つ。今回UIから使う卓は1つ。途中の秘密の箱はサーバーメモリにだけ保持し、公開用viewに含めない。

元の6000 tickカウンターは演出・進行用で、確認した対人の選択ループには入力期限による失格処理がない。今回も独自の自動失格時間を導入していない。フォームを閉じても再開でき、参加者はいずれも中止できる。再起動時は試合を継続せず初期状態へ戻す。プレイヤーの持ち物、位置、gamemode、他パックのtag/scoreboardは操作しないので復元処理は不要。

## パック・既存反映機構・操作

パック：`minecraft/behavior_packs/ichiyon_junket_bp/`。header UUIDは `74d3d36b-630a-4cff-926c-141ec361d0b8`、初版 `0.1.0`。既存BPへJSを埋め込まず、`@minecraft/server` / `@minecraft/server-ui` の安定版2.0.0に依存する。最低engineは既存bridgeと同じ `[1,26,44]`。この新BP単独ではBeta APIを要求しない。フォームの呼出しは公式APIに合わせた。[ModalFormData](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server-ui/modalformdata?view=minecraft-bedrock-stable)

既存の `pack_zip` に独立BPを追加し、Control APIの `minecraft_cosmetics_apply.py` で受理する。バックアップ、version更新、ファイル検証、既存world pack参照の保持、stop/start、rollbackは既存transactionを使う。新script module専用のpermissionsへ `server` / `server-ui` だけを追加し、この設定も同じバックアップ・rollbackに含める。過去のarchiveも回復できるよう新BPはControl API上ではoptional扱い。旧archiveを適用しても無関係な追加パックとして参照を保持する。DB素材生成・digest算出は変更していない。

実反映は [managed release](operations/managed-minecraft-release.md) の既存runnerに委ねる。今回はcommit/push/配備を実施していない。Control API側の新BP受入れコードも配備先で更新されていることが必要（旧コードは新BPを拒否する）。手動SSHコピーのfallbackは作っていない。

配備後、ゲーム内で本人として実行：

```mcfunction
/scriptevent junket:menu
/scriptevent junket:leave
```

通常権限のプレイヤーには、運用者が用意した受付エンティティ1体だけへ `junket:reception` tagを付け、その受付を操作してもらう。tag付き受付の作成・配置は今回自動実行しない。既存NPCへの意図しないtag付与を避ける。参加ボタンで参加し、2人目が参加すると開始する。手番の画面には進行と中止があり、非手番側も受付から中止できる。フォームを閉じたときは再び受付を操作する。

会場がまだなくてもUIによるルール検証は可能。実機では2クライアントで、他アドオンのUIとの共存、通常権限での受付操作、手番の自動表示、キャンセルからの再開、切断時中止を確認する必要がある。

## 検証

```powershell
node scripts/check_minecraft_junket.mjs
python scripts/check_minecraft_junket_analysis.py
python scripts/check_minecraft_cosmetics.py
python scripts/check_minecraft_resource_packs.py
python scripts/check_minecraft_cosmetics_apply.py
```

新規JS検証を既存CIに追加。全8判定分岐、奇数の半額、6ラウンド同点、早期終了・負残高、不正入力、二重参加、古いフォーム、秘密情報の非公開、退出、無関係なNPC・観客への非干渉をオフライン検証する。Bedrock実機QA・建築変換品質・本番稼働の証明とは区別する。

今回の実行結果：JS 9項目、ZIP解析2テスト、既存compiler24テスト、resource pack9テスト、配備transaction42テストの計86件が成功。Python構文確認と `git diff --check` も成功。追加パックを含む失敗時のrollback、専用permissionsの復元、旧archive適用時の既存パック・world fixture保持を確認した。

ローカルの独立パック出力：`data/junket-private/ichiyon_junket_bp-0.1.0.mcpack`（5,696 bytes）。manifestと4つの自作JSだけを格納し、ZIP整合性と作業ツリーとのバイト一致を検証済み。SHA-256は `13384f03d85d8c4f777b3e60ebdfbf5937474f8aa687c2e93f6ac207a7900277`。これはローカル試験用出力で、本番のmanaged配備を置き換えるものではない。
