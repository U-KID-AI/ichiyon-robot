import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { collect, withdraw, canStore, seekDrop, createGarbageMolcar, GARBAGE, OWNER, PAW, isMob } from '../minecraft/behavior_packs/import_structures/scripts/garbage_molcar_core.js';

class Stack {
  constructor(typeId,amount=1,nameTag=''){Object.assign(this,{typeId,amount,nameTag,maxAmount:64,opaque:'map-uuid-123'});}
  clone(){return Object.assign(new Stack(this.typeId,this.amount,this.nameTag),this);}
  isStackableWith(other){return this.typeId===other.typeId&&this.nameTag===other.nameTag&&this.opaque===other.opaque;}
}
class Container {
  constructor(size=54){this.size=size;this.slots=[];}
  getItem(i){return this.slots[i]?.clone();}
  setItem(i,item){this.slots[i]=item?.clone();}
  clearAll(){this.slots=[];}
  transferItem(i,target){
    const item=this.getItem(i);if(!item)return;
    for(let j=0;j<target.size&&item.amount;j++){
      const old=target.getItem(j);if(old&&!old.isStackableWith(item))continue;
      const n=Math.min(item.amount,64-(old?.amount??0));
      if(!n)continue;const merged=(old??item).clone();merged.amount=(old?.amount??0)+n;
      target.setItem(j,merged);item.amount-=n;
    }
    this.setItem(i,item.amount?item:undefined);return item.amount?item:undefined;
  }
}
function entity(typeId=GARBAGE,id='m'){
  const container=new Container(),props=new Map(),events=[];
  return {typeId,id,isValid:true,container,props,events,selectedSlotIndex:0,
    dimension:{id:'overworld',playSound(){}},location:{x:0,y:64,z:0},
    getDynamicProperty:k=>props.get(k),setDynamicProperty:(k,v)=>v===undefined?props.delete(k):props.set(k,v),
    getComponent:k=>k==='minecraft:inventory'?{container}:k==='minecraft:health'?{currentValue:20}:
      k==='minecraft:type_family'?{hasTypeFamily:f=>f==='mob'&&typeId!=='minecraft:painting'}:
      k==='minecraft:tameable'?{tame:p=>{props.set('tamed',p.id);return true;}}:undefined,
    triggerEvent:e=>events.push(e),sendMessage(){},remove(){this.isValid=false;}};
}
function drop(stack){return{typeId:'minecraft:item',isValid:true,getComponent:()=>({itemStack:stack}),remove(){this.isValid=false;}};}
let passed=0;
function test(name,fn){fn();passed++;console.log(`PASS ${name}`);}
test('normal removes without storage',()=>{
  const m=entity(),d=drop(new Stack('diamond'));assert(collect(m,d));assert(!d.isValid);assert(!m.container.getItem(0));
});
test('offline owner retains metadata',()=>{
  const m=entity();m.setDynamicProperty(OWNER,'offline');
  assert(collect(m,drop(new Stack('filled_map',1,'Narita'))));
  assert.equal(m.container.getItem(0).opaque,'map-uuid-123');assert.equal(m.container.getItem(0).nameTag,'Narita');
});
test('full and partial capacity leave ground stack intact',()=>{
  const m=entity();m.setDynamicProperty(OWNER,'owner');for(let i=0;i<54;i++)m.container.setItem(i,new Stack('stone',64));
  m.container.setItem(0,new Stack('diamond',63));const d=drop(new Stack('diamond',2));
  assert(!collect(m,d));assert(d.isValid);assert.equal(m.container.getItem(0).amount,63);
});
test('write failure restores original slots',()=>{
  const m=entity();m.setDynamicProperty(OWNER,'owner');m.container.setItem(0,new Stack('diamond',63));
  const set=m.container.setItem.bind(m.container);let calls=0;
  m.container.setItem=(i,s)=>{if(++calls===2)throw Error('write');set(i,s);};
  const d=drop(new Stack('diamond',2));assert.throws(()=>collect(m,d),/write/);assert(d.isValid);assert.equal(m.container.getItem(0).amount,63);
});
test('remove failure rolls back',()=>{
  const m=entity();m.setDynamicProperty(OWNER,'owner');const d=drop(new Stack('diamond'));
  d.remove=()=>{throw Error('remove');};assert.throws(()=>collect(m,d),/remove/);assert(!m.container.getItem(0));
});
test('native partial withdrawal retains remainder and metadata',()=>{
  const m=entity(),p=entity('minecraft:player','p');for(let i=0;i<54;i++)p.container.setItem(i,new Stack('stone',64));
  m.container.setItem(0,new Stack('diamond',7,'named'));p.container.setItem(0,new Stack('diamond',62,'named'));
  assert.equal(withdraw(m,p,0),2);assert.equal(m.container.getItem(0).amount,5);assert.equal(m.container.getItem(0).opaque,'map-uuid-123');
  assert.equal(withdraw(m,p,0),0);
});
const scheduled=[];
const core=createGarbageMolcar({world:{},system:{currentTick:0,run:fn=>scheduled.push(fn)},ActionFormData:class{}});
test('follow starts once with fixed owner',()=>{
  const m=entity(),p=entity('minecraft:player','owner');core.start(p,m);core.start(entity('minecraft:player','other'),m);
  scheduled.splice(0).forEach(fn=>fn());assert.equal(m.getDynamicProperty(OWNER),'owner');assert.equal(m.getDynamicProperty('tamed'),'owner');
  assert.deepEqual(m.events,['ichiyon:garbage_start']);
});
test('explicit OFF clears storage and owner',()=>{
  const m=entity();m.setDynamicProperty(OWNER,'owner');m.container.setItem(0,new Stack('diamond'));core.stop(m);
  assert(!m.getDynamicProperty(OWNER));assert(!m.container.getItem(0));assert.deepEqual(m.events,['ichiyon:garbage_stop']);
});
test('paw removes custom and vanilla mobs, never player/painting/item',()=>{
  const p=entity('minecraft:player','p');p.container.setItem(0,new Stack(PAW));
  for(const type of [GARBAGE,'minecraft:cow','ichiyon:mokuro']){
    const target=entity(type),e={player:p,target,cancel:false};core.interact(e);scheduled.splice(0).forEach(fn=>fn());assert(e.cancel);assert(!target.isValid);
  }
  for(const target of [entity('minecraft:player'),entity('minecraft:painting'),drop(new Stack('diamond'))]){
    assert(!isMob(target));const e={player:p,target,cancel:false};core.interact(e);assert(!e.cancel);assert(target.isValid);
  }
  assert.equal(p.container.getItem(0).amount,1);
});
test('switching held item before scheduled paw use cancels removal',()=>{
  const p=entity('minecraft:player','p'),m=entity();p.container.setItem(0,new Stack(PAW));core.interact({player:p,target:m});p.container.setItem(0,undefined);
  scheduled.splice(0).forEach(fn=>fn());assert(m.isValid);
});
const bp=JSON.parse(readFileSync(new URL('../minecraft/behavior_packs/ichiyon_avatar_bp/entities/garbage_molcar.json',import.meta.url)))['minecraft:entity'];
test('native fall immunity matches all regular Molcars without changing health or other damage',()=>{
  const sensor=bp.components['minecraft:damage_sensor'];
  assert.deepEqual(sensor,{triggers:[{cause:'fall',deals_damage:'no'}]});
  assert.deepEqual(bp.components['minecraft:health'],{value:30,max:30});
  for(const type of ['molcar','molcar2','molcar3']){
    const regular=JSON.parse(readFileSync(new URL(`../minecraft/behavior_packs/ichiyon_avatar_bp/entities/${type}.behavior.json`,import.meta.url)))['minecraft:entity'];
    assert.deepEqual(sensor,regular.components['minecraft:damage_sensor']);
  }
  // Following/pickup events must not remove or replace the permanent fall guard.
  for(const group of Object.values(bp.component_groups))assert(!('minecraft:damage_sensor' in group));
});
test('isolated friendly native AI and persistent inventory',()=>{
  assert.equal(bp.components['minecraft:movement'].value,.34);
  assert.equal(bp.component_groups['ichiyon:garbage_following']['minecraft:behavior.follow_owner'].speed_multiplier,1.25);
  assert(bp.components['minecraft:type_family'].family.includes('mob'));
  assert.deepEqual(bp.components['minecraft:collision_box'],{width:1.5,height:1.25});
  assert.deepEqual(bp.components['minecraft:physics'],{});
  assert('minecraft:pushable_by_entity' in bp.components);
  assert('minecraft:pushable_by_block' in bp.components);
  assert('minecraft:leashable' in bp.components);
  assert.equal(bp.components['minecraft:inventory'].inventory_size,54);assert.equal(bp.components['minecraft:inventory'].private,true);
  assert('minecraft:persistent' in bp.components);assert(!bp.components['minecraft:type_family'].family.includes('molcar'));
  for(const c of [bp.components,...Object.values(bp.component_groups)]){
    assert(!Object.keys(c).some(k=>/attack|rideable|input_ground|horsejump/.test(k)));
    assert(!('minecraft:inventory' in c)||c===bp.components);
  }
  assert(bp.component_groups['ichiyon:garbage_idle']['minecraft:behavior.random_stroll']);
  assert.equal(bp.component_groups['ichiyon:garbage_following']['minecraft:behavior.follow_owner'].can_teleport,false);
  assert.deepEqual(bp.events['ichiyon:garbage_pickup_on'].remove.component_groups,['ichiyon:garbage_idle','ichiyon:garbage_following']);
  assert(!bp.events['ichiyon:garbage_pickup_on'].remove.component_groups.includes('ichiyon:garbage_follow'));
  assert(!JSON.stringify(bp).includes('pickup_items'));
});

