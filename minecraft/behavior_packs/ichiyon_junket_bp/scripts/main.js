import { world, system } from "@minecraft/server";
import { ActionFormData, ModalFormData } from "@minecraft/server-ui";
import { createRuntime } from "./runtime.js";
import { GameManager } from "./game_manager.js";
import { sparrowGame } from "./sparrow.js";
import { temperanceGame } from "./temperance.js";
import { createTemperanceRuntime } from "./temperance_runtime.js";
import { createWarpSystem } from "./warp.js";
import { lucyGame } from "./lucy.js";
import { createLucyRuntime } from "./lucy_runtime.js";
import { silenceGame } from "./silence.js";
import { createSilenceRuntime } from "./silence_runtime.js";
import { jinnGame } from "./jinn.js";
import { createJinnRuntime } from "./jinn_runtime.js";
import { aceGame } from "./ace.js";
import { STRATEGY_GAMES } from "./strategy_games.js";
import { BOARD_GAMES } from "./board_games.js";
import { createCompleteRuntime } from "./complete_runtime.js";
import { createProductionVenues } from "./production_venue.js";
import { installFixtureFocusHints } from "./venue_presentation.js";
import { installDecisionResume } from "./decision_resume.js";
import { bankCatalog, createBankHub } from "./bank_hub.js";

const manager = new GameManager([
  sparrowGame, temperanceGame, lucyGame, silenceGame, jinnGame,
  aceGame, ...STRATEGY_GAMES, ...BOARD_GAMES,
], { world, system });
const context = { world, system, ActionFormData, ModalFormData, manager };
const sparrow = createRuntime(context);
const temperance = createTemperanceRuntime({ ...context, installer: sparrow.venue });
const lucy = createLucyRuntime({ ...context, installer: sparrow.venue });
const silence = createSilenceRuntime({ ...context, installer: sparrow.venue });
const jinn = createJinnRuntime({ ...context, installer: sparrow.venue });
const productionVenues = createProductionVenues(context);
const completeRuntimes = new Map([
  aceGame, ...STRATEGY_GAMES, ...BOARD_GAMES,
].map(game => [game.id, createCompleteRuntime({ ...context, game, venue: productionVenues })]));
const specialVenues = {
  sparrow: sparrow.venue,
  temperance: temperance.venue,
  lucy: lucy.venue,
  silence: silence.venue,
  jinn: jinn.venue,
};
productionVenues.setSpecialVenues(specialVenues);

const destinations = Object.fromEntries(
  ["sparrow", "temperance", "lucy", "silence", "jinn", ...completeRuntimes.keys()]
    .map(id => [id, () => productionVenues.destination(id)]),
);
const warp = createWarpSystem({ ...context, destinations, catalog: bankCatalog });
createBankHub({ ...context, warp });
installFixtureFocusHints({ world, system, venues: productionVenues.entries });

const runtimes = new Map([
  ["sparrow", sparrow], ["temperance", temperance], ["lucy", lucy],
  ["silence", silence], ["jinn", jinn], ...completeRuntimes,
]);
const restored = manager.restore();
installDecisionResume({ world, system, manager, runtimes });
if (restored.restored || restored.skipped) {
  console.log(`[JUNKET] restored=${restored.restored} skipped=${restored.skipped}`);
}

function resumeAll(playerId = null) {
  try {
    productionVenues.resume?.(playerId);
  } catch (error) {
    console.warn(`[JUNKET] production venue resume failed: ${error?.message ?? error}`);
  }
  for (const [id, runtime] of runtimes) {
    try {
      runtime.resume?.(playerId);
    } catch (error) {
      console.warn(`[JUNKET] ${id} resume failed: ${error?.message ?? error}`);
    }
  }
}

let initialized = false;
function initialize() {
  if (initialized) return;
  initialized = true;
  // Restore session timers before venue verification. The destination repair
  // path is still authoritative and may run again when a player uses a warp.
  resumeAll();
  void productionVenues.installAll(warp, specialVenues).catch(error => {
    console.warn(`[JUNKET] installation failed: ${error?.message ?? error}`);
  });
}

// worldLoad is the normal path, while the startup timeout covers a missed
// event during a hot reload or a Script API version without that event.
system.runTimeout(initialize, 1);
world.afterEvents.worldLoad?.subscribe(() => {
  system.runTimeout(initialize, 20);
});

const joinedEvent = world.afterEvents.playerJoin ?? world.afterEvents.playerSpawn;
joinedEvent?.subscribe(event => {
  const playerId = event.playerId ?? event.player?.id;
  if (!playerId) return;
  system.runTimeout(() => resumeAll(playerId), 1);
});

system.afterEvents.scriptEventReceive.subscribe(event => {
  if (event.id === "junket:installall") {
    void productionVenues.installAll(warp, specialVenues).catch(error => {
      console.warn(`[JUNKET] installation failed: ${error?.message ?? error}`);
    });
  } else if (event.id === "junket:resumeall") {
    resumeAll();
  }
});
