# Minecraft公開ソースの統合境界

2026-10-27に `main` の `60aad2dcaeb9afaa17663c1d059bd008de56c19f` を基準として、保全済みのローカル成果から後続コードを選択した。元の作業コピーや保全バックアップを上書きせず、共有Gitオブジェクトを使う専用worktreeで統合した。本番更新を実施した記録ではない。

## 保存するもの

| 領域 | 公開ソース | 非公開入力との境界 |
| --- | --- | --- |
| JUNKET | 17ゲームの後続runtime、GameManager、受付・意思決定、ワープ、修復、銀行接続、関連offline tests | 原作建築・モデル・テクスチャ・実配置データは含めない |
| 変換・生成 | 汎用変換、private bundle構築、native world操作のコード、自作代替会場の生成 | 原作入力とprivate profile/runtime manifestをローカルで指定する |
| Mob | 所有者・回復・追従、イカルン／トゥマイニのoptional behavior、マネキン散策 | structure由来のgeometry/textureはprivate overlay出力のみ |
| 装備 | 成田眼鏡の自作生成器・runtime・合成fixture | 本番用の登録は明示的なoverlayに含める |
| レコード | 登録生成器・再生処理の関連検証 | 提供MP3/OGGと私的な追加catalogを配布しない |
| 配備 | static overlay保持、native release、隔離検証、復旧契約 | 候補ZIP、配備証明、world、接続先・認証を同梱しない |
| 容量管理 | 内容共通化、容量警告、再利用、dry-run保持判定、記録 | CAS storeと世代ディレクトリは非公開の復旧データ |

管理画面とControl APIは最新mainを正本とする。古い作業コピーのファイルを丸ごと戻さない。Big/Akkiのmaterialも最新mainを維持する。既存の実装を別名コピーとして増やさない。

## 本番との対応

統合前の読み取り専用照合では、JUNKETのJavaScript 55本中52本が現行ローカルソースと一致した。相違した `bank_hub_generated.js`、`all17_original_generated.js`、`junket_warp_generated.js` はprivate生成物／配備固有の配置情報を含み、そのまま公開しない。

同日取得済みの本番スナップショットと、Mob・装備・所有者管理のruntime 9本もSHA-256で一致した。private本番のentrypointにはoptional機能の登録があるが、公開標準packには素材なしのMobを無条件登録しない。

稼働Control APIは上記mainと一致する。`minecraft_cosmetics_apply.py` は容量管理追加前の保存済みソースと一致し、static overlayとJUNKET overlayも現行ソースと一致する。容量管理と公開入力境界の調整は後続のローカル変更であり、本番適用済みと扱わない。照合のパス・ハッシュ一覧と実配備証明は制限付きローカル監査領域に保持する。

## 公開しないもの

- 原作ZIP、原作由来の建築・モデル・テクスチャ・structure、抽出した座標表。
- 提供されたMP3/OGG、private登録catalog、配布可否の確認できない素材。
- world、LevelDB、サーバーバックアップ、配備候補、CAS blob。
- 認証、秘密鍵、トークン、プレイヤー等の個人データ、実配備証明・運用固有設定。
- `.codex-tmp`、古い作業コピー、重複生成物、検証用の大きな環境。

これらを「不要」と判定して削除していない。既存のprivate保存場所と検証済み保全バックアップを保持する。非公開素材なしで本番の見た目や配置まで再現できるという意味ではない。

## 復元・再生成の前提

公開標準packのgenerator/checkは非公開原作を必要としない。原作を使う生成処理には、別管理の入力とprivate profile/runtime manifestが必要になる。原作固有の大きな座標レシピを含む生成器は、この公開統合の対象から外し、保存済みprivate版を復元元とする。公開版からそれらの実配置レシピを自動復元することはできない。

既存バックアップの読取り互換、新CAS世代の必要ファイル、容量制限と復旧例外は [Minecraft容量管理](minecraft-storage-management.md) を参照。新CAS世代の復旧には対応readerとstoreを一緒に保持する。

## 検証の範囲

既存mainのoffline回帰と、追加した合成fixtureを利用する。標準entrypointからの相対import、pack UUID/version/dependency、公開generatorの再現、private入力の拒否境界を確認する。原作データや実音源をCIへ渡さない。BDS起動、全ゲームの実機完走、本番配備をこの統合のテストとして実行しない。

Push対象は個別の公開ファイル一覧から登録し、新規コミット範囲を検査する。過去のmain履歴を書き換えず、既存作業コピーの未コミット成果も破棄しない。
