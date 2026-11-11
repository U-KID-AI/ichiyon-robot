import { createDecisionPrompts } from "./decision_prompts.js";
import { eligibleSpecialDecisions, specialDecisionWindow } from "./special_decisions.js";
import { isServiceGuide } from "./service_controls.js";
import { createRuntimeForms, requestCatalogReturn, showReceptionNavigation } from "./venue_presentation.js";
import { JINN_CARDS, JINN_INPUT_TICKS, jinnGame } from "./jinn.js";
import {
  createJinnVenue, JINN_CARD, JINN_LEVER, JINN_RECEPTION, JINN_SURRENDER,
} from "./jinn_venue.js";

const TABLE = "jinn";
const CARD_LABEL = { gold: "黄金", thief: "盗賊", demon: "魔人" };

export function jinnCardChoice(state, seat, cardType) {
  if (!state || state.phase !== "cards") throw new Error("カード選択中ではありません。");
  if (seat !== 0 && seat !== 1) throw new Error("この試合の参加者ではありません。");
  if (state.chosen ? state.chosen[seat] : state.choices[seat] !== null) throw new Error("このラウンドのカードは選択済みです。");
  if (!JINN_CARDS.includes(cardType) || state.hands[seat][cardType] <= 0) {
    throw new Error("そのカードは残っていません。");
  }
  return { action: "choose", value: cardType };
}

