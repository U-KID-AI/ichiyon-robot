# 静止展示マネキン（Bedrock）

既存の `ichiyon_avatar_bp` / `ichiyon_avatar_rp` に共通Entity
`ichiyon:avatar` を追加する。キアナ・芽衣・ブローニャ・アルベールは
Entityを複製せず `minecraft:variant` とテクスチャ配列で切り替える。
配置後は従来の共通avatarを使う。ロボット操作は既存Narita Bridgeの固定操作を拡張する。

## パックと対応範囲

- BP **1.0.28**、RP **1.0.31**、Imported Structures **1.0.27**。既存UUIDを維持する。
- 最低エンジンバージョンは既存の **1.26.45** を維持する。
- 両パックを同じ検証用ワールドで有効にする。既存ワールドから更新する場合は
  ワールド側のパック参照バージョンも合わせる。RPの受信・適用が必要。
- Switchを含むBedrock向けの標準JSON/PNG構成。Switch実機での描画・参加可否は
  未検証。Switch側の接続方法やパック配布環境は、この変更では構築しない。
- この変更はリポジトリ内の実装のみ。本番worldへのコピー、再起動、
  パック有効化は行わない。

## 生成と選択

チートが有効なローカル検証ワールドで、権限のあるプレイヤーが実行する。
単純な `/summon ichiyon:avatar` はvariant 0（キアナ）になる。
共通Entityは `is_spawnable: false` とし、Creativeには専用エッグ4種だけを公開する。
ランダム選択はしない。

Creativeには4種の名前付きエッグ `ichiyon:avatar_<skin>_placer` が表示される。
`<skin>` は `kiana` / `mei` / `bronya` / `albert`。配置用proxyは
delay 0のtransformationにより `ichiyon:avatar<ichiyon:<skin>>` へ変換される。
変換先の既存選択イベントを明示してvariantを設定する。
変換前後で同じgeometry・texture配列・アニメーションを使う。
この選択はBP/RPだけで動作し、Bridgeや新規Script API依存を必要としない。

### スポーンエッグ修正の調査結果

共通Entityとキアナproxyの両方が `is_spawnable: true` だったため、
キアナになるエッグが2種類公開されていた。共通Entityのエッグ公開だけを停止し、
既存identifier、共通Entityのsummon、保存済み個体、翻訳名は維持する。

