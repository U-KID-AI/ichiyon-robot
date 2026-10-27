import { BANK_HUB } from "./bank_hub_generated.js";
import { WARP_CONFIG } from "./junket_warp_generated.js";
import { getSharedLoader, arrivalBounds, inspectArrival } from "./shared_loader.js";
import { showFormSafely } from "./venue_presentation.js";
import { installFixtureActorTracking, markCreatedFixture, preferredFixture, waitForFixtureActors } from "./fixture_reconciliation.js";

const pos = a => ({ x: a[0], y: a[1], z: a[2] });
const near = (a, b, limit) => Math.hypot(a.x-b.x, a.y-b.y, a.z-b.z) <= limit;
const cell = a => `${Math.floor(a.x)},${Math.floor(a.y)},${Math.floor(a.z)}`;
const MANNEQUIN = "ichiyon:avatar";
const MANNEQUIN_STEPS = [[0, 0, 0], [0, 1, 0], [0, -1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1],
  [2, 0, 0], [-2, 0, 0], [0, 0, 2], [0, 0, -2], [1, 1, 0], [-1, 1, 0], [0, 1, 1], [0, 1, -1]];

export function insideBank(location, bounds = BANK_HUB.bounds) {
  return !!location && location.x >= bounds[0] && location.x <= bounds[3]
    && location.y >= bounds[1] && location.y <= bounds[4]
    && location.z >= bounds[2] && location.z <= bounds[5];
}

const LIGHT_BLOCK = /^minecraft:light_block(?:_\d+)?$/;
function standingRoom(block) {
  return !!block && (block.isAir === true || LIGHT_BLOCK.test(block.typeId ?? ""));
}
function mannequinFits(dimension, at) {
  const feet = dimension.getBlock(at);
  const head = dimension.getBlock({ x: at.x, y: at.y + 1, z: at.z });
  const floor = dimension.getBlock({ x: at.x, y: at.y - 1, z: at.z });
  return standingRoom(feet) && standingRoom(head) && !!floor && !standingRoom(floor) && !floor.isLiquid;
}

// The admin spawn stands two blocks along the look direction. Inside the bank
// that cell is often a counter, and Bedrock then refuses the summon. Keep the
// same command and search only this building for a standing cell.
export function bankMannequinLocation(dimension, location, bounds = BANK_HUB.bounds) {
  if (!insideBank(location, bounds)) return location;
  for (const [dx, dy, dz] of MANNEQUIN_STEPS) {
    const at = { x: location.x + dx, y: location.y + dy, z: location.z + dz };
    if (insideBank(at, bounds) && mannequinFits(dimension, at)) return at;
  }
  return location;
}

function installBankMannequinPlacement(dimension) {
  const proto = dimension?.constructor?.prototype;
  const original = proto?.spawnEntity;
  if (typeof original !== "function" || original.__bankMannequin) return;
  function spawnEntity(identifier, location, options) {
    const typeId = typeof identifier === "string" ? identifier : identifier?.id;
    let at = location;
    if (typeId === MANNEQUIN && location && insideBank(location)) {
      try { at = bankMannequinLocation(this, location); }
      catch { at = location; }
    }
    return original.call(this, identifier, at, options);
  }
  spawnEntity.__bankMannequin = true;
  proto.spawnEntity = spawnEntity;
}
export const bankCatalog = BANK_HUB.enabled ? {
  ...WARP_CONFIG, WARP_RETURN_POINT: BANK_HUB.arrival, WARP_RETURN_TITLE: "カラス銀行",
} : WARP_CONFIG;

