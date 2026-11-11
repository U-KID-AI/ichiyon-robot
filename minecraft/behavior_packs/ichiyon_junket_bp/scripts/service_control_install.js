import { serviceControlBinding } from "./service_controls.js";

const at = values => ({ x: values[0], y: values[1], z: values[2] });
const contains = (venue, values) => values?.length === 3 && values.every((value, axis) =>
  Number.isInteger(value) && value >= venue.origin[axis] && value < venue.origin[axis] + venue.size[axis]);
const key = values => values.join(",");
const retirementKey = venue => `junket:panel_retirement_v1:${venue.id}`;
const protectedLocations = control => [control.button, control.sign, control.support, control.sign_support].filter(Boolean);
const original = (control, role) => {
  const value = control.source_evidence?.[`${role}_original_block`] ?? control.source_evidence?.[`${role}_original`];
  return typeof value === "string" ? { name: value } : value;
};
function matches(block, expected, pressed = false) {
  if (!block || !expected || block.typeId !== expected.name || !expected.states) return false;
  const actual = { ...block.permutation.getAllStates() };
  // BDS persists light_block_N with empty states, but Script API exposes its
  // intrinsic numeric level. Normalize only that exact, type-derived value;
  // different levels and every other extra state remain identity mismatches.
  const light = /^minecraft:light_block_([0-9]|1[0-5])$/.exec(expected.name);
  if (light && !Object.prototype.hasOwnProperty.call(expected.states, "block_light_level") &&
      actual.block_light_level === Number(light[1])) delete actual.block_light_level;
  if (Object.keys(actual).length !== Object.keys(expected.states).length) return false;
  return Object.entries(expected.states).every(([name, value]) => {
    if (pressed && name === "button_pressed_bit") return [false, true, 0, 1].includes(actual[name]);
    return actual[name] === value || (typeof actual[name] === "boolean" && Number(actual[name]) === value);
  });
}
const retirementIdentity = control => JSON.stringify([control.button, control.sign,
  control.button_block, control.sign_block, control.text, original(control, "button"), original(control, "sign")]);
function retirementJournal(world, venue) {
  try { return JSON.parse(world.getDynamicProperty?.(retirementKey(venue)) ?? "{}"); }
  catch { return {}; }
}
function writeBlock(dimension, location, expected) {
  let block = dimension.getBlock(at(location));
  if (block.typeId !== expected.name) block.setType(expected.name);
  // Native Block objects/permutations can retain their pre-setType snapshot.
  // Resolve the current block again before applying original block states.
  block = dimension.getBlock(at(location));
  if (!Object.keys(expected.states ?? {}).length) return;
  let permutation = block.permutation;
  for (const [name, value] of Object.entries(expected.states ?? {})) permutation = permutation.withState(name, value);
  block.setPermutation(permutation);
}

export function inspectLegacyServiceControls(world, venue) {
  const dimension = world.getDimension("minecraft:overworld");
  const protectedCells = new Set((venue.service_controls ?? []).flatMap(control => protectedLocations(control).map(key)));
  const journal = retirementJournal(world, venue);
  return (venue.legacy_service_controls ?? []).map(control => {
    const entry = { action: control.action, status: "retained", reason: "identity-mismatch" };
    if (![control.button, control.sign, control.support].every(value => contains(venue, value)) ||
        control.source_evidence?.support_preserved !== true) return { ...entry, reason: "invalid-source-evidence" };
    const roles = ["button", "sign"].filter(role => !protectedCells.has(key(control[role])));
    if (!roles.length) return { ...entry, status: "protected", reason: "new-panel" };
    if (roles.some(role => !original(control, role)?.states)) return { ...entry, reason: "missing-original-states" };
    const blocks = Object.fromEntries(["button", "sign"].map(role => [role, dimension.getBlock(at(control[role]))]));
    if (roles.every(role => matches(blocks[role], original(control, role)))) return { ...entry, status: "restored", reason: null };
    // A partial/mismatched pair is retained. Coordinate alone is not ownership.
    const continuation = journal[control.action] === retirementIdentity(control);
    const owned = roles.every(role => matches(blocks[role], control[`${role}_block`], role === "button") ||
      (continuation && matches(blocks[role], original(control, role))));
    const signProtected = protectedCells.has(key(control.sign));
    const text = blocks.sign?.getComponent("minecraft:sign")?.getText?.();
    const originalSign = continuation && matches(blocks.sign, original(control, "sign"));
    if (!owned || (!signProtected && !originalSign && text !== control.text)) return entry;
    return { ...entry, status: "legacy", reason: null };
  });
}