従来のproxyは全てイベント指定なしの `ichiyon:avatar` に変換し、
スキン選択を `transformation.add` のみに委ねていた。変換先のデフォルトは
キアナであり、選択が反映されなければ全種キアナになる経路だった。
`add` の形式自体は公式仕様に記載されているため、JSON構文違反とは断定しない。
報告された症状に対応するため、[Mojang公式husk定義](https://github.com/Mojang/bedrock-samples/blob/main/behavior_pack/entities/husk.json)
で使われる `entity<event>` 形式に変更し、変換先で各選択イベントを明示的に実行する。
実際のエンジンで旧 `add` が反映されなかった原因の確定と修正後の動作確認は、
Content Logを含むローカル検証ワールドで要確認。

offline checkは全BP/RPのentity identifier重複、公開エッグが正確に4種であること、
itemの追加エッグとの競合、名前の一意性、および変換先イベントからvariant・textureまでの
対応を検証する。既存のPNGハッシュ、geometry、移動・体力・アニメーションのチェックも維持する。

| キャラクター | variant | イベント |
| --- | --- | --- |
| キアナ | 0 | `ichiyon:kiana` |
| 芽衣 | 1 | `ichiyon:mei` |
| ブローニャ | 2 | `ichiyon:bronya` |
| アルベール | 3 | `ichiyon:albert` |

それぞれ向き（yaw、pitch）と名前を指定して生成できる。
以下の `0 0` は回転角で、名前は選択用の例である。

```mcfunction
/summon ichiyon:avatar ~ ~ ~ 0 0 ichiyon:kiana avatar_kiana
/summon ichiyon:avatar ~2 ~ ~ 0 0 ichiyon:mei avatar_mei
/summon ichiyon:avatar ~4 ~ ~ 0 0 ichiyon:bronya avatar_bronya
/summon ichiyon:avatar ~6 ~ ~ 0 0 ichiyon:albert avatar_albert
```

設置済みの個体は、位置・向き・名前を保ったままスキンを変更できる。
名前が重複する場合は先に固有のtagを付け、対象を限定する。

```mcfunction
/event entity @e[type=ichiyon:avatar,name=avatar_mei,c=1] ichiyon:bronya
```

向きは生成時のyaw、または明示的なテレポートで変更する。次の例は各対象自身の
座標を基準にするため、実行プレイヤーの位置へ移動しない。

```mcfunction
/execute as @e[type=ichiyon:avatar,name=avatar_kiana,c=1] at @s run tp @s ~ ~ ~ 90 0
```

無敵の展示物を片付ける場合は専用イベントを使用する（ドロップなし）。

```mcfunction
/event entity @e[type=ichiyon:avatar,name=avatar_kiana,c=1] ichiyon:remove
```

## 静止と描画の設計

行動AI、navigation、movement controller、注視、歩行は定義しない。
movementは0、既存physicsの重力・自身の物理移動衝突・ブロック内からの押し出し無効を維持する。
別途 `minecraft:is_collidable` と幅0.6・高さ1.8のcollision boxで、他のプレイヤー/Mob側に
静止展示物との衝突を持たせる。毎tickのteleportや追尾で固定する方式ではない。
水流・ピストン・接触時の不動性とプレイヤー側の衝突感は、静的検証では保証できず実機未検証。

BP Entityのformatは `1.26.10`。このformatでは旧 `minecraft:pushable` が
解釈されないため使用せず、`minecraft:pushable_by_entity` と
`minecraft:pushable_by_block` の両方を省略して押されない構成にする。
全damage causeのdamage sensor、knockback resistance 1、fire immunityで
攻撃・環境ダメージから保護し、persistentで通常の距離despawnを防ぐ。
管理コマンドや他のパックのスクリプトによる明示的な移動・削除まで禁止する設計ではない。

モデルは標準のclassic（腕幅4px）プレイヤー相当。頭・胴体・左右の手足に
独立したUVを割り当て、帽子は0.5、上着・袖・ズボンは0.25だけ膨張させる。
第2レイヤーは対応する部位の子boneで、透明部分は `entity_alphatest` で抜く。
原本は `minecraft/avatar_skins/source/*.png`、実行用のバイト同一コピーは
`minecraft/resource_packs/ichiyon_avatar_rp/textures/entity/avatar/*.png`。

## 拡張

スキンを追加する際はvariant番号を末尾へ追加し、既存番号は変更しない。
原本とRPコピー、BPのvariant component group・選択イベント、全選択イベントの
removeリスト、client entityのtexture名、render controller配列、テストを更新する。
既存個体の選択情報を維持するため、生成イベントでvariantを再初期化しない。

共通の8秒ループidleは頭・胴・腕のpitchのみを最大0.6度動かす。
呼吸相当の周期は4秒、頭は8秒。root・脚・座標・yawを動かさず、プレイヤー位置を参照しない。
帽子・上着・袖は既存の子bone構造で追従する。geometry、UV、textureは変更しない。
variantはスキン用に予約し、ポーズや向きの状態と混用しない。
slim（腕幅3px）スキンを将来追加する場合は別geometryの選択を拡張する。

## いちよんロボ操作

既存の権限確認、プレイヤー名検証、DBキュー、内部API、BDS側Bridge pollingを使用する。
DiscordからBDSへの直接接続や任意コマンドは追加しない。

| Discordコマンド（先頭に `@いちよんロボ マイクラ`） | 動作 |
| --- | --- |
| `キアナ召喚 <Minecraft名>` / `キアナ削除 <Minecraft名>` | Kianaを召喚 / 最寄り1体削除 |
| `芽衣召喚 <Minecraft名>` / `芽衣削除 <Minecraft名>` | Meiを召喚 / 最寄り1体削除 |
| `ブローニャ召喚 <Minecraft名>` / `ブローニャ削除 <Minecraft名>` | Bronyaを召喚 / 最寄り1体削除 |
| `アルベール召喚 <Minecraft名>` / `アルベール削除 <Minecraft名>` | Albertを召喚 / 最寄り1体削除 |
| `マネキン全削除 <Minecraft名>` | 範囲内の4種すべて削除 |

対象プレイヤーのオンラインが必要。召喚位置は既存Bridgeと同じ前方2ブロック、
高さはプレイヤーと同じ。yawは配置時に一度だけ設定する。
削除は同じdimensionの16ブロック以内の `ichiyon:avatar` のみ。
「全削除」はこの範囲内の全種・全個体であり、未ロードチャンクや別dimensionには及ばない。
既存summon個体・Creative設置個体も現在のvariantで選択し、名前や古いtagに依存しない。
対象なしは0体の成功、削除途中の例外は部分削除の可能性があるため失敗として返す。
variant適用や向き設定の失敗時には生成個体をremoveし、cleanup失敗も別の失敗理由で返す。

DB制約に9操作を追加するmigration `064_add_minecraft_avatar_commands.sql` を追加した。
適用はこの作業では行わない。利用にはmigrationと更新パック・Botの反映が必要。
Bridgeが既に使う実験Script API依存は変更していない。

## 検証

```text
python scripts/check_minecraft_avatars.py
python scripts/check_minecraft_posters.py
node scripts/check_minecraft_avatar_bridge.mjs
```

新規checkはネットワーク、環境設定、DB、worldにアクセスせず、PNGの寸法・
原本ハッシュ・コピー一致、BP/RP参照、UV・第2レイヤー、16通りのvariant間遷移と
同じ選択の繰り返し、静止・collision設定、Creative proxy変換、idleの振幅・周期、
ロボット操作のparser・DB制約・Bridge固定操作契約を検証する。
Node checkは偽Entityで召喚・削除範囲・失敗時cleanup・不正入力を検証する。
現在CIでは実行されておらず、CIへの組み込みは承認済みの別作業として扱う。
これはBedrockエンジン上の動作試験を代替しない。

本番適用前に、別のローカル検証ワールドとSwitch実機で次を確認する。

1. 両パックが読まれ、Content LogにEntity・geometry・Molangのエラーがない。
2. デフォルト生成と4イベント生成、Creativeの4種配置・変換、16通りの切替が正しい。顔・左右の腕と脚・
   背面・袖・帽子・上着・ズボンのUVと透過を全方向から確認する。
3. プレイヤー/Mobとの接触、ノックバック付き攻撃、矢、爆発、炎、溶岩、
   水中・水流、窒息、足場除去、ピストンで位置・向き・生存状態が変わらない。
4. 放置、プレイヤー移動、チャンクの再読込、ワールドの保存・再開でも
   座標・向き・variantが維持される。平和難易度でも消えない。
5. 明示的なyaw変更とremoveイベントが対象個体だけに作用する。
   idle中も足とyawは固定。ロボットから4種召喚、最寄り1体削除、範囲内全種削除ができる。
6. タケツミ、ポスター、既存Narita Bridge機能が従来どおり動く。

## 参照したBedrock公式仕様

- [variantによるテクスチャ選択](https://learn.microsoft.com/en-us/minecraft/creator/documents/introductiontoaddentity?view=minecraft-bedrock-stable)
- [physics](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_physics?view=minecraft-bedrock-stable)
- [pushableのformat変更](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_pushable?view=minecraft-bedrock-stable)
- [押し出しコンポーネントの省略](https://feedback.minecraft.net/hc/en-us/articles/43775430834701-Minecraft-Beta-Preview-26-10-25)
- [damage sensor](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_damage_sensor?view=minecraft-bedrock-stable)
- [summon構文](https://learn.microsoft.com/en-us/minecraft/creator/commands/commands/summon?view=minecraft-bedrock-stable)

- [静止Entityへの衝突](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_is_collidable?view=minecraft-bedrock-stable)
- [proxy変換とcomponent group追加](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_transformation?view=minecraft-bedrock-stable)
