export const MOLCAR_TYPES = ["ichiyon:molcar", "ichiyon:molcar2", "ichiyon:molcar3"];
export const BASE_SPEED = 0.4;
export const BOOST_SPEED = 0.65;
export const BOOST_TICKS = 100;
export const BOOST_MARKER = "molcar:carrot_boost";
const TYPES = new Set(MOLCAR_TYPES);
const CARROT = "minecraft:carrot";
const MOVEMENT_EPSILON = 0.000001;
const alive = e => e?.isValid && e.getComponent("minecraft:health")?.currentValue > 0;

export function createMolcarBoost({ world, system, report = console.warn }) {
  const active = new Map();
  const pending = new Set();
  const lastUse = new Map();
  let lastError = -200;
  function error(e) {
    if (system.currentTick - lastError < 200) return;
    lastError = system.currentTick;
    report(`[MolcarBoost] ${String(e).slice(0, 300)}`);
  }
  function mountOf(player) {
    const mount = player.getComponent("minecraft:riding")?.entityRidingOn;
    return mount && TYPES.has(mount.typeId) ? mount : undefined;
  }
  function setSpeed(entity, speed) {
    const movement = entity.getComponent("minecraft:movement");
    // BDS exposes float32 attributes (0.65 is returned as 0.6499999761581421).
    if (!movement || movement.effectiveMax + MOVEMENT_EPSILON < speed
        || !movement.setCurrentValue(Math.min(speed, movement.effectiveMax))) {
      throw Error(`movement attribute cannot accept ${speed} for ${entity.typeId}`);
    }
  }
  function stop(state) {
    if (state.entity.isValid) {
      setSpeed(state.entity, BASE_SPEED);
      state.entity.setDynamicProperty(BOOST_MARKER, undefined);
    }
    active.delete(state.entity.id);
  }
  function recover(entity) {
    if (!entity?.isValid || !TYPES.has(entity.typeId)) return;
    // Attribute changes can survive a save even if the JS timer cannot.
    setSpeed(entity, BASE_SPEED);
    entity.setDynamicProperty(BOOST_MARKER, undefined);
    active.delete(entity.id);
  }
  function activate(player, mountId, slot) {
    if (!alive(player) || player.selectedSlotIndex !== slot || lastUse.get(player.id) === system.currentTick) return false;
    const entity = mountOf(player);
    if (!alive(entity) || entity.id !== mountId || entity.dimension.id !== player.dimension.id) return false;
    const container = player.getComponent("minecraft:inventory")?.container;
    const item = container?.getItem(slot);
    if (item?.typeId !== CARROT || item.amount < 1) return false;
    const previous = active.get(entity.id);
    // Persist recovery intent before changing movement; commit the timer only after consumption.
    entity.setDynamicProperty(BOOST_MARKER, true);
    try {
      setSpeed(entity, BOOST_SPEED);
      if (item.amount === 1) container.setItem(slot, undefined);
      else { item.amount--; container.setItem(slot, item); }
    } catch (e) {
      setSpeed(entity, previous ? BOOST_SPEED : BASE_SPEED);
      if (!previous) entity.setDynamicProperty(BOOST_MARKER, undefined);
      throw e;
    }
    active.set(entity.id, { entity, player, until: system.currentTick + BOOST_TICKS });
    lastUse.set(player.id, system.currentTick);
    try { entity.dimension.playSound("ichiyon:molcar.pui", entity.location, { volume: 1 }); }
    catch (e) { error(e); }
    return true;
  }
  function request(event, player, target) {
    if (event.cancel || event.itemStack?.typeId !== CARROT || !alive(player)) return;
    const mount = mountOf(player);
    if (!alive(mount) || (target && target.id !== mount.id)) return;
    event.cancel = true;
    if (pending.has(player.id)) return;
    const slot = player.selectedSlotIndex, mountId = mount.id;
    pending.add(player.id);
    system.run(() => {
      try { activate(player, mountId, slot); }
      catch (e) { error(e); }
      finally { pending.delete(player.id); }
    });
  }
  function use(event) { request(event, event.source); }
  function interact(event) { request(event, event.player, event.target); }
  function releasePlayer(id) {
    for (const state of active.values()) {
      if (state.player.id === id) {
        try { stop(state); } catch (e) { error(e); }
      }
    }
  }
  function died(entity) {
    const state = active.get(entity.id);
    if (state) stop(state);
    else releasePlayer(entity.id);
  }
  function tick() {
    for (const state of active.values()) {
      try {
        if (system.currentTick >= state.until || !alive(state.entity) || !alive(state.player)
            || mountOf(state.player)?.id !== state.entity.id
            || state.player.dimension.id !== state.entity.dimension.id) stop(state);
      } catch (e) { error(e); }
    }
    for (const [id, tick] of lastUse) if (tick < system.currentTick) lastUse.delete(id);
  }
  function scan() {
    for (const id of ["overworld", "nether", "the_end"]) {
      try {
        const dimension = world.getDimension(id);
        for (const type of MOLCAR_TYPES) for (const entity of dimension.getEntities({ type })) {
          if (!active.has(entity.id)) {
            try {
              if (entity.getDynamicProperty(BOOST_MARKER) !== undefined
                  || entity.getComponent("minecraft:movement")?.currentValue > BASE_SPEED + MOVEMENT_EPSILON) recover(entity);
            } catch (e) { error(e); }
          }
        }
      } catch (e) { error(e); }
    }
  }
  return { use, interact, activate, recover, releasePlayer, died, tick, scan, active };
}
