# モルカー・ゴン太のリード接続点

## 調査と修正

対象は `ichiyon:molcar` と `ichiyon:gonta`。別entityである
`molcar2`、`molcar3` を含め、他のentityは変更しない。

両対象はBPの `minecraft:leashable` が空の設定で、RPに `lead` locatorが
なかった。モデル形状に対応した描画接続点を明示していなかったため、
今回RPのclient entityにだけ `locators.lead` を追加した。
未指定時にエンジンが用いる座標の厳密な計算式と、報告された現象の
実機再現は未確認。collision高さから計算式を推測して修正してはいない。

[Bedrock公式のclient entity仕様](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/cliententitydocumentation/cliententitydocumentationintroduction?view=minecraft-bedrock-stable#locators)
では、`lead` locatorが描画上の接続位置を指定し、オフセットはモデル座標である。
ボーン名をキーとする公式の記法を使用する。geometryの座標は16単位が1ブロックで、
指定値はワールド座標やpivotからの差分ではない。
[leashable仕様](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_leashable?view=minecraft-bedrock-stable)
の距離・ばね・追従設定には触れない。

| 対象 | bodyボーン上の接続点 | 根拠 |
| --- | --- | --- |
| モルカー | `[-8.5, 7.5, 0]` | 前側中央のcube（origin `[-8.5, 6, -1]`、size `[2, 3, 2]`）の外面 |
| ゴン太 | `[0, 4, -4]` | 胴体cube（origin `[-4, 0, -4]`、size `[8, 8, 8]`）の前面中央 |

モルカーはrootのpivotが `[0, 0, 0]`、回転が `[0, -90, 0]`。
bodyのpivotは `[0, 6, 0]`。bodyへの紐付けによって親の回転とidle/walkの
上下移動・揺れに接続点を追従させる。RP scale指定はない。
ゴン太のbodyは原点pivotのgonta_bodyの子で、RP scaleは `1.0`。
walkは脚のbone〜bone4だけを上下移動するため、接続点は脚に追従させない。

BPは変更せず、collision boxはモルカー幅1.5・高さ1.25、ゴン太幅1.0・高さ0.65、
基本movementはともに0.4のまま。geometry、pivot、scale、animation、AI、
騎乗・追従設定、名前、テクスチャ、pack manifestも変更しない。

## 検証と引き継ぎ

`python3 scripts/check_minecraft_lead_anchors.py` でJSON・参照と、両接続点が
実在するbody cube表面にあることを検証する。これはエンジンの描画検証ではない。

Runner規則により、この作業ではcommit、push、PR、merge、production接続、
pack配置、world参照同期、BDS再起動を行わない。本番反映済みやcompletedとは扱わない。
人間側でレビュー・CI・merge後、承認済みの運用手順に従ってBP/RPとworld pack参照を
同期し、起動・health・配置ファイルとmerge済みコードの一致を確認する必要がある。
変更対象はRPの2つのclient entityのみなので、復旧時は承認済み手順で直前の
整合するpack一式へ戻す。worldデータ自体を変更する修正ではない。

クライアントでの最終確認事項:

- 両entityにリードを付け、静止・歩行・旋回時とプレイヤー／フェンス接続時に、
  線が胴体から自然に伸びて浮いたり脚に引っ張られたりしないこと。
- モルカーの親回転とbodyの揺れに接続点が追従すること。
- Content Logにlocator・geometryエラーがなく、モデル・大きさ・当たり判定・
  騎乗・移動・既存AIが従来どおりで、他entityのリード位置も変わらないこと。

既存のavatar/poster checkはこの環境ではPillow（PIL）が未導入のため起動不可。
依存を備えたCI環境で再実行が必要。
