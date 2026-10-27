import { BANK_HUB } from "./bank_hub_generated.js";
// Shared, state-free fixture text. Never expose a game's secret state through
// name tags or focus hints; private choices continue to use its existing forms.
export const NAVIGATION_LABELS = Object.freeze({
  reception: "参加受付", rules: "説明", join: "参加する", rematch: "再戦",
  leave: "退出", catalog: BANK_HUB.enabled ? "カラス銀行へ戻る" : "カタログへ戻る", act: "ゲーム操作", close: "閉じる",
  venue: "会場受付へ戻る",
  menu: "ゲーム操作", start: "参加者で開始", reset: "ゲームをリセット（全員同意）",
  repair: "管理者：会場を修復",
});
export const MAX_PERMANENT_FIXTURE_LABELS = 2;
const visited = new Map();
const catalogReturns = new WeakMap();
const formTicket = Symbol("junketFormTicket");
const receptionPanels = new WeakMap();

// Only the player who pressed a bound wall panel receives these maintenance
// actions. Existing turn forms and guide interactions keep their own routing.
export async function withReceptionPanel(player, gameId, panel, open) {
  const context = { ...panel, gameId };
  receptionPanels.set(player, context);
  try { return await open(); }
  finally { if (receptionPanels.get(player) === context) receptionPanels.delete(player); }
}

function formNotice(player, text) {
  try { player?.sendMessage(`[JUNKET] ${text}`); } catch { /* Disconnected. */ }
}

export async function showFormSafely(form, player) {
  try {
    const response = await form.show(player);
    const reason = response?.cancelationReason ?? response?.cancellationReason ?? "";
    if (response?.canceled && /userbusy/i.test(String(reason))) {
      formNotice(player, "別の画面が開いています。その画面やチャットを閉じ、もう一度受付ボタンをタップしてください。");
    }
    return response ?? { canceled: true, cancelationReason: "NoResponse" };
  } catch (error) {
    formNotice(player, `画面を開けませんでした。画面やチャットを閉じて再操作してください。（${error?.message ?? error}）`);
    return { canceled: true, cancelationReason: "FormError" };
  }
}

// Every game form, including a private turn form, gets the same cancellation
// diagnostics and a reset fence. Invalidating a pending form settles its await
// immediately; its later client reply can never affect a new match.
export function createRuntimeForms({ ActionFormData, ModalFormData, world, system }) {
  const pending = new Set();
  function wrap(Base) {
    if (!Base) return undefined;
    return class {
      constructor(...args) {
        const target = new Base(...args);
        let proxy;
        proxy = new Proxy(target, { get(object, key) {
          if (key === formTicket) return true;
          if (key === "show") return async player => {
            const ticket = { playerId: player.id, invalidated: false, cancel: null };
            const canceled = new Promise(resolve => { ticket.cancel = () => {
              ticket.invalidated = true;
              resolve({ canceled: true, cancelationReason: "SessionReset" });
            }; });
            pending.add(ticket);
            try {
              let result;
              for (let attempt = 0; attempt < 3; attempt++) {
                result = await Promise.race([showFormSafely(target, player), canceled]);
                const reason = result?.cancelationReason ?? result?.cancellationReason ?? "";
                if (!system || !result?.canceled || !/userbusy/i.test(String(reason)) || ticket.invalidated) break;
                if (attempt < 2) await Promise.race([
                  new Promise(resolve => system.runTimeout(resolve, 10 * (attempt + 1))), canceled,
                ]);
                if (ticket.invalidated) break;
              }
              const online = !world?.getAllPlayers || world.getAllPlayers().some(value => value.id === player.id);
              return ticket.invalidated || !online
                ? { canceled: true, cancelationReason: "SessionReset" } : result;
            } finally { pending.delete(ticket); }
          };
          const value = Reflect.get(object, key, object);
          if (typeof value !== "function") return value;
          return (...methodArgs) => {
            const result = value.apply(object, methodArgs);
            return result === object ? proxy : result;
          };
        } });
        return proxy;
      }
    };
  }
  return {
    ActionFormData: wrap(ActionFormData), ModalFormData: wrap(ModalFormData),
    invalidate(playerIds) {
      const ids = playerIds ? new Set(playerIds) : null;
      for (const ticket of pending) if (!ids || ids.has(ticket.playerId)) ticket.cancel();
    },
  };
}

export function permanentFixtureLabel(venue, fixture) {
  if (fixture.role === "reception") return `${NAVIGATION_LABELS.reception}\n説明・再戦`;
  if (fixture.role === "return") return NAVIGATION_LABELS.catalog;
  return "";
}

export function applyFixturePresentation(entity, venue, fixture) {
  if (!entity) return false;
  const text = permanentFixtureLabel(venue, fixture);
  try {
    if (entity.nameTag !== text) entity.nameTag = text;
    return true;
  } catch { return false; } // An unloaded entity is handled by venue lifecycle.
}

export function applyVenuePresentation(venue, resolveEntity) {
  let applied = 0;
  for (const fixture of venue.fixtures) {
    const result = resolveEntity(fixture);
    for (const entity of Array.isArray(result) ? result : [result]) {
      if (applyFixturePresentation(entity, venue, fixture)) applied++;
    }
  }
  return applied;
}

