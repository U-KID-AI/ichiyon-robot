# マネキン着替え・モルカー装備

## 実装範囲

- 管理画面 `/minecraft/cosmetics`：全体管理者限定の素材登録、プレビュー、3パック一括生成、プレイヤー前方へのマネキン配置、結果一覧。
- 登録画像・モデルはPostgreSQLのBYTEAで保存。リリースの差し替えで消えるローカルフォルダーへ保存しない。
- スキンPNGは64×64のclassic/slim。既存4人のPNG原本とvariant 0〜3は保持する。
- 共通エッグ、ページ付き選択UI、マネキンを使った着替え／解除、クリエイティブでの変更／撤去。
- プレイヤーのdynamic propertyに選択を保存し、spawn（参加・復活）時に同期済みEntity Propertyへ復元する。他クライアントも同じパックで描画する。
- モルカー3種類に頭・顔・首・背中の独立した同期／永続Entity Propertyを追加。装備モデルは元の骨格のコピーに重ね、既存の歩行・乗車・戦闘・リードを維持する。
- サバイバルの装備交換・返却、満杯時の取り外し中止、同tickの二重交換防止、無効化された個体や移動後のUI操作の中止。

マネキンは既存仕様どおり、狭い範囲を歩き、プレイヤーを見て、ダメージを受ける。今回の拡張で静止・無敵にはしない。
旧キャラクター別エッグをクリエイティブ一覧から外すが、既存マネキン・旧summonイベント・Discordの固定召喚／削除コマンドは維持する。

素材制作の詳細は [素材ガイド](../minecraft/cosmetics/README.md) を参照。

## 管理画面とBridge

セッション認証後に `has_global_admin` を毎回確認する。変更・出力のPOSTにはセッションに紐づくCSRFトークンが必要。
multipartを解析する前に合計サイズを制限し、PNG再エンコード、geometryの要素制限・有限数チェック・親ボーン検証を行う。
ファイル名は使用せず、生成パス・item ID・entity eventは採番済みIDから組み立てる。shell・任意コマンドは受け取らない。

Bridgeの既存認証付きpollへ `cosmetics_digest` を追加する。値はロード済みの生成済みカタログのハッシュ。
既存キューを優先し、空なら専用配置キューからclaimする。旧パックのpollは追加テーブルに触れない。
Web配置は60秒以内のheartbeatと一致するカタログを要求し、ゲーム側もID・カタログ・オンライン状態・配置先の空きを再確認する。
claimは `FOR UPDATE SKIP LOCKED` で一度だけ行う。期限切れを再実行しない。実行後の応答喪失はtimeoutとして人間が現地確認する。

管理画面の「読み込み済み」は**サーバースクリプトの素材一覧**の一致であり、各スマホのRPダウンロード完了まで検証するものではない。
追加画像はBDSのパック配信を通るため、HTTPアップロード直後の動的な画像差し替えは行わない。

## 初回反映の引き継ぎ

コード・migration・パックはレビュー用変更。テストで本番へ接続せず、DB更新・サーバー再起動・ワールド変更は実行しない。

1. Draft PRのコードとテストを確認し、人間がマージする。
2. 既存の承認済み手順でDBをバックアップし、migration `065_add_minecraft_cosmetics.sql` を適用してWebアプリを反映する。
3. ワールドとパックをバックアップし、3パック（avatar BP、avatar RP、import_structures）を同時に反映する。
4. ワールドのpack UUIDは維持し、参照versionを実際のmanifestに合わせる。既存のScript API実験設定・HTTP権限・Bridge設定は維持する。BDSの `config/2fbc1c02-0c4d-4e98-a851-c1e41337c7a8/permissions.json` の `allowed_modules` に `@minecraft/server-ui` を追加する（リポジトリの同名テンプレートを参照）。既存の許可先・secret設定を上書きしない。
5. サーバー再起動後、再接続してパックを取得する。Content Logにスクリプト・描画・プロパティのエラーがないことを確認する。
6. 下記の実機確認を終えてから利用開始する。

初期版はBP 1.0.31 / RP 1.0.35 / import 1.0.28。Web生成版はすべて1.1.<DB採番番号>とし、再生成ごとに番号が増える。
固定パック反映アダプターとWebのZIPはBP/RPを扱うため、上記のBDSモジュール許可は初回に別途反映する必要がある。許可がないままではUIのimportが拒否され、Bridgeスクリプトも起動しない。
Web登録済み素材を含むパックを使い始めたら、以降のコード更新時にも**更新後のアプリから同じDBカタログで再生成**して反映する。
Gitにある初期4人のみのパックで上書きすると追加素材が見えなくなる。DBの素材と番号は残るため、再生成・再反映で戻せる。
古いZIPを新しいコードへ上書きしない。ZIPにはそのアプリ版のBridgeスクリプトも含まれる。
DBバックアップの復元時は既配布パックより高いexport sequenceへ合わせ、パックversionを巻き戻さない。

## 実機で残る確認

自動テストはエンジンの描画やクライアント配信を再現しない。以下は人間による検証ワールドでの受入項目。

- PCとスマホの2人で既存4人に着替え、互いの表示・一人称の腕・防具・手持ち・泳ぎ・騎乗を確認する。
- 再ログイン、死亡からの復活、BDS再起動後に着替えと個体ごとの装備が戻ること。元のスキンへ戻ること。
- classic/slimを各1件アップロードして反映し、共通エッグ／Webから配置する。新規素材未反映時はWeb配置が拒否されること。
- テスト用アクセサリーで頭＋顔を併用し、3種のモルカーで移動・騎乗・戦闘・リード位置を確認する。
- 満杯時の返却、同時操作、メニュー中のログアウト、対象個体の消滅を確認する。
- 別のplayer.entity.json上書きパック、Persona、特殊スキン、見た目を制限するクライアント設定との組み合わせはこの実装だけでは保証できない。

プレイヤーのバニラ定義を保持して上書きするため、他のplayer定義パックとの併用やゲームバージョン更新には再確認が必要。
アクセサリーはキューブモデルのみ。装備中の個体を削除・死亡させた場合のアイテムドロップは追加していない。取り外してから個体を撤去する。

## 検証

```console
python scripts/build_minecraft_cosmetics.py --check
python scripts/check_minecraft_cosmetics.py
python scripts/check_minecraft_cosmetics_admin.py
node scripts/check_minecraft_cosmetics.mjs
python scripts/check_minecraft_avatars.py
node scripts/check_minecraft_avatar_bridge.mjs
python scripts/check_minecraft_posters.py
python scripts/check_minecraft_lead_anchors.py
python scripts/check_minecraft_pikachu.py
```

CIの `minecraft-cosmetics` jobでは使い捨てPostgreSQL 16で `check_minecraft_cosmetics_db.py` も実行し、migration再適用、同時採番、永続保存、claimの競合、期限切れ、内部HTTPの認証と結果登録を検証する。
