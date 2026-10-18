export const RECEPTION_TAG = "junket:reception";
const TEMPORARY_TAG = "junket:temporary_reception";
const SETUP_VERSION = "0.1.2";
// Stable server 2.0.0 has no Block.isSolid. Use known full-block floors for
// this temporary reception, including both Bedrock grass identifiers.
const FLOORS = new Set([
  "minecraft:grass_block", "minecraft:grass", "minecraft:dirt",
  "minecraft:coarse_dirt", "minecraft:rooted_dirt", "minecraft:dirt_with_roots",
  "minecraft:stone", "minecraft:cobblestone", "minecraft:bedrock",
]);
const isAir = block => block?.isAir === true || block?.typeId === "minecraft:air";
function feetY(value) {
  const nearest = Math.round(value);
  return Math.floor(Math.abs(value - nearest) < 0.0001 ? nearest : value);
}

// Explicit operator setup only. No automatic spawning, terrain edits or ticking.
export function setupReception(player) {
  const dimension = player.dimension;
  const existing = dimension.getEntities({ type: "minecraft:npc", tags: [TEMPORARY_TAG],
    location: player.location, maxDistance: 8 });
  if (existing.length) return "8ブロック以内に仮受付があります。NPCを操作してください。";
  const original = player.location;
  const position = { x: Math.floor(original.x), y: feetY(original.y), z: Math.floor(original.z) };
  const read = (label, y) => {
    const at = { ...position, y };
    let block, error;
    try { block = dimension.getBlock(at); }
    catch (e) { error = e.name ?? "取得エラー"; }
    return { block, description: `${label}=(${at.x},${at.y},${at.z}) ${error ?? block?.typeId ?? "未取得"}` };
  };
  const feet = read("足元", position.y);
  const head = read("頭上", position.y + 1);
  const floor = read("床", position.y - 1);
  const failures = [];
  if (!isAir(feet.block)) failures.push("足元が空気ではない/未取得");
  if (!isAir(head.block)) failures.push("頭上が空気ではない/未取得");
  if (!FLOORS.has(floor.block?.typeId)) failures.push("床が未取得または仮受付に未対応（草・土・石の床で実行）");
  if (failures.length) {
    throw new Error(`[受付${SETUP_VERSION}] ${failures.join("、")}\nプレイヤーY=${original.y}\n${feet.description}\n${head.description}\n${floor.description}`);
  }
  const location = { x: position.x + 0.5, y: position.y, z: position.z + 0.5 };
  const npc = dimension.spawnEntity("minecraft:npc", location);
  npc.addTag(RECEPTION_TAG);
  npc.addTag(TEMPORARY_TAG);
  npc.nameTag = "ウラギリスズメ受付（操作で参加・再戦）";
  return `[受付${SETUP_VERSION}] 仮受付を設置しました。一歩離れてNPCを操作してください。2人とも通常権限で参加できます。`;
}
