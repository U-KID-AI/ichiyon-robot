import { LUCY_TURN_TICKS, LUCY_COLUMNS, LUCY_ROWS, lucyGame } from "./lucy.js";
import { createLucyVenue, LUCY_BOX, LUCY_RECEPTION } from "./lucy_venue.js";

const TABLE = "lucy";
const LABEL = {
  lock: "金庫の当たり指定",
  attempt: "鍵を刺す",
  success: "開錠成功",
  recover: "鍵を返却",
  finished: "終了",
};

export function createLucyRuntime({ world, system, ActionFormData, ModalFormData, manager, installer }) {
  const venue = createLucyVenue({ world });
  const busy = new Set();
  let timer, generation = 0;
  const table = () => manager.view(TABLE);
  const tell = (player, message) => {
    try { player?.sendMessage(`[気分屋ルーシー] ${message}`); } catch { /* Player disconnected. */ }
  };
  const members = current => world.getAllPlayers().filter(player => current.players.includes(player.id));
  const notify = (current, message) => members(current).forEach(player => tell(player, message));
  function status(current) {
    if (!current?.state) return "対戦相手を待っています。";
    const s = current.state;
    return `ラウンド${s.round} / ${LABEL[s.phase]}\n開錠 ${s.remaining[0]} : ${s.remaining[1]} / 体力 ${Math.max(0, s.hp[0])} : ${Math.max(0, s.hp[1])}`;
  }
  function clearTimer() {
    if (timer !== undefined) system.clearRun(timer);
    timer = undefined;
    generation++;
  }
  function finish(current, message) {
    clearTimer();
    const result = manager.close(TABLE);
    notify(result ?? current, message);
    try { venue.end(result ?? current); } catch { /* Venue may not be installed. */ }
  }
  function complete(current) {
    if (current?.state?.phase !== "finished") return false;
    const winner = current.state.winner === null
      ? "引き分け"
      : `${members(current).find(player => player.id === current.players[current.state.winner])?.name ?? "勝者"}の勝利`;
    finish(current, `${winner}（${current.state.reason}）。受付から再戦できます。`);
    return true;
  }
  function arm(current, duration = LUCY_TURN_TICKS) {
    clearTimer();
    const activeGeneration = generation;
    timer = system.runTimeout(() => {
      timer = undefined;
      if (activeGeneration !== generation || !table()?.state || table().state.phase === "finished") return;
      const next = manager.systemAct(TABLE, "timeout");
      if (!complete(next)) {
        notify(next, "入力時間切れです。");
        void openActor(next);
      }
    }, duration);
  }
  function visuals(current) {
    try { venue.update(current); } catch (error) { notify(current, `会場表示: ${error.message}`); }
  }
  async function openActor(current) {
    if (!current?.state || current.state.phase === "finished") return;
    if (["success", "recover"].includes(current.state.phase)) {
      arm(current);
    }
    const player = members(current).find(candidate => candidate.id === current.players[current.state.actor]);
    if (player) void open(player);
  }
  async function lockRow(player, current) {
    const row = current.state.progress[current.players.indexOf(player.id)];
    if (row >= LUCY_ROWS) {
      tell(player, "当たり指定は完了しています。");
      return;
    }
    const form = new ModalFormData().title(`気分屋ルーシー：${row + 1}面`);
    for (let column = 0; column < LUCY_COLUMNS; column++) {
      form.toggle(`鍵穴 ${column + 1}`, { defaultValue: false });
    }
    const response = await form.show(player);
    if (response.canceled) return;
    const values = response.formValues?.map(value => value === true);
    const next = manager.act(player.id, current.token, "lock", values);
    notify(next, status(next));
    visuals(next);
    if (next.state.phase === "attempt") {
      arm(next);
      void openActor(next);
    }
  }
  async function attempt(player, current) {
    const seat = current.players.indexOf(player.id);
    if (current.state.actor !== seat || current.state.phase !== "attempt") {
      tell(player, status(current));
      return;
    }
    const visibleRow = LUCY_ROWS - current.state.remaining[seat];
    const form = new ActionFormData()
      .title(`気分屋ルーシー：${visibleRow + 1}面`)
      .body(status(current));
    for (let column = 0; column < LUCY_COLUMNS; column++) {
      const tried = current.state.attempted[seat][visibleRow][column] === 1;
      form.button(`鍵穴 ${column + 1}${tried ? "（使用済み）" : ""}`);
    }
    const response = await form.show(player);
    if (response.canceled || !Number.isInteger(response.selection)) return;
    const next = manager.act(player.id, current.token, "attempt", {
      row: visibleRow,
      column: response.selection,
    });
    visuals(next);
    if (complete(next)) return;
    notify(next, `${player.name}：${next.state.last.hit ? "開錠成功" : "開錠失敗"}\n${status(next)}`);
    for (const member of members(next)) {
      try {
        member.onScreenDisplay.setTitle(next.state.last.hit ? "開錠成功" : "開錠失敗", {
          stayDuration: 30, fadeInDuration: 5, fadeOutDuration: 5,
        });
      } catch { /* Optional client display. */ }
    }
    arm(next);
    void openActor(next);
  }
  async function resolve(player, current) {
    if (current.state.phase !== "success" && current.state.phase !== "recover") return;
    if (current.players[current.state.actor] !== player.id) {
      tell(player, "相手の操作を待っています。");
      return;
    }
    const success = current.state.phase === "success";
    const response = await new ActionFormData()
      .title(`気分屋ルーシー：${LABEL[current.state.phase]}`)
      .body(success ? "開錠した面を閉じ、次の手番へ進みます。" : "鍵を回収すると体力を失います。")
      .button(success ? "次の面へ" : "鍵を回収する")
      .show(player);
    if (response.canceled || response.selection !== 0) return;
    const next = manager.act(player.id, current.token, success ? "ack" : "recover", undefined);
    visuals(next);
    if (complete(next)) return;
    notify(next, status(next));
    arm(next);
    void openActor(next);
  }
  async function open(player) {
    if (busy.has(player.id)) return;
    busy.add(player.id);
    try {
      if (installer.installing()) throw new Error("会場設置中です。");
      const mine = manager.forPlayer(player.id);
      if (mine && mine.gameId !== TABLE) throw new Error("別のゲームに参加しています。");
      if (mine?.state?.phase === "lock") {
        await lockRow(player, mine);
        return;
      }
      if (mine?.state?.phase === "attempt") {
        await attempt(player, mine);
        return;
      }
      if (mine?.state && ["success", "recover"].includes(mine.state.phase)) {
        await resolve(player, mine);
        return;
      }
      const current = mine ?? table();
      const canJoin = !mine && (!current || current.players.length < lucyGame.seats);
      const response = await new ActionFormData()
        .title("気分屋ルーシー")
        .body(`${current ? status(current) : "2人対戦。互いの金庫に5面分の当たりを指定し、交互に鍵を試して5面すべてを開けたプレイヤーの勝利です。"}`)
        .button(mine ? "退出して試合を中止" : canJoin ? "参加する" : "閉じる")
        .show(player);
      if (response.canceled || response.selection !== 0) return;
      if (mine) {
        finish(mine, "参加者が退出したため中止しました。");
        return;
      }
      if (canJoin) {
        const joined = manager.join(TABLE, TABLE, player.id);
        notify(joined, status(joined));
        if (joined.state) {
          try {
            venue.start(joined);
            arm(joined);
            void openActor(joined);
          } catch (error) {
            finish(joined, `会場を準備できませんでした: ${error.message}`);
          }
        }
      }
    } catch (error) {
      tell(player, error.message ?? "操作に失敗しました。");
    } finally {
      busy.delete(player.id);
    }
  }

  world.beforeEvents.playerInteractWithEntity.subscribe(event => {
    if (!event.target.hasTag(LUCY_RECEPTION) && !event.target.hasTag(LUCY_BOX)) return;
    event.cancel = true;
    const player = event.player;
    system.run(() => { void open(player); });
  });
  world.afterEvents.playerLeave.subscribe(event => {
    const mine = manager.forPlayer(event.playerId);
    if (mine?.gameId === TABLE) finish(mine, "参加者が切断したため中止しました。");
    busy.delete(event.playerId);
  });
  system.afterEvents.scriptEventReceive.subscribe(event => {
    if (!event.id.startsWith("junket:lucy") || event.sourceEntity?.typeId !== "minecraft:player") return;
    const player = event.sourceEntity;
    const command = event.message.trim();
    if (event.id === "junket:lucymenu" || command === "menu") {
      void open(player);
    } else if (command === "leave") {
      const mine = manager.forPlayer(player.id);
      if (mine?.gameId === TABLE) finish(mine, "参加者が退出したため中止しました。");
    } else if (command === "install") {
      if (manager.tables.size) {
        tell(player, "ゲーム参加者がいる間は設置できません。");
      } else {
        void installer.install(player, "lucy").then(message => {
          venue.ensure();
          tell(player, message);
        }).catch(error => tell(player, error.message));
      }
    } else {
      try {
        if (command === "goto") venue.go(player);
        else if (command === "repair" && !table()) {
          venue.ensure();
          tell(player, "気分屋ルーシーの会場オブジェクトを補完しました。");
        }
      } catch (error) {
        tell(player, error.message);
      }
    }
  });
  return { open, venue };
}
