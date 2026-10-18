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

const manager = new GameManager([
  sparrowGame, temperanceGame, lucyGame, silenceGame, jinnGame,
  aceGame, ...STRATEGY_GAMES, ...BOARD_GAMES,
]);
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
const warp = createWarpSystem({
  ...context,
  destinations: {
    sparrow: () => {
      const target = sparrow.venue.destination("hall");
      return target && { ...target, afterTeleport: () => sparrow.venue.repair() };
    },
    temperance: () => {
      const target = temperance.venue.destination();
      return target && { ...target, afterTeleport: () => temperance.venue.ensure() };
    },
    lucy: () => {
      const target = lucy.venue.destination();
      return target && { ...target, afterTeleport: () => lucy.venue.ensure() };
    },
    silence: () => {
      const target = silence.venue.destination();
      return target && { ...target, afterTeleport: () => silence.venue.ensure() };
    },
    jinn: () => {
      const target = jinn.venue.destination();
      return target && { ...target, afterTeleport: () => jinn.venue.ensure() };
    },
    ...Object.fromEntries([...completeRuntimes].map(([id]) => [
      id,
      () => productionVenues.destination(id),
    ])),
  },
});
const specialVenues = {
  sparrow: sparrow.venue,
  temperance: temperance.venue,
  lucy: lucy.venue,
  silence: silence.venue,
  jinn: jinn.venue,
};

world.afterEvents.worldLoad?.subscribe(() => {
  system.runTimeout(() => productionVenues.installAll(warp, specialVenues), 20);
});
system.afterEvents.scriptEventReceive.subscribe(event => {
  if (event.id !== "junket:installall") return;
  productionVenues.installAll(warp, specialVenues);
});
