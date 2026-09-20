# 静止展示マネキン（Bedrock）

既存の `ichiyon_avatar_bp` / `ichiyon_avatar_rp` に共通Entity
`ichiyon:avatar` を追加する。キアナ・芽衣・ブローニャ・アルベールは
Entityを複製せず `minecraft:variant` とテクスチャ配列で切り替える。
スクリプト、Java Edition機能、実験機能、Discordコマンドの追加はない。

## パックと対応範囲

- BP **1.0.5**、RP **1.0.4**。既存UUIDを維持している。
- 最低エンジンバージョンは既存の **1.26.45** を維持する。
- 両パックを同じ検証用ワールドで有効にする。既存ワールドから更新する場合は
  ワールド側のパック参照バージョンも合わせる。RPの受信・適用が必要。
- Switchを含むBedrock向けの標準JSON/PNG構成。Switch実機での描画・参加可否は
  未検証。Switch側の接続方法やパック配布環境は、この変更では構築しない。
- この変更はパックのリポジトリ内実装のみ。本番worldへのコピー、再起動、
  パック有効化は行わない。

## 生成と選択

チートが有効なローカル検証ワールドで、権限のあるプレイヤーが実行する。
単純な `/summon ichiyon:avatar` とクリエイティブの共通スポーンエッグは
variant 0（キアナ）になる。ランダム選択はしない。

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

行動AI、navigation、movement controller、注視、歩行・待機アニメーションは
定義しない。movementは0、重力と物理衝突とブロック内からの押し出しを無効にする。
足場を外しても落ちない展示物で、通行を遮る壁にはならない。
Entityの当たり判定は選択用に残す。

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

`root` と頭・胴体・手足のboneを分離してあるので、将来のポーズは共通モデルの
静的animationと専用の同期property等で追加できる。今回は直立ポーズのみ。
variantはスキン用に予約し、ポーズや向きの状態と混用しない。
slim（腕幅3px）スキンを将来追加する場合は別geometryの選択を拡張する。

## 検証

```text
python scripts/check_minecraft_avatars.py
python scripts/check_minecraft_posters.py
```

新規checkはネットワーク、環境設定、DB、worldにアクセスせず、PNGの寸法・
原本ハッシュ・コピー一致、BP/RP参照、UV・第2レイヤー、16通りのvariant間遷移と
同じ選択の繰り返し、静止設定を検証する。CIでも実行する。
これはBedrockエンジン上の動作試験を代替しない。

本番適用前に、別のローカル検証ワールドとSwitch実機で次を確認する。

1. 両パックが読まれ、Content LogにEntity・geometry・Molangのエラーがない。
2. デフォルト生成と4イベント生成、16通りの切替が正しい。顔・左右の腕と脚・
   背面・袖・帽子・上着・ズボンのUVと透過を全方向から確認する。
3. プレイヤー/Mobとの接触、ノックバック付き攻撃、矢、爆発、炎、溶岩、
   水中・水流、窒息、足場除去、ピストンで位置・向き・生存状態が変わらない。
4. 放置、プレイヤー移動、チャンクの再読込、ワールドの保存・再開でも
   座標・向き・variantが維持される。平和難易度でも消えない。
5. 明示的なyaw変更とremoveイベントが対象個体だけに作用する。
6. タケツミ、ポスター、既存Narita Bridge機能が従来どおり動く。

## 参照したBedrock公式仕様

- [variantによるテクスチャ選択](https://learn.microsoft.com/en-us/minecraft/creator/documents/introductiontoaddentity?view=minecraft-bedrock-stable)
- [physics](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_physics?view=minecraft-bedrock-stable)
- [pushableのformat変更](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_pushable?view=minecraft-bedrock-stable)
- [押し出しコンポーネントの省略](https://feedback.minecraft.net/hc/en-us/articles/43775430834701-Minecraft-Beta-Preview-26-10-25)
- [damage sensor](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_damage_sensor?view=minecraft-bedrock-stable)
- [summon構文](https://learn.microsoft.com/en-us/minecraft/creator/commands/commands/summon?view=minecraft-bedrock-stable)
