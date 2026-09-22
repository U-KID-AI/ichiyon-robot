# マネキン（Bedrock）

`ichiyon:avatar` を共有し、キアナ・芽衣・ブローニャ・アルベールと追加スキンをvariantで切り替えます。
着替え・共通エッグ・Web登録・モルカー装備の現行仕様は [minecraft-cosmetics.md](minecraft-cosmetics.md)、制作形式は [素材ガイド](../minecraft/cosmetics/README.md) を参照してください。

## ゲーム内の操作

クリエイティブの「マネキン共通エッグ」を地面に使うと、キャラクターを選んで配置できます。
マネキンを使うとそのキャラクターへ着替える／元のスキンへ戻す画面が開きます。
クリエイティブでしゃがんで使うと、配置済みマネキンの変更・撤去もできます。
旧4種類のplacer entityは互換性のため残しますが、キャラクター別エッグは一覧から外します。

## 維持する動作

マネキンはHP20、移動速度0.12で、配置場所から半径2ブロックの範囲を歩き、プレイヤーを見ます。
攻撃によるダメージを受けます。ノックバック耐性1・火炎耐性・persistentは維持します。
移動AI、重力なしのphysics、幅0.6×高さ1.8のcollision、既存のidle／walk／注視アニメーションは変更しません。

初期4人の原本は `minecraft/avatar_skins/source/*.png`、従来のRPコピーは `textures/entity/avatar/*.png`。
新しい生成先は `textures/entity/cosmetics/skin_<ID>.png` で、画像メタデータを除いてRGBAとして保存します。
標準64×64のclassic／slimと外側レイヤーに対応します。スキン用variantを向きやポーズに流用しません。

| キャラクター | 永続スキンID | 既存variant | 互換イベント |
| --- | --- | --- | --- |
| キアナ | 1 | 0 | `ichiyon:kiana` |
| 芽衣 | 2 | 1 | `ichiyon:mei` |
| ブローニャ | 3 | 2 | `ichiyon:bronya` |
| アルベール | 4 | 3 | `ichiyon:albert` |

追加スキンはID5以降、variantはID-1。すべての選択イベントで以前のvariant groupを解除します。
既存個体のID・variantを初期化するspawn eventは追加しません。

## 互換コマンド

```mcfunction
/summon ichiyon:avatar ~ ~ ~ 0 0 ichiyon:kiana avatar_kiana
/event entity @e[type=ichiyon:avatar,name=avatar_kiana,c=1] ichiyon:mei
/event entity @e[type=ichiyon:avatar,name=avatar_kiana,c=1] ichiyon:remove
```

Discordでは既存の権限確認と固定操作キューを引き続き使います。
先頭は `@いちよんロボ マイクラ`。Minecraft名は従来どおり英数字・アンダースコア1〜16文字です。

| 操作 | 動作 |
| --- | --- |
| `キアナ召喚 <Minecraft名>` / `キアナ削除 <Minecraft名>` | キアナを召喚／最寄り1体削除 |
| `芽衣召喚 <Minecraft名>` / `芽衣削除 <Minecraft名>` | 芽衣を召喚／最寄り1体削除 |
| `ブローニャ召喚 <Minecraft名>` / `ブローニャ削除 <Minecraft名>` | ブローニャを召喚／最寄り1体削除 |
| `アルベール召喚 <Minecraft名>` / `アルベール削除 <Minecraft名>` | アルベールを召喚／最寄り1体削除 |
| `マネキン全削除 <Minecraft名>` | 同dimension・16ブロック以内の全マネキンを削除（追加キャラも対象） |

召喚はオンラインのプレイヤー前方2ブロック。同種削除は現在のvariantで判定します。
新しいキャラクターの配置はゲーム内のマネキン共通エッグから行い、配置場所の空きはScript側で確認します。
任意shell・任意Minecraftコマンドを実行する機能は追加しません。

## 反映と確認

初期版BP 1.0.31 / RP 1.0.35 / Imported Structures 1.0.28。UUIDは保持しています。
3パックとワールド側の参照versionを合わせて反映し、プレイヤーは再接続してRPを受信します。
本番反映の順序と受入項目は [minecraft-cosmetics.md](minecraft-cosmetics.md) を参照してください。
自動テストは構造・処理の検証であり、実際のクライアント描画の保証ではありません。
