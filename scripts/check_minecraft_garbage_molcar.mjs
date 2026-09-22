import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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
test('isolated friendly native AI and persistent inventory',()=>{
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
  core.scan();assert.equal(impulses.length,1);assert(impulses[0].x>0);assert.equal(m.events.at(-1),'ichiyon:garbage_pickup_on');
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
console.log(`${passed} garbage/paw checks passed`);