async function retireLegacyControls(world, system, venue) {
  const dimension = world.getDimension("minecraft:overworld");
  const protectedCells = new Set((venue.service_controls ?? []).flatMap(control => protectedLocations(control).map(key)));
  const inspected = inspectLegacyServiceControls(world, venue);
  const journal = retirementJournal(world, venue);
  for (const [index, result] of inspected.entries()) {
    if (result.status === "retained") {
      console.warn(`[JUNKET ORIGINAL] legacy service retained: ${JSON.stringify({ id: venue.id, ...result })}`);
      continue;
    }
    if (result.status !== "legacy") continue;
    const control = venue.legacy_service_controls[index];
    // Record only a pair whose exact legacy identity was already proven.
    // If shutdown interrupts its two writes, resume only this unchanged pair.
    journal[control.action] = retirementIdentity(control);
    world.setDynamicProperty?.(retirementKey(venue), JSON.stringify(journal));
    for (const role of ["button", "sign"]) {
      if (protectedCells.has(key(control[role]))) continue;
      const expected = original(control, role);
      writeBlock(dimension, control[role], expected);
      let block, attempt = 0;
      for (; attempt < 5; attempt++) {
        block = dimension.getBlock(at(control[role]));
        if (matches(block, expected)) break;
        if (attempt < 4) await new Promise(resolve => system.runTimeout(resolve, 1));
      }
      if (attempt) console.warn(`[JUNKET PANEL] restoration readback: ${JSON.stringify({ id: venue.id,
        action: control.action, role, expected, actual: block ? { name: block.typeId, states: block.permutation.getAllStates() } : null,
        waited_ticks: Math.min(attempt, 4), matched: matches(block, expected) })}`);
      if (!matches(block, expected)) throw new Error(`${venue.id}: 旧${control.action}/${role}の原作復元に失敗しました`);
    }
  }
  const result = inspectLegacyServiceControls(world, venue);
  for (const record of result) if (["restored", "protected"].includes(record.status)) delete journal[record.action];
  world.setDynamicProperty?.(retirementKey(venue), Object.keys(journal).length ? JSON.stringify(journal) : undefined);
  return result;
}

export async function ensureServiceControls(world, system, venue) {
  const dimension = world.getDimension("minecraft:overworld");
  const controls = venue.service_controls ?? [];
  // Preflight every cell before writes. Keep the original floor/support and
  // refuse unrelated additions; full source recovery handles those explicitly.
  for (const control of controls) {
    if (!protectedLocations(control).every(value => contains(venue, value)) ||
        control.source_evidence?.support_preserved !== true) throw new Error(`${venue.id}: サービス設備の範囲証拠が不正です`);
    for (const role of ["support", ...(control.sign_support ? ["sign_support"] : [])]) {
      const support = dimension.getBlock(at(control[role]));
      if (support?.typeId !== control[`${role}_block`].name) throw new Error(`${venue.id}/${control.action}: 原作の支持壁・床が変更されています`);
    }
    for (const role of ["button", "sign"]) {
      const block = dimension.getBlock(at(control[role]));
      if (!block) throw new Error(`${venue.id}/${control.action}: 設備セルが未読込です`);
      const allowed = [control[`${role}_block`].name, original(control, role)?.name, "minecraft:air"];
      if (!allowed.includes(block.typeId)) throw new Error(`${venue.id}/${control.action}: 設備セルに未知のブロックがあります (${block.typeId})`);
    }
  }
  for (const control of controls) {
    for (const role of ["button", "sign"]) {
      const block = dimension.getBlock(at(control[role])), expected = control[`${role}_block`];
      if (block.typeId !== expected.name) block.setType(expected.name);
      let permutation = block.permutation;
      for (const [name, value] of Object.entries(expected.states ?? {})) {
        if (name === "button_pressed_bit") continue;
        permutation = permutation.withState(name, value);
      }
      if (permutation) block.setPermutation(permutation);
    }
  }
  for (const control of controls) {
    let sign;
    for (let retry = 0; retry < 10; retry++) {
      sign = dimension.getBlock(at(control.sign))?.getComponent("minecraft:sign");
      if (sign?.setText) break;
      await new Promise(resolve => system.runTimeout(resolve, 1));
    }
    if (!sign?.setText) throw new Error(`${venue.id}/${control.action}: 看板componentを取得できません`);
    sign.setText(control.text);
    sign.setWaxed?.(true);
    if (sign.getText() !== control.text) throw new Error(`${venue.id}/${control.action}: 看板の文字を確認できません`);
  }
  let legacy = [];
  if (venue.legacy_service_controls?.length) {
    if (controls.length !== 1 || !serviceControlBinding(world, venue, controls[0])) {
      throw new Error(`${venue.id}: 新ゲーム操作パネルの本人向け処理を確認できません（旧設備を保持）`);
    }
    legacy = await retireLegacyControls(world, system, venue);
  }
  const result = { id: venue.id, controls: controls.length, originalSupportsPreserved: true, legacy };
  if (venue.legacy_service_controls?.length && controls.length === 1 && controls[0].action === "menu") {
    result.handler_bound = serviceControlBinding(world, venue, controls[0]);
    console.log(`[JUNKET PANEL] initialized: ${JSON.stringify(result)}`);
  }
  return result;
}
