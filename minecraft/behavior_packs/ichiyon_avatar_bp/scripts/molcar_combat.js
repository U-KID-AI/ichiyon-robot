import { world, system } from "@minecraft/server";

const MOLCAR_TYPES = [
  "ichiyon:molcar",
  "ichiyon:molcar2",
  "ichiyon:molcar3"
];

const MOLCAR_SET = new Set(MOLCAR_TYPES);

const MIN_RAM_SPEED = 0.12;
const HIT_RADIUS = 1.8;
const HIT_COOLDOWN = 12;

const ZOMBIE_SEARCH_RADIUS = 32;
const ZOMBIE_ACCEL = 0.065;
const ZOMBIE_MAX_SPEED = 0.50;

let tick = 0;

const cooldowns = new Map();
const protectedRiders = new Map();

const molcarSoundStates = new Map();
const molcarSoundCooldowns = new Map();
const molcarDeathSoundTicks = new Map();

const RUN_SOUND_SPEED = 0.08;

function randomPuiDelay() {
  // 約15～45秒
  return 300 + Math.floor(Math.random() * 601);
}

function playMolcarSound(
  molcar,
  eventName,
  {
    volume = 1.0,
    pitch = 1.0,
    minGap = 6,
    force = false
  } = {}
) {
  try {
    if (!molcar || !MOLCAR_SET.has(molcar.typeId)) {
      return false;
    }

    const until =
      molcarSoundCooldowns.get(molcar.id) ?? 0;

    if (!force && tick < until) {
      return false;
    }

    molcar.dimension.playSound(
      `ichiyon:molcar.${eventName}`,
      molcar.location,
      {
        volume,
        pitch
      }
    );

    molcarSoundCooldowns.set(
      molcar.id,
      tick + minGap
    );

    return true;
  } catch {
    return false;
  }
}

function updateMolcarSoundState(molcar) {
  try {
    const riders =
      getRiders(molcar);

    const riderIds =
      riders
        .map(rider => rider.id)
        .sort();

    const velocity =
      molcar.getVelocity();

    const moving =
      speedXZ(velocity) >= RUN_SOUND_SPEED;

    let state =
      molcarSoundStates.get(molcar.id);

    // Scriptロード時に既に乗車している場合などは
    // 初回だけ状態を記録し、偽の乗車音を出さない。
    if (!state) {
      molcarSoundStates.set(
        molcar.id,
        {
          riderIds,
          moving,
          nextPui:
            tick + randomPuiDelay(),
          lastSeen: tick
        }
      );

      return;
    }

    const previousRiders =
      new Set(state.riderIds);

    const currentRiders =
      new Set(riderIds);

    const mounted =
      riderIds.some(
        id => !previousRiders.has(id)
      );

    const dismounted =
      state.riderIds.some(
        id => !currentRiders.has(id)
      );

    if (mounted) {
      playMolcarSound(
        molcar,
        "mount",
        {
          minGap: 8
        }
      );
    }

    if (dismounted) {
      playMolcarSound(
        molcar,
        "dismount",
        {
          minGap: 8
        }
      );
    }

    if (
      moving &&
      !state.moving
    ) {
      playMolcarSound(
        molcar,
        "run",
        {
          minGap: 8
        }
      );
    }

    if (
      tick >= state.nextPui
    ) {
      if (
        playMolcarSound(
          molcar,
          "pui",
          {
            minGap: 8
          }
        )
      ) {
        state.nextPui =
          tick + randomPuiDelay();
      } else {
        // 他の音と被った場合は1秒後くらいに再試行
        state.nextPui =
          tick + 20;
      }
    }

    state.riderIds =
      riderIds;

    state.moving =
      moving;

    state.lastSeen =
      tick;
  } catch {}
}

function speedXZ(v) {
  return Math.hypot(v.x, v.z);
}

function getRiders(molcar) {
  try {
    return molcar.getComponent("minecraft:rideable")?.getRiders() ?? [];
  } catch {
    return [];
  }
}

function isPlayerOnMolcar(player) {
  try {
    const riding =
      player.getComponent("minecraft:riding");

    const mount =
      riding?.entityRidingOn;

    return (
      !!mount &&
      MOLCAR_SET.has(mount.typeId)
    );
  } catch {
    return false;
  }
}

function protectRiders(riders) {
  for (const rider of riders) {
    if (rider.typeId !== "minecraft:player") continue;

    // 着地前後のride状態の揺れも吸収する短い猶予
    protectedRiders.set(rider.id, tick + 40);
  }
}

