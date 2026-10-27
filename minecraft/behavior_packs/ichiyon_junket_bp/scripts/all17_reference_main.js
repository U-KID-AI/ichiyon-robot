// Private production entry. all17_original_generated.js is created only in
// the local pack by the native deployment builder; source assets stay private.
import { world, system } from "@minecraft/server";
import { ActionFormData, ModalFormData } from "@minecraft/server-ui";
import { ORIGINAL_VENUES } from "./all17_original_generated.js";
import { registerOriginalVenues } from "./original_venue.js";
import { GameManager } from "./game_manager.js";
import { sparrowGame } from "./sparrow.js";
import { temperanceGame } from "./temperance.js";
import { lucyGame } from "./lucy.js";
import { silenceGame } from "./silence.js";
import { jinnGame } from "./jinn.js";
import { aceGame } from "./ace.js";
import { STRATEGY_GAMES } from "./strategy_games.js";
import { BOARD_GAMES } from "./board_games.js";
import { createRuntime } from "./runtime.js";
import { createTemperanceRuntime } from "./temperance_runtime.js";
import { createLucyRuntime } from "./lucy_runtime.js";
import { createSilenceRuntime } from "./silence_runtime.js";
import { createJinnRuntime } from "./jinn_runtime.js";
import { createCompleteRuntime } from "./complete_runtime.js";
import { createProductionVenues } from "./production_venue.js";
import { createWarpSystem } from "./warp.js";
import { createQualityProbe } from "./quality_probe.js";
import { installFixtureFocusHints } from "./venue_presentation.js";
import { createVenueRecovery } from "./venue_recovery.js";
import { createServiceControls, createResetPrompt } from "./service_controls.js";
import { installFixtureAnchors } from "./fixture_anchor.js";
import { createResilienceProbe } from "./resilience_probe.js";
import { installLegacyGuideCleanup } from "./legacy_guide_cleanup.js";
import { installDecisionResume } from "./decision_resume.js";
import { bankCatalog, createBankHub } from "./bank_hub.js";

let live = null;
let initializationStarted = false;
let installation = null;
let venuesReady = false;

function installVenues() {
  if (!live) return;
  if (installation) return installation;
  installation = live.venues.installAll(live.warp, live.specialVenues).then(report => {
    venuesReady = report?.ok === true && report.completed?.length === ORIGINAL_VENUES.length;
    console.log(`[JUNKET ORIGINAL] runtime fixtures initialized: ${JSON.stringify(report)}`);
    return report;
  }).catch(error => {
    console.error(`[JUNKET ORIGINAL] fixture setup: ${error?.stack ?? error}`);
  }).finally(() => { installation = null; });
  return installation;
}