test('nearby seeking respects obstacles, owner range, height and capacity',()=>{
  const m=entity(),p=entity('minecraft:player','owner');m.dimension.getBlockFromRay=()=>undefined;
  const d=drop(new Stack('diamond',2));d.location={x:6,y:64,z:0};
  assert.deepEqual(seekDrop(m,[d]).direction,{x:1,y:0,z:0});
  m.dimension.getBlockFromRay=()=>({block:{}});assert(!seekDrop(m,[d]));
  m.dimension.getBlockFromRay=()=>undefined;
  p.location.x=-4;assert(!seekDrop(m,[d],p));
  d.location.y=67;assert(!seekDrop(m,[d]));d.location.y=64;
  m.setDynamicProperty(OWNER,'owner');for(let i=0;i<54;i++)m.container.setItem(i,new Stack('stone',64));
  assert(!canStore(m,d));assert(!seekDrop(m,[d]));assert(d.isValid);
});

test('seeking pauses native wandering/follow and yields to owner/leash',()=>{
  const m=entity(),p=entity('minecraft:player','owner'),d=drop(new Stack('diamond'));d.location={x:6,y:64,z:0};
  m.isOnGround=true;m.getVelocity=()=>({x:0,y:0,z:0});m.setRotation=()=>{};
  const impulses=[];m.applyImpulse=v=>impulses.push(v);
  m.getProperty=k=>m.props.get(k)??false;
  m.triggerEvent=e=>{m.events.push(e);m.props.set('ichiyon:pickup_enabled',e==='ichiyon:garbage_pickup_on');};
  m.dimension.getBlockFromRay=()=>undefined;
  m.dimension.getEntities=q=>q.type===GARBAGE?[m]:q.maxDistance===8?[d]:[];
  const world={getDimension:n=>n==='overworld'?m.dimension:{getEntities:()=>[]},getAllPlayers:()=>[p]};
  const core=createGarbageMolcar({world,system:{currentTick:0},ActionFormData:class{}});
  core.scan();assert.equal(impulses.length,1);assert.equal(impulses[0].x,.38);assert.equal(m.events.at(-1),'ichiyon:garbage_pickup_on');
  m.setDynamicProperty(OWNER,p.id);p.location.x=24;core.scan();assert.equal(impulses.length,1);assert.equal(m.events.at(-1),'ichiyon:garbage_pickup_off');
  p.location.x=0;const component=m.getComponent;m.getComponent=k=>k==='minecraft:leashable'?{isLeashed:true}:component(k);
  core.scan();assert.equal(impulses.length,1);
});
test('compact integrated cab, low lead and no headphone protrusions',()=>{
  const geo=JSON.parse(readFileSync(new URL('../minecraft/resource_packs/ichiyon_avatar_rp/models/entity/garbage_molcar.geo.json',import.meta.url)))['minecraft:geometry'][0];
  const body=geo.bones.find(b=>b.name==='body');assert.deepEqual(body.locators.lead,[-10,5.2,0]);
  assert(!geo.bones.find(b=>b.name==='dj').cubes?.length);
  assert(body.cubes.some(c=>c.size[0]>2&&c.uv.west.uv[0]===100));
  assert(body.cubes.some(c=>c.size[0]>2&&c.origin[1]===6&&c.uv.west.uv[0]===36));
  assert(geo.bones.reduce((n,b)=>n+(b.cubes?.length??0),0)<100);
});