function nearestPlayer(molcar) {
  let best;
  let bestDist2 = ZOMBIE_SEARCH_RADIUS * ZOMBIE_SEARCH_RADIUS;

  for (const player of world.getAllPlayers()) {
    try {
      if (player.dimension.id !== molcar.dimension.id) continue;

      const dx = player.location.x - molcar.location.x;
      const dz = player.location.z - molcar.location.z;
      const d2 = dx * dx + dz * dz;

      if (d2 < bestDist2) {
        bestDist2 = d2;
        best = player;
      }
    } catch {}
  }

  return best;
}

function driveZombieMolcar(molcar) {
  const target = nearestPlayer(molcar);
  if (!target) return;

  const dx = target.location.x - molcar.location.x;
  const dz = target.location.z - molcar.location.z;
  const distance = Math.hypot(dx, dz);

  if (distance < 0.25) return;

  const dirX = dx / distance;
  const dirZ = dz / distance;

  try {
    if (speedXZ(molcar.getVelocity()) < ZOMBIE_MAX_SPEED) {
      molcar.applyImpulse({
        x: dirX * ZOMBIE_ACCEL,
        y: 0,
        z: dirZ * ZOMBIE_ACCEL
      });
    }
  } catch {}

  try {
    molcar.setRotation({
      x: 0,
      y: Math.atan2(-dirX, dirZ) * 180 / Math.PI
    });
  } catch {}
}

function canHit(molcar, target, riders) {
  try {
    if (target.id === molcar.id) return false;

    for (const rider of riders) {
      if (target.id === rider.id) return false;
    }

    return target.getComponent("minecraft:health") !== undefined;
  } catch {
    return false;
  }
}

function ram(molcar, target, speed, dirX, dirZ) {
  const key = `${molcar.id}:${target.id}`;

  if (tick < (cooldowns.get(key) ?? 0)) return;

  const damage = Math.min(
    40,
    Math.max(
      4,
      Math.round(4 + (speed - MIN_RAM_SPEED) * 80)
    )
  );

  try {
    target.applyDamage(damage);
  } catch {
    return;
  }

  const force = Math.min(2.5, 0.6 + speed * 4);

  try {
    target.applyKnockback(
      {
        x: dirX * force,
        z: dirZ * force
      },
      0.30
    );
  } catch {
    try {
      target.applyImpulse({
        x: dirX * force * 0.15,
        y: 0.12,
        z: dirZ * force * 0.15
      });
    } catch {}
  }

  playMolcarSound(
    molcar,
    "hit",
    {
      volume: 1.0,
      pitch: 1.0,
      minGap: 6
    }
  );

  cooldowns.set(key, tick + HIT_COOLDOWN);
}

function ramMolcar(
  molcar,
  target,
  speed,
  dirX,
  dirZ
) {
  const key =
    `molcarCollision:${molcar.id}:${target.id}`;

  if (
    tick <
    (cooldowns.get(key) ?? 0)
  ) {
    return;
  }

  const reverseKey =
    `molcarCollision:${target.id}:${molcar.id}`;

  const riders =
    getRiders(target);

  // モルカー同士の衝突音。
  // target側で鳴らすことで、直後の強制下車音も
  // cooldownによって二重再生されにくくする。
  playMolcarSound(
    target,
    "crash",
    {
      volume: 1.0,
      pitch: 0.95,
      minGap: 10,
      force: true
    }
  );

  try {
    target
      .getComponent("minecraft:rideable")
      ?.ejectRiders();
  } catch {}

  const force = Math.min(
    5.0,
    2.0 + speed * 8
  );

  try {
    target.applyKnockback(
      {
        x: dirX * force,
        z: dirZ * force
      },
      0.9
    );
  } catch {
    try {
      target.applyImpulse({
        x: dirX * force * 0.20,
        y: 0.45,
        z: dirZ * force * 0.20
      });
    } catch {}
  }

  // 降ろされた乗員も少し吹き飛ばす
  for (const rider of riders) {
    try {
      rider.applyKnockback(
        {
          x: dirX * force * 0.55,
          z: dirZ * force * 0.55
        },
        0.55
      );
    } catch {
      try {
        rider.applyImpulse({
          x: dirX * force * 0.10,
          y: 0.25,
          z: dirZ * force * 0.10
        });
      } catch {}
    }
  }

  // 当てた側にも少しだけ反動
  try {
    molcar.applyImpulse({
      x: -dirX * 0.10,
      y: 0.04,
      z: -dirZ * 0.10
    });
  } catch {}

  cooldowns.set(
    key,
    tick + HIT_COOLDOWN
  );

  cooldowns.set(
    reverseKey,
    tick + HIT_COOLDOWN
  );
}

