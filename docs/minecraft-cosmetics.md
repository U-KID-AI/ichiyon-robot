# マネキン着替え・モルカー装備

## 実装範囲

- 管理画面 `/minecraft/cosmetics`：全体管理者限定の素材登録、スキン削除、プレビュー、ゲームへの自動反映。
- 登録画像・モデルはPostgreSQLのBYTEAで保存。リリースの差し替えで消えるローカルフォルダーへ保存しない。
- スキンPNGは64×64のclassic/slim。既存4人のPNG原本とvariant 0〜3は保持する。
- 共通エッグ、ページ付き選択UI、マネキンを使った着替え／解除、クリエイティブでの配置／変更／撤去。
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
Webからのマネキン配置キューは廃止し、Bridge側では素材一覧のheartbeatだけを受け取る。配置はゲーム内の「マネキン共通エッグ」から行う。

管理画面の「読み込み済み」は**サーバースクリプトの素材一覧**の一致であり、各スマホのRPダウンロード完了まで検証するものではない。
追加画像はBDSのパック配信を通るため、HTTPアップロード直後の動的な画像差し替えは行わない。

## 利用者の操作

モルカーのアクセサリーは、手に持って対象へ使うと装着する。外すときは手を空にして、しゃがみながら対象へ使い、「アクセサリーを外す」メニューで部位を選ぶ（PCはShift＋右クリック）。サバイバルでは返却先の空きスロットが1つ必要。クリエイティブでは返却アイテムを増やさずに取り外す。

クリエイティブの装備カテゴリでは「モルカー：頭／顔／首／背中」の4つの折りたたみグループへまとめる。各部位の登録ID順に並び、先頭アイテムを代表アイコンにする。素材の追加・将来の除外もPack生成時の有効なカタログに追従し、空のグループは作らない。既存のほかのクリエイティブグループは保持する。

生成対象はBPの `item_catalog/crafting_item_catalog.json`、各itemの `menu_category.group`、RPのグループ表示名。Bedrock 1.21.60以降の[公式Crafting Item Catalog](https://learn.microsoft.com/en-us/minecraft/creator/documents/craftingitemcatalogdocumentation?view=minecraft-bedrock-stable)を使い、素材ID・装着処理・モデル・画像は変更しない。UI表示と開閉はクライアントで最終確認する。

既存管理画面の「マネキンとモルカー」から素材をアップロードし、「ゲームに反映」を押す。進行状況は自動更新される。完了後はMinecraftに入り直す。ZIPの移動やSSH操作は不要。
登録済みスキンは同じ画面から削除できる。削除したIDは永久欠番になり、通常一覧、ゲーム内の選択UI、次回生成パックから除外される。削除済みIDを保存していたプレイヤーは次回復元時に通常スキンへ戻る。

## 自動反映と運用

WebがDBの全素材と現在のアプリ版から3パックを生成し、既存の認証付きControl APIへ送る。利用者は送信先・保存先・実行コマンドを指定できない。APIは展開容量、パス、リンク、重複、既存pack UUIDとカタログを検証してから処理を受け付ける。

処理は固定のBDS専用ロックで直列化し、受付IDによる再送重複を防ぐ。停止→パック・参照・モジュール許可のバックアップ→3パック反映→起動→Docker healthとUDP応答確認の順で実行する。ワールドDBには手を加えず、他パック参照・既存HTTP許可を維持し、server-ui許可のみ追加する。失敗・サービス中断後は保存済み原本から復旧する。処理記録とバックアップはBDS側の `cosmetics-applications` に保存する。

反映用versionはDB採番と現行manifestの双方より古くならないように決め、相互依存とワールド参照も合わせる。通常の再起動時には古い配布元パックで上書きしない。画面では稼働Bridgeからのカタログheartbeatも確認する。

初回インストールはアプリのmigration 065とControl APIの2ファイル（`minecraft_control_api.py`, `minecraft_cosmetics_apply.py`）を同時期に更新する。利用者へこの初期設定を要求しない。今後Minecraftコードを更新する運用でも、DB素材を含む最新アプリの生成パックを同APIから適用する。固定アダプターから初期4人だけのGitパックで上書きして完了扱いにしない。

Webによる反映が有効な間、Git原本の固定BDSアダプターは共通ロック内で `cosmetics-applications/active.json` を検出し、書き込み・停止前に拒否する。待機中Runnerの定期catch-upは成功扱いをせず、そのまま通常のタスク受付へ戻る。Web管理を外すためにこの記録を削除しない。Minecraftコードの更新も、更新済み管理アプリとDB素材から生成してControl APIへ反映する。Git原本だけの明示デプロイも同じ保護対象であり、Web素材を消して成功扱いにしない。

## 実機で残る確認

自動テストはエンジンの描画やクライアント配信を再現しない。以下は人間による検証ワールドでの受入項目。

- PCとスマホの2人で既存4人に着替え、互いの表示・一人称の腕・防具・手持ち・泳ぎ・騎乗を確認する。
- 再ログイン、死亡からの復活、BDS再起動後に着替えと個体ごとの装備が戻ること。元のスキンへ戻ること。
- classic/slimを各1件アップロードして反映し、共通エッグから配置する。削除済み素材がゲーム内の選択UIから消えること。
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
python scripts/check_minecraft_cosmetics_apply.py
node scripts/check_minecraft_cosmetics.mjs
python scripts/check_minecraft_avatars.py
node scripts/check_minecraft_avatar_bridge.mjs
python scripts/check_minecraft_posters.py
python scripts/check_minecraft_lead_anchors.py
python scripts/check_minecraft_pikachu.py
```

CIの `minecraft-cosmetics` jobでは使い捨てPostgreSQL 16で `check_minecraft_cosmetics_db.py` も実行し、migration再適用、同時採番、永続保存、claimの競合、期限切れ、内部HTTPの認証と結果登録を検証する。