export function createBankHub({ world, system, ActionFormData, manager, warp }) {
  if (!BANK_HUB.enabled) return null;
  installFixtureActorTracking(world);
  const loader = getSharedLoader(world, system), tickets = new Map(), cooldown = new Map();
  const dimension = () => world.getDimension("overworld");
  try { installBankMannequinPlacement(dimension()); } catch { /* Placement still uses the original summon. */ }
  const tell = (p, text) => { try { p.sendMessage(`[カラス銀行] ${text}`); } catch {} };
  const online = id => world.getAllPlayers().find(p => p.id === id);
  const fixtures = BANK_HUB.fixtures;
  let ensuring = null;
  function readFixtures() { return dimension().getEntities({ tags: ["junket:bank_fixture"] }); }
  function ensure() {
    if (ensuring) return ensuring;
    ensuring = loader.withArea({ dimension: dimension(), bounds: BANK_HUB.bounds }, async ctx => {
      for (let n=0; n<80; n++) {
        if (inspectArrival(dimension(), pos(BANK_HUB.arrival.location)).loaded) break;
        await ctx.wait(5);
      }
      const safety = inspectArrival(dimension(), pos(BANK_HUB.arrival.location));
      if (!safety.safe) throw new Error(`銀行の到着床を確認できません: ${safety.message}`);
      for (const c of BANK_HUB.controls) {
        const b = dimension().getBlock(pos(c.location));
        if (b?.typeId !== c.type) throw new Error(`銀行の操作パネルがありません: ${c.location}`);
      }
      await waitForFixtureActors({ read: () => readFixtures().map(e=>e.id),
        wait: ctx.wait, guard: ctx.guard, expectedCount: fixtures.length });
      let added=0, duplicates=0, anchored=0;
      for (const f of fixtures) {
        const tag=`junket:bank:${f.id}`, at=pos(f.location);
        const owned=readFixtures().filter(e=>e.hasTag(tag));
        let e=preferredFixture(owned,f.type,x=>near(x.location,at,.02));
        for (const other of owned) if (other!==e && other.typeId!==MANNEQUIN) { other.remove(); duplicates++; }
        if (!e) { e=markCreatedFixture(dimension().spawnEntity(f.type,at)); e.addTag("junket:bank_fixture");e.addTag(tag);added++; }
        e.nameTag=f.name ?? "";
        const r=e.getRotation();
        const yawError=Math.abs(((r.y-f.rotation)%360+540)%360-180);
        if (!near(e.location,at,.02) || yawError>0.1) {
          e.clearVelocity();e.teleport(at,{rotation:{x:0,y:f.rotation}});anchored++;
        }
        e.clearVelocity();
      }
      const result={ok:true,fixtures:readFixtures().length,expected:fixtures.length,added,duplicates,anchored};
      if (result.fixtures!==result.expected) throw new Error("銀行装飾の個数が不一致です。");
      console.log(`[JUNKET_BANK_INIT] ${JSON.stringify(result)}`);return result;
    }).finally(()=>{ensuring=null;});
    return ensuring;
  }
  async function open(player, legacyGame=null) {
    const now=system.currentTick??0;
    if (tickets.has(player.id) || (cooldown.get(player.id)??-100)>now) return false;
    if (manager.forPlayer(player.id)) { tell(player,"試合を退出してから移動してください。");return false; }
    const ticket={origin:{...player.location},dimension:player.dimension.id,opened:now};tickets.set(player.id,ticket);
    cooldown.set(player.id,now+8);
    const currentPlayer=()=>{
      const current=online(player.id);
      if (tickets.get(player.id)!==ticket || !current) return null;
      if ((system.currentTick??now)-ticket.opened>2400) {
        tell(current,"操作の有効時間を過ぎました。もう一度操作してください。");return null;
      }
      if (current.dimension.id!==ticket.dimension || !near(current.location,ticket.origin,6) || manager.forPlayer(player.id)) {
        tell(current,"場所または参加状況が変わりました。もう一度操作してください。");return null;
      }
      return current;
    };
    try {
      const games=warp.controls.filter(g=>g.enabled);
      const form=new ActionFormData().title(legacyGame?"JUNKET｜新しいカタログ拠点":"カラス銀行｜17ゲーム選択")
        .body(legacyGame?"正式なカタログ拠点をカラス銀行へ移しました。元のゲームへ直接移動することもできます。":"遊ぶゲームを選択してください。移動先を読み込んでから、操作した本人だけを会場受付へ移動します。");
      if (legacyGame) form.button("カラス銀行へ移動").button(`${legacyGame.title}へ直接移動`);
      else for (const g of games) form.button(g.title);
      form.button("閉じる");
      const result=await showFormSafely(form,player);
      if (result.canceled) return false;
      const current=currentPlayer();if (!current) return false;
      if (!Number.isInteger(result.selection)) return false;
      if (legacyGame && result.selection===0) {
        await ensure();
        // Loading/reconciliation may queue behind another venue. Never turn a
        // pre-load answer into a fresh transfer after the player moves/rejoins.
        const ready=currentPlayer();
        return ready ? await warp.returnToEntryAndWait(ready) : false;
      }
      const g=legacyGame ? (result.selection===1?legacyGame:null) : games[result.selection];
      return g ? await warp.warp(current,g.id) : false;
    } catch(e) {
      tell(online(player.id),`移動できませんでした。${e?.message??String(e)}`);return false;
    } finally { if (tickets.get(player.id)===ticket) tickets.delete(player.id); }
  }
  async function check(requestId) {
    const report={request_id:requestId,ok:false,player_test:false,issues:[]};
    try {
      report.fixtures=await ensure();
      report.outbound=await warp.checkDestinations(requestId);
      report.warps=report.outbound.checks.map(c=>c.id);
      report.return=await loader.withArea({dimension:dimension(),bounds:arrivalBounds(pos(BANK_HUB.arrival.location))},async ctx=>{
        const at=pos(BANK_HUB.arrival.location);let inspection;
        for(let i=0;i<80;i++){inspection=inspectArrival(dimension(),at);if(inspection.loaded)break;await ctx.wait(5);}
        if(!inspection.safe)throw new Error(inspection.message);
        const probe=dimension().spawnEntity("minecraft:armor_stand",{...at,y:at.y+1});
        try{probe.teleport(at,{checkForBlocks:true});return {safe:inspection.safe,teleported:near(probe.location,at,.75),location:probe.location,from_all_games_same_route:true};}
        finally{probe.remove();}
      });
      report.repeated=await ensure();
      report.ok=report.outbound.ok && report.return.teleported && report.repeated.added===0 && report.repeated.duplicates===0;
    }catch(e){report.issues.push(e?.message??String(e));}
    console.log(`[JUNKET_BANK_CHECK] ${JSON.stringify(report)}`);return report;
  }
  world.beforeEvents.playerInteractWithBlock.subscribe(event=>{
    if(event.block.dimension.id!=="minecraft:overworld")return;
    const c=BANK_HUB.controls.find(c=>cell(event.block.location)===c.location.join(",") && event.block.typeId===c.type);
    if(!c)return;event.cancel=true;const id=event.player.id;
    system.run(()=>{const p=online(id);if(p)void open(p).catch(e=>tell(p,e.message));});
  });
  world.beforeEvents.playerInteractWithEntity.subscribe(event=>{
    if(!event.target.hasTag("junket:bank_fixture"))return;
    event.cancel=true;const id=event.player.id;
    system.run(()=>{const p=online(id);if(p)void open(p).catch(e=>tell(p,e.message));});
  });
  world.afterEvents.playerLeave?.subscribe(e=>{tickets.delete(e.playerId);cooldown.delete(e.playerId);});
  world.afterEvents.entitySpawn?.subscribe(event => {
    const entity = event.entity;
    if (entity?.typeId !== MANNEQUIN || !insideBank(entity.location)) return;
    system.run(() => {
      try {
        if (!entity.isValid || mannequinFits(entity.dimension, entity.location)) return;
        const at = bankMannequinLocation(entity.dimension, entity.location);
        if (at !== entity.location) entity.teleport(at);
      } catch { /* A rejected summon never produces an entity to move. */ }
    });
  });
  system.afterEvents.scriptEventReceive.subscribe(e=>{
    if(e.id==="junket:bankcheck" && !e.sourceEntity)void check(e.message.trim());
    if(e.id==="junket:bank" && e.sourceEntity?.typeId==="minecraft:player")void open(e.sourceEntity).catch(error=>tell(e.sourceEntity,error?.message??String(error)));
  });
  warp.setLegacyNavigator((p,g)=>void open(p,g).catch(e=>tell(p,e.message)));
  system.runTimeout(()=>void ensure().catch(e=>console.warn(`[JUNKET_BANK_INIT] ${e.message}`)),100);
  // Reconcile only when the bank is near an actual player; no permanent area.
  system.runInterval(()=>{
    if(world.getAllPlayers().some(p=>p.dimension.id==="minecraft:overworld" && near(p.location,pos(BANK_HUB.arrival.location),65)))
      void ensure().catch(e=>console.warn(`[JUNKET_BANK_INIT] ${e.message}`));
  },200);
  return {open,ensure,check};
}