function resumeAll(playerId = null) {
  if (!live || !venuesReady) return;
  for (const [id, runtime] of live.runtimes) {
    try { runtime.resume?.(playerId); }
    catch (error) { console.warn(`[JUNKET ORIGINAL] ${id} resume: ${error?.message ?? error}`); }
  }
  try { live.venues.resume?.(playerId); }
  catch (error) { console.warn(`[JUNKET ORIGINAL] resume: ${error?.message ?? error}`); }
}
async function initialize() {
  if (live || initializationStarted) return;
  initializationStarted = true;
  if (ORIGINAL_VENUES.length !== 17 || new Set(ORIGINAL_VENUES.map(v => v.id)).size !== 17 ||
      ORIGINAL_VENUES.some(v => v.installation !== "native-preinstalled")) {
    throw new Error("全17原作会場のnative配置manifestが不正です。");
  }
  registerOriginalVenues(ORIGINAL_VENUES);
  installLegacyGuideCleanup({ world, system, definitions: ORIGINAL_VENUES });
  const manager = new GameManager([
    sparrowGame, temperanceGame, lucyGame, silenceGame, jinnGame,
    aceGame, ...STRATEGY_GAMES, ...BOARD_GAMES,
  ], { world, system });
  const context = { world, system, ActionFormData, ModalFormData, manager };
  const sparrow = createRuntime(context);
  const special = new Map([
    ["sparrow", sparrow],
    ["temperance", createTemperanceRuntime({ ...context, installer: sparrow.venue })],
    ["lucy", createLucyRuntime({ ...context, installer: sparrow.venue })],
    ["silence", createSilenceRuntime({ ...context, installer: sparrow.venue })],
    ["jinn", createJinnRuntime({ ...context, installer: sparrow.venue })],
  ]);
  const venues = createProductionVenues({ ...context, venues: ORIGINAL_VENUES });
  const specialVenues = Object.fromEntries([...special].map(([id, runtime]) => [id, runtime.venue]));
  venues.setSpecialVenues(specialVenues);
  const runtimes = new Map([
    ...special,
    ...[aceGame, ...STRATEGY_GAMES, ...BOARD_GAMES].map(game => [
      game.id, createCompleteRuntime({ ...context, game, venue: venues }),
    ]),
  ]);
  const destinations = Object.fromEntries(ORIGINAL_VENUES.map(v => [v.id, () => venues.destination(v.id)]));
  installDecisionResume({ world, system, manager, runtimes });
  const warp = createWarpSystem({ ...context, destinations, catalog: bankCatalog });
  live = { manager, runtimes, venues, warp, specialVenues };
  live.bank = createBankHub({ ...context, warp });
  live.recovery = createVenueRecovery({ world, system, manager, venues, runtimes,
    confirm: createResetPrompt({ ActionFormData }),
    evacuate: async (players, { gameId }) => {
      for (const player of players) {
        if (!await warp.evacuateForRecovery(player, gameId)) throw new Error(`${player.name}: 安全な退避を完了できませんでした。`);
      }
    } });
  live.services = createServiceControls({ ...context, runtimes, venues: ORIGINAL_VENUES,
    warp, recovery: live.recovery }).install();
  installFixtureAnchors({ world, system, manager, definitions: ORIGINAL_VENUES });
  live.resilience = createResilienceProbe({ world, system, live, definitions: ORIGINAL_VENUES });
  live.quality = createQualityProbe({ world, system, live, definitions: ORIGINAL_VENUES });
  installFixtureFocusHints({ world, system, venues: ORIGINAL_VENUES });
  const restored = manager.restore();
  console.log(`[JUNKET ORIGINAL] restored=${restored.restored} skipped=${restored.skipped}`);
  await live.recovery.resumePending();
  await installVenues();
  resumeAll();
}

async function bootstrap() {
  if (live || initializationStarted) return;
  try {
    // This read is forbidden in early execution. The scheduled fallback must
    // wait for world readiness too, even if a hot reload misses worldLoad.
    world.getDynamicProperty("junket:original_boot_readiness");
    await initialize();
  } catch (error) {
    console.error(`[JUNKET ORIGINAL] bootstrap: ${error?.stack ?? error}`);
  }
}

// World reads/runtime construction are deferred. The timer also covers a
// Script API hot reload with no connected player and no new worldLoad event.
world.afterEvents.worldLoad.subscribe(() => { system.run(bootstrap); });
system.runTimeout(bootstrap, 40);
world.afterEvents.playerSpawn.subscribe(event => {
  system.run(() => {
    if (!live) bootstrap();
    if (event.initialSpawn) resumeAll(event.player.id);
  });
});
system.afterEvents.scriptEventReceive.subscribe(event => {
  if (!live && ["junket:resumeall", "junket:installall"].includes(event.id)) bootstrap();
  if (!live) return;
  if (live.recovery.handleScriptEvent(event)) return;
  if (event.id === "junket:resumeall") resumeAll();
  else if (event.id === "junket:installall") void installVenues();
  else if (event.id === "junket:qualitycheck" && !event.sourceEntity && !event.sourceBlock &&
           /^[0-9a-f-]{36}$/i.test(event.message.trim())) {
    void live.quality.run(event.message.trim()).catch(error => console.error(`[JUNKET QUALITY] unexpected: ${error?.stack ?? error}`));
  }
  else if (event.id === "junket:resiliencecheck" && event.sourceType === "Server" && !event.sourceEntity && !event.sourceBlock) {
    const [phase, requestId] = event.message.trim().split(/\s+/);
    if (["destructive", "restart", "snapshot", "snapshot_restart"].includes(phase) && /^[0-9a-f-]{36}$/i.test(requestId ?? ""))
      void live.resilience.run(requestId, phase).catch(error => console.error(`[JUNKET RESILIENCE] unexpected: ${error?.stack ?? error}`));
  }
});
