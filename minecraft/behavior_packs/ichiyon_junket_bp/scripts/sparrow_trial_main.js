// Entry for the private reference .mcworld only. The regular 17-game main.js
// remains unchanged and never loads source-derived buildings automatically.
import { world, system } from "@minecraft/server";
import { ActionFormData, ModalFormData } from "@minecraft/server-ui";
import { GameManager } from "./game_manager.js";
import { sparrowGame } from "./sparrow.js";
import { createRuntime } from "./runtime.js";
import { registerOriginalVenues, createOriginalVenueInstaller, translateVenue } from "./original_venue.js";
import { SPARROW_REFERENCE } from "./sparrow_reference.js";
import { ensureManifestFixtures, verifyManifestFixtures } from "./special_venue.js";

if (!SPARROW_REFERENCE) throw new Error("Private Sparrow reference data is required; use the private trial builder");
registerOriginalVenues([translateVenue(SPARROW_REFERENCE)]);
let runtime, installer, starting = false;
const welcomed = new Set();
function welcome(player) {
  if (!runtime || welcomed.has(player.id)) return;
  welcomed.add(player.id);
  runtime.venue.go(player);
  player.sendMessage("[JUNKET] 原作会場テスト。受付を右クリック/タッチして2人で参加してください。操作を閉じた場合は受付または /scriptevent junket:menu open で再開できます。");
  runtime.resume(player.id);
}
async function initialize() {
  if (starting || runtime) return;
  starting = true;
  try {
    installer ??= createOriginalVenueInstaller({ world, system });
    await installer.install("sparrow");
    ensureManifestFixtures(world, "sparrow");
    verifyManifestFixtures(world, "sparrow");
    const manager = new GameManager([sparrowGame], { world, system });
    manager.restore();
    runtime = createRuntime({ world, system, ActionFormData, ModalFormData, manager });
    runtime.resume();
    world.getAllPlayers().forEach(welcome);
    console.log("[JUNKET REFERENCE] Original Sparrow hall and six fixtures ready; no 17-game installer loaded.");
  } catch (error) {
    console.error(`[JUNKET REFERENCE] ${error.stack ?? error}`);
    world.getAllPlayers().forEach(player => player.sendMessage(`[JUNKET] 会場準備失敗: ${error.message}`));
  } finally { starting = false; }
}
// Script API v2 cannot read world state during early execution.
world.afterEvents.worldLoad.subscribe(() => { void initialize(); });
world.afterEvents.playerSpawn.subscribe(event => {
  system.run(() => {
    if (!runtime) void initialize();
    else if (event.initialSpawn) welcome(event.player);
  });
});
world.afterEvents.playerLeave.subscribe(event => welcomed.delete(event.playerId));
system.afterEvents.scriptEventReceive.subscribe(event => {
  if (event.id !== "junket:reference") return;
  const player = event.sourceEntity;
  if (player?.typeId !== "minecraft:player") return;
  if (event.message.trim() === "status") {
    player.sendMessage(`[JUNKET] 原作会場: ${runtime ? "準備完了" : "未準備"} / 参加者 ${runtime?.manager.view("sparrow")?.players.length ?? 0}/2`);
  } else if (!runtime) void initialize();
  else if (!runtime.manager.tables.size) {
    try { installer.verify(SPARROW_REFERENCE); player.sendMessage(runtime.venue.repair()); runtime.venue.go(player); }
    catch (error) { player.sendMessage(`[JUNKET] ${error.message}`); }
  } else player.sendMessage("[JUNKET] 対戦中は修復できません。退出後に実行してください。");
});
