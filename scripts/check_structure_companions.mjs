import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import { createIkarun, IKARUN, IKARUN_OWNER, HEAL, HOME_RADIUS, safeHoverReturn, roamStep, horizontalDistance, writeHome, readHome, showMannequinForm, installMannequinPlacement, cellKey } from '../minecraft/behavior_packs/import_structures/scripts/ikarun_core.js';
import { canHealFriendly, healFriendly } from '../minecraft/behavior_packs/import_structures/scripts/friendly_healing.js';
let passed=0;
let chain=Promise.resolve();
const test=(name,fn)=>{chain=chain.then(async()=>{await fn();passed++;console.log(`PASS ${name}`);});};
function actor(typeId,id=typeId){
  const props=new Map(),events=[],messages=[],tags=[];
  const hp={currentValue:10,effectiveMax:20,setCurrentValue(v){this.currentValue=v;return true;}};
  const tame={tamedToPlayerId:undefined,tame(p){this.tamedToPlayerId=p.id;return true;}};
  const e={typeId,id,isValid:true,props,events,messages,tags,hp,tame,selectedSlotIndex:0,location:{x:0,y:64,z:0},
    dimension:{id:'overworld',spawnParticle(){},playSound(){}},getTags:()=>tags,
    getComponent:k=>k==='minecraft:health'?hp:k==='minecraft:tameable'?tame:k==='minecraft:type_family'?{hasTypeFamily:()=>false}:k==='minecraft:inventory'?{container:{getItem:()=>e.held}}:undefined,
    getDynamicProperty:k=>props.get(k),setDynamicProperty:(k,v)=>v===undefined?props.delete(k):props.set(k,v),getProperty:k=>props.get(k),setProperty:(k,v)=>props.set(k,v),
    triggerEvent:e=>events.push(e),sendMessage:s=>messages.push(s)};
  return e;
}
const pending=[],system={currentTick:0,run:fn=>pending.push(fn)},flush=()=>pending.splice(0).forEach(f=>f());
const core=createIkarun({world:{},system});
test('explicit friendly policy excludes hostile/boss/game/player and full HP',()=>{
  for(const type of ['minecraft:zombie','minecraft:wither','minecraft:ender_dragon','minecraft:player','junket:reception','ichiyon:video_screen'])assert(!canHealFriendly(actor(type)));
  const cow=actor('minecraft:cow');assert(canHealFriendly(cow));cow.tags.push('junket:hope:ai');assert(!canHealFriendly(cow));cow.tags.length=0;cow.hp.currentValue=20;assert(!canHealFriendly(cow));cow.hp.currentValue=0;assert(!canHealFriendly(cow));
  const angry=actor('minecraft:wolf');angry.target=actor('minecraft:player');assert(!canHealFriendly(angry));
});
test('bounded heal respects effective max, dead, and no-heal tag',()=>{
  const cow=actor('minecraft:cow');cow.hp.currentValue=19;assert.equal(healFriendly(cow,4),1);assert.equal(cow.hp.currentValue,20);assert.equal(healFriendly(cow,4),0);
  cow.hp.currentValue=10;cow.tags.push('no_heal');assert.equal(healFriendly(cow,4),0);assert.equal(cow.hp.currentValue,10);
});
test('simultaneous follow claims never steal and release belongs to exact player',()=>{
  const e=actor(IKARUN),p=actor('minecraft:player','p'),other=actor('minecraft:player','q');
  assert(core.toggle(p,e));assert(!core.toggle(other,e));flush();assert.equal(e.props.get(IKARUN_OWNER),'p');assert.equal(e.tame.tamedToPlayerId,'p');
  assert(core.toggle(p,e));assert(!e.props.has(IKARUN_OWNER));assert(e.events.includes('ichiyon:ikarun_stop'));
});
test('mobile duplicate interaction is debounced; item switches abort scheduled operation',()=>{
  const e=actor(IKARUN,'mobile'),p=actor('minecraft:player','p');
  for(let i=0;i<8;i++){const ev={target:e,player:p};core.interact(ev);assert(ev.cancel);}
  flush();assert.equal(e.props.get(IKARUN_OWNER),'p');assert.equal(e.events.filter(e=>e==='ichiyon:ikarun_start').length,1);flush();
  system.currentTick=20;core.interact({target:e,player:p});p.held={typeId:'minecraft:lead'};flush();assert.equal(e.props.get(IKARUN_OWNER),'p');
});
test('disconnect and other dimension pause; reload resumes exact owner without losing identity',()=>{
  const e=actor(IKARUN,'persist'),p=actor('minecraft:player','persist-owner');e.props.set(IKARUN_OWNER,p.id);
  core.maintain(e,[]);assert(e.events.includes('ichiyon:ikarun_pause'));assert.equal(e.props.get(IKARUN_OWNER),p.id);
  p.dimension.id='nether';core.maintain(e,[p]);assert(!e.events.includes('ichiyon:ikarun_resume'));
  p.dimension.id='overworld';core.maintain(e,[p]);assert.equal(e.tame.tamedToPlayerId,p.id);assert(e.events.includes('ichiyon:ikarun_resume'));
  const restarted=createIkarun({world:{},system});restarted.maintain(e,[p]);assert.equal(e.props.get(IKARUN_OWNER),p.id);
});
test('safe far return refuses unloaded/blocked/cross-dimension destinations',()=>{
  const e=actor(IKARUN),p=actor('minecraft:player');p.location.x=100;let teleports=0;e.clearVelocity=()=>{};
  e.tryTeleport=(pos,opts)=>{assert(opts.checkForBlocks);teleports++;assert.equal(pos.y,65);return true;};
  e.dimension.getBlock=()=>undefined;assert(!safeHoverReturn(e,p));e.dimension.getBlock=()=>({isAir:false});assert(!safeHoverReturn(e,p));assert.equal(teleports,0);
  e.dimension.getBlock=()=>({isAir:true});assert(safeHoverReturn(e,p));assert.equal(teleports,1);
  p.dimension.id='nether';assert(!safeHoverReturn(e,p));assert.equal(teleports,1);
});
test('healers share target cooldown and bounded nearby scan; visual state expires',()=>{
  system.currentTick=100;const a=actor(IKARUN,'a'),b=actor(IKARUN,'b'),cow=actor('minecraft:cow','cow');
  let queries=0;for(const e of [a,b])e.dimension.getEntities=q=>{queries++;assert.equal(q.maxDistance,6);assert.equal(q.closest,16);return[cow];};
  assert.equal(core.heal(a),1);assert.equal(core.heal(b),0);assert.equal(cow.hp.currentValue,14);assert(a.props.get('ichiyon:healing'));
  assert.equal(core.heal(a),0);assert.equal(queries,2);system.currentTick=120;core.maintain(a,[]);assert.equal(a.props.get('ichiyon:healing'),false);
  system.currentTick=200;assert.equal(core.heal(b),1);assert.equal(cow.hp.currentValue,18);
});
test('many healers budget four scans per second without starvation',()=>{
  const herd=Array.from({length:11},(_,i)=>actor(IKARUN,`herd${i}`)),visited=new Set();let queries=0;
  for(const e of herd)e.dimension.getEntities=()=>{visited.add(e.id);queries++;return[];};
  const world={getAllPlayers:()=>[],getDimension:n=>({getEntities:()=>n==='overworld'?herd:[]})};
  const clock={currentTick:0},c=createIkarun({world,system:clock});
  for(let i=0;i<3;i++){clock.currentTick=i*20;const before=queries;c.scan();assert(queries-before<=4);}
  assert.equal(visited.size,11);assert.equal(HEAL.intervalTicks,100);
});
const load=name=>JSON.parse(readFileSync(new URL(name === 'taketumi'
  ? `../minecraft/behavior_packs/ichiyon_avatar_bp/entities/${name}.json`
  : `./templates/structure_companions/${name}.json`,import.meta.url)));
