import {
  SILENCE_DURATIONS, SILENCE_TURN_TICKS, silenceGame,
} from "./silence.js";
import {
  createSilenceVenue, SILENCE_CONTROL, SILENCE_GUARD, SILENCE_RECEPTION,
} from "./silence_venue.js";

const TABLE = "silence";
const LABEL = {
  arrange: "ジャケットを並べる",
  choose: "ジャケットを選ぶ",
  playback: "レコード再生中",
  return: "レコードを返却",
  finished: "終了",
};

export function createSilenceRuntime({ world, system, ActionFormData, ModalFormData, manager, installer }) {
  const venue = createSilenceVenue({ world });
  const busy = new Set();
  let timer, playback, generation = 0;
  const table = () => manager.view(TABLE);
  const tell = (player, message) => {
    try { player?.sendMessage(`[サウンド・オブ・サイレンス] ${message}`); } catch { /* Player disconnected. */ }
  };
  const members = current => world.getAllPlayers().filter(player => current.players.includes(player.id));
  const notify = (current, message) => members(current).forEach(player => tell(player, message));
  function status(current) {
    if (!current?.state) return "対戦相手を待っています。";
    const s = current.state;
    return `${LABEL[s.phase]}\n合計 ${s.totalSeconds}秒 / 共有残り ${Math.max(0, s.remainingTicks)}t\n体力 ${Math.max(0, s.hp[0])} : ${Math.max(0, s.hp[1])} / 耳栓 ${s.guard[0] ? "有" : "無"} : ${s.guard[1] ? "有" : "無"}`;
  }
  function stopRuns() {
    if (timer !== undefined) system.clearRun(timer);
    if (playback !== undefined) system.clearRun(playback);
    timer = playback = undefined;
    generation++;
  }
  function finish(current, message) {
    stopRuns();
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
  function arm(current, duration = SILENCE_TURN_TICKS) {
    if (timer !== undefined) system.clearRun(timer);
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
  function otherInside(current) {
    const s = current.state;
    const other = members(current).find(player => player.id === current.players[1 - s.playSeat]);
    return other ? venue.isInsideSoundRoom(other) : false;
  }
  function startPlayback(current) {
    if (timer !== undefined) system.clearRun(timer);
    timer = undefined;
    if (playback !== undefined) system.clearRun(playback);
    try { venue.playbackPositions(current); } catch (error) { notify(current, error.message); }
    visuals(current);
    const activeGeneration = generation;
    let ticks = 0;
    playback = system.runInterval(() => {
      if (activeGeneration !== generation) return;
      const live = table();
      if (!live?.state || live.state.phase !== "playback") {
        if (playback !== undefined) system.clearRun(playback);
        playback = undefined;
        return;
      }
      const next = manager.systemAct(TABLE, "tick", { otherInside: otherInside(live) });
      if (complete(next)) return;
      ticks++;
      if (ticks % 10 === 0) {
        visuals(next);
        for (const player of members(next)) {
          player.onScreenDisplay?.setActionBar(`${next.state.last.duration}秒盤 / ${Math.max(0, next.state.remainingTicks)} / HP ${Math.max(0, next.state.hp[0])} : ${Math.max(0, next.state.hp[1])}`);
        }
      }
      if (next.state.phase === "return") {
        if (playback !== undefined) system.clearRun(playback);
        playback = undefined;
        try { venue.selectionPositions(next); } catch { /* Venue is optional in logic tests. */ }
        notify(next, "レコード再生が終了しました。返却してください。");
        arm(next);
        void openActor(next);
      }
    }, 1);
  }
  async function openActor(current) {
    if (!current?.state || current.state.phase === "finished") return;
    const actorId = current.players[current.state.actor];
    const player = members(current).find(candidate => candidate.id === actorId);
    if (player) void open(player);
  }
  async function arrange(player, current) {
    const options = SILENCE_DURATIONS.map(value => `${value}秒`);
    const form = new ModalFormData().title("サウンド・オブ・サイレンス：ジャケット配置");
    for (let slot = 0; slot < 3; slot++) {
      form.dropdown(`位置 ${slot + 1}`, options, { defaultValue: [1, 2, 0][slot] });
    }
    const response = await form.show(player);
    if (response.canceled) return;
    const order = response.formValues?.map(index => SILENCE_DURATIONS[index]);
    const next = manager.act(player.id, current.token, "arrange", order);
    visuals(next);
    notify(next, status(next));
    arm(next);
    void openActor(next);
  }
  async function choose(player, current) {
    const form = new ActionFormData()
      .title("サウンド・オブ・サイレンス：ジャケット選択")
      .body(status(current));
    for (let slot = 0; slot < 3; slot++) form.button(`ジャケット ${slot + 1}`);
    const response = await form.show(player);
    if (response.canceled || !Number.isInteger(response.selection)) return;
    const next = manager.act(player.id, current.token, "choose", response.selection);
    visuals(next);
    if (complete(next)) return;
    notify(next, `レコードは${next.state.last.duration}秒でした。`);
    if (next.state.phase === "playback") startPlayback(next);
    else {
      arm(next);
      void openActor(next);
    }
  }
  async function returnRecord(player, current) {
    const response = await new ActionFormData()
      .title("サウンド・オブ・サイレンス：レコード返却")
      .body(status(current))
      .button("レコードを返却する")
      .show(player);
    if (response.canceled || response.selection !== 0) return;
    const next = manager.act(player.id, current.token, "return", undefined);
    visuals(next);
    notify(next, status(next));
    arm(next);
    void openActor(next);
  }
  async function useGuard(player, current) {
    const seat = current.players.indexOf(player.id);
    if (current.state.phase !== "playback") throw new Error("耳栓はレコード再生中に使用できます。");
    if (current.state.guard[seat]) {
      tell(player, "耳栓はすでに使用しています。");
      return;
    }
    const next = manager.act(player.id, current.token, "guard", undefined);
    notify(next, `${player.name}が耳栓を使用しました。`);
    visuals(next);
  }
  async function open(player) {
    if (busy.has(player.id)) return;
    busy.add(player.id);
    try {
      if (installer.installing()) throw new Error("会場設置中です。");
      const mine = manager.forPlayer(player.id);
      if (mine && mine.gameId !== TABLE) throw new Error("別のゲームに参加しています。");
      if (mine?.state?.phase === "arrange" && mine.state.actor === mine.players.indexOf(player.id)) {
        await arrange(player, mine);
        return;
      }
      if (mine?.state?.phase === "choose" && mine.state.actor === mine.players.indexOf(player.id)) {
        await choose(player, mine);
        return;
      }
      if (mine?.state?.phase === "return" && mine.state.actor === mine.players.indexOf(player.id)) {
        await returnRecord(player, mine);
        return;
      }
      if (mine?.state?.phase === "playback") {
        await useGuard(player, mine);
        return;
      }
      const current = mine ?? table();
      const canJoin = !mine && (!current || current.players.length < silenceGame.seats);
      const response = await new ActionFormData()
        .title("サウンド・オブ・サイレンス")
        .body(`${current ? status(current) : "2人対戦。互いの0秒・20秒・30秒ジャケットを伏せて並べ、相手に1枚選ばせます。選ばれたレコードを返却まで聴き、先に体力を失った側が敗北。合計100秒を超えると共有時間切れにより致命ダメージが発生します。"}`)
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
    const guard = event.target.hasTag(SILENCE_GUARD);
    if (!guard && !event.target.hasTag(SILENCE_RECEPTION) && !event.target.hasTag(SILENCE_CONTROL)) return;
    event.cancel = true;
    const player = event.player;
    system.run(() => {
      const mine = manager.forPlayer(player.id);
      if (guard && mine?.gameId === TABLE && mine.state?.phase === "playback") {
        void useGuard(player, mine).catch(error => tell(player, error.message));
      } else {
        void open(player);
      }
    });
  });
  world.afterEvents.playerLeave.subscribe(event => {
    const mine = manager.forPlayer(event.playerId);
    if (mine?.gameId === TABLE) finish(mine, "参加者が切断したため中止しました。");
    busy.delete(event.playerId);
  });
  system.afterEvents.scriptEventReceive.subscribe(event => {
    if (!event.id.startsWith("junket:silence") || event.sourceEntity?.typeId !== "minecraft:player") return;
    const player = event.sourceEntity;
    const command = event.message.trim();
    if (event.id === "junket:sosmenu" || command === "menu") {
      void open(player);
    } else if (command === "leave") {
      const mine = manager.forPlayer(player.id);
      if (mine?.gameId === TABLE) finish(mine, "参加者が退出したため中止しました。");
    } else if (command === "install") {
      if (manager.tables.size) {
        tell(player, "ゲーム参加者がいる間は設置できません。");
      } else {
        void installer.install(player, "silence").then(message => {
          venue.ensure();
          tell(player, message);
        }).catch(error => tell(player, error.message));
      }
    } else {
      try {
        if (command === "goto") venue.go(player);
        else if (command === "repair" && !table()) {
          venue.ensure();
          tell(player, "サウンド・オブ・サイレンスの会場オブジェクトを補完しました。");
        }
      } catch (error) {
        tell(player, error.message);
      }
    }
  });
  return { open, venue };
}