test('pre-upgrade owner and storage survive load-time following goal repair',()=>{
  const m=entity(),p=entity('minecraft:player','owner');m.setDynamicProperty(OWNER,p.id);
  m.container.setItem(0,new Stack('diamond',7,'saved'));m.getProperty=()=>false;m.getVelocity=()=>({x:0,z:0});
  let loaded=true;m.dimension.getEntities=q=>q.type===GARBAGE&&loaded?[m]:[];
  const world={getDimension:n=>n==='overworld'?m.dimension:{getEntities:()=>[]},getAllPlayers:()=>[p]};
  const core=createGarbageMolcar({world,system:{currentTick:0},ActionFormData:class{}});
  core.scan();assert.deepEqual(m.events,['ichiyon:garbage_pickup_off']);
  core.scan();assert.equal(m.events.length,1);
  loaded=false;core.scan();loaded=true;core.scan();assert.equal(m.events.length,2);
  assert.equal(m.getDynamicProperty(OWNER),p.id);assert.equal(m.container.getItem(0).amount,7);
  assert.equal(m.container.getItem(0).nameTag,'saved');
});
const combatPacks=['import_structures','ichiyon_avatar_bp'];
const combatSource=pack=>readFileSync(new URL(`../minecraft/behavior_packs/${pack}/scripts/molcar_combat.js`,import.meta.url),'utf8');
test('combat copies remain identical',()=>assert.equal(combatSource(combatPacks[0]),combatSource(combatPacks[1])));
for(const pack of combatPacks){
  function collisionScene(sourceType='ichiyon:molcar',fallback=false){
    const intervals=[],sounds=[];
    const rider={id:'rider',typeId:'minecraft:player'};
    const make=(typeId,id)=>Object.assign(entity(typeId,id),{location:{x:.5,y:64,z:0},hp:30,hits:0,knockbacks:[],impulses:[],ejected:0,speed:.4,
      getComponent(name){
        if(name==='minecraft:health')return{currentValue:this.hp};
        if(name==='minecraft:rideable'&&this.typeId!==GARBAGE)return{getRiders:()=>this.id==='source'?[rider]:[],ejectRiders:()=>this.ejected++};
      },
      getVelocity(){return{x:this.speed,y:0,z:0};},applyDamage(n){this.hits++;this.hp-=n;},
      applyKnockback(horizontal,vertical){if(fallback&&this.typeId===GARBAGE)throw Error('unavailable');this.knockbacks.push({horizontal,vertical});},
      applyImpulse(v){this.impulses.push(v);}});
    const source=make(sourceType,'source'),garbage=make(GARBAGE,'garbage'),cow=make('minecraft:cow','cow'),other=make('ichiyon:molcar2','other');
    source.location.x=0;
    garbage.setDynamicProperty(OWNER,'owner');garbage.setDynamicProperty('ichiyon:pickup_enabled',true);
    garbage.container.setItem(0,new Stack('filled_map',1,'saved map'));
    const all=[source,garbage,cow,other];
    const dimension={playSound:(name,location)=>sounds.push({name,location}),
      getEntities:q=>q.type?all.filter(e=>e.typeId===q.type):all.filter(e=>Math.hypot(e.location.x-q.location.x,e.location.z-q.location.z)<=q.maxDistance)};
    all.forEach(e=>e.dimension=dimension);
    const signal={subscribe(){}};
    const world={getAllPlayers:()=>[],getDimension:n=>n==='overworld'?dimension:{getEntities:()=>[]},
      beforeEvents:{entityHurt:signal},afterEvents:{entitySpawn:signal,entityHurt:signal,entityDie:signal}};
    const sourceText=combatSource(pack).replace(/^import .*;\r?\n/,'');
    vm.runInNewContext(sourceText,{world,system:{runInterval:fn=>intervals.push(fn)},console:{warn(){}}});
    return{step:intervals[0],source,garbage,cow,other,sounds};
  }
  for(const type of ['ichiyon:molcar','ichiyon:molcar2','ichiyon:molcar3'])test(`${pack}: ${type} knocks garbage back without damage or state loss`,()=>{
    const {step,garbage,cow,other,sounds}=collisionScene(type);step();
    assert.equal(garbage.hits,0);assert.equal(garbage.hp,30);assert.equal(garbage.ejected,0);
    assert.equal(garbage.knockbacks.length,1);
    assert.equal(garbage.knockbacks[0].horizontal.x,5);assert.equal(garbage.knockbacks[0].horizontal.z,0);
    assert.equal(garbage.knockbacks[0].vertical,.9);
    assert(sounds.some(s=>s.name==='ichiyon:molcar.crash'&&s.location===garbage.location));
    assert.equal(garbage.getDynamicProperty(OWNER),'owner');assert.equal(garbage.getDynamicProperty('ichiyon:pickup_enabled'),true);
    assert.equal(garbage.container.getItem(0).opaque,'map-uuid-123');assert.equal(garbage.container.getItem(0).nameTag,'saved map');
    assert.deepEqual(garbage.events,[]);assert(garbage.isValid);
    assert.equal(cow.hits,1);assert.equal(cow.hp,4);assert.equal(cow.knockbacks.length,1);
    assert.equal(other.hits,0);assert.equal(other.knockbacks.length,1);assert.equal(other.ejected,1);
    assert.equal(other.knockbacks[0].horizontal.x,5);assert.equal(other.knockbacks[0].vertical,.9);
    step();assert.equal(garbage.knockbacks.length,1);assert.equal(cow.hits,1);assert.equal(other.knockbacks.length,1);
    for(let i=0;i<5;i++)step(); // Existing 12-tick collision cooldown, loop runs every 2 ticks.
    assert.equal(garbage.knockbacks.length,2);assert.equal(garbage.hp,30);
  });
  test(`${pack}: garbage collision retains speed, radius and forward gates`,()=>{
    const {step,source,garbage}=collisionScene();
    source.speed=.119;step();assert.equal(garbage.knockbacks.length,0);
    source.speed=.12;garbage.location.x=1.81;step();assert.equal(garbage.knockbacks.length,0);
    garbage.location.x=-.5;step();assert.equal(garbage.knockbacks.length,0);
    garbage.location.x=1.8;step();assert.equal(garbage.knockbacks.length,1);assert.equal(garbage.hp,30);
  });
  test(`${pack}: garbage uses existing impulse fallback without damage`,()=>{
    const {step,garbage}=collisionScene('ichiyon:molcar',true);step();
    assert.equal(garbage.hp,30);assert.equal(garbage.hits,0);assert.equal(garbage.impulses.length,1);
    assert.equal(garbage.impulses[0].x,1);assert.equal(garbage.impulses[0].y,.45);assert.equal(garbage.impulses[0].z,0);
  });
  test(`${pack}: garbage is never processed as an attacking Molcar`,()=>{
    const {step,garbage,cow,other,sounds}=collisionScene(GARBAGE);step();
    assert.equal(cow.hits,0);assert.equal(other.knockbacks.length,0);assert.equal(garbage.knockbacks.length,0);assert.equal(sounds.length,0);
  });
}
console.log(`${passed} garbage/paw checks passed`);
