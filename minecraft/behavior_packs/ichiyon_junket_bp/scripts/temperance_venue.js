const KEY = "junket:temperance_venue_v1", TAG = "junket:temperance_fixture";
export const TEMPERANCE_RECEPTION = "junket:temperance_reception";
export function createTemperanceVenue({ world }) {
  let beamSignature;
  const config = () => { const raw = world.getDynamicProperty(KEY); return typeof raw === "string" ? JSON.parse(raw) : null; };
  const at = (c, x, y, z) => ({ x: c.origin.x + x - 80, y: c.origin.y + y - 99, z: c.origin.z + z - 205 });
  const dim = c => world.getDimension(c.dimension);
  const objects = c => dim(c).getEntities({ tags: [TAG], location: at(c, 94, 110, 251), maxDistance: 64 });
  function ensure() {
    const c = config(); if (!c) throw new Error("ブルー・テンパランス会場が未設置です。");
    const definitions = [
      ["reception", "minecraft:npc", 94,101,241,"ブルー・テンパランス受付 / 参加・再戦"],
      ["return", "minecraft:npc", 94,101,239,"入口へ戻る / JUNKET"],
      ["tv", "junket:temperance_tv",94,108.3,255.05,"ブルー・テンパランス"],
      ["base", "junket:temperance_base",94,102.5,254.001,""],
      ["balance0", "junket:temperance_balance",88,114.5,251,"1P"],
      ["balance1", "junket:temperance_balance",100,114.5,251,"2P"],
      ["stone0", "junket:temperance_stone",88,114.5,251,"石 0"],
      ["stone1", "junket:temperance_stone",100,114.5,251,"石 0"],
    ];
    for (let i = 0; i < 3; i++) {
      definitions.push([`card${i}`, "junket:temperance_card",99,102.5,250.1+i*1.1,`カード ${i+1}`]);
      definitions.push([`frame${i}`, "junket:temperance_frame",99,102.44,250.1+i*1.1,""]);
    }
    const existing = objects(c);
    for (const [role,type,x,y,z,name] of definitions) if (!existing.some(e => e.getDynamicProperty("junket:temperance_role") === role)) {
      const e = dim(c).spawnEntity(type, at(c,x,y,z)); e.addTag(TAG); e.setDynamicProperty("junket:temperance_role",role); e.nameTag = name;
      if (role === "reception") e.addTag(TEMPERANCE_RECEPTION);
      if (role === "return") e.addTag("junket:return");
      if (role.startsWith("card")) { e.addTag("junket:temperance_control"); e.setDynamicProperty("junket:temperance_slot",Number(role.slice(4))); }
    }
    return c;
  }
  function update(table, reveal = false) {
    const c = config(); if (!c) return;
    const s = table.state, x = s.actor === 0 ? 89 : 99;
    for (const e of objects(c)) {
      const role = e.getDynamicProperty("junket:temperance_role");
      if (role === "tv") e.nameTag = `ラウンド${s.round} / 石 ${s.stones[0]} : ${s.stones[1]}\n体力 ${Math.max(0,s.hp[0])} : ${Math.max(0,s.hp[1])}`;
      if (role?.startsWith("balance") || role?.startsWith("stone")) {
        const stone = role.startsWith("stone"), seat = Number(role.slice(stone ? 5 : 7)), offset = .06 * Math.max(-100,Math.min(100,s.balance));
        e.teleport(at(c,seat === 0 ? 88 : 100,114.5+(seat === 0 ? -offset : offset),251));
        e.nameTag = `${seat+1}P / 石 ${s.stones[seat]}`;
      }
      if (role?.startsWith("card") || role?.startsWith("frame")) {
        const card = role.startsWith("card"), slot = Number(role.slice(card ? 4 : 5));
        if (!reveal) e.teleport(at(c,x,card ? 102.5 : 102.44,250.1+slot*1.1), { rotation:{x:0,y:90} });
        const value = s.last?.cards[slot];
        e.setProperty("junket:temperance_frame", card ? reveal && value ? (Math.abs(value)-1)*2+(value>0?1:2) : 0 : !reveal && s.phase !== "arrange" ? 1 : 0);
      }
    }
    const k = [9,26,42,59,76,92].filter(n => Math.abs(s.balance) >= n).length;
    const index=6-Math.sign(s.balance)*k, signature=JSON.stringify([c.origin,index]);
    if(beamSignature!==signature){
      world.structureManager.place(`junket:temperance_beam_${index}`,dim(c),at(c,88,109,251),{includeEntities:false});
      beamSignature=signature;
    }
  }
  function destination() {
    const c=config();
    if(!c) return null;
    return {location:at(c,94,101,241),dimension:dim(c),rotation:0};
  }
  function go(p) {
    const target=destination();
    if(!target) throw new Error("会場が未設置です。");
    p.teleport(target.location,{dimension:target.dimension,rotation:{x:0,y:target.rotation}});
  }
  function start(t) {
    const c=ensure();
    for (const id of t.players) { const p=world.getAllPlayers().find(p=>p.id===id); if (p) p.teleport(at(c,t.players.indexOf(id)===0?88:100,101,251),{dimension:dim(c),rotation:{x:0,y:t.players.indexOf(id)===0?-90:90}}); }
    update(t);
  }
  function end(t) {
    const c=config(); if (!c) return;
    if(t.state) update({...t,state:{...t.state,actor:1,balance:0,phase:"arrange"}});
    for(const p of world.getAllPlayers().filter(p=>t.players.includes(p.id))) {
      if(p.dimension.id===c.dimension && Math.hypot(p.location.x-at(c,94,101,251).x,p.location.z-at(c,94,101,251).z)<80) go(p);
    }
  }
  return { ensure, start, update, end, go, destination, config };
}
