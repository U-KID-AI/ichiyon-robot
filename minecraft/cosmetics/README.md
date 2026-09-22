# マネキン・モルカー素材の作り方

初期素材は従来の4人です。新しい素材は管理画面の「マイクラ素材」で登録し、「ゲームに反映」を押すだけで利用できます。完了後はMinecraftに入り直してください。
アクセサリーの完成モデルは同梱していません。以下の形式で用意すると、登録後のコード編集は不要です。

## 用意するファイル

| 素材 | 必要なもの |
| --- | --- |
| マネキン／着替え用スキン | 64×64 PNG、表示名、通常腕（4px）またはスリム腕（3px）の指定 |
| モルカーアクセサリー | Bedrock `.geo.json`、モデル用PNG、持ち物アイコンPNG、表示名、装着部位 |

PNG・モデルはそれぞれ1MB以下。アクセサリーの画像は最大1024×1024、アイコンは16×16または32×32推奨。
128×128スキンや旧64×32スキンは、作者側で64×64へ整えてから登録してください。
スキンは標準UVの外側レイヤーも表示します。人型を変える独自プレイヤーモデルはこの登録形式の対象外です。

## アクセサリーをBlockbenchで作る

1. Bedrock Entity形式を使い、参考用に `../resource_packs/ichiyon_avatar_rp/models/entity/molcar.geo.json` と対応PNGを開く。
2. `molcar2.geo.json`、`molcar3.geo.json` でも位置を確認する。3種類に同じモデルを装着する。
3. モルカーの `body` と同じモデル座標系で、アクセサリーだけを作る。元の `root` の回転（Y=-90度）をアクセサリー側へ焼き込まない。
4. アクセサリーだけを独立したモデルとして書き出す。元のモルカーのキューブ・ボーンは含めない。最上位ボーンに親は指定しない。
5. 最上位のアクセサリーボーンは生成時に `body` の子になる。すべての名前は自動で専用名へ変換するため、他の装備と衝突しない。

頭・顔・首・背中の4部位に1つずつ、合計4つまで同時装着できます。部位は排他制御用で、モデルの位置を自動補正するものではありません。
表情・アニメーション付きの独自モデル、Molang、poly_mesh、外部画像参照、ボーンの循環参照は受け付けません。
対応するgeometry formatは1.12.0／1.16.0／1.21.0。キューブ、回転、標準box UV・面ごとのUVを使用できます。

## ゲームで使う

- クリエイティブの「マネキン共通エッグ」を地面に使い、キャラクターを選ぶ。IDは `ichiyon:avatar_selector`。
- マネキンを使うと着替え・元のスキンへ戻す操作ができる。クリエイティブでしゃがんで使うと変更・撤去もできる。
- 着替えはこのワールド内の表示。Microsoftアカウントや更衣室のスキンは書き換えない。
- アクセサリーを持ってモルカーを使うと装着する。空の手でしゃがんで使うと部位ごとに外せる。
- サバイバルでは手持ち1個を使用し、交換時は元の装備を手に返す。取り外すときは空き枠が必要。
- クリエイティブではアイテムを消費・返却しない。通常の空手操作は乗車、リード等の操作も維持する。

素材番号は永続IDです。1〜4番のスキンは既存キャラクター、追加スキンは5〜127、アクセサリーは1〜127。
管理画面からスキンを削除できますが、削除済み番号は永久欠番になり再利用しません。着替えとモルカーごとの装備が別素材に変わる事故を避けます。
モデル側の修正版を追加する場合も新番号になります。

## パック生成

Web管理画面ではPNG／モデルをDBに保存し、「反映用パックをダウンロード」で既存コンテンツも含む3パックを生成します。
登録だけでプレイヤーへ画像を配信することはできません。パック反映と再接続が必要です。

開発時の初期4人の再生成・照合:

```console
python scripts/build_minecraft_cosmetics.py --write
python scripts/build_minecraft_cosmetics.py --check
```

外部素材をローカルで試す場合は次のJSON配列を素材と同じフォルダーに置きます。各パスはそのフォルダーの内側のみ参照できます。

```json
[
  {"kind":"skin","id":5,"key":"new_character","name":"追加キャラ","model":"slim","texture":"skin.png"},
  {"kind":"accessory","id":1,"key":"new_hat","name":"帽子","slot":"hat","texture":"hat.png","geometry":"hat.geo.json","icon":"hat_icon.png"}
]
```

```console
python scripts/build_minecraft_cosmetics.py --assets ./my-assets/catalog.json --zip ./test-packs.zip --revision 1
```

既存のWebカタログとローカルで別々に番号を採番しないでください。運用を開始したらWebのDBを正としてください。
ZIPのrevisionはテスト用。運用パックはWebから生成し、DBの単調増加する番号を使用します。

## バニラプレイヤー定義の出典

`vendor/` は [Mojang/bedrock-samples](https://github.com/Mojang/bedrock-samples/tree/46ba6ea985fb5a92d79a9419198f10dda14c199d) の同コミットから取得したものです。
元の条件付きレンダー・アニメーション・プレイヤーbehaviorを保持し、着替えIDのプロパティと専用描画経路のみ追加します。
利用条件は `vendor/LICENSE.md`、パックには `COPYING-Mojang.txt` を同梱します。Minecraft更新時はこの上書きと新しいバニラ定義を比較してください。

Entity Propertyの永続化／同期は[公式ドキュメント](https://learn.microsoft.com/en-us/minecraft/creator/documents/introductiontoentityproperties?view=minecraft-bedrock-stable)を使用。
状態変更は[before eventの制約](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/playerinteractwithentitybeforeevent?view=minecraft-bedrock-stable)に従って次のtickに行い、UIは `@minecraft/server-ui` 2.0.0を使用します。
