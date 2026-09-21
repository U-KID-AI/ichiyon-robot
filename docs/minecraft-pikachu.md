# ピカチュウMob

`ichiyon:pikachu` とCreativeスポーンエッグを既存Avatar BP/RPへ追加する。
自然スポーンルール、既存Mob、Bridge、worldデータには変更を加えない。
BPは1.0.29、RPは1.0.33。既存UUIDと最低エンジン版を維持する。

## 参照構造物とモデル

`minecraft/behavior_packs/import_structures/structures/pikachu_reference.mcstructure`
をlittle-endian NBTとして解析した。サイズはX/Y/Z = 8/23/20、
全15,607バイトを読み切り、第一block layerはair 3,035、yellow_wool 607、
black_wool 14、brown_wool 12、red_wool 8、white_wool 2、
nether_brick_fence 2。赤い頬はX=6・Y=11–12、白い目の光はX=6・Y=15、
黒色はY=14–22にあり、顔は+X側、耳は上方へ伸びる構成。
背面の茶色はX=0–1・Y=6–10。口元のfenceはX=7・Y=11。

ブロックを縮小コピーせず、幅9モデル単位の頭、小さな胴、左右の耳と黒い先端、
頬、目と光、口、背中の2本線、茶色い根元と稲妻形の尾を独立cube/boneで構成した。
足には子関節を設けない。64×16 RGBAのUV atlasは6色の領域を使い、
各faceを余白付きの色領域へ割り当てる。生成元は
`scripts/build_pikachu_assets.py`。標準ライブラリだけでgeometry/PNGを再生成できる。

## 行動

- 通常は速度0.16を基準にrandom stroll。歩行時の全身rollは最大2度、
  足はZ方向に最大0.65モデル単位だけ交互に動く。膝・足の回転はない。
- 接地中のインタラクトでserver同期property `ichiyon:spin` を更新。
  99%で+360度、1%で−360度。client controllerが0.45秒の非ループ回転を再生し、
  最終frameを保持する。rootの座標はアニメーションしない。
- 回転時は移動・注視・睡眠追尾AIを含むmobile groupを外し、速度0、重力なし、
  knockback resistance 1にする。0.7秒timerで復帰し、インタラクトのcooldownは1.2秒。
  spin中の再イベントもproperty条件で拒否する。空中での開始は受け付けない。
- 視認・到達可能な同dimension内12ブロックの睡眠中playerを標準target selectorで探す。
  attack component/攻撃goalは追加しない。targetの条件を再評価し、起床後は追尾を停止。
  move_around_targetで2.2–3.2ブロックの周囲360度にランダムな行き先を選ぶ。
  高低差の探索は1ブロック。固定座標へのteleportやベッド上への直接移動は行わない。

公式仕様:
[睡眠フィルター](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/filters/is_sleeping?view=minecraft-bedrock-stable)、
[targetの継続再評価](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitygoals/minecraftbehavior_nearest_attackable_target?view=minecraft-bedrock-stable)、
[周囲への移動](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitygoals/minecraftbehavior_move_around_target?view=minecraft-bedrock-stable)。
Entity formatは1.26.10で、周回距離にはこのformatのlegacy min/max objectを使う。

## 検証と未完了事項

`python3 scripts/check_minecraft_pikachu.py` は全pack JSON、identifier重複、
Entity/geometry/texture/animation参照、bone階層、UV、PNG CRC・展開サイズ、
エッグ翻訳、1回転の端点・単調性・無移動、両random分岐、連打拒否、timer復帰、
睡眠条件・距離制限・攻撃goal不在、client回転状態の開始・保持・復帰と歩行抑止を検証する。
CIへの登録は未完了。保護対象の`.github/workflows/checks.yml`は変更せず、
新規checkは上記コマンドでローカル実行する。
JSONのイベント評価テストはBedrockエンジンの代替ではない。

今回のoffline結果: 新規6テスト、version-policy 2テスト、HEAD manifestと
作業ツリーのBP/RPに対する既存`validate`、タケツミAI check、変更Pythonの
compile、`git diff --check`は成功。Avatar/Poster checkはPillow不足、
Avatar Bridge checkはNode.js不足で起動できなかった。Lead Anchor checkは
4テスト成功、world-referenceの純粋関数テスト1件が既存module import時の
Python 3.8非互換でエラー。これらの回帰checkは対応環境で再実行が必要。

本番反映前には別途、ローカルBedrockでContent Log、Creativeでのエッグ、
睡眠targetの取得（特にCreative/無敵プレイヤーと平和難易度）、複数ベッド・
障害物・狭い場所での経路、起床/離脱/チャンク再読込、回転開始時の残留速度・
水流/接触による位置変化、通信遅延時の一回転を確認する必要がある。
周回AIは候補地点を分散させるが、個体別の予約枠を設けていないため、
群れの重なりやベッドを横切る経路がないことはオフラインでは保証していない。
見た目、歩き方、時計回りの向きと回転の感触、自然な群がり方も実機で要確認。

この作業はRunnerの許可範囲での編集とoffline検証のみ。
PR/remote CI/review/merge、本番BDS deploy、world pack参照同期、再起動、
health、本番packとmainの一致確認は実施しない。
