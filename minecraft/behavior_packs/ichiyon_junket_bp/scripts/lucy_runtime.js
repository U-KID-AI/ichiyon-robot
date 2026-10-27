import { createDecisionPrompts } from "./decision_prompts.js";
import { eligibleSpecialDecisions, specialDecisionWindow } from "./special_decisions.js";
import { isServiceGuide } from "./service_controls.js";
import { createRuntimeForms, requestCatalogReturn, showReceptionNavigation } from "./venue_presentation.js";
import { LUCY_TURN_TICKS, LUCY_COLUMNS, LUCY_ROWS, lucyGame } from "./lucy.js";
import {
  createLucyVenue, LUCY_BOX, LUCY_CONFIRM, LUCY_KEYHOLE, LUCY_RECEPTION,
} from "./lucy_venue.js";

const TABLE = "lucy";
const LABEL = {
  lock: "金庫の当たり指定",
  attempt: "鍵を刺す",
  success: "開錠成功",
  recover: "鍵を返却",
  finished: "終了",
};

export function createLucyRuntime({ world, system, ActionFormData, ModalFormData, manager, installer }) {
  const runtimeForms = createRuntimeForms({ ActionFormData, ModalFormData, world, system });
  ({ ActionFormData, ModalFormData } = runtimeForms);
  const venue = createLucyVenue({ world, system });
  const busy = new Set();
  const disconnectTimers = new Map();
  let timer, generation = 0;
  const table = () => manager.view(TABLE);
  const tell = (player, message) => {
    try { player?.sendMessage(`[気分屋ルーシー] ${message}`); } catch { /* Player disconnected. */ }
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
    const setting = s.phase === "lock"
      ? `\n設定 金庫1 ${s.progress[0]}/5（2P担当） / 金庫2 ${s.progress[1]}/5（1P担当）`
      : "";
    return `ラウンド${s.round} / ${LABEL[s.phase]}${setting}\n開錠 ${s.remaining[0]} : ${s.remaining[1]} / 体力 ${Math.max(0, s.hp[0])} : ${Math.max(0, s.hp[1])}`;
  }
  function clearTimer() {
    if (timer !== undefined) system.clearRun(timer);
    timer = undefined;
    generation++;
  }
  function finish(current, message) {
    runtimeForms.invalidate();
    busy.clear();
    decisions.clear();
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
  function continueSuccess(current) {
    clearTimer();
    const activeGeneration = generation, token = current.token;
    timer = system.runTimeout(() => {
      timer = undefined;
      const live = table();
      if (activeGeneration !== generation || live?.token !== token || live.state.phase !== "success") return;
      const next = manager.act(live.players[live.state.actor], live.token, "ack", undefined);
      visuals(next); notify(next, status(next)); arm(next); decisions.schedule(next);
    }, 100);
  }
  async function openActor(current) {
    if (current?.state?.phase === "success") { continueSuccess(current); return; }
    decisions.schedule(current);
  }
  async function lockRow(player, current) {
    const seat = current.players.indexOf(player.id);
    const box = 1 - seat;
    const row = current.state.progress[box];
    if (row >= LUCY_ROWS) {
      tell(player, "当たり指定は完了しています。");
      return;
    }
    const form = new ModalFormData().title(`気分屋ルーシー：${box + 1}P金庫 ${row + 1}面`);
    for (let column = 0; column < LUCY_COLUMNS; column++) {
      form.toggle(`鍵穴 ${column + 1}`, {
        defaultValue: current.state.lockDrafts?.[box]?.[row]?.[column] === true,
      });
    }
    const response = await form.show(player);
    if (response.canceled) return;
    const values = response.formValues?.map(value => value === true);
    const next = decide(player.id, current, "lock", values);
    notify(next, status(next));
    visuals(next);
    decisions.schedule(next);
    if (next.state.phase === "attempt") {
      arm(next);
      void openActor(next);
    }
  }
  async function submitAttempt(player, current, cell) {
    const seat = current.players.indexOf(player.id);
    if (current.state.actor !== seat || current.state.phase !== "attempt") {
      tell(player, status(current));
      return;
    }
    const visibleRow = LUCY_ROWS - current.state.remaining[seat];
    if (cell.box !== seat) {
      tell(player, "自分の金庫の鍵穴を選んでください。");
      return;
    }
    if (cell.row !== visibleRow) {
      tell(player, `現在は鍵穴の${visibleRow + 1}面目を選べます。`);
      return;
    }
    if (!Number.isInteger(cell.column) || cell.column < 0 || cell.column >= LUCY_COLUMNS) return;
    if (current.state.attempted[seat][visibleRow][cell.column] === 1) {
      tell(player, "その鍵穴は使用済みです。");
      return;
    }
    const next = decide(player.id, current, "attempt", {
      row: visibleRow,
      column: cell.column,
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
    const columns = Array.from({ length: LUCY_COLUMNS }, (_, i) => i)
      .filter(column => !current.state.attempted[seat][visibleRow][column]);
    for (const column of columns) form.button(`鍵穴 ${column + 1}`);
    const response = await form.show(player);
    if (response.canceled || !Number.isInteger(response.selection)) return;
    await submitAttempt(player, current, {
      box: seat, row: visibleRow, column: columns[response.selection],
    });
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
    const next = decide(player.id, current, success ? "ack" : "recover", undefined);
    visuals(next);
    if (complete(next)) return;
    notify(next, status(next));
    arm(next);
    void openActor(next);
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
      const canJoin = !mine && (!current || (!current.state && current.players.length < lucyGame.seats));
      let navigationAction;
      if (navigation || !mine?.state) {
        navigationAction = await showReceptionNavigation({ ActionFormData, player, gameId: TABLE,
          title: "気分屋ルーシー", body: current ? status(current) : "参加者募集中です。",
          rules: "2人対戦。互いの金庫に5面分の当たりを指定し、交互に鍵を試して5面すべてを開けたプレイヤーの勝利です。", joined: !!mine, canJoin,
          canAct: !!mine?.state && (mine.state.phase === "lock" || mine.state.actor === mine.players.indexOf(player.id)) });
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
      if (!["join", "rematch"].includes(navigationAction)) return;
      if (canJoin) {
        const joined = manager.join(TABLE, TABLE, player.id);
        notify(joined, status(joined));
        if (joined.state) {
          try {
            await venue.start(joined);
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

  function keyholeCell(target) {
    const box = target.getDynamicProperty("junket:lucy_box");
    const row = target.getDynamicProperty("junket:lucy_row");
    const column = target.getDynamicProperty("junket:lucy_column");
    if (Number.isInteger(box) && Number.isInteger(row) && Number.isInteger(column)) {
      return { box, row, column };
    }
    const tags = target.getTags?.() ?? [];
    const prefix = "junket:lucy:fixture:keyhole_";
    const role = tags.find(tag => tag.startsWith(prefix));
    if (role) {
      const [parsedBox, parsedRow, parsedColumn] = role.slice(prefix.length)
        .split("_").map(Number);
      if ([parsedBox, parsedRow, parsedColumn].every(Number.isInteger)) {
        return { box: parsedBox, row: parsedRow, column: parsedColumn };
      }
    }
    return null;
  }
  function confirmCell(target) {
    const box = target.getDynamicProperty("junket:lucy_box");
    const row = target.getDynamicProperty("junket:lucy_row");
    if (Number.isInteger(box) && Number.isInteger(row)) return { box, row };
    const tags = target.getTags?.() ?? [];
    const prefix = "junket:lucy:fixture:confirm_";
    const role = tags.find(tag => tag.startsWith(prefix));
    if (role) {
      const [parsedBox, parsedRow] = role.slice(prefix.length).split("_").map(Number);
      if (Number.isInteger(parsedBox) && Number.isInteger(parsedRow)) {
        return { box: parsedBox, row: parsedRow };
      }
    }
    return null;
  }
  async function physicalAttempt(player, current, cell) {
    if (busy.has(player.id)) return;
    busy.add(player.id);
    try {
      await submitAttempt(player, current, cell);
    } catch (error) {
      tell(player, error.message ?? "鍵穴を操作できませんでした。");
    } finally {
      busy.delete(player.id);
    }
  }
  async function physicalToggle(player, current, cell) {
    if (busy.has(player.id)) return;
    busy.add(player.id);
    try {
      const next = decide(player.id, current, "lock-toggle", {
        row: cell.row, column: cell.column,
      });
      visuals(next);
      const box = 1 - next.players.indexOf(player.id);
      const selected = manager.forPlayer(player.id).state.lockDrafts[box][cell.row][cell.column];
      tell(player, `${box + 1}P金庫 ${cell.row + 1}面 鍵穴${cell.column + 1}: ${selected ? "当たり" : "空欄"}`);
    } catch (error) {
      tell(player, error.message ?? "鍵穴を切り替えられませんでした。");
    } finally {
      busy.delete(player.id);
    }
  }
  async function physicalConfirm(player, current, cell) {
    if (busy.has(player.id)) return;
    busy.add(player.id);
    try {
      const next = await decide(player.id, current, "lock-confirm", { row: cell.row });
      visuals(next);
      notify(next, status(next));
      if (next.state.phase === "attempt") {
        arm(next);
        void openActor(next);
      }
    } catch (error) {
      tell(player, error.message ?? "当たりを確定できませんでした。");
    } finally {
      busy.delete(player.id);
    }
  }
  world.beforeEvents.playerInteractWithEntity.subscribe(event => {
    if (isServiceGuide(world, event.target)) return;
    const keyhole = event.target.hasTag(LUCY_KEYHOLE);
    const confirm = event.target.hasTag(LUCY_CONFIRM);
    if (!event.target.hasTag(LUCY_RECEPTION) && !event.target.hasTag(LUCY_BOX) &&
        !keyhole && !confirm) return;
    if (!venue.controlRole(event.target)) return;
    event.cancel = true;
    const player = event.player;
    system.run(() => {
      const mine = manager.forPlayer(player.id);
      if (keyhole && mine?.gameId === TABLE && mine.state?.phase === "lock") {
        const cell = keyholeCell(event.target);
        if (cell) void physicalToggle(player, mine, cell);
        return;
      }
      if (keyhole && mine?.gameId === TABLE && mine.state?.phase === "attempt") {
        const cell = keyholeCell(event.target);
        if (cell) void physicalAttempt(player, mine, cell);
        return;
      }
      if (confirm && mine?.gameId === TABLE && mine.state?.phase === "lock") {
        const cell = confirmCell(event.target);
        if (cell) void physicalConfirm(player, mine, cell);
        return;
      }
      void open(player, event.target.hasTag(LUCY_RECEPTION));
    });
  });
  world.afterEvents.playerLeave.subscribe(event => {
    runtimeForms.invalidate([event.playerId]);
    decisions.forget(event.playerId);
    busy.delete(event.playerId);
    const mine = manager.forPlayer(event.playerId);
    if (mine?.gameId !== TABLE) return;
    clearTimer();
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
    clearTimer();
    const current = table();
    if (!current) return;
    visuals(current);
    if (complete(current)) return;
    if (!current.state) return;
    if (!allMembersOnline(current)) { waitForMissing(current); return; }
    arm(current);
    void openActor(current);
  }
  system.afterEvents.scriptEventReceive.subscribe(event => {
    if (!event.id.startsWith("junket:lucy") || event.sourceEntity?.typeId !== "minecraft:player") return;
    const player = event.sourceEntity;
    const command = event.message.trim();
    if (event.id === "junket:lucymenu" || command === "menu") {
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
          tell(player, "既存の気分屋ルーシー会場と操作物を検証・修復しました。");
        } catch (error) {
          tell(player, error.message);
        }
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
    clearTimer();
    for (const timerId of disconnectTimers.values()) system.clearRun?.(timerId);
    disconnectTimers.clear();
    busy.clear();
    const resetTable = current ? { ...current, players: evacuated ? [] : [...current.players] } : { players: [], state: null };
    await venue.end(resetTable);
    if (current) notify(current, reason);
  }
  return { open, openReception, resetSession, venue, resume };
}
