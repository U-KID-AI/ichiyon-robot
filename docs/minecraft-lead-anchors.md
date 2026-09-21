# リード表示回帰修正 — RP 1.0.32

## 履歴と実装方式

PR #42 (84dd167) はmolcar/gontaのclient entityにlocatorsを追加したが、
molcar2/molcar3は対象外で、version更新もなかった。PR #44 (66e3c8e) は
1.0.31への更新と検証・version-policy CIを追加したが、locator方式は変更しなかった。
以前の検証はJSON構文と座標を検証するだけで、エンジンの描画成功を証明していない。
本番1.0.31でのmolcar・卵の透明化は依頼者の実機報告であり、こちらで再現していない。

[公式client entity文書](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/cliententitydocumentation/cliententitydocumentationintroduction?view=minecraft-bedrock-stable#locators)
にもclient側の旧形式は掲載されている。したがって「公式に無効なJSONだった」とは断定しない。
今回、回帰報告のあるclient側指定を除去し、
[公式geometry仕様](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/visualreference/geometry.v1.12.0?view=minecraft-bedrock-stable)
のboneに属するモデル空間locatorへ統一する。locatorはboneのanimationに追従する。
[Mojang vanilla pig](https://github.com/Mojang/bedrock-samples/blob/main/resource_pack/models/entity/pig.geo.json)
および[cow](https://github.com/Mojang/bedrock-samples/blob/main/resource_pack/models/entity/cow_v1.0.geo.json)
でもbone内の `locators: {"lead": [x,y,z]}` を使用している。
client entityにはboneマッピングを残さず、geometryのbodyにleadを1件だけ置く。
公式仕様に基づく修正だが、本番Content Logがないため透明化の根本原因と解消は実機で要確認。

## 個別モデルの確認

座標はpivot差分ではなく、回転前のモデル空間。モデル位置・scale・cube・pivot・
rotation・animation・texture・identifier・BP（collision/AI/速度/騎乗）を変更しない。
spawn_eggは全4体の既存色をそのまま維持する。

| entity | body→親 / pivot / 親回転 | leadと接続面 | animation・別枝 |
| --- | --- | --- | --- |
| molcar | body→root / [0,6,0] / [0,-90,0] | [-8.5,7.5,0]、cube origin [-8.5,6,-1], size [2,3,2] の−X面中央 | body上下動・Z回転、djはbodyの子、車輪はrootの子 |
| molcar2 | body→root / [0,6,0] / [0,-90,0] | [-8.5,7.5,0]、独立に確認した同じorigin/sizeのcube面 | body上下動・Z回転、djなし、車輪はrootの子 |
| molcar3 | body→root / [0,6,0] / [0,-90,0] | [-8.5,7.5,0]、独立に確認した同じorigin/sizeのcube面 | body上下動・Z回転、hatと車輪はrootの子。車輪pivotは他2体と異なる |
| gonta | body→gonta_body / 両方原点 / 回転なし | [0,4,-4]、origin [-4,0,-4], size [8,8,8] の−Z面中央 | 動くbone〜bone4はlegの子でbodyの祖先ではない |

3台のbody接続面、pivot、root回転、body animationを個別照合した結果が同じ座標である。
モルカー3体のroot適用後は概算 [0,7.5,-8.5]、高さ0.46875ブロック。
idle上下動は0〜0.1、walk上下動は0〜0.24、Z回転は±1.8度。
保守的な計算包絡は高さ7.23〜8.01、Z −8.55〜−8.44モデル単位。
ゴン太は高さ0.25ブロックで脚animationの影響を受けない。
これらは静的計算で、エンジンや旋回時の見た目の保証ではない。

## オフライン検証

- `python3.12 scripts/check_minecraft_lead_anchors.py`: 全4体のJSON、geometry/PNG参照、
  spawn_egg色、描画参照、animation参照、locator所有bodyの存在・一意性・有限座標・
  cube表面、全boneの親存在・重複・循環、親変換・animation包絡、pack JSONを確認。
  manifest header/modules 1.0.32と、ダミーworld参照1.0.31→1.0.32同期を確認。
- `python3.12 scripts/check_resource_pack_versions_test.py`: 既存version-policy回帰検証。
  未コミット差分には同じvalidate関数でHEAD manifestからのversion増加を確認する。
- CIの `.github/workflows/checks.yml` は既にPR base/headのversionチェックを実行する。
  GitHub CI成功はPR未作成のため未確認。lead検査のCI組み込みはまだない。

## 人間への引き継ぎ（needs_human）

Runner指示によりcommit/push/PR/merge/本番操作は行わない。
人間側でPR、CI、review、merge、承認済みBDS配備を実施する。
world_resource_packs.jsonのRP UUID `3e1bcf76-b5e3-465a-a184-d2d90cfa0d74` を
[1,0,32]へ同期し、必要な再起動、health、配置packとmerge済みmainの一致を確認する。
本番world JSONはリポジトリに新設しない。復旧は承認されたpack/参照の復元手順を使う。

各4entityについて人間が以下を確認するまで表示回帰の解消を確定しない。

- 既存個体の表示、召喚、新規スポーン。
- インベントリ内スポーンエッグの表示と使用によるスポーン。
- 静止・歩行・旋回時の胴体からのリード位置、プレイヤー/フェンス接続。
- RP再取得とContent Log（client entity、geometry、texture、locatorエラーなし）。
- モデル位置・大きさ・collision・AI・速度・texture・騎乗に回帰がないこと。

## 検証結果

Python 3.12でlead検証5件と既存version-policy回帰2件が成功。
HEAD→worktree manifestを既存validate関数で検証して成功。
client/geometryをHEADと比較し、locator以外の内容が同一であることを確認した。
Python compile、git diff --checkも成功。
標準Python 3.8では既存配備モジュールの型注釈を読み込めず、3.12で再検証した。
既存avatar総合検査はPillow未導入で起動できないため、依存を備えた環境で要再実行。
