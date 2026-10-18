import { JUNKET_INPUT_TICKS } from "./strategy_games.js";

const DICE = ["9", "10", "J", "Q", "K", "A"];
const HOPE_REGISTRATION_TICKS = 300;
const DRAW = "引き分け";

const actorName = (table, seat) =>
  table.players[seat] ? `${seat + 1}P` : `${seat + 1}P`;

function shuffledDice() {
  return Array.from({ length: 5 }, () => DICE[Math.floor(Math.random() * DICE.length)]);
}

export function createCompleteRuntime({
  game, world, system, ActionFormData, ModalFormData, manager, venue = null,
}) {
  const tableId = game.id;
  const receptionTag = `junket:${game.id}:reception`;
  const controlTag = `junket:${game.id}:control`;
  const busy = new Set();
  let timer;
  let registrationTimer;
  let interval;
  let generation = 0;
  const table = () => manager.view(tableId);
  const tell = (player, message) => {
    try { player?.sendMessage(`[${game.title}] ${message}`); } catch { /* Disconnected. */ }
  };
  const members = current =>
    current ? world.getAllPlayers().filter(player => current.players.includes(player.id)) : [];
  const notify = (current, message) => members(current).forEach(player => tell(player, message));
  const seatOf = (current, player) => current?.players.indexOf(player.id) ?? -1;

  function winnerText(current) {
    const state = current?.state;
    if (!state) return "";
    if (state.reason === "timeout") return "時間切れ";
    if (Array.isArray(state.winners)) {
      if (!state.winners.length) return "全員脱落";
      return state.winners.map(seat => actorName(current, seat)).join("・");
    }
    if (Number.isInteger(state.winner)) return actorName(current, state.winner);
    if (Number.isInteger(state.winnerTeam)) return `チーム${state.winnerTeam === 0 ? "A" : "B"}`;
    return DRAW;
  }

  function status(current) {
    if (!current) return "参加者募集中です。";
    if (!current.state) return `${current.players.length}人参加済み。開始を待っています。`;
    const s = current.state;
    const names = current.players.map((id, seat) =>
      world.getAllPlayers().find(player => player.id === id)?.name ?? `参加者${seat + 1}`);
    switch (game.id) {
      case "ace":
        return `第${s.game}/5ゲーム / ${s.phase}\nコイン ${s.coins.join(" : ")}\n場代 ${s.tableBet} / 現在 ${s.currentBet}\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "grail":
        return `第${s.round}/10ラウンド / ${s.phase}\n聖水 ${s.holy.join(" : ")} / 毒 ${s.poison.join(" : ")} / 体力 ${s.hp.map(value => Math.max(0, value)).join(" : ")}\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "auction":
        return `第${s.set}セット ${s.round}/4 / ${s.phase}\nダイヤ A ${s.diamonds[0]} : B ${s.diamonds[1]}\nラウンド A ${s.roundWins[0]} : B ${s.roundWins[1]}\n支払 ${s.payment.join(" : ")} / 手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "fire":
        return `第${s.round}勝負 / ${s.phase}\n獲得 ${s.wins.join(" : ")} / 室温 ${s.temperature.join("℃ : ")}℃ / 体力 ${s.hp.map(v => Math.max(0, v)).join(" : ")}\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "pig":
        return `第${s.round}/4ラウンド / ${s.phase}\n子豚 ${s.pigs.join(" : ")}\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "light":
        return `第${s.round}ラウンド / ${s.phase}\nハート ${s.hearts.join(" : ")} / たいまつ ${s.torches.join(" : ")} / 残酸素 ${s.fuel.map(v => Math.max(0, v)).join(" : ")}\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "mine":
        return `第${s.round}/20ラウンド / ${s.phase}\n体力 ${s.hp.map(v => Math.max(0, v)).join(" : ")} / インゴット ${s.ingots.join(" : ")} / 採掘 ${s.mined.join(" : ")}\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "hope":
        return `第${s.round}/10ラウンド / ${s.phase}\n生存 ${s.alive.map((alive, seat) => alive ? names[seat] : `${names[seat]}（脱落）`).join(" / ")}\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "clock":
        return `第${s.round}/8ラウンド / ${s.phase}\n体力 ${s.hp.map(v => Math.max(0, v)).join(" : ")}\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "walker":
        return `第${s.round}/10ラウンド / ${s.phase}\n位置 ${s.positions.join(" : ")} / 体力 ${s.hp.map(v => Math.max(0, v)).join(" : ")}\n${s.phase === "walk" ? "ウォーカー" : "ブレイカー"} ${names[s.actor]}`;
      case "garden":
        return `第${s.round}/6ラウンド / ${s.phase}\n宝石箱 ${s.chest} / 負荷 ${s.loads.join(" : ")} / 弁 ${s.valve + 1}P側\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "rabbit":
        return `第${s.round}/10ラウンド / ${s.phase}\n脱出側 ${s.escaper} / 仲間 ${s.cpu.join(", ") || "全滅"} / 罠 ${s.phase === "finished" ? s.traps.join(", ") || "なし" : "非公開"}\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      default:
        return `${s.phase}`;
    }
  }

  function clearTimer() {
    if (timer !== undefined) system.clearRun(timer);
    timer = undefined;
    generation++;
  }
  function clearRegistration() {
    if (registrationTimer !== undefined) system.clearRun(registrationTimer);
    registrationTimer = undefined;
  }
  function stopTicker() {
    if (interval !== undefined) system.clearRun(interval);
    interval = undefined;
  }
  function finish(current, message) {
    clearTimer();
    clearRegistration();
    stopTicker();
    const result = manager.close(tableId);
    try { venue?.update(game.id, result ?? current); } catch { /* The optional venue may not be installed. */ }
    notify(result ?? current, message);
    return result;
  }
  function complete(current) {
    if (current?.state?.phase !== "finished") return false;
    finish(current, `終了：${winnerText(current)}（${current.state.reason ?? "決着"}）。受付から再戦できます。`);
    return true;
  }
  function arm(current) {
    clearTimer();
    if (!current?.state || current.state.phase === "finished" || current.state.actor === null) return;
    const activeGeneration = generation;
    timer = system.runTimeout(() => {
      timer = undefined;
      if (activeGeneration !== generation || !table()?.state) return;
      const next = manager.systemAct(tableId, "timeout");
      if (!next || !complete(next)) {
        notify(next, "入力時間切れです。");
        void openActor(next);
      }
    }, JUNKET_INPUT_TICKS);
  }
  function automatic(current) {
    const phase = current?.state?.phase;
    if (!["between", "set_result"].includes(phase)) return false;
    const action = phase === "between" ? "next_game" : "next_set";
    const activeGeneration = generation;
    system.runTimeout(() => {
      if (activeGeneration !== generation || table()?.token !== current.token) return;
      const next = manager.systemAct(tableId, action);
      if (!next || !complete(next)) afterAction(next);
    }, 60);
    return true;
  }
  function afterAction(current) {
    if (!current || complete(current)) return;
    try { venue?.update(game.id, current); } catch { /* The optional venue may not be installed. */ }
    notify(current, status(current));
    if (!automatic(current)) {
      arm(current);
      void openActor(current);
    }
  }
  async function openActor(current) {
    if (!current?.state || current.state.phase === "finished" || current.state.actor === null) return;
    const player = members(current).find(candidate =>
      candidate.id === current.players[current.state.actor]);
    if (player) void open(player);
  }
  function startTicker() {
    if (interval !== undefined || !["fire", "light"].includes(game.id)) return;
    interval = system.runInterval(() => {
      const current = table();
      if (!current?.state || current.state.phase === "finished") {
        stopTicker();
        return;
      }
      try {
        const next = manager.systemAct(tableId, "tick");
        if (!next) return;
        if (!complete(next)) {
          if (current.token !== next.token) void openActor(next);
          for (const player of members(next)) {
            player.onScreenDisplay?.setActionBar(status(next).replace(/\n/g, " / "));
          }
        }
      } catch (error) {
        notify(current, error.message);
      }
    }, 20);
  }
  function start(current) {
    clearTimer();
    clearRegistration();
    try { venue?.start(game.id, current); } catch (error) { notify(current, `会場移動: ${error.message}`); }
    notify(current, status(current));
    startTicker();
    arm(current);
    void openActor(current);
  }

  async function aceForm(player, current) {
    const s = current.state;
    let value;
    if (s.phase === "table_bet") {
      const response = await new ModalFormData().title("タンブリング・エース：場代")
        .textField(`10〜${s.coins[s.actor]}枚`, "半角数字", { defaultValue: "10" }).show(player);
      if (response.canceled) return null;
      value = Number(String(response.formValues?.[0] ?? "").trim());
    } else if (s.phase === "betting") {
      const response = await new ActionFormData().title("タンブリング・エース：ベット")
        .body(status(current)).button("コール").button("レイズ").button("フォールド").show(player);
      if (response.canceled || !Number.isInteger(response.selection)) return null;
      if (response.selection === 2) value = { type: "fold" };
      else if (response.selection === 0) value = { type: "call" };
      else {
        const raise = await new ModalFormData().title("レイズ枚数")
          .textField("1〜200枚", "半角数字", { defaultValue: "20" }).show(player);
        if (raise.canceled) return null;
        value = { type: "raise", amount: Number(String(raise.formValues?.[0] ?? "").trim()) };
      }
    } else if (s.phase === "roll" || s.phase === "reroll") {
      const response = await new ActionFormData().title("タンブリング・エース：ダイス")
        .body(status(current)).button("5個のダイスを振る").show(player);
      if (response.canceled || response.selection !== 0) return null;
      value = shuffledDice();
    } else {
      tell(player, status(current));
      return null;
    }
    return { action: s.phase === "betting" ? "bet" : s.phase, value };
  }

  async function grailForm(player, current) {
    const s = current.state, seat = seatOf(current, player);
    if (s.phase === "arrange") {
      const response = await new ModalFormData().title("ホーリーグレイル：配置")
        .dropdown("本物の聖杯", ["位置1", "位置2", "位置3"], { defaultValue: 0 })
        .dropdown("聖水ポーション", ["位置1", "位置2", "位置3"], { defaultValue: 1 }).show(player);
      if (response.canceled) return null;
      return { action: "arrange", value: { trueIndex: response.formValues[0], holyIndex: response.formValues[1] } };
    }
    const action = s.phase === "cup" ? "cup" : "potion";
    const response = await new ActionFormData().title(action === "cup" ? "聖杯を選ぶ" : "ポーションを選ぶ")
      .body(status(current))
      .button("位置1").button("位置2").button("位置3").show(player);
    if (response.canceled || !Number.isInteger(response.selection)) return null;
    return { action, value: response.selection, seat };
  }

  async function auctionForm(player, current) {
    const s = current.state, seat = seatOf(current, player);
    const used = s.selected[seat];
    const available = [1, 2, 3, 4].filter(card => card !== used);
    const form = new ActionFormData().title("ライフ・イズ・オークショニア")
      .body(`${status(current)}\n未使用：${available.join("・")}`);
    for (const card of available) form.button(`${card}の札`);
    const response = await form.show(player);
    if (response.canceled || !Number.isInteger(response.selection)) return null;
    return { action: "bid", value: available[response.selection] };
  }

  async function fireForm(player, current) {
    const response = await new ActionFormData().title("シヴァリング・ファイア")
      .body(status(current)).button("グー").button("パー").button("チョキ").show(player);
    if (response.canceled || !Number.isInteger(response.selection)) return null;
    return {
      action: "choice",
      value: { choice: ["rock", "paper", "scissors"][response.selection], ticks: 120 },
    };
  }

  async function pigForm(player, current) {
    const seat = seatOf(current, player), s = current.state;
    const available = [1, 2, 3, 4].filter(card => !s.used[seat].includes(card));
    const labels = available.map(card => ({ 1: "わらの家", 2: "木の家", 3: "レンガの家", 4: "オオカミ" })[card]);
    const targets = current.players
      .map((_, index) => index)
      .filter(index => index !== seat && s.pigs[index] > 0)
      .map(index => `${index + 1}P`);
    const form = new ModalFormData().title("ピーキー・ピッグ・パレス")
      .dropdown("カード", labels, { defaultValue: 0 });
    if (targets.length) form.dropdown("オオカミの襲撃先", targets, { defaultValue: 0 });
    const response = await form.show(player);
    if (response.canceled) return null;
    const targetOptions = current.players.map((_, index) => index)
      .filter(index => index !== seat && s.pigs[index] > 0);
    return {
      action: "choose",
      value: { card: available[response.formValues[0]], target: targetOptions[response.formValues[1] ?? 0] },
    };
  }

  async function lightForm(player, current) {
    const s = current.state;
    if (s.phase === "arrange") {
      const guards = ["強欲", "幸運", "怠惰"], tourists = ["心臓", "槌", "松明"];
      const form = new ModalFormData().title("キャンドルライト：配置");
      for (let slot = 0; slot < 3; slot++) {
        form.dropdown(`門番 ${slot + 1}`, guards, { defaultValue: slot });
        form.dropdown(`死者 ${slot + 1}`, tourists, { defaultValue: slot });
      }
      const response = await form.show(player);
      if (response.canceled) return null;
      const values = response.formValues;
      return {
        action: "arrange",
        value: {
          guards: [values[0], values[2], values[4]],
          tourists: [values[1], values[3], values[5]],
        },
      };
    }
    const response = await new ModalFormData().title("相手の配置を選ぶ")
      .dropdown("門番", ["位置1", "位置2", "位置3"], { defaultValue: 0 })
      .dropdown("死者", ["位置1", "位置2", "位置3"], { defaultValue: 0 }).show(player);
    if (response.canceled) return null;
    return { action: "select", value: { guard: response.formValues[0], tourist: response.formValues[1] } };
  }

  async function mineForm(player, current) {
    const s = current.state;
    if (s.phase === "arrange") {
      const options = ["ブランク", "大喰らいの人魂", "先人のツルハシ", "無気力瓶"];
      const form = new ModalFormData().title("デビルズマイン：岩の配置");
      for (let slot = 0; slot < 4; slot++) form.dropdown(`位置${slot + 1}`, options, { defaultValue: slot });
      const response = await form.show(player);
      if (response.canceled) return null;
      return { action: "arrange", value: { cards: response.formValues } };
    }
    const response = await new ModalFormData().title("デビルズマイン：2つの岩を選ぶ")
      .dropdown("1つ目", ["位置1", "位置2", "位置3", "位置4"], { defaultValue: 0 })
      .dropdown("2つ目", ["位置2", "位置3", "位置4", "位置1"], { defaultValue: 0 }).show(player);
    if (response.canceled) return null;
    const second = [1, 2, 3, 0][response.formValues[1]];
    return { action: "mine", value: { indices: [response.formValues[0], second], ticks: 120 } };
  }

  async function boardForm(player, current) {
    const s = current.state;
    if (game.id === "hope") {
      const response = await new ActionFormData().title("ショートホープ：安全な床")
        .body(status(current)).button("1").button("2").button("3").button("4").button("5").show(player);
      if (response.canceled || !Number.isInteger(response.selection)) return null;
      return { action: "floor", value: response.selection + 1 };
    }
    if (game.id === "clock") {
      if (s.phase === "set") {
        const response = await new ModalFormData().title("ヘル・オ・クロック：時刻")
          .dropdown("時計位置", Array.from({ length: 12 }, (_, index) => `${index}時`), { defaultValue: 0 })
          .show(player);
        if (response.canceled) return null;
        return { action: "set", value: response.formValues[0] };
      }
      const response = await new ActionFormData().title("ヘル・オ・クロック：針")
        .body(status(current)).button("短針").button("長針").show(player);
      if (response.canceled || !Number.isInteger(response.selection)) return null;
      return { action: "attack", value: response.selection === 0 ? "short" : "long" };
    }
    if (game.id === "walker") {
      if (s.phase === "walk") {
        const form = new ActionFormData().title("ウォーカー：移動");
        const options = [];
        for (let distance = 1; distance <= 5; distance++) {
          const forward = s.positions[s.actor] + distance;
          const backward = s.positions[s.actor] - distance;
          if (forward <= 36) { options.push({ label: `${forward}へ +${distance}`, value: forward }); }
          if (backward >= 1) { options.push({ label: `${backward}へ -${distance}`, value: backward }); }
        }
        options.forEach(option => form.button(option.label));
        const response = await form.show(player);
        if (response.canceled) return null;
        return { action: "walk", value: options[response.selection].value };
      }
      const response = await new ModalFormData().title("ブレイカー：爆弾")
        .textField("床番号 1〜36", "半角数字", { defaultValue: "1" }).show(player);
      if (response.canceled) return null;
      return { action: "bomb", value: Number(String(response.formValues[0]).trim()) };
    }
    if (game.id === "garden") {
      const response = await new ActionFormData().title("ジュエルシャワー")
        .body(status(current)).button("切替").button("宝石倍化").button("開放").show(player);
      if (response.canceled || !Number.isInteger(response.selection)) return null;
      return { action: "choose", value: ["switch", "double", "open"][response.selection] };
    }
    if (game.id === "rabbit") {
      if (s.phase === "trap") {
        const response = await new ModalFormData().title("オニ：罠")
          .textField("床番号 1〜60", "半角数字", { defaultValue: "30" }).show(player);
        if (response.canceled) return null;
        return { action: "trap", value: Number(String(response.formValues[0]).trim()) };
      }
      const form = new ActionFormData().title("脱出側：移動").body(status(current));
      const options = [];
      for (let distance = 1; distance <= 5; distance++) {
        for (const target of [s.escaper + distance, s.escaper - distance]) {
          if (target >= 1 && target <= 60) options.push({ label: `${target}へ`, value: target });
        }
      }
      options.forEach(option => form.button(option.label));
      const response = await form.show(player);
      if (response.canceled) return null;
      return { action: "move", value: options[response.selection].value };
    }
    return null;
  }

  async function actionForm(player, current) {
    let request;
    if (game.id === "ace") request = await aceForm(player, current);
    else if (game.id === "grail") request = await grailForm(player, current);
    else if (game.id === "auction") request = await auctionForm(player, current);
    else if (game.id === "fire") request = await fireForm(player, current);
    else if (game.id === "pig") request = await pigForm(player, current);
    else if (game.id === "light") request = await lightForm(player, current);
    else if (game.id === "mine") request = await mineForm(player, current);
    else request = await boardForm(player, current);
    if (!request) return;
    if (manager.forPlayer(player.id)?.token !== current.token) {
      throw new Error("画面が古くなりました。開き直してください。");
    }
    const next = manager.act(player.id, current.token, request.action, request.value);
    afterAction(next);
  }

  async function open(player) {
    if (busy.has(player.id)) return;
    busy.add(player.id);
    let reopenAfterJoin = false;
    try {
      const mine = manager.forPlayer(player.id);
      if (mine && mine.gameId !== tableId) throw new Error("別のゲームに参加しています。");
      if (mine?.state) {
        const seat = seatOf(mine, player);
        if (!mine.state.alive || mine.state.alive?.[seat] !== false) {
          if (mine.state.actor === seat && mine.state.phase !== "finished") {
            await actionForm(player, mine);
            return;
          }
        }
        const response = await new ActionFormData().title(game.title)
          .body(status(mine)).button("退出して試合を中止").show(player);
        if (!response.canceled && response.selection === 0) {
          finish(mine, "参加者が退出したため中止しました。");
        }
        return;
      }
      const current = mine ?? table();
      const canJoin = !mine && (!current || current.players.length < (game.maxPlayers ?? game.seats));
      const response = await new ActionFormData().title(`JUNKET：${game.title}`)
        .body(`${current ? status(current) : "参加者募集中です。"}\n\n${gameRules(game.id)}`)
        .button(mine ? "退出" : canJoin ? "参加する" : "閉じる").show(player);
      if (response.canceled || response.selection !== 0) return;
      if (mine) {
        finish(mine, "参加者が退出したため中止しました。");
        return;
      }
      if (!canJoin) return;
      const joined = manager.join(tableId, tableId, player.id);
      notify(joined, status(joined));
      if (joined.state) {
        start(joined);
        reopenAfterJoin = joined.state.actor === seatOf(joined, player) &&
          joined.state.phase !== "finished";
      }
      else if (game.registration) {
        clearRegistration();
        registrationTimer = system.runTimeout(() => {
          registrationTimer = undefined;
          const live = table();
          if (!live || live.state || !live.players.length) return;
          try {
            const started = manager.start(tableId);
            start(started);
          } catch (error) {
            notify(live, error.message);
          }
        }, HOPE_REGISTRATION_TICKS);
        notify(joined, "15秒以内の追加参加を受け付けます。");
      }
    } catch (error) {
      tell(player, error.message ?? "操作に失敗しました。");
    } finally {
      busy.delete(player.id);
      if (reopenAfterJoin) system.run(() => { void open(player); });
    }
  }

  world.beforeEvents.playerInteractWithEntity.subscribe(event => {
    if (!event.target.hasTag(receptionTag) && !event.target.hasTag(controlTag)) return;
    event.cancel = true;
    const player = event.player;
    system.run(() => { void open(player); });
  });

  world.afterEvents.playerLeave.subscribe(event => {
    const mine = manager.forPlayer(event.playerId);
    busy.delete(event.playerId);
    if (!mine || mine.gameId !== tableId) return;
    if (game.id === "hope") {
      const seat = mine.players.indexOf(event.playerId);
      const next = manager.systemAct(tableId, "disconnect", seat);
      manager.detach(event.playerId);
      if (next?.state?.phase === "finished") complete(next);
      else {
        const live = table();
        if (live) afterAction(live);
      }
      return;
    }
    finish(mine, "参加者が切断したため中止しました。");
  });

  system.afterEvents.scriptEventReceive.subscribe(event => {
    if (event.sourceEntity?.typeId !== "minecraft:player") return;
    const menuId = `junket:${game.id}`;
    const menuAlias = `junket:${game.id}menu`;
    if (event.id !== menuId && event.id !== menuAlias) return;
    const player = event.sourceEntity;
    const command = event.message.trim();
    if (!command || command === "menu") {
      system.run(() => { void open(player); });
    } else if (command === "leave") {
      const mine = manager.forPlayer(player.id);
      if (mine?.gameId === tableId) finish(mine, "参加者が退出したため中止しました。");
    } else if (command === "start" && game.registration) {
      system.run(() => {
        try { start(manager.start(tableId)); } catch (error) { tell(player, error.message); }
      });
    }
  });

  return { open, table, status, venue };
}

export function gameRules(id) {
  return ({
    ace: "4人・100枚から開始。場代とベットを行い、5ダイスの役で勝者総取り。5ゲーム後のチーム合計で判定します。",
    grail: "2人・10ラウンド。聖杯とポーションを配置し、相手の選択を読みます。聖水100ml獲得、毒は累積し500mlで致命傷。",
    auction: "4人。1・3がA、2・4がBチーム。1〜4の札を同時公開し、最高値の単独提示が落札。支払い16以上か、先にダイヤ3個で決着。",
    fire: "2人。グー・パー・チョキを同時に選び先に3勝。勝敗で室温が上下し、室温から毎秒ダメージを受けます。",
    pig: "4人。わら・木・レンガ・オオカミの使い切りカードで子豚を争います。最後の子豚を守った側が宮殿を建てます。",
    light: "2人。互いの門番・死者を3枠へ隠し、相手の1枠を選びます。ハート7個で勝利。松明は酸欠までの時間を延ばします。",
    mine: "2人・20ラウンド。互いの4つの岩を伏せて配置し、2つを選んで採掘。猛毒とヘルインゴット残数で決着します。",
    hope: "1〜16人・10ラウンド。各ラウンドの安全な床へ先着登録者が同時に進みます。15秒後に開始し、生存者が勝利します。",
    clock: "2人・8ラウンド。互いの時刻位置を秘匿し、短針・長針の攻撃で相手を水中へ落とします。",
    walker: "2人・36マス。ウォーカーは1〜5マス進み、ブレイカーは爆弾を設置。爆風を避け、最後まで残った側の勝利です。",
    garden: "2人。切替・宝石倍化・開放を選び、宝石の放出先を操作します。先に落下した側が敗北。",
    rabbit: "2人。オニが罠を伏せ、脱出側は1〜5マス移動。仲間とともにゴールを目指し、罠・時間切れを避けます。",
  })[id] ?? "";
}

export function bestOfAceTeam(state) {
  if (!state || state.phase !== "finished" || !Number.isInteger(state.winnerTeam)) return null;
  return state.winnerTeam;
}
