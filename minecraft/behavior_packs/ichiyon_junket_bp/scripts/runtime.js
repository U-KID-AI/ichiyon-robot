import { isServiceGuide } from "./service_controls.js";
import { GameManager } from "./game_manager.js";
import { sparrowGame } from "./sparrow.js";
import { RECEPTION_TAG, setupReception } from "./reception.js";
import { createVenue } from "./venue.js";
import { createRuntimeForms, requestCatalogReturn, showReceptionNavigation } from "./venue_presentation.js";
import { createDecisionPrompts } from "./decision_prompts.js";

const TABLE = "sparrow";
const LABEL = { bet: "賭け金決定", hide: "宝石を隠す", choose: "箱を選ぶ", finished: "終了" };
const BOX = { big: "大箱", small: "小箱" };
const RULES = "2人対戦 / 各5,000万円 / 全6ラウンド\n賭け金を決めた側が宝石を隠し、相手が箱を選びます。的中なら選んだ側、外れなら隠した側が獲得。大箱は全額、小箱は半額（端数切捨て）。手番は交互です。\n所持金100万円未満で終了。金額は試合内だけの仮想スコアです。";

export function createRuntime({ world, system, ActionFormData, ModalFormData, manager = new GameManager([sparrowGame]) }) {
  const runtimeForms = createRuntimeForms({ ActionFormData, ModalFormData, world, system });
  ({ ActionFormData, ModalFormData } = runtimeForms);
  const venue = createVenue({ world, system });
  const busy = new Map();
  const disconnectTimers = new Map();
  const players = () => new Map(world.getAllPlayers().map(p => [p.id, p]));
  const allMembersOnline = table => Boolean(table?.state) &&
    table.players.every(playerId => players().has(playerId));
  const prompts = createDecisionPrompts({ world, system, read: () => manager.view(TABLE),
    eligible: current => allMembersOnline(current) ? current.decisionSeats : [],
    key: (current, seat) => `${current.sessionToken}:${current.decisionSerials[seat]}:${manager.forPlayer(current.players[seat])?.decisionKey}`,
    busy: id => busy.has(id) || venue.locked(), open: player => open(player), notify: tell });
  function tell(player, message) {
    try { player?.sendMessage(`[JUNKET] ${message}`); } catch { /* Player disconnected. */ }
  }
  function notify(table, message) {
    const online = players();
    for (const id of table.players) tell(online.get(id), message);
  }
  function summary(table) {
    if (!table?.state) return "対戦相手を待っています。";
    const online = players(), s = table.state;
    const names = table.players.map(id => online.get(id)?.name ?? "退出済み");
    let text = `${s.round}/6 ラウンド・${LABEL[s.phase]}\n${names[0]}: ${s.money[0]}円 / ${names[1]}: ${s.money[1]}円`;
    if (s.phase !== "finished") text += `\n手番: ${names[s.actor]}`;
    if (s.last) text += `\n前回: 宝石=${BOX[s.last.hidden]} / 選択=${BOX[s.last.chosen]} / ${names[s.last.winner]} +${s.last.amount}円`;
    if (s.phase === "finished") text += `\n${s.winner === null ? "引き分け" : `${names[s.winner]}の勝利`}`;
    return text;
  }
  function abort(id, reason) {
    if (manager.forPlayer(id)?.gameId !== "sparrow") return;
    const table = manager.leave(id);
    if (table) {
      prompts.clear(); runtimeForms.invalidate();
      for (const member of table.players) {
        const pending = disconnectTimers.get(member);
        if (pending !== undefined) system.clearRun?.(pending);
        disconnectTimers.delete(member);
      }
      notify(table, reason);
      try { venue.end(table); } catch (error) { notify(table, `会場の後片付け: ${error.message}`); }
    }
  }
  function scheduleTurn(table) {
    prompts.schedule(table);
  }
  async function actionForm(player, table) {
    const s = table.state;
    let value, response;
    if (s.phase === "bet") {
      response = await new ModalFormData().title("ウラギリスズメ：賭け金")
        .textField(`${summary(table)}\n1000000〜${s.money[s.actor]}円（整数）`, "半角数字", { defaultValue: "1000000" }).show(player);
      if (response.canceled) return;
      const raw = String(response.formValues?.[0] ?? "").trim();
      if (!/^\d{1,9}$/.test(raw)) throw new Error("賭け金を半角数字の整数で入力してください。");
      value = Number(raw);
    } else {
      response = await new ActionFormData().title(`ウラギリスズメ：${LABEL[s.phase]}`)
        .body(`${summary(table)}\n` + (s.phase === "hide" ? "宝石を入れる箱を選んでください。選択は公開まで相手に表示されません。" : `賭け金 ${s.bet}円。宝石のある箱を選んでください。`))
        .button("大箱（全額）").button("小箱（半額）").show(player);
      if (response.canceled) return;
      if (response.selection !== 0 && response.selection !== 1) throw new Error("選択が不正です。");
      value = response.selection === 0 ? "big" : "small";
    }
    await act(player, table, value);
  }
  async function act(player, table, value) {
    if (venue.locked()) throw new Error("開封演出中です。少しお待ちください。");
    if (manager.forPlayer(player.id)?.token !== table.token) {
      throw new Error("画面が古くなりました。受付から開き直してください。");
    }
    if (!allMembersOnline(table)) throw new Error("対戦相手の再接続を待っています。操作は再接続後に行ってください。");
    const next = manager.act(player.id, table.token, table.state.phase, value);
    notify(next, summary(next));
    try { await venue.transition(table, next); }
    catch (error) { notify(next, `会場演出: ${error.message}（対戦は継続できます）`); }
    if (manager.view(next.id)?.token !== next.token) return;
    if (next.state.phase === "finished") {
      manager.close(next.id);
      notify(next, "試合終了。再戦する場合は、2人とも参加受付で「再戦」を選んでください。");
      try { venue.end(next, true); } catch (error) { notify(next, `会場の終了処理: ${error.message}`); }
    }
    else scheduleTurn(next);
  }
  async function open(player, navigation = false) {
    if (manager.maintenance) { tell(player, "会場を確認中です。完了後に再操作してください。"); return; }
    if (venue.installing()) { tell(player, "会場の設置中です。完了してから参加してください。"); return; }
    if (busy.has(player.id)) return;
    const request = {};
    busy.set(player.id, request);
    try {
      const mine = manager.forPlayer(player.id);
      if (mine && mine.gameId !== "sparrow") { tell(player, "別のゲームに参加しています。先に退出してください。"); return; }
      if (venue.locked()) {
        tell(player, "開封演出中です。演出終了後に操作してください。");
        return;
      }
      if (!navigation && mine?.state && mine.players[mine.state.actor] === player.id) {
        await actionForm(player, mine);
      } else {
        const table = mine ?? manager.view(TABLE);
        const canJoin = !mine && (!table || table.players.length < 2);
        const action = await showReceptionNavigation({ ActionFormData, player, gameId: TABLE,
          title: "ウラギリスズメ", body: table ? summary(table) : "参加者募集中。2人揃うと開始します。",
          rules: RULES, joined: !!mine, canJoin,
          canAct: !!mine?.state && mine.players[mine.state.actor] === player.id });
        if (!action) return;
        if (!players().has(player.id)) return;
        // A menu may remain open across disconnects, joins, or a new match.
        if ((manager.forPlayer(player.id)?.token ?? null) !== (mine?.token ?? null) ||
            (manager.view(TABLE)?.token ?? null) !== (table?.token ?? null)) {
          throw new Error("受付の状態が変わりました。開き直してください。");
        }
        if (action === "catalog") {
          if (mine) tell(player, "退出を選んでからカタログへ戻ってください。");
          else await requestCatalogReturn(player, world);
        } else if (action === "leave" && mine) abort(player.id, "参加者が退出したため中止しました。");
        else if (action === "act" && mine) await actionForm(player, mine);
        else if (canJoin && ["join", "rematch"].includes(action)) {
          const joined = manager.join(TABLE, "sparrow", player.id);
          if (joined.state) {
            try { venue.start(joined); }
            catch (error) {
              abort(player.id, `会場を準備できませんでした: ${error.message}。設置者が /scriptevent junket:venue repair を実行してください。`);
              return;
            }
          }
          notify(joined, summary(joined));
          scheduleTurn(joined);
        }
      }
    } catch (error) {
      tell(player, error.message ?? "操作に失敗しました。受付から再試行してください。");
    } finally {
      if (busy.get(player.id) === request) busy.delete(player.id);
      prompts.schedule(manager.view(TABLE));
    }
  }
  world.beforeEvents.playerInteractWithEntity.subscribe(event => {
    if (isServiceGuide(world, event.target)) return;
    // The shared warp handler owns catalog-return fixtures. In particular this
    // must not also abort the match and teleport back into the Sparrow venue.
    if (event.target.hasTag("junket:return")) return;
    const box = venue.boxValue(event.target);
    if (box) {
      event.cancel = true;
      const table = manager.forPlayer(event.player.id);
      system.run(() => {
        if (!table || table.gameId !== "sparrow" || !["hide", "choose"].includes(table.state?.phase)) {
          tell(event.player, "箱は参加者の『隠す』『選ぶ』手番で操作できます。");
          return;
        }
        void act(event.player, table, box).catch(error => tell(event.player, error.message));
      });
      return;
    }
    const control = venue.control(event.target);
    if (control) {
      event.cancel = true;
      const player = event.player;
      system.run(() => {
        if (control === "return") {
          if (manager.forPlayer(player.id)) {
            tell(player, "退出を選んでからカタログへ戻ってください。");
            return;
          }
          void Promise.resolve().then(() => requestCatalogReturn(player, world)).catch(error => tell(player, error.message));
        } else {
          // The monitor opens the current status/turn, and the public jewel
          // offers the same private hide form as reception. Neither encodes
          // a secret in its appearance or world-visible properties.
          void open(player);
        }
      });
      return;
    }
    if (event.target.hasTag("junket:hotel_route")) {
      event.cancel = true;
      system.run(() => { try { venue.go(event.player); } catch (error) { tell(event.player, error.message); } });
      return;
    }
    if (!event.target.hasTag(RECEPTION_TAG)) return;
    // Suppress the NPC's own editor/dialogue; defer forms out of read-only mode.
    event.cancel = true;
    const player = event.player;
    system.run(() => { void open(player, true); });
  });
  function waitForReconnect(playerId, mine) {
    if (disconnectTimers.has(playerId)) return;
    // Tokens change on every action and when a second player joins. The table
    // identity remains stable for this match, so a reconnect timeout cannot
    // accidentally spare a disconnected player or cancel a later rematch.
    const session = manager.tables.get(mine.id);
    const timer = system.runTimeout(() => {
      disconnectTimers.delete(playerId);
      const live = manager.forPlayer(playerId);
      if (live?.id !== mine.id || manager.tables.get(mine.id) !== session) return;
      if (players().has(playerId)) return;
      abort(playerId, "参加者が再接続しなかったため中止しました。");
    }, 200);
    disconnectTimers.set(playerId, timer);
  }
  world.afterEvents.playerLeave.subscribe(event => {
    runtimeForms.invalidate([event.playerId]);
    prompts.forget(event.playerId);
    busy.delete(event.playerId);
    const mine = manager.forPlayer(event.playerId);
    if (mine?.gameId !== "sparrow") return;
    waitForReconnect(event.playerId, mine);
  });

  function resume(playerId = null) {
    if (playerId) {
      const pending = disconnectTimers.get(playerId);
      if (pending !== undefined) system.clearRun?.(pending);
      disconnectTimers.delete(playerId);
    }
    const current = manager.view(TABLE);
    if (!current) return;
    if (playerId && !current.players.includes(playerId)) return;
    if (current.state?.phase === "finished") {
      manager.close(TABLE);
      venue.end(current, true);
      return;
    }
    // Restored sessions may contain offline players for whom this process
    // never saw playerLeave. Apply the same bounded reconnect grace period.
    const online = players();
    for (const member of current.players) {
      if (!online.has(member)) waitForReconnect(member, current);
    }
    if (current.state) venue.resume(current, playerId);
    if (current.state && allMembersOnline(current)) {
      scheduleTurn(current);
    }
  }
  system.afterEvents.scriptEventReceive.subscribe(event => {
    if (!event.id.startsWith("junket:")) return;
    const player = event.sourceEntity;
    if (player?.typeId !== "minecraft:player") return;
    if (event.id === "junket:menu") void open(player, true);
    if (event.id === "junket:leave") abort(player.id, "参加者が退出したため中止しました。");
    if (event.id === "junket:setup") {
      try { tell(player, setupReception(player)); }
      catch (error) { tell(player, error.message ?? "受付を設置できませんでした。"); }
    }
    if (event.id === "junket:venue") {
      if (manager.tables.size) { tell(player, "参加者がいる間は会場操作できません。"); return; }
      const command = event.message.trim();
      if (command === "hall" || command === "hotel") {
        void venue.install(player, command).then(message => tell(player, message)).catch(error => tell(player, error.message));
      } else {
        try {
          if (command === "repair") tell(player, venue.repair());
          else if (command === "goto") venue.go(player);
          else if (command === "hotel-goto") venue.go(player, "hotel");
          else tell(player, "会場: hall / hotel / goto / hotel-goto / repair");
        } catch (error) { tell(player, error.message); }
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
    prompts.clear();

    for (const timerId of disconnectTimers.values()) system.clearRun?.(timerId);
    disconnectTimers.clear();
    busy.clear();
    const resetTable = current ? { ...current, players: evacuated ? [] : [...current.players] } : { players: [], state: null };
    await venue.end(resetTable);
    if (current) notify(current, reason);
  }
  return { manager, open, openReception, resetSession, venue, resume };
}