function processMolcar(dimension, molcar) {
  const riders = getRiders(molcar);

  if (!riders.length) return;

  protectRiders(riders);

  if (riders.some(r => r.typeId === "minecraft:zombie")) {
    driveZombieMolcar(molcar);

    for (const rider of riders) {
      if (rider.typeId !== "minecraft:zombie") continue;

      try {
        rider.addEffect(
          "fire_resistance",
          40,
          {
            amplifier: 0,
            showParticles: false
          }
        );
      } catch {}
    }
  }

  let velocity;

  try {
    velocity = molcar.getVelocity();
  } catch {
    return;
  }

  const speed = speedXZ(velocity);

  if (speed < MIN_RAM_SPEED) return;

  const dirX = velocity.x / speed;
  const dirZ = velocity.z / speed;

  let nearby;

  try {
    nearby = dimension.getEntities({
      location: molcar.location,
      maxDistance: HIT_RADIUS
    });
  } catch {
    return;
  }

  for (const target of nearby) {
    if (target.id === molcar.id) {
      continue;
    }

    const dx =
      target.location.x -
      molcar.location.x;

    const dz =
      target.location.z -
      molcar.location.z;

    const distance =
      Math.hypot(dx, dz);

    if (distance > 0.01) {
      const forward =
        (
          dx * dirX +
          dz * dirZ
        ) /
        distance;

      if (forward < 0.15) {
        continue;
      }
    }

    if (MOLCAR_SET.has(target.typeId)) {
      ramMolcar(
        molcar,
        target,
        speed,
        dirX,
        dirZ
      );

      continue;
    }

    if (
      !canHit(
        molcar,
        target,
        riders
      )
    ) {
      continue;
    }

    ram(
      molcar,
      target,
      speed,
      dirX,
      dirZ
    );
  }
}

system.runInterval(() => {
  tick += 2;

  for (const dimensionName of ["overworld", "nether", "the_end"]) {
    let dimension;

    try {
      dimension = world.getDimension(dimensionName);
    } catch {
      continue;
    }

    for (const typeId of MOLCAR_TYPES) {
      let molcars;

      try {
        molcars = dimension.getEntities({ type: typeId });
      } catch {
        continue;
      }

      for (const molcar of molcars) {
        updateMolcarSoundState(molcar);
        processMolcar(dimension, molcar);
      }
    }
  }

  if (tick % 200 === 0) {
    for (const [key, expiry] of cooldowns) {
      if (expiry <= tick) cooldowns.delete(key);
    }

    for (const [id, expiry] of protectedRiders) {
      if (expiry <= tick) protectedRiders.delete(id);
    }

    for (const [id, state] of molcarSoundStates) {
      if ((state.lastSeen ?? 0) + 400 < tick) {
        molcarSoundStates.delete(id);
      }
    }

    for (const [id, expiry] of molcarSoundCooldowns) {
      if (expiry <= tick) {
        molcarSoundCooldowns.delete(id);
      }
    }

    for (const [id, expiry] of molcarDeathSoundTicks) {
      if (expiry <= tick) {
        molcarDeathSoundTicks.delete(id);
      }
    }
  }
}, 2);


// ---------------------------------------------------------
// モルカー サウンドイベント
// ---------------------------------------------------------

try {
  const signal =
    world.afterEvents?.entitySpawn;

  if (
    signal &&
    typeof signal.subscribe === "function"
  ) {
    signal.subscribe(event => {
      try {
        const entity =
          event.entity;

        if (
          !entity ||
          !MOLCAR_SET.has(entity.typeId)
        ) {
          return;
        }

        playMolcarSound(
          entity,
          "spawn",
          {
            volume: 1.0,
            pitch: 1.0,
            minGap: 10,
            force: true
          }
        );
      } catch {}
    });

    console.warn(
      "[MolcarCombat] spawn sound hook loaded"
    );
  }
} catch (error) {
  console.warn(
    `[MolcarCombat] spawn sound hook error: ${error}`
  );
}


