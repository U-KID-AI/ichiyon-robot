import { isLiving } from "./companion_owner.js";

// Explicit allow-list: custom game actors and future/unknown mobs never become healing targets by accident.
const FRIENDLY_TYPES = new Set([
  "cow", "pig", "sheep", "chicken", "rabbit", "horse", "donkey", "mule", "llama", "trader_llama",
  "camel", "cat", "wolf", "parrot", "villager", "villager_v2", "wandering_trader", "iron_golem",
  "snow_golem", "allay", "bee", "axolotl", "turtle", "fox", "goat", "frog", "sniffer", "armadillo",
  "panda", "polar_bear", "dolphin", "squid", "glow_squid", "cod", "salmon", "tropicalfish", "pufferfish", "mooshroom"
].map((name) => `minecraft:${name}`));
for (const name of ["molcar", "molcar2", "molcar3", "garbage_molcar", "mokuro", "pikachu", "taketumi", "tumaini", "ikarun"])
  FRIENDLY_TYPES.add(`ichiyon:${name}`);

export function canHealFriendly(entity) {
  if (!isLiving(entity) || !FRIENDLY_TYPES.has(entity.typeId)) return false;
  const tags = entity.getTags();
  if (tags.some((tag) => /^(junket[:_]|jg[:_]|ichiyon:game|game[:_]|no_heal$)/i.test(tag))) return false;
  const family = entity.getComponent("minecraft:type_family");
  if (["monster", "boss", "undead", "junket", "game_actor"].some((name) => family?.hasTypeFamily(name))) return false;
  // Wild wolves / neutral mobs actively fighting players must not become unintended boss support.
  if (entity.target?.typeId === "minecraft:player") return false;
  const hp = entity.getComponent("minecraft:health");
  return Number.isFinite(hp.effectiveMax) && hp.currentValue < hp.effectiveMax;
}

export function healFriendly(entity, amount) {
  if (!canHealFriendly(entity) || !(amount > 0)) return 0;
  const hp = entity.getComponent("minecraft:health");
  const before = hp.currentValue, after = Math.min(hp.effectiveMax, before + amount);
  if (!hp.setCurrentValue(after)) return 0;
  return after - before;
}
