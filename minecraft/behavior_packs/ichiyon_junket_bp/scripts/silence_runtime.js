import { createDecisionPrompts } from "./decision_prompts.js";
import { eligibleSpecialDecisions, specialDecisionWindow } from "./special_decisions.js";
import { isServiceGuide } from "./service_controls.js";
import { createRuntimeForms, requestCatalogReturn, showReceptionNavigation } from "./venue_presentation.js";
import {
  SILENCE_DURATIONS, SILENCE_TURN_TICKS, silenceGame,
} from "./silence.js";
import {
  createSilenceVenue, SILENCE_CONTROL, SILENCE_COVER, SILENCE_DISC, SILENCE_GUARD,
  SILENCE_JACKET, SILENCE_RECEPTION, SILENCE_RETURNER, SILENCE_SETTER,
} from "./silence_venue.js";

const TABLE = "silence";
const LABEL = {
  arrange: "ジャケットを並べる",
  choose: "ジャケットを選ぶ",
  set: "レコードをセット",
  playback: "レコード再生中",
  return: "レコードを返却",
  finished: "終了",
};

export function createSilenceRuntime({ world, system, ActionFormData, ModalFormData, manager, installer }) {
  const runtimeForms = createRuntimeForms({ ActionFormData, ModalFormData, world, system });
  ({ ActionFormData, ModalFormData } = runtimeForms);
  const venue = createSilenceVenue({ world });
  const busy = new Set();
  const disconnectTimers = new Map();
  let timer, playback, generation = 0;
  const table = () => manager.view(TABLE);
  const tell = (player, message) => {
    try { player?.sendMessage(`[サウンド・オブ・サイレンス] ${message}`); } catch { /* Player disconnected. */ }
  };
  const members = current => world.getAllPlayers().filter(player => current.players.includes(player.id));
  const allMembersOnline = current => members(current).length === current.players.length;
  const notify = (current, message) => members(current).forEach(player => tell(player, message));
  const decisions = createDecisionPrompts({ world, system, read: table,
    eligible: current => allMembersOnline(current) ? (current.decisionSeats ?? eligibleSpecialDecisions(TABLE, current.state)) : [],
    key: (current, seat) => `${current.sessionToken}:${manager.forPlayer(current.players[seat])?.decisionKey ?? specialDecisionWindow(TABLE, current.state, seat)}:${current.decisionSerials?.[seat] ?? 0}`,
    busy: id => busy.has(id), open: player => open(player), notify: tell });
  const decide = (playerId, current, action, value) => {
    if (!allMembersOnline(current)) throw new Error("対戦相手の再接続を待っています。");
    return manager.actDecision ? manager.actDecision(playerId, current, action, value)
      : manager.act(playerId, current.token, action, value);
  };

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
    runtimeForms.invalidate();
    busy.clear();
    decisions.clear();
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
    decisions.schedule(current);
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
      if (!allMembersOnline(live)) return;
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
  async function openActor(current) { decisions.schedule(current); }
  async function arrange(player, current) {
    const options = SILENCE_DURATIONS.map(value => `${value}秒`);
    const form = new ModalFormData().title("サウンド・オブ・サイレンス：ジャケット配置");
    for (let slot = 0; slot < 3; slot++) {
      form.dropdown(`位置 ${slot + 1}`, options, { defaultValue: [1, 2, 0][slot] });
    }
    const response = await form.show(player);
    if (response.canceled) return;
    const order = response.formValues?.map(index => SILENCE_DURATIONS[index]);
    const next = decide(player.id, current, "arrange", order);
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
    await submitChoice(player, current, response.selection);
  }
  async function submitChoice(player, current, slot) {
    const next = decide(player.id, current, "choose", slot);
    visuals(next);
    if (complete(next)) return;
    notify(next, `レコードは${next.state.last.duration}秒でした。`);
    arm(next);
    decisions.schedule(next);
    if (next.state.phase === "set") {
      tell(player, "操作メニューでレコードをセットしてください。");
    } else if (next.state.phase === "return") {
      tell(player, "レコードを返却してください。");
    }
  }
  async function arrangeSelect(player, current, slot) {
    if (busy.has(player.id)) return;
    busy.add(player.id);
    try {
      const next = decide(player.id, current, "arrange-select", slot);
      visuals(next);
      const selected = next.state.arrangeSelected;
      const order = manager.forPlayer(player.id).state.arrangeDraft;
      tell(player, selected === null
        ? `並び: ${order.join("秒 / ")}秒`
        : `位置${selected + 1}を選択中。入れ替える位置を選んでください。`);
    } catch (error) {
      tell(player, error.message ?? "ジャケットを選択できませんでした。");
    } finally {
      busy.delete(player.id);
    }
  }
  async function arrangeConfirm(player, current) {
    if (busy.has(player.id)) return;
    busy.add(player.id);
    try {
      const next = decide(player.id, current, "arrange-confirm", undefined);
      visuals(next);
      notify(next, status(next));
      arm(next);
      void openActor(next);
    } catch (error) {
      tell(player, error.message ?? "ジャケット配置を確定できませんでした。");
    } finally {
      busy.delete(player.id);
    }
  }
  async function setRecord(player, current) {
    const response = await new ActionFormData()
      .title("サウンド・オブ・サイレンス：レコード設置")
      .body(status(current))
      .button("レコードをセットする")
      .show(player);
    if (response.canceled || response.selection !== 0) return;
    await placeRecord(player, current, true);
  }
  async function placeRecord(player, current, alreadyBusy = false) {
    if (!alreadyBusy && busy.has(player.id)) return;
    if (!alreadyBusy) busy.add(player.id);
    try {
      const next = decide(player.id, current, "set", undefined);
      visuals(next);
      if (complete(next)) return;
      if (next.state.phase === "playback") {
        notify(next, "レコード再生を開始します。");
        startPlayback(next);
      } else {
        notify(next, "0秒レコードの再生が終了しました。返却してください。");
        arm(next);
        decisions.schedule(next);
      }
    } catch (error) {
      tell(player, error.message ?? "レコードをセットできませんでした。");
    } finally {
      if (!alreadyBusy) busy.delete(player.id);
    }
  }
  async function physicalReturn(player, current) {
    if (busy.has(player.id)) return;
    busy.add(player.id);
    try {
      const next = decide(player.id, current, "return", undefined);
      visuals(next);
      notify(next, status(next));
      arm(next);
      void openActor(next);
    } catch (error) {
      tell(player, error.message ?? "レコードを返却できませんでした。");
    } finally {
      busy.delete(player.id);
    }
  }
  async function returnRecord(player, current) {
    const response = await new ActionFormData()
      .title("サウンド・オブ・サイレンス：レコード返却")
      .body(status(current))
      .button("レコードを返却する")
      .show(player);
    if (response.canceled || response.selection !== 0) return;
    const next = decide(player.id, current, "return", undefined);
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
    const next = decide(player.id, current, "guard", undefined);
    notify(next, `${player.name}が耳栓を使用しました。`);
    visuals(next);
  }
  async function open(player, navigation = false) {
    if (manager.maintenance) { tell(player, "会場を確認中です。完了後に再操作してください。"); return; }
    if (busy.has(player.id)) return;
    busy.add(player.id);
    try {
      if (installer.installing()) throw new Error("会場設置中です。");
      const mine = manager.forPlayer(player.id);
      if (mine && mine.gameId !== TABLE) throw new Error("別のゲームに参加しています。");
      const current = mine ?? table();
      const canJoin = !mine && (!current || (!current.state && current.players.length < silenceGame.seats));
      let navigationAction;
      if (navigation || !mine?.state) {
        navigationAction = await showReceptionNavigation({ ActionFormData, player, gameId: TABLE,
          title: "サウンド・オブ・サイレンス", body: current ? status(current) : "参加者募集中です。",
          rules: "2人対戦。互いの0秒・20秒・30秒ジャケットを伏せて並べ、相手に1枚選ばせます。選ばれたレコードを返却まで聴き、先に体力を失った側が敗北。合計100秒を超えると共有時間切れにより致命ダメージが発生します。", joined: !!mine, canJoin,
          canAct: !!mine?.state && (mine.state.phase === "playback" || mine.state.actor === mine.players.indexOf(player.id)) });
        if ((manager.forPlayer(player.id)?.token ?? null) !== (mine?.token ?? null) ||
            (table()?.token ?? null) !== (current?.token ?? null)) throw new Error("画面が古くなりました。");
        if (!navigationAction) return;
        if (navigationAction === "catalog") {
          if (mine) tell(player, "退出を選んでからカタログへ戻ってください。");
          else await requestCatalogReturn(player, world);
          return;
        }
        if (navigationAction === "leave" && mine) {
          finish(mine, "参加者が退出したため中止しました。");
          return;
        }
      }
      if (mine?.state?.phase === "arrange" && mine.state.actor === mine.players.indexOf(player.id)) {
        await arrange(player, mine);
        return;
      }
      if (mine?.state?.phase === "choose" && mine.state.actor === mine.players.indexOf(player.id)) {
        await choose(player, mine);
        return;
      }
      if (mine?.state?.phase === "set" && mine.state.actor === mine.players.indexOf(player.id)) {
        await setRecord(player, mine);
        return;
      }
      if (mine?.state?.phase === "return" && mine.state.actor === mine.players.indexOf(player.id)) {
        await returnRecord(player, mine);
        return;
      }
      if (mine?.state?.phase === "playback") {
        const answer = await new ActionFormData().title("耳栓・音響室")
          .body(`${status(mine)}\n耳栓は音によるダメージを軽減します。待機側は音響室の外へ移動できます。`)
          .button("耳栓を使う").button("今は使わず移動する").show(player);
        if (answer.canceled || answer.selection !== 0) return;
        await useGuard(player, mine);
        return;
      }
      if (!["join", "rematch"].includes(navigationAction)) return;
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

  function fixtureSlot(target) {
    const stored = target.getDynamicProperty("junket:silence_slot");
    if (Number.isInteger(stored)) return stored;
    const role = target.getDynamicProperty("junket:silence_role");
    const match = typeof role === "string" ? /^(?:jacket|cd)([0-2])$/.exec(role) : null;
    if (match) return Number(match[1]);
    const tags = target.getTags?.() ?? [];
    const tag = tags.find(value => /^junket:silence:fixture:(?:jacket|cd)[0-2]$/.test(value));
    return tag ? Number(tag.at(-1)) : -1;
  }

  world.beforeEvents.playerInteractWithEntity.subscribe(event => {
    if (isServiceGuide(world, event.target)) return;
    const guard = event.target.hasTag(SILENCE_GUARD);
    const jacket = event.target.hasTag(SILENCE_JACKET);
    const disc = event.target.hasTag(SILENCE_DISC);
    const cover = event.target.hasTag(SILENCE_COVER);
    const setter = event.target.hasTag(SILENCE_SETTER);
    const returner = event.target.hasTag(SILENCE_RETURNER);
    if (!guard && !jacket && !disc && !cover && !setter && !returner &&
        !event.target.hasTag(SILENCE_RECEPTION) && !event.target.hasTag(SILENCE_CONTROL)) return;
    event.cancel = true;
    const player = event.player;
    system.run(() => {
      const mine = manager.forPlayer(player.id);
      if (guard && mine?.gameId === TABLE && mine.state?.phase === "playback") {
        void useGuard(player, mine).catch(error => tell(player, error.message));
      } else if ((jacket || disc) && mine?.gameId === TABLE &&
          mine.state?.phase === "arrange" && mine.state.actor === mine.players.indexOf(player.id)) {
        const slot = fixtureSlot(event.target);
        if (slot >= 0) void arrangeSelect(player, mine, slot);
      } else if ((jacket || disc) && mine?.gameId === TABLE &&
          mine.state?.phase === "choose" && mine.state.actor === mine.players.indexOf(player.id)) {
        const slot = fixtureSlot(event.target);
        if (slot >= 0) void submitChoice(player, mine, slot).catch(error => tell(player, error.message));
      } else if (cover && mine?.gameId === TABLE && mine.state?.phase === "arrange" &&
          mine.state.actor === mine.players.indexOf(player.id)) {
        void arrangeConfirm(player, mine);
      } else if (setter && mine?.gameId === TABLE && mine.state?.phase === "set" &&
          mine.state.actor === mine.players.indexOf(player.id)) {
        void placeRecord(player, mine);
      } else if (returner && mine?.gameId === TABLE && mine.state?.phase === "return" &&
          mine.state.actor === mine.players.indexOf(player.id)) {
        void physicalReturn(player, mine);
      } else {
        void open(player, event.target.hasTag(SILENCE_RECEPTION));
      }
    });
  });
  world.afterEvents.playerLeave.subscribe(event => {
    runtimeForms.invalidate([event.playerId]);
    decisions.forget(event.playerId);
    busy.delete(event.playerId);
    const mine = manager.forPlayer(event.playerId);
    if (mine?.gameId !== TABLE) return;
    stopRuns();
    const previous = disconnectTimers.get(event.playerId);
    if (previous !== undefined) system.clearRun(previous);
    const token = mine.token;
    const disconnect = system.runTimeout(() => {
      disconnectTimers.delete(event.playerId);
      const live = manager.forPlayer(event.playerId);
      if (live?.token !== token) return;
      if (world.getAllPlayers().some(player => player.id === event.playerId)) return;
      finish(live, "参加者が切断したため中止しました。");
    }, 200);
    disconnectTimers.set(event.playerId, disconnect);
  });

  function waitForMissing(current) {
    const session = manager.tables.get(current.id);
    for (const id of current.players) {
      if (world.getAllPlayers().some(player => player.id === id) || disconnectTimers.has(id)) continue;
      disconnectTimers.set(id, system.runTimeout(() => {
        disconnectTimers.delete(id);
        const live = manager.forPlayer(id);
        if (manager.tables.get(current.id) !== session || !live ||
            world.getAllPlayers().some(player => player.id === id)) return;
        finish(live, "参加者が再接続しなかったため中止しました。");
      }, 200));
    }
  }
  function resume(joinedPlayerId = null) {
    if (joinedPlayerId) {
      const pending = disconnectTimers.get(joinedPlayerId);
      if (pending !== undefined) system.clearRun(pending);
      disconnectTimers.delete(joinedPlayerId);
    }
    stopRuns();
    const current = table();
    if (!current) return;
    visuals(current);
    if (complete(current)) return;
    if (!current.state) return;
    if (!allMembersOnline(current)) { waitForMissing(current); return; }
    if (current.state.phase === "playback") {
      startPlayback(current);
      return;
    }
    arm(current);
    void openActor(current);
  }
  system.afterEvents.scriptEventReceive.subscribe(event => {
    if (!event.id.startsWith("junket:silence") || event.sourceEntity?.typeId !== "minecraft:player") return;
    const player = event.sourceEntity;
    const command = event.message.trim();
    if (event.id === "junket:sosmenu" || command === "menu") {
      void open(player, true);
    } else if (command === "leave") {
      const mine = manager.forPlayer(player.id);
      if (mine?.gameId === TABLE) finish(mine, "参加者が退出したため中止しました。");
    } else if (command === "install") {
      if (manager.tables.size) {
        tell(player, "ゲーム参加者がいる間は設置できません。");
      } else {
        try {
          venue.ensure();
          venue.verify();
          tell(player, "既存のサウンド・オブ・サイレンス会場と操作物を検証・修復しました。");
        } catch (error) {
          tell(player, error.message);
        }
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
  async function openReception(player) {
    if (busy.has(player.id)) {
      tell(player, "別の操作画面を処理中です。画面やチャットを閉じて、受付ボタンを再操作してください。");
      return false;
    }
    await open(player, true);
    return true;
  }
  async function resetSession(current, reason = "会場をリセットしました。", { evacuated = false } = {}) {
    runtimeForms.invalidate();
    decisions.clear();
    stopRuns();
    for (const timerId of disconnectTimers.values()) system.clearRun?.(timerId);
    disconnectTimers.clear();
    busy.clear();
    const resetTable = current ? { ...current, players: evacuated ? [] : [...current.players] } : { players: [], state: null };
    await venue.end(resetTable);
    if (current) notify(current, reason);
  }
  return { open, openReception, resetSession, venue, resume };
}