try {
  const signal =
    world.afterEvents?.entityHurt;

  if (
    signal &&
    typeof signal.subscribe === "function"
  ) {
    signal.subscribe(event => {
      try {
        const entity =
          event.hurtEntity;

        if (
          !entity ||
          !MOLCAR_SET.has(entity.typeId)
        ) {
          return;
        }

        // 落下は無効化対象なので鳴らさない
        if (
          event.damageSource?.cause === "fall"
        ) {
          return;
        }

        const health =
          entity.getComponent("minecraft:health");

        if (
          health &&
          health.currentValue <= 0
        ) {
          playMolcarSound(
            entity,
            "death",
            {
              volume: 1.0,
              pitch: 0.9,
              minGap: 12,
              force: true
            }
          );

          molcarDeathSoundTicks.set(
            entity.id,
            tick + 20
          );

          return;
        }

        playMolcarSound(
          entity,
          "hurt",
          {
            volume: 1.0,
            pitch: 1.0,
            minGap: 8
          }
        );
      } catch {}
    });

    console.warn(
      "[MolcarCombat] hurt sound hook loaded"
    );
  }
} catch (error) {
  console.warn(
    `[MolcarCombat] hurt sound hook error: ${error}`
  );
}


try {
  const signal =
    world.afterEvents?.entityDie;

  if (
    signal &&
    typeof signal.subscribe === "function"
  ) {
    signal.subscribe(event => {
      try {
        const entity =
          event.deadEntity;

        if (
          !entity ||
          !MOLCAR_SET.has(entity.typeId)
        ) {
          return;
        }

        if (
          tick <
          (molcarDeathSoundTicks.get(entity.id) ?? 0)
        ) {
          return;
        }

        playMolcarSound(
          entity,
          "death",
          {
            volume: 1.0,
            pitch: 0.9,
            minGap: 12,
            force: true
          }
        );

        molcarDeathSoundTicks.set(
          entity.id,
          tick + 20
        );
      } catch {}
    });

    console.warn(
      "[MolcarCombat] death sound hook loaded"
    );
  }
} catch (error) {
  console.warn(
    `[MolcarCombat] death sound hook error: ${error}`
  );
}


// ---------------------------------------------------------
// モルカー搭乗者だけ落下ダメージを無効化
// ---------------------------------------------------------

let beforeHookLoaded = false;

try {
  const signal = world.beforeEvents?.entityHurt;

  if (signal && typeof signal.subscribe === "function") {
    signal.subscribe(event => {
      try {
        if (event.damageSource?.cause !== "fall") return;

        const entity = event.hurtEntity;

        // モルカー本体の落下ダメージを完全無効化
        if (MOLCAR_SET.has(entity.typeId)) {
          event.cancel = true;
          return;
        }

        // 搭乗者側も必要に応じて保護
        if (entity.typeId !== "minecraft:player") return;

        const ridingNow =
          isPlayerOnMolcar(entity);

        const expiry =
          protectedRiders.get(entity.id) ?? -1;

        if (
          ridingNow ||
          expiry >= tick
        ) {
          event.cancel = true;
        }
      } catch {}
    });

    beforeHookLoaded = true;

    console.warn(
      "[MolcarCombat] rider fall cancel hook loaded"
    );
  }
} catch (error) {
  console.warn(
    `[MolcarCombat] before fall hook error: ${error}`
  );
}


// before-eventを使えないAPI版だった場合のみ
// after-eventで受けたfall分を即時回復する。
if (!beforeHookLoaded) {
  try {
    world.afterEvents.entityHurt.subscribe(event => {
      try {
        if (event.damageSource?.cause !== "fall") return;

        const entity = event.hurtEntity;

        if (entity.typeId !== "minecraft:player") return;

        const ridingNow =
          isPlayerOnMolcar(entity);

        const expiry =
          protectedRiders.get(entity.id) ?? -1;

        if (
          !ridingNow &&
          expiry < tick
        ) {
          return;
        }

        const health =
          entity.getComponent("minecraft:health");

        if (!health) return;

        const restored =
          health.currentValue + event.damage;

        const maximum =
          health.effectiveMax ??
          health.defaultValue ??
          restored;

        health.setCurrentValue(
          Math.min(restored, maximum)
        );
      } catch {}
    });

    console.warn(
      "[MolcarCombat] rider fall heal fallback loaded"
    );
  } catch (error) {
    console.warn(
      `[MolcarCombat] rider fall protection failed: ${error}`
    );
  }
}

console.warn(
  "[MolcarCombat] stable script loaded"
);
