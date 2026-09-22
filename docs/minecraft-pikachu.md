# ピカチュウMob

既存 `ichiyon:pikachu` の改善。identifier、Creativeスポーンエッグ、翻訳、
pack UUIDと最低エンジン版を維持する。BP 1.0.30、RP 1.0.34。
他Mob、他pack、参照mcstructure、worldデータ、自然スポーンルールは変更しない。

## 参照構造物とモデル

生成元は `scripts/build_pikachu_assets.py`。標準ライブラリだけを使い、
チェックイン済み `minecraft/behavior_packs/import_structures/structures/pikachu_reference.mcstructure`
をlittle-endian NBTとして解析する。サイズ8×23×20、15,607バイト。
非airはyellow_wool 607、black_wool 14、brown_wool 12、red_wool 8、
white_wool 2、nether_brick_fence 2。

旧モデルの独自の頭身、傾けた耳、鼻、口、尾などの造形を撤去し、構造物の
全非airブロックの配置から直接geometryを生成する。+Xの顔をモデル-Zへ向け、
1ブロックを0.7モデル単位に縮小する。左右非対称な輪郭、中空部分、顔、耳、
手足、背面の形を維持し、参照にない尻尾や装飾は追加しない。
同色ブロックは形を変えない直方体へ統合。口のfenceは支柱と隣接する横桟で表現する。
64×16 RGBA atlasの6色は羊毛5色と暗いnether brick色の単色近似であり、
元ブロックの表面テクスチャや照明の完全再現ではない。

Y=0〜3の左右の脚を独立boneへ割り当て、Y=4を支点にX軸で±30度、
逆位相に振る。脚全体は剛体で、膝関節や平行移動アニメーションはない。
全身の左右の揺れは最大2度。回転中は歩行を抑止する。

## 回転・復帰

旧実装には単発timerとpropertyのロックはあったが、timer経路以外の復帰や
読み込み時のリセットがなかった。報告された再インタラクト不能の実機原因は未確定。
本変更はtimer依存を廃止し、BPのサーバーanimation controllerを復帰の主体にする。

- `idle`: `spin_ready`でspin=0、spin_busy=false、移動AI・物理・interactを再追加。
- 接地したplayerのインタラクトでmobile/readyを外してspinningへ。
  spin_busy=trueにし、99%で+360度、1%で−360度を選ぶ。
- クライアントの0.45秒・その場で1回転するアニメーションと最終frame保持は維持。
  サーバーの回転状態は移動速度0、注視AIなし、重力なし、knockback resistance 1。
- サーバーの`spinning`はpropertyの変化に依存せず、state_time 0.7秒で必ず退出。
- `cooldown`入口の`spin_end`でspin=0、通常移動・物理を戻す。
  さらに0.5秒はspin_busy=trueかつinteractなしで再受付を拒否する。
- 0.5秒後に`idle`へ入り、interactを再初期化する。interaction自体の1.2秒cooldownも維持。
  イベント側もspinとspin_busyの両方で連打を拒否する。
- 初期状態も`cooldown`。スポーン／controller再初期化時に残留回転を解除し、
  0.5秒後に再受付する。古いspin propertyが残った個体も同じ復帰経路を通る。

サーバーtickの遅延やチャンク停止中は実時間どおりには進まない。
クライアント受信遅延や物理的な押し出しはオフライン検証では保証できない。

仕様参照:
[controllerからのentity eventと読み込み時の初期状態](https://learn.microsoft.com/en-us/minecraft/creator/documents/introductiontoaddentity?view=minecraft-bedrock-stable)、
[state_time](https://learn.microsoft.com/en-us/minecraft/creator/documents/update1.21.20?view=minecraft-bedrock-stable)、
[componentの再追加・削除](https://learn.microsoft.com/en-us/minecraft/creator/documents/entitybehaviorintroduction?view=minecraft-bedrock-stable)。

## 睡眠プレイヤーへの集合

基礎速度0.16、通常徘徊係数0.8は維持。睡眠追従係数を1.0から1.8へ変更し、
従来追従の1.8倍・通常徘徊の2.25倍の設定速度にする。
同dimension・12ブロック以内のplayer＋is_sleeping filter、非戦闘follow_mob、
停止距離2.5ブロックを維持する。起床時は睡眠filterから外れ、通常AIへ戻る設計。
Creative/無敵playerを除外するfilterや攻撃goalは追加しない。
複数個体の配置予約はなく、群れの重なりや障害物を含む経路は実機で要確認。

## 自動検証と残件

`python3 scripts/check_minecraft_pikachu.py`: 7テスト成功。
pack JSON、entity/geometry/texture/animation/controller参照、スポーンエッグ翻訳、
bone階層、UV、PNG CRC・展開サイズ、geometry再生成一致、羊毛全643セルの
位置・色の一致、脚の剛体回転、1回転の端点・単調性・無移動、両方向各100回の
状態遷移・各tickで50回連打・cooldown拒否・通常AI/物理/interact復帰・
旧ロック状態と回転中/cooldown中のcontroller再初期化、睡眠filterと速度比を検証。
状態テストはJSONの限定的な実行モデルであり、Bedrockエンジンそのものではない。

今回の追加検証:

- version-policy: 2テスト成功。既存`validate`でHEADと作業ツリーの両manifestの
  バージョン増加・header/modules一致を確認し、UUIDと最低エンジン版の維持も確認。
- タケツミAI check成功。Lead Anchor checkは4テスト成功、world-referenceの1件は
  既存moduleの`list[str]`注釈がPython 3.8非互換のためimportエラー。
- Avatar/Poster checkはPillow不足、Avatar Bridge checkはNode.js不足で起動不可。
- 変更Pythonのcompileと`git diff --check`成功。
- 差分を確認し、他の追跡済みMinecraft assetsと参照構造物に変更がないことを検証。

対応環境で未完了の回帰checkを再実行する必要がある。
CI workflowとRunner test registryは保護対象のため変更しておらず、本checkのCI登録は残件。
実機のContent Log、エッグ、連続インタラクト、同tick入力、チャンク再読込、
Creativeを含む睡眠追従・起床/離脱・経路探索・同期は未検証。
最終的な見た目・歩き方・回転の感触も未確認。

Runner指示によりcommit/push/PR/remote CI/review/merge、本番BDS deploy、
world pack参照同期、再起動、health、本番packとmain一致確認は実施しない。
これらと実機検証は、権限を持つ外部の運用・検証工程へ引き継ぐ。
