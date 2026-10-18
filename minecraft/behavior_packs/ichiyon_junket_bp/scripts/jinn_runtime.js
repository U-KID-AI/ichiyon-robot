import { JINN_CARDS, JINN_INPUT_TICKS, jinnGame } from "./jinn.js";
import {
  createJinnVenue, JINN_CARD, JINN_LEVER, JINN_RECEPTION, JINN_SURRENDER,
} from "./jinn_venue.js";

const TABLE = "jinn";
const CARD_LABEL = { gold: "黄金", thief: "盗賊", demon: "魔人" };

export function createJinnRuntime({ world, system, ActionFormData, manager, installer }) {
  const venue = createJinnVenue({ world });
  const busy = new Set();
  let deadline, reveal, interval, generation = 0;
  const table = () => manager.view(TABLE);
  const tell = (player, message) => {
    try { player?.sendMessage(`[ジャックポット・ジニー] ${message}`); } catch { /* Player disconnected. */ }
  };
  const members = current => world.getAllPlayers().filter(player => current.players.includes(player.id));
  const notify = (current, message) => members(current).forEach(player => tell(player, message));
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
  function startCards(current) {
    clearRuns();
    try { venue.start(current); } catch (error) { notify(current, error.message); }
    visuals(current);
    armDeadline(current);
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
      if (live.state.lever[0] && live.state.lever[1] && deadline !== undefined) {
        system.clearRun(deadline);
        deadline = undefined;
      }
      const next = manager.systemAct(TABLE, "tick");
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
    if (current.state.choices[seat] !== null) {
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
    const next = manager.act(player.id, current.token, "choose", available[response.selection]);
    visuals(next);
    if (next.state.phase === "reveal") {
      if (deadline !== undefined) system.clearRun(deadline);
      deadline = undefined;
      notify(next, `両者公開：${CARD_LABEL[next.state.last.choices[0]]} vs ${CARD_LABEL[next.state.last.choices[1]]}\nコイン ${next.state.coins[0]} : ${next.state.coins[1]}`);
      void revealCards(next);
    } else {
      tell(player, "選択を受け付けました。相手を待っています。");
    }
  }
  async function settlementAction(player, current) {
    const seat = current.players.indexOf(player.id);
    const form = new ActionFormData().title("ジャックポット・ジニー：精算").body(status(current));
    if (!current.state.lever[seat]) form.button("レバーを引く");
    form.button("降参する");
    const response = await form.show(player);
    if (response.canceled || !Number.isInteger(response.selection)) return;
    const leverFirst = !current.state.lever[seat];
    if (leverFirst && response.selection === 0) {
      const next = manager.act(player.id, current.token, "lever", undefined);
      visuals(next);
      notify(next, `${player.name}がレバーを引きました。`);
      return;
    }
    const next = manager.act(player.id, current.token, "surrender", undefined);
    if (!complete(next)) notify(next, "降参しました。");
  }
  async function openAll(current) {
    for (const player of members(current)) void open(player);
  }
  async function open(player) {
    if (busy.has(player.id)) return;
    busy.add(player.id);
    try {
      if (installer.installing()) throw new Error("会場設置中です。");
      const mine = manager.forPlayer(player.id);
      if (mine && mine.gameId !== TABLE) throw new Error("別のゲームに参加しています。");
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
      const current = mine ?? table();
      const canJoin = !mine && (!current || current.players.length < jinnGame.seats);
      const response = await new ActionFormData()
        .title("ジャックポット・ジニー")
        .body(`${current ? status(current) : "2人対戦。黄金4・盗賊1・魔人1のカードを6ラウンドで使い、同時公開の組み合わせでコインを増減させます。3セットの精算後、最終コインが多いプレイヤーの勝利です。"}`)
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
        if (joined.state) startCards(joined);
      }
    } catch (error) {
      tell(player, error.message ?? "操作に失敗しました。");
    } finally {
      busy.delete(player.id);
    }
  }

  world.beforeEvents.playerInteractWithEntity.subscribe(event => {
    const target = event.target;
    const isCard = target.hasTag(JINN_CARD);
    const isLever = target.hasTag(JINN_LEVER);
    const isSurrender = target.hasTag(JINN_SURRENDER);
    if (!isCard && !isLever && !isSurrender && !target.hasTag(JINN_RECEPTION)) return;
    event.cancel = true;
    const player = event.player;
    system.run(() => {
      const mine = manager.forPlayer(player.id);
      if ((isLever || isSurrender) && mine?.gameId === TABLE && mine.state?.phase === "settlement") {
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
        }
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
    if (!event.id.startsWith("junket:jinn") || event.sourceEntity?.typeId !== "minecraft:player") return;
    const player = event.sourceEntity;
    const command = event.message.trim();
    if (event.id === "junket:jinnmenu" || command === "menu") {
      void open(player);
    } else if (command === "leave") {
      const mine = manager.forPlayer(player.id);
      if (mine?.gameId === TABLE) finish(mine, "参加者が退出したため中止しました。");
    } else if (command === "install") {
      if (manager.tables.size) {
        tell(player, "ゲーム参加者がいる間は設置できません。");
      } else {
        void installer.install(player, "jinn").then(message => {
          venue.ensure();
          tell(player, message);
        }).catch(error => tell(player, error.message));
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
  return { open, venue };
}