export function fixtureInteractionHint(venue, fixture) {
  if (fixture.role === "reception") return `${venue.title}｜参加受付・説明・再戦・退出`;
  if (fixture.role === "return") return NAVIGATION_LABELS.catalog;
  // Only the immutable manifest name is used. In particular, Lucy's selected
  // answer and Jinn's hidden card choice must never be read from entity.nameTag.
  const name = String(fixture.name || fixture.role).replace(/\s+/g, " ").trim();
  return `${name}｜操作`;
}

export function receptionActions({ joined = false, canJoin = false, canAct = false, rematch = false } = {}) {
  const actions = [];
  if (joined && canAct) actions.push("act");
  if (!joined && canJoin) actions.push(rematch ? "rematch" : "join");
  actions.push("rules");
  if (joined) actions.push("leave");
  actions.push("catalog", "close");
  return actions;
}

export function registrationGuide({ seats = 2, registration = false } = {}) {
  return registration
    ? `1〜${seats}人で参加できます。最初の参加から15秒後に自動で開始します。`
    : `${seats}人の参加が揃うと自動で開始します。`;
}

export async function showReceptionNavigation({ ActionFormData, player, gameId, title,
  body = "", rules = "", joined = false, canJoin = false, canAct = false,
  seats = 2, registration = false }) {
  const seen = visited.get(gameId) ?? new Set();
  const panel = receptionPanels.get(player);
  const bound = panel?.gameId === gameId ? panel : null;
  for (;;) {
    if (bound && !bound.valid()) return null;
    const actions = receptionActions({ joined, canJoin, canAct, rematch: seen.has(player.id) });
    if (bound) {
      if (joined) actions.splice(actions.indexOf("catalog"), 1);
      const extra = bound.actions();
      actions.splice(actions.indexOf("close"), 0, ...extra);
    }
    const form = new ActionFormData().title(`${title}｜${bound ? NAVIGATION_LABELS.menu : NAVIGATION_LABELS.reception}`)
      .body(`${body}\n${registrationGuide({ seats, registration })}${joined ? "\n退出すると参加中の試合を中止します。カタログへ戻る前に退出してください。" : ""}`);
    for (const action of actions) form.button(NAVIGATION_LABELS[action]);
    const response = form[formTicket] ? await form.show(player) : await showFormSafely(form, player);
    if (response.canceled) return null;
    if (bound && !bound.valid()) { formNotice(player, "参加状況や試合が変わりました。ゲーム操作を開き直してください。"); return null; }
    const action = actions[response.selection];
    if (!action || action === "close") return null;
    if (action === "rules") {
      const explanation = new ActionFormData().title(`${title}｜${NAVIGATION_LABELS.rules}`)
        .body(rules || "会場の操作物に照準を合わせると操作名が表示されます。参加受付から参加してください。")
        .button(NAVIGATION_LABELS.reception);
      const returned = explanation[formTicket] ? await explanation.show(player) : await showFormSafely(explanation, player);
      if (returned.canceled) return null;
      continue;
    }
    if (bound && ["start", "reset", "repair"].includes(action)) {
      await bound.run(action);
      return null;
    }
    if (action === "join" || action === "rematch") {
      seen.add(player.id); visited.set(gameId, seen);
    }
    return action;
  }
}

export function registerCatalogReturn(world, handler) {
  catalogReturns.set(world, handler);
}

export function requestCatalogReturn(player, world) {
  const handler = world && catalogReturns.get(world);
  if (!handler) throw new Error("カタログ帰還を準備中です。帰還ボタンから再操作してください。");
  // Direct Script API call: ordinary players do not need command permissions.
  return handler(player);
}

const focusInstallations = new WeakMap();
export function installFixtureFocusHints({ world, system, venues }) {
  if (focusInstallations.has(world)) return focusInstallations.get(world);
  const byId = new Map(venues.map(venue => [venue.id, venue]));
  const timer = system.runInterval(() => {
    for (const player of world.getAllPlayers()) {
      try {
        const hit = player.getEntitiesFromViewDirection?.({ maxDistance: 6 })?.[0]?.entity;
        if (!hit) continue;
        const id = hit.getDynamicProperty?.("junket:venue_id");
        const venue = byId.get(id);
        if (!venue) continue;
        const prefix = `junket:${id}:fixture:`;
        const role = hit.getTags?.().find(tag => tag.startsWith(prefix))?.slice(prefix.length)
          ?? (venue.provider?.role_property ? hit.getDynamicProperty?.(venue.provider.role_property) : undefined)
          ?? hit.getDynamicProperty?.("junket:venue_role");
        const fixture = venue.fixtures.find(entry => entry.role === role);
        if (fixture) player.onScreenDisplay.setActionBar(fixtureInteractionHint(venue, fixture));
      } catch { /* Player or focus target unloaded. Do not alter other HUDs. */ }
    }
  }, 8);
  focusInstallations.set(world, timer);
  return timer;
}
