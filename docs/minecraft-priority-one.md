# モクローのリード・低速落下 / モルカーの浮き輪

## 実装と操作

- モクローの `body` にモデル空間の `lead: [0, 6, -5]` を追加。
  胸の前面、高さ0.375ブロックを基準に既存のbody animationへ追従する。
  exporterにも追加し再生成で消えない。元のbbmodel、cube、pivot、テクスチャ、
  当たり判定、原点、装着時の位置、元の4アニメーションは維持する。
- 通常状態で下降すると8tickの `slow_falling` amplifier 0を6tick間隔で更新。
  既存の型限定scanとspawn/loadで個体を追跡し、`ichiyon:falling` が既存flapを選択。
  着地・上昇・水中・装着時に解除。上下impulse・位置補正・追加の全Mob検索はない。
  `damage_sensor` は落下だけ無効化。頭装着時のジャンプ・滑空は元の処理を維持し、
  短時間効果の更新関数だけを共有する。
- 浮き輪は `ichiyon:molcar_float_ring`（クリエイティブの装備欄）。作業台では
  白い羊毛4個と赤い羊毛2個を次の形で配置する（空白は空き）。

  ```text
   白赤
  白 白
  赤白
  ```

  手に持って通常モルカーへ使うと装着。素手でしゃがんで使い、既存の
  「アクセサリーを外す」メニューから浮き輪を選ぶと返却される。
  サバイバルでは1個消費・返却、クリエイティブでは消費・返却なし。
  空き不足、選択中の状態変更、同時操作には既存の検証とロックを使う。
- `ichiyon:float_ring` は専用の永続・client sync property（0/1）。
  頭・顔・首・背中の4枠とDB素材IDは変更しない。molcar/molcar2/molcar3だけが対象で、
  ゴミ収集モルカーへは適用しない。
- native buoyancy（base 0.55、波なし、追加重力なし）と水中速度0.04を使用し、
  既存のnative騎乗入力・旋回を維持。ニンジンは陸上0.4→0.65、水中0.04→0.065を
  同じ100tickタイマーで管理。着脱・降車・期限・load/restartで古い速度を復旧する。
  通常float AIと浮力は排他のcomponent group。native environment sensorが
  保存propertyから復元し、外すと元のfloat AIと水中挙動へ戻す。
  group削除だけでは残る水中速度属性は、通常groupで元の0.02へ明示的に復元する。
- 浮き輪は12cubeの楕円形状、32×32のテクスチャ・アイコン、専用animation。
  各モデルの骨格を再利用するoverlayなので既存アニメーションへ追従する。
  水中のみ浮き輪ボーンを±0.35度動かし、Mobの物理位置は揺らさない。
  追加の乗り物・不可視Mob・毎tickの水面探索は使わない。

## 生成・配備

`minecraft_molcar_float.py` を既存 `compile_files` から呼び、DB素材の処理後に追加。
登録済み素材の置換やDB migrationは不要。ring素材はmanaged releaseの
アクセサリーRPへ振り分け、元のスキン・ポスター・動画は既存経路で保持する。
authoring manifestはBP 1.0.44 / RP 1.0.49 / Bridge 1.0.42。
本番は固定Gitパックで上書きせず、現行の
[managed release](operations/managed-minecraft-release.md)でDB素材と単調増加版を生成する。

浮力フィールドのため通常3体のentity formatを1.26.30へ更新。そのformatで廃止された
pushableとaddrider.entity_typeも同等の新形式に変換し、押され方とゾンビ騎乗を保持する。

## 検証

- `check_minecraft_priority_one.py`: 4枠との共存、ID非競合、生成アセットと分割RPの参照、
  永続propertyからのgroup復旧、胸locator、当たり判定。
- 既存cosmetics / Mokuro / boostの実行テストを拡張。同時操作・返却失敗・
  降下／着地／水中／上昇／装着・外部効果・再読み込み・水中加速復旧を検証。
- 既存Minecraft CIへ新checkerを登録。JSON、JS、Python構文とversion policyも確認。
- ローカルのMinecraft検査30コマンド中、DB接続検査以外は成功。
  DB検査はローカルに専用PostgreSQLがないためCIの隔離PostgreSQLで実行する。

2026-10-13の隔離BDS 1.26.51.1（server 2.11.0-beta、server-ui 2.0.0、
テストのみGameTest 1.0.0-beta）で、水上・落下35項目と再起動・陸上13項目が成功。
3種類とも水面Y=64で足元Y≈63.55、静止60tickの上下幅は0.00022ブロック以下。
30tickの前進は5.201ブロック、加速時8.860ブロック。20ブロック落下はモクロー67tick、
対照pig24tick、着地後HP20。落下以外の2ダメージではHP18になった。
再起動後も3体の装着property・浮力が保持され、浮き輪あり／なしの陸上走行・
加速・降車時リセット・ゾンビ騎乗イベントが通った。

再現用 `scripts/minecraft_priority_one_smoke.js` は**テスト専用**。
新規の隔離ワールド、beta API有効、対象BP/RPとScript librariesを備えたBDSで、
テスト用bridgeのscriptsへコピーし、コピー側manifestのentryに指定する。
最初の起動で水槽・床・Mob・simulated playerを作成し、終了後の再起動で
保存した3体の装着状態・浮力と陸上走行を検証する。
arenaを書き換えるため既存ワールドでは実行しない。
ハーネスとGameTest依存は通常パックに含まれない。

ローカルの試験ログは `acceptance-final.log` / `acceptance-restart.log`。
公式Windows BDSと新規ワールドのみ使用し、各試験後にstopで終了。
空のallowlistを有効にして外部プレイヤーの参加を許可せず、LAN公開も無効。
fixtureのNetherNet transport警告は外部クライアント接続に関するもの。
製品の通信設定は変更していない。

## 実機QAと制約

BDSとオフラインgeometry previewではクライアントの描画・操作感は保証できない。
Switch/スマートフォン/PCで以下を確認する。

- RP再取得、Content Log、卵とアイテム表示、3種の浮き輪の色・装着位置。
- モクローの歩行・停止・下降中のリード位置（手持ちとフェンスの両方）。
- idleの羽ばたき、落下flapと着地復帰、頭／背中への装着・降ろす・従来滑空。
- 浮き輪と登録アクセサリー4枠の同時表示、マルチプレイヤーの同期。
- 水深1／深い水／流水／岸への出入り、タッチ・コントローラーの旋回と加速。
- チャンクの出入りと再ログイン。BDS保存・再起動検証と区別して確認する。
- 泡柱・滝・強いリード牽引は水やリードのnative物理が優先される。
  水平な水源面の安定性をこれらの特殊条件に対する保証とはしない。

実装PR作成時点では本番未反映だったが、2026-10-13にPR #130をmainへマージし、
既存Linux Runnerのmanaged releaseによる本番反映成功を確認した。
[反映SHA・パック版・確認範囲](operations/minecraft-priority-one-release-2026-10-13.md)を参照。
ワールドLevelDBの編集、バックアップ削除は行っていない。

## 参照した公式仕様

- [Geometry locator](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/visualreference/geometry.v1.12.0?view=minecraft-bedrock-stable)
- [Buoyant](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_buoyant?view=minecraft-bedrock-stable)
- [Ground input](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_input_ground_controlled?view=minecraft-bedrock-stable)
- [Add rider](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_addrider?view=minecraft-bedrock-stable)
- [Entity effects](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/entity?view=minecraft-bedrock-stable)