test('Tumaini equals current Taketsumi for every balance, target, event and AI field',()=>{
  const actual=load('tumaini'),expected=load('taketumi');actual['minecraft:entity'].description.identifier='ichiyon:taketumi';
  actual['minecraft:entity'].components['minecraft:type_family'].family=actual['minecraft:entity'].components['minecraft:type_family'].family.map(v=>v==='tumaini'?'taketumi':v);
  assert.deepEqual(actual,expected);
});
test('hover has native flying navigation and correct current random_hover range schema',()=>{
  const e=load('ikarun')['minecraft:entity'],c=e.components;assert.equal(c['minecraft:physics'].has_gravity,false);assert('minecraft:movement.hover' in c);assert('minecraft:navigation.hover' in c);
  const hover=c['minecraft:behavior.random_hover'];
  assert.deepEqual(hover.hover_height,{min:1,max:1});
  assert.equal(hover.interval,80);assert.equal(hover.xz_dist,2);assert.equal(hover.y_dist,1);assert.equal(hover.speed_multiplier,0.65);
  assert.equal(c['minecraft:behavior.random_look_around'].probability,0.08);
  assert.equal(e.description.properties['ichiyon:healing'].client_sync,true);
  const follow=e.component_groups['ichiyon:ikarun_following']['minecraft:behavior.follow_owner'];
  assert.equal(follow.can_teleport,false);assert.equal(follow.speed_multiplier,1.25);assert.equal(follow.start_distance,3);
  const home=e.component_groups['ichiyon:ikarun_home']['minecraft:home'];
  assert.equal(home.restriction_radius,4);assert.equal(home.restriction_type,'random_movement');
  assert.equal(e.events['minecraft:entity_spawned'].add.component_groups[0],'ichiyon:ikarun_home');
  assert.equal(e.events['ichiyon:ikarun_stop'].add.component_groups[0],'ichiyon:ikarun_home');
  assert.ok(e.events['ichiyon:ikarun_start'].remove.component_groups.includes('ichiyon:ikarun_home'));
  assert.ok(e.events['ichiyon:ikarun_resume'].remove.component_groups.includes('ichiyon:ikarun_home'));
  assert.equal(e.events['ichiyon:ikarun_start'].add.component_groups.includes('ichiyon:ikarun_following'),true);
  assert(!Object.keys(c).some(k=>k.includes('attack')));assert('minecraft:persistent' in c);
});
test('idle hover stays near home for 20000 cycles and returns from far away',()=>{
  const home={x:12,y:70,z:-4};
  let loc={...home},max=0;
  for(let i=0;i<20000;i++){
    const angle=i*2.399963229728653;
    const proposed={x:loc.x+Math.cos(angle)*2,y:loc.y+((i%3)-1),z:loc.z+Math.sin(angle)*2};
    loc=roamStep(loc,home,proposed,HOME_RADIUS).location;
    max=Math.max(max,horizontalDistance(loc,home));
  }
  assert.equal(max<=HOME_RADIUS+1e-6,true);
  let drifted={x:home.x,y:home.y,z:home.z},overshoot=0;
  for(let i=0;i<20000;i++){
    if(i%4===0){
      const angle=i*0.7;
      drifted={x:drifted.x+Math.cos(angle)*2,y:drifted.y,z:drifted.z+Math.sin(angle)*2};
    }
    const dist=horizontalDistance(drifted,home);
    overshoot=Math.max(overshoot,dist);
    if(dist>HOME_RADIUS)drifted=roamStep(drifted,home,drifted,HOME_RADIUS).location;
  }
  assert.equal(overshoot<=HOME_RADIUS+2+1e-6,true);
  assert.equal(horizontalDistance(drifted,home)<=HOME_RADIUS+1e-6,true);
  let far={x:home.x+80,y:home.y,z:home.z-30};
  for(let i=0;i<80;i++)far=roamStep(far,home,far,HOME_RADIUS).location;
  assert.equal(horizontalDistance(far,home)<=HOME_RADIUS+1e-6,true);
  let line={...home},lineMax=0,raw={...home},rawMax=0;
  for(let i=0;i<100;i++){
    const proposed={x:line.x+2,y:line.y,z:line.z};
    line=roamStep(line,home,proposed,HOME_RADIUS).location;
    lineMax=Math.max(lineMax,horizontalDistance(line,home));
    raw={x:raw.x+2,y:raw.y,z:raw.z};
    rawMax=Math.max(rawMax,horizontalDistance(raw,home));
    if(horizontalDistance(raw,home)>HOME_RADIUS)raw=roamStep(raw,home,raw,HOME_RADIUS).location;
  }
  assert.equal(lineMax<=HOME_RADIUS+1e-6,true);
  assert.equal(rawMax<=HOME_RADIUS+2+1e-6,true);
  assert.equal(horizontalDistance(raw,home)<=HOME_RADIUS+1e-6,true);
  console.log(`home leash cycles=20000 filteredMax=${max.toFixed(3)} straightMax=${lineMax.toFixed(3)} hopThenReturnMax=${rawMax.toFixed(3)} final=${horizontalDistance(raw,home).toFixed(3)}`);
});
test('follow releases the leash and turning it off recenters home',()=>{
  const e=actor(IKARUN,'home-follow'),p=actor('minecraft:player','home-player');
  e.location={x:30,y:70,z:-8};p.location={...e.location};p.dimension=e.dimension;
  e.clearVelocity=()=>{};
  e.tryTeleport=(pos)=>{e.location={...pos};return true;};
  writeHome(e,{x:0,y:70,z:0});
  const clock={currentTick:0,run:fn=>fn()};
  const c=createIkarun({world:{},system:clock});
  assert(c.toggle(p,e));
  assert.equal(readHome(e).x,0);
  c.maintain(e,[p]);
  assert.equal(e.location.x,30);
  assert(c.toggle(p,e));
  assert.equal(readHome(e).x,30);assert.equal(readHome(e).z,-8);
  e.location.x=48;
  for(let i=0;i<20;i++)c.maintain(e,[]);
  assert.equal(horizontalDistance(e.location,readHome(e))<=HOME_RADIUS+1e-6,true);
  assert.equal(readHome(e).x,30);
  assert.equal(HEAL.range,6);assert.equal(HEAL.amount,4);assert.equal(HEAL.intervalTicks,100);
});
test('mannequin form waits out UserBusy and venue light reads as room',async()=>{
  let waits=0,shows=0;
  const opened=await showMannequinForm(async()=>++shows<3?{canceled:true,cancelationReason:'UserBusy'}:{selection:1},async()=>{waits++;},{});
  assert.equal(opened.selection,1);assert.equal(shows,3);assert.equal(waits,2);
  const closed=await showMannequinForm(async()=>({canceled:true,cancelationReason:'UserClosed'}),async()=>{throw new Error('retried a closed form');},{});
  assert.equal(closed.canceled,true);
  function Dimension(){}
  const blocks=new Map([
    ['0,64,2',{typeId:'minecraft:light_block_12',isAir:false}],
    ['0,65,2',{typeId:'minecraft:light_block_12',isAir:false}],
    ['0,63,2',{typeId:'minecraft:smooth_quartz',isAir:false}],
  ]);
  Dimension.prototype.getBlock=function(loc){return blocks.get(cellKey(loc))??{typeId:'minecraft:air',isAir:true};};
  const dimension=Object.create(Dimension.prototype);
  const subs={};
  class Form{show(){return Promise.resolve({canceled:true});}}
  const world={
    getDimension:()=>dimension,
    beforeEvents:{playerInteractWithBlock:{subscribe:fn=>{subs.block=fn;}},itemUse:{subscribe:fn=>{subs.use=fn;}}},
    afterEvents:{entitySpawn:{subscribe:fn=>{subs.spawn=fn;}}},
  };
  let tick=0;
  installMannequinPlacement({world,system:{get currentTick(){return tick;},runTimeout:fn=>fn(),runInterval(){}},ActionFormData:Form});
  const event={itemStack:{typeId:'ichiyon:avatar_selector'},isFirstEvent:true,cancel:false,block:{location:{x:0,y:63,z:2}}};
  subs.block(event);subs.use(event);
  assert.equal(event.cancel,false);
  assert.equal(dimension.getBlock({x:.5,y:64,z:2.5}).isAir,true);
  assert.equal(dimension.getBlock({x:.5,y:65,z:2.5}).isAir,true);
  assert.equal(dimension.getBlock({x:.5,y:63,z:2.5}).isAir,false);
  tick=20*180+1;
  assert.equal(dimension.getBlock({x:.5,y:64,z:2.5}).isAir,false);
});
test('shared Taketsumi home script processes both actors, preserving separate home/combat identity',()=>{
  const source=readFileSync(new URL('../minecraft/behavior_packs/import_structures/scripts/taketumi_leash.js',import.meta.url),'utf8').replace(/^import .*?;\s*/,'');
  const callbacks={},scheduled=[];
  const a=actor('ichiyon:taketumi','t'),b=actor('ichiyon:tumaini','u');a.location.x=1.25;b.location.x=9.75;
  for(const e of [a,b]){e.hasTag=t=>e.tags.includes(t);e.addTag=t=>e.tags.push(t);e.removeTag=t=>e.tags.splice(e.tags.indexOf(t),1);e.dimension.getEntities=()=>[];}
  const world={afterEvents:{entityHurt:{subscribe:f=>callbacks.hurt=f}},beforeEvents:{playerInteractWithEntity:{subscribe:f=>callbacks.interact=f}},getDimension:id=>({getEntities:q=>id==='overworld'?[a,b].filter(e=>e.typeId===q.type):[]})};
  vm.runInNewContext(source,{world,system:{runInterval:f=>callbacks.scan=f,run:f=>scheduled.push(f)},console});callbacks.scan();
  assert.equal(a.props.get('taketumi.home_x'),1.25);assert.equal(b.props.get('taketumi.home_x'),9.75);assert(a.tags.some(t=>t.includes('1250')));assert(b.tags.some(t=>t.includes('9750')));
});
await chain;
console.log(`${passed} structure companion runtime checks passed; PC/mobile client execution not tested.`);
