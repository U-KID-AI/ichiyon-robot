# モルカー・ゴン太のリード接続点再調査

## PR #42と反映漏れ

ローカル履歴の84dd167（PR #42）はclient entity 2件、静的check、文書のみを
変更し、RP manifestは1.0.30のままだった。今回header/modulesを1.0.31に更新する。
UUID、entityの座標、geometry、モデル位置・サイズ、collision、AI、速度、animation、
texture、名前は変更しない。

[公式manifest仕様](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/addonsreference/examples/addonmanifest)
は同一以下のversionのインポートを無視し、高いversionで置換すると説明する。
旧キャッシュは反映されない原因の候補だが、BDSの配置内容とクライアントキャッシュを
確認していないため実際の原因とは断定しない。未配置・world参照不一致も未確認。

## 座標の再計算

[公式client entity仕様](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/cliententitydocumentation/cliententitydocumentationintroduction?view=minecraft-bedrock-stable#locators)
のleadはボーンに紐付くモデル座標で、pivotからの差分ではない。
[公式animation仕様](https://learn.microsoft.com/en-us/minecraft/creator/documents/animations/animationsoverview?view=minecraft-bedrock-stable)
のX→Y→Z回転を使い、各親へ順に `pivot + R(point - pivot) + translation` を適用する。
以下はentityの向きが適用される前のモデル座標計算で、エンジン実測ではない。

| 対象 | 入力 | 親変換後 | ブロック換算 |
| --- | --- | --- | --- |
| モルカー | [-8.5, 7.5, 0] | [0, 7.5, -8.5] | [0, 0.46875, -0.53125] |
| ゴン太 | [0, 4, -4] | [0, 4, -4] | [0, 0.25, -0.25] |

モルカーはbody pivot [0,6,0]、root pivot [0,0,0]、root回転 [0,-90,0]。
Y回転は `x'=cos(a)x+sin(a)z, z'=-sin(a)x+cos(a)z` なので、元の−X側は
回転後の−Z（モデル前方）側になる。bodyの接続cubeはorigin [-8.5,6,-1]、size [2,3,2]。
body walkのZ回転θではroot適用後の接続点は
`[0, 6-8.5*sin(θ)+1.5*cos(θ)+dy, -8.5*cos(θ)-1.5*sin(θ)]`。
θは±1.8度、dyは0〜0.24。全組合せを含む保守的な包絡は
高さ7.23〜8.01（0.4519〜0.5007ブロック）、Zは−8.55〜−8.44。
idleは高さ7.5〜7.6。djと車輪は祖先ではないので接続点を変換しない。
ゴン太はbody→gonta_bodyともpivot原点・回転なし。脚のanimationは別の枝なので
接続点は動かない。body cubeの−Z面中央、高さ0.25ブロックを維持する。
entityの旋回はこの前方向をワールド上の向きへ回す。実際の方角・描画は実機確認が必要。

## 自動検証

- `python3 scripts/check_minecraft_lead_anchors.py`: cube表面、親階層、pivot、
  回転・animation包絡、JSON、実manifestを使うworld参照1.0.30→1.0.31同期を検証。
- `python3 scripts/check_resource_pack_versions_test.py`: version更新漏れ、module不一致、
  asset追加・変更・削除・renameを検証。
- `python3 scripts/check_resource_pack_versions.py <base SHA> <head SHA>` で
  PR base/headのコミットを比較し、RP内の変更（バイナリを含む）に対して
  header versionの増加と全moduleの一致を要求する。新規packも検証し、完全削除は除外。
  PR #42のような変更を拒否する。比較対象コミットが取得できない場合も失敗する。
- CIへの組み込みは未完了。保護対象 `.github/workflows/checks.yml` の変更が
  Runnerにより差し戻されたため、このタスクでは変更しない。権限のある担当者が
  比較対象の両コミットを取得し、上記コマンドと回帰テストをPRチェックへ追加する必要がある。

## 配備引き継ぎ（未実施）

world_resource_packs.jsonはリポジトリの管理ファイルではなく実world内にある。
既存の固定配備処理 `scripts/ai_task_minecraft_deploy_remote.py` のsync_worldが
manifestのUUID/versionから参照を更新するため、固定のworld JSONは新設しない。
本番の参照・配置状況は権限外で未確認。ローカルテストは実worldに触れない。

Runner指示によりcommit、push、PR作成、merge、本番接続・操作は実施しない。
人間側で次を完了する必要がある。

1. PR作成、CI、review、merge。
2. 承認された運用でmerge済みRPを本番BDSに再配置し、world_resource_packs.jsonの
   pack_id 3e1bcf76-b5e3-465a-a184-d2d90cfa0d74をversion [1,0,31]へ同期。
3. BDS再起動、health正常確認。配置済みmanifestのheader/modulesが1.0.31であること、
   molcar.entity.json / gonta.entity.jsonがmerge済み内容と一致することを照合。
4. クライアントで新RPの再取得を確認し、静止・歩行・旋回、プレイヤー／フェンス接続で
   リードの方向・高さ・追従、Content Logを確認する。

復旧は人間が承認済み手順で整合するpackとworld参照を戻す。worldデータの破壊的復旧は行わない。

## 今回の検証結果

Python 3.12でlead 4件、version policy 2件、既存のoffline配備検査12件が成功。
変更したPythonのcompileとgit diff --checkも成功。新しい比較チェックが実際のPR #42
のbase/headをversion更新漏れとして拒否することを確認した。
標準python3は古く既存配備モジュールをimportできないため、検証には3.12を使用した。
avatar検査はPillow未導入で起動できず、依存を備えた環境での再実行が必要。