export function createJinnRuntime({ world, system, ActionFormData, manager, installer }) {
  const runtimeForms = createRuntimeForms({ ActionFormData, world, system });
  ({ ActionFormData } = runtimeForms);
  const venue = createJinnVenue({ world });
  const busy = new Set();
  const disconnectTimers = new Map();
  let deadline, reveal, interval, generation = 0;
  const table = () => manager.view(TABLE);
  const tell = (player, message) => {
    try { player?.sendMessage(`[ジャックポット・ジニー] ${message}`); } catch { /* Player disconnected. */ }
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
    if (s.phase === "cards") {
      return `第${s.game}セット ${s.round}/6\nコイン ${s.coins[0]} : ${s.coins[1]}`;
    }
    if (s.phase === "settlement") {
      return `第${s.game}セット精算\n支払い ${s.payment[0]} : ${s.payment[1]}\nレバー ${s.lever[0] ? "済" : "未"} : ${s.lever[1] ? "済" : "未"} / 空腹 ${s.food[0]} : ${s.food[1]}`;
    }
    return `第${s.game}セット / ${s.phase}\nコイン ${s.coins[0]} : ${s.coins[1]}`;
  }
  function clearRuns(includeInterval = true) {
    if (deadline !== undefined) system.clearRun(deadline);
    if (reveal !== undefined) system.clearRun(reveal);
    if (includeInterval && interval !== undefined) system.clearRun(interval);
    deadline = reveal = undefined;
    if (includeInterval) interval = undefined;
    generation++;
  }
  function finish(current, message) {
    runtimeForms.invalidate();
    busy.clear();
    decisions.clear();
    clearRuns();
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
  function visuals(current) {
    try { venue.update(current); } catch (error) { notify(current, `会場表示: ${error.message}`); }
  }
  function armDeadline(current) {
    if (deadline !== undefined) system.clearRun(deadline);
    const activeGeneration = generation;
    deadline = system.runTimeout(() => {
      deadline = undefined;
      if (activeGeneration !== generation) return;
      const live = table();
      if (!live?.state) return;
      if (live.state.phase === "settlement" && live.state.lever[0] && live.state.lever[1]) return;
      const action = live.state.phase === "settlement" ? "deadline" : "timeout";
      const next = manager.systemAct(TABLE, action);
      if (!complete(next)) {
        notify(next, "時間切れです。");
      }
    }, JINN_INPUT_TICKS);
  }
  function startHungerClock() {
    if (interval !== undefined) system.clearRun(interval);
    const activeGeneration = generation;
    interval = system.runInterval(() => {
      if (activeGeneration !== generation) return;
      const live = table();
      if (!live?.state || !['cards', 'reveal'].includes(live.state.phase) || !allMembersOnline(live)) return;
      complete(manager.systemAct(TABLE, "clock", undefined, { persist: false }));
    }, 1);
  }
  function startCards(current) {
    clearRuns();
    try { venue.start(current); } catch (error) { notify(current, error.message); }
    visuals(current);
    armDeadline(current);
    startHungerClock();
    openAll(current);
  }
  async function revealCards(current) {
    const activeGeneration = generation;
    reveal = system.runTimeout(() => {
      reveal = undefined;
      if (activeGeneration !== generation) return;
      const live = table();
      if (!live?.state || live.state.phase !== "reveal") return;
      const next = manager.act(live.players[0], live.token, "reveal", undefined);
      visuals(next);
      if (complete(next)) return;
      if (next.state.phase === "settlement") startSettlement(next);
      else {
        notify(next, `第${next.state.game}セット ${next.state.round}/6\n${status(next)}`);
        armDeadline(next);
        openAll(next);
      }
    }, 40);
  }
  function startSettlement(current) {
    clearRuns();
    try { venue.settlementPositions(current); } catch (error) { notify(current, error.message); }
    visuals(current);
    armDeadline(current);
    const activeGeneration = generation;
    let ticks = 0;
    interval = system.runInterval(() => {
      if (activeGeneration !== generation) return;
      const live = table();
      if (!live?.state || live.state.phase !== "settlement") {
        if (interval !== undefined) system.clearRun(interval);
        interval = undefined;
        return;
      }
      if (!allMembersOnline(live)) return;
      if (live.state.lever[0] && live.state.lever[1] && deadline !== undefined) {
        system.clearRun(deadline);
        deadline = undefined;
      }
      const next = manager.systemAct(TABLE, "tick", undefined, { persist: ticks % 20 === 0 });
      if (complete(next)) return;
      ticks++;
      if (ticks % 20 === 0) {
        visuals(next);
        for (const player of members(next)) {
          player.onScreenDisplay?.setActionBar(`支払い ${next.state.payment[0]} : ${next.state.payment[1]} / 空腹 ${next.state.food[0]} : ${next.state.food[1]}`);
        }
      }
      if (next.state.phase === "cards") {
        notify(next, `第${next.state.game}セット開始`);
        startCards(next);
      }
    }, 1);
    openAll(current);
  }
  async function choose(player, current) {
    const seat = current.players.indexOf(player.id);
    if (current.state.chosen[seat]) {
      tell(player, "このラウンドのカードは選択済みです。相手を待っています。");
      return;
    }
    const available = JINN_CARDS.filter(card => current.state.hands[seat][card] > 0);
    const form = new ActionFormData()
      .title(`ジャックポット・ジニー：${current.state.round}/6`)
      .body(`${status(current)}\n黄金${current.state.hands[seat].gold} / 盗賊${current.state.hands[seat].thief} / 魔人${current.state.hands[seat].demon}`);
    for (const card of available) form.button(CARD_LABEL[card]);
    const response = await form.show(player);
    if (response.canceled || !Number.isInteger(response.selection)) return;
    const next = decide(player.id, current, "choose", available[response.selection]);
    afterChoice(player, next);
  }
  function afterChoice(player, next) {
    visuals(next);
    if (next.state.phase === "reveal") {
      if (deadline !== undefined) system.clearRun(deadline);
      deadline = undefined;
      notify(next, `両者公開：${CARD_LABEL[next.state.last.choices[0]]} vs ${CARD_LABEL[next.state.last.choices[1]]}\nコイン ${next.state.coins[0]} : ${next.state.coins[1]}`);
      void revealCards(next);
      return;
    }
    tell(player, "選択を受け付けました。相手を待っています。");
  }
  async function settlementAction(player, current) {
    const seat = current.players.indexOf(player.id);
    const form = new ActionFormData().title("ジャックポット・ジニー：精算").body(status(current));
    if (!current.state.lever[seat]) form.button("レバーを引く");
    if (current.state.game === 3) form.button("降参する");
    if (current.state.lever[seat] && current.state.game !== 3) { tell(player, "精算が終わるのを待っています。"); return; }
    const response = await form.show(player);
    if (response.canceled || !Number.isInteger(response.selection)) return;
    const leverFirst = !current.state.lever[seat];
    if (leverFirst && response.selection === 0) {
      const next = decide(player.id, current, "lever", undefined);
      visuals(next);
      notify(next, `${player.name}がレバーを引きました。`);
      decisions.schedule(next);
      return;
    }
    const next = decide(player.id, current, "surrender", undefined);
    if (!complete(next)) notify(next, "降参しました。");
  }
  async function openAll(current) { decisions.schedule(current); }
  async function open(player, navigation = false) {
    if (manager.maintenance) { tell(player, "会場を確認中です。完了後に再操作してください。"); return; }
    if (busy.has(player.id)) return;
    busy.add(player.id);
    try {
      if (installer.installing()) throw new Error("会場設置中です。");
      const mine = manager.forPlayer(player.id);
      if (mine && mine.gameId !== TABLE) throw new Error("別のゲームに参加しています。");
      const current = mine ?? table();
      const canJoin = !mine && (!current || (!current.state && current.players.length < jinnGame.seats));
      let navigationAction;
      if (navigation || !mine?.state) {
        navigationAction = await showReceptionNavigation({ ActionFormData, player, gameId: TABLE,
          title: "ジャックポット・ジニー", body: current ? status(current) : "参加者募集中です。",
          rules: "2人対戦。黄金4・盗賊1・魔人1のカードを6ラウンドで使い、同時公開の組み合わせでコインを増減させます。3セットの精算後、最終コインが多いプレイヤーの勝利です。", joined: !!mine, canJoin,
          canAct: !!mine?.state && ["cards", "settlement"].includes(mine.state.phase) });
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
      if (mine?.state?.phase === "cards") {
        await choose(player, mine);
        return;
      }
      if (mine?.state?.phase === "settlement") {
        await settlementAction(player, mine);
        return;
      }
      if (mine?.state?.phase === "reveal") {
        tell(player, status(mine));
        return;
      }
      if (!["join", "rematch"].includes(navigationAction)) return;
      if (canJoin) {
        const joined = manager.join(TABLE, TABLE, player.id);
        notify(joined, status(joined));
        if (joined.state) startCards(joined);
      }
    } catch (error) {
      tell(player, error.message ?? "操作に失敗しました。");
    } finally {
      busy.delete(player.id);
    }
  }

  world.beforeEvents.playerInteractWithEntity.subscribe(event => {
    if (isServiceGuide(world, event.target)) return;
    const target = event.target;
    const isCard = target.hasTag(JINN_CARD);
    const isLever = target.hasTag(JINN_LEVER);
    const isSurrender = target.hasTag(JINN_SURRENDER);
    if (!isCard && !isLever && !isSurrender && !target.hasTag(JINN_RECEPTION)) return;
    event.cancel = true;
    const player = event.player;
    system.run(() => {
      const mine = manager.forPlayer(player.id);
      if (isCard && mine?.gameId === TABLE && mine.state?.phase === "cards") {
        const seat = mine.players.indexOf(player.id);
        const targetSeat = target.getDynamicProperty("junket:jinn_seat");
        if (targetSeat !== seat) {
          tell(player, "自分のカードではありません。");
          return;
        }
        const cardType = String(target.getDynamicProperty("junket:jinn_card_type") ?? "");
        const request = jinnCardChoice(mine.state, seat, cardType);
        afterChoice(player, manager.act(player.id, mine.token, request.action, request.value));
      } else if ((isLever || isSurrender) && mine?.gameId === TABLE && mine.state?.phase === "settlement") {
        const seat = mine.players.indexOf(player.id);
        const targetSeat = target.getDynamicProperty("junket:jinn_seat");
        if (Number.isInteger(targetSeat) && targetSeat !== seat) {
          tell(player, "自分の操作物ではありません。");
          return;
        }
        if (isSurrender) {
          complete(manager.act(player.id, mine.token, "surrender", undefined));
        } else if (!mine.state.lever[seat]) {
          const next = manager.act(player.id, mine.token, "lever", undefined);
          visuals(next);
          notify(next, `${player.name}がレバーを引きました。`);
      decisions.schedule(next);
        }
      } else {
        void open(player, event.target.hasTag(JINN_RECEPTION));
      }
    });
  });
  world.afterEvents.playerLeave.subscribe(event => {
    runtimeForms.invalidate([event.playerId]);
    decisions.forget(event.playerId);
    busy.delete(event.playerId);
    const mine = manager.forPlayer(event.playerId);
    if (mine?.gameId !== TABLE) return;
    clearRuns();
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
    clearRuns();
    const current = table();
    if (!current) return;
    if (complete(current)) return;
    if (!current.state) return;
    if (!allMembersOnline(current)) { waitForMissing(current); return; }
    if (current.state.phase === "settlement") startSettlement(current);
    else if (current.state.phase === "reveal") { startHungerClock(); void revealCards(current); }
    else {
      visuals(current);
      armDeadline(current);
      startHungerClock();
      openAll(current);
    }
  }
  system.afterEvents.scriptEventReceive.subscribe(event => {
    if (!event.id.startsWith("junket:jinn") || event.sourceEntity?.typeId !== "minecraft:player") return;
    const player = event.sourceEntity;
    const command = event.message.trim();
    if (event.id === "junket:jinnmenu" || command === "menu") {
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
          tell(player, "既存のジャックポット・ジニー会場と操作物を検証・修復しました。");
        } catch (error) {
          tell(player, error.message);
        }
      }
    } else {
      try {
        if (command === "goto") venue.go(player);
        else if (command === "repair" && !table()) {
          venue.ensure();
          tell(player, "ジャックポット・ジニーの会場オブジェクトを補完しました。");
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
    clearRuns();
    for (const timerId of disconnectTimers.values()) system.clearRun?.(timerId);
    disconnectTimers.clear();
    busy.clear();
    const resetTable = current ? { ...current, players: evacuated ? [] : [...current.players] } : { players: [], state: null };
    await venue.end(resetTable);
    if (current) notify(current, reason);
  }
  return { open, openReception, resetSession, venue, resume };
}
