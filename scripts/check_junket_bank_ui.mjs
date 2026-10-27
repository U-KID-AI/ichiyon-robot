// Offline form/input contract checks. Uses the real safe form wrapper, fixture
// reconciliation and shared area loader; no Minecraft client rendering claim.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const folder=resolve("minecraft/behavior_packs/ichiyon_junket_bp/scripts");
const ids=["sparrow","temperance","lucy","silence","jinn","ace","grail","hope","auction","fire","pig","light","mine","clock","walker","garden","rabbit"];
const config={enabled:true,bounds:[-194,63,284,-149,93,313],arrival:{location:[-186.5,65,304.5],rotation:0},
  controls:[{location:[-187,66,309],type:"minecraft:stone_button"},{location:[-186,66,309],type:"minecraft:birch_wall_sign"}],fixtures:[]};
const url=source=>`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
let source=await readFile(resolve(folder,"bank_hub.js"),"utf8");
source=source.replace('import { BANK_HUB } from "./bank_hub_generated.js";',`const BANK_HUB = ${JSON.stringify(config)};`)
  .replaceAll(/from "(\.\/[^\"]+)"/g,(_,relative)=>`from ${JSON.stringify(pathToFileURL(resolve(folder,relative)).href)}`);
const {createBankHub,bankCatalog,bankMannequinLocation}=await import(url(source));
assert.deepEqual(bankCatalog.WARP_RETURN_POINT,config.arrival);
assert.equal(bankCatalog.WARP_RETURN_TITLE,"カラス銀行");
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();await new Promise(r=>setImmediate(r));};

function harness() {
  let tick=0,readOnly=false,failArea=false,failWarp=false;
  const events={},forms=[],players=[],tasks=[],immediate=[],intervals=[],warps=[],returns=[],areas=new Set(),commands=[],sessions=new Map();
  const signal=name=>({subscribe(fn){(events[name]??=[]).push(fn);}});
  const fire=(name,value)=>{for(const fn of events[name]??[])fn(value);};
  const dimension={id:"minecraft:overworld",heightRange:{min:-64,max:320},
    getBlock(at){
      const control=config.controls.find(c=>c.location.join(",")===[at.x,at.y,at.z].join(","));
      const typeId=control?.type??(Math.floor(at.y)===64?"minecraft:stone":"minecraft:air");
      return {typeId,isSolid:typeId==="minecraft:stone",isLiquid:false};
    },
    getEntities(){return[];},
    runCommand(command){
      assert.equal(readOnly,false,"before-event cannot mutate ticking areas");commands.push(command);
      if(command.startsWith("tickingarea add")){
        if(failArea)throw new Error("tickingarea capacity 10");
        areas.add(command.split(" ").at(-2));
      }else if(command.startsWith("tickingarea remove"))areas.delete(command.split(" ").at(-1));
      return {successCount:1};
    }};
  const world={getAllPlayers:()=>players,getDimension:()=>dimension,
    beforeEvents:{playerInteractWithBlock:signal("block"),playerInteractWithEntity:signal("entity")},
    afterEvents:{playerLeave:signal("leave"),entityLoad:signal("load"),entitySpawn:signal("spawn")}};
  const system={get currentTick(){return tick;},run(fn){immediate.push(fn);},
    runTimeout(fn,delay=1){tasks.push({fn,tick:tick+delay});return tasks.length;},
    runInterval(fn,delay){intervals.push({fn,delay});return intervals.length;},
    afterEvents:{scriptEventReceive:signal("script")}};
  class Form {
    constructor(){this.buttons=[];}
    title(v){this.heading=v;return this;}
    body(v){this.bodyText=v;return this;}
    button(v){this.buttons.push(v);return this;}
    show(player){assert.equal(readOnly,false,"before-event cannot open forms");return new Promise((resolve,reject)=>forms.push({player,form:this,resolve,reject}));}
  }
  const warp={controls:ids.map((id,i)=>({id,title:`正式ゲーム${i+1}`,enabled:true})),
    async warp(player,id){if(failWarp)throw new Error("transfer failed");warps.push({player,id});return true;},
    async returnToEntryAndWait(player){returns.push(player);return true;},
    setLegacyNavigator(fn){this.legacy=fn;}};
  const manager={forPlayer:id=>sessions.get(id)};
  const hub=createBankHub({world,system,ActionFormData:Form,manager,warp});
  function player(id){const p={id,typeId:"minecraft:player",location:{x:-186.5,y:65,z:304.5},dimension,messages:[],
    sendMessage(text){assert.equal(readOnly,false,"before-event cannot send messages");this.messages.push(text);}};players.push(p);return p;}
  async function press(player,index=0){
    const control=config.controls[index];const event={cancel:false,player,block:{location:Object.fromEntries("xyz".split("").map((a,i)=>[a,control.location[i]])),dimension,typeId:control.type}};
    readOnly=true;fire("block",event);readOnly=false;assert.equal(event.cancel,true);
    for(const fn of immediate.splice(0))fn();await flush();return event;
  }
  async function drain(promise){let done=false,result,error;promise.then(v=>{done=true;result=v;},e=>{done=true;error=e;});
    for(let i=0;i<300&&!done;i++){
      await flush();if(done)break;
      if(immediate.length){for(const fn of immediate.splice(0))fn();continue;}
      tasks.sort((a,b)=>a.tick-b.tick);const task=tasks.shift();assert.ok(task,"loader did not complete");tick=Math.max(tick,task.tick);task.fn();
    }
    assert.ok(done,"bounded loader completion");if(error)throw error;return result;
  }
  return {hub,forms,players,player,press,fire,drain,world,dimension,system,warp,sessions,warps,returns,areas,commands,
    setTick:v=>{tick=v;},failArea:()=>{failArea=true;},failWarp:()=>{failWarp=true;}};
}

const tests=[];
const test=(name,fn)=>tests.push([name,fn]);
test("bank panel exact presser / bystander isolation",async()=>{
  const h=harness(),a=h.player("actual"),b=h.player("bystander");await h.press(a);
  assert.equal(h.forms.length,1);assert.equal(h.forms[0].player,a);
  assert.equal(h.forms[0].form.buttons.length,18);h.forms[0].resolve({selection:2});await flush();
  assert.deepEqual(h.warps,[{player:a,id:"lucy"}]);assert.equal(b.messages.length,0);
});
test("two simultaneous forms keep selections and sessions separate",async()=>{
  const h=harness(),a=h.player("one"),b=h.player("two");await h.press(a);await h.press(b,1);
  assert.equal(h.forms.length,2);h.forms[1].resolve({selection:16});h.forms[0].resolve({selection:0});await flush();
  assert.deepEqual(h.warps,[{player:b,id:"rabbit"},{player:a,id:"sparrow"}]);
});
test("repeated touch and cooldown never multiply forms",async()=>{
  const h=harness(),p=h.player("repeat");await h.press(p);await h.press(p);assert.equal(h.forms.length,1);
  h.forms[0].resolve({canceled:true});await flush();await h.press(p);assert.equal(h.forms.length,1);
  h.setTick(8);await h.press(p);assert.equal(h.forms.length,2);h.forms[1].resolve({canceled:true});await flush();
});
test("close and invalid indexes never transfer or auto-reopen",async()=>{
  for(const response of [{canceled:true},{selection:17},{selection:-1},{selection:99},{selection:0.5},{selection:"0"},{}]){
    const h=harness(),p=h.player("closed"),request=h.hub.open(p);h.forms[0].resolve(response);assert.equal(await request,false);
    assert.equal(h.forms.length,1);assert.equal(h.warps.length,0);assert.equal(h.returns.length,0);
  }
});
test("disabled routes are not menu choices",async()=>{
  const h=harness(),p=h.player("enabled");h.warp.controls[1].enabled=false;
  const request=h.hub.open(p);assert.equal(h.forms[0].form.buttons.length,17);
  h.forms[0].resolve({selection:1});assert.equal(await request,true);assert.equal(h.warps[0].id,"lucy");
});
test("participants cannot open migration menu",async()=>{
  const h=harness(),p=h.player("joined");h.sessions.set(p.id,{gameId:"sparrow"});
  assert.equal(await h.hub.open(p),false);assert.equal(h.forms.length,0);assert.match(p.messages[0],/退出/);
});
test("location / dimension / participation changed during form is rejected",async()=>{
  for(const change of ["move","dimension","join"]){
    const h=harness(),p=h.player(change),request=h.hub.open(p);
    if(change==="move")p.location.x+=7;else if(change==="dimension")p.dimension={id:"minecraft:nether"};else h.sessions.set(p.id,{});
    h.forms[0].resolve({selection:0});assert.equal(await request,false);assert.equal(h.warps.length,0);assert.match(p.messages.at(-1),/変わりました/);
  }
});
test("expired form is rejected with retry guidance",async()=>{
  const h=harness(),p=h.player("expired"),request=h.hub.open(p);h.setTick(2401);h.forms[0].resolve({selection:0});
  assert.equal(await request,false);assert.match(p.messages.at(-1),/有効時間/);assert.equal(h.warps.length,0);
});
test("disconnect and old answer cannot consume a rejoined ticket",async()=>{
  const h=harness(),old=h.player("same-id"),request=h.hub.open(old);h.fire("leave",{playerId:old.id});h.players.splice(0,1);
  const current=h.player("same-id"),next=h.hub.open(current);h.forms[0].resolve({selection:1});assert.equal(await request,false);
  h.forms[1].resolve({selection:3});assert.equal(await next,true);assert.deepEqual(h.warps,[{player:current,id:"silence"}]);
});
test("busy cancellation tells only the presser and permits retry",async()=>{
  for(const reasonKey of ["cancelationReason","cancellationReason"]){
    const h=harness(),p=h.player(reasonKey),other=h.player("other"),request=h.hub.open(p);
    h.forms[0].resolve({canceled:true,[reasonKey]:"UserBusy"});assert.equal(await request,false);
    assert.match(p.messages.at(-1),/別の画面/);assert.equal(other.messages.length,0);assert.equal(h.forms.length,1);
    h.setTick(8);const retry=h.hub.open(p);h.forms[1].resolve({selection:0});assert.equal(await retry,true);
  }
});
test("form rejection reports failure and clears ticket",async()=>{
  const h=harness(),p=h.player("failure"),request=h.hub.open(p);h.forms[0].reject(new Error("form API unavailable"));
  assert.equal(await request,false);assert.match(p.messages.at(-1),/form API unavailable/);
  h.setTick(8);const next=h.hub.open(p);h.forms[1].resolve({canceled:true});assert.equal(await next,false);
});
test("all 17 legacy buttons retain direct destination mapping",async()=>{
  for(const id of ids){const h=harness(),p=h.player(id),g=h.warp.controls.find(g=>g.id===id);
    h.warp.legacy(p,g);assert.deepEqual(h.forms[0].form.buttons,["カラス銀行へ移動",`${g.title}へ直接移動`,"閉じる"]);
    h.forms[0].resolve({selection:1});await flush();assert.deepEqual(h.warps,[{player:p,id}]);assert.equal(h.returns.length,0);
  }
});
test("legacy bank route loads safely then returns only its requester",async()=>{
  const h=harness(),p=h.player("legacy"),other=h.player("observer"),request=h.hub.open(p,h.warp.controls[0]);
  h.forms[0].resolve({selection:0});assert.equal(await h.drain(request),true);
  assert.deepEqual(h.returns,[p]);assert.equal(other.messages.length,0);assert.equal(h.areas.size,0);
  assert.ok(h.commands.some(c=>c.startsWith("tickingarea add")));assert.ok(h.commands.some(c=>c.startsWith("tickingarea remove")));
});
test("legacy loading rechecks move / join / dimension / logout",async()=>{
  for(const change of ["move","join","dimension","logout"]){
    const h=harness(),p=h.player(change),request=h.hub.open(p,h.warp.controls[0]);h.forms[0].resolve({selection:0});await flush();
    assert.ok(h.commands.some(c=>c.startsWith("tickingarea add")),"change occurs after loading starts");
    if(change==="move")p.location.x+=7;else if(change==="join")h.sessions.set(p.id,{});else if(change==="dimension")p.dimension={id:"minecraft:nether"};
    else {h.fire("leave",{playerId:p.id});h.players.splice(0,1);}
    assert.equal(await h.drain(request),false);assert.equal(h.returns.length,0);assert.equal(h.areas.size,0);
  }
});
test("loader and transfer failures reach requester without unhandled rejection",async()=>{
  for(const type of ["loader","transfer"]){
    const h=harness(),p=h.player(type);if(type==="loader")h.failArea();else h.failWarp();
    const request=h.hub.open(p,type==="loader"?h.warp.controls[0]:null);h.forms[0].resolve({selection:0});
    assert.equal(await h.drain(request),false);assert.match(p.messages.at(-1),/移動できませんでした/);assert.equal(h.returns.length,0);
  }
});
test("script event identifies source player and ignores server/nonplayer",async()=>{
  const h=harness(),p=h.player("script");h.fire("script",{id:"junket:bank",sourceEntity:null,message:""});
  h.fire("script",{id:"junket:bank",sourceEntity:{typeId:"minecraft:pig"},message:""});assert.equal(h.forms.length,0);
  h.fire("script",{id:"junket:bank",sourceEntity:p,message:""});assert.equal(h.forms[0].player,p);
  h.failWarp();h.forms[0].resolve({selection:0});await flush();assert.match(p.messages.at(-1),/transfer failed/);
});
test("bank mannequin placement leaves a blocked counter for the open floor behind it",()=>{
  const blocks=new Map([["-170,65,300","minecraft:smooth_quartz"],["-170,66,300","minecraft:smooth_quartz"],
    ["-170,64,300","minecraft:polished_andesite"],["-171,64,300","minecraft:polished_andesite"]]);
  const dimension={getBlock(at){const typeId=blocks.get(`${Math.floor(at.x)},${Math.floor(at.y)},${Math.floor(at.z)}`)??"minecraft:air";
    return {typeId,isAir:typeId==="minecraft:air",isLiquid:false};}};
  const moved=bankMannequinLocation(dimension,{x:-169.5,y:65,z:300.5},config.bounds);
  assert.deepEqual(moved,{x:-170.5,y:65,z:300.5});
  assert.equal(bankMannequinLocation(dimension,{x:10,y:65,z:10},config.bounds).x,10);
  blocks.set("-171,65,300","minecraft:light_block_12");
  blocks.set("-171,66,300","minecraft:light_block_12");
  const stayed=bankMannequinLocation(dimension,{x:-170.5,y:65,z:300.5},config.bounds);
  assert.deepEqual(stayed,{x:-170.5,y:65,z:300.5});
});
test("unrelated block and wrong dimension are not intercepted",async()=>{
  const h=harness(),p=h.player("block");
  for(const block of [{typeId:"minecraft:dirt",dimension:h.dimension,location:{x:-187,y:66,z:309}},
    {typeId:"minecraft:stone_button",dimension:{id:"minecraft:nether"},location:{x:-187,y:66,z:309}},
    {typeId:"minecraft:stone_button",dimension:h.dimension,location:{x:100,y:66,z:309}}]){
    const e={player:p,block,cancel:false};h.fire("block",e);assert.equal(e.cancel,false);
  }
  assert.equal(h.forms.length,0);
});

for(const [name,fn] of tests){await fn();console.log(`PASS ${name}`);}
console.log(`Bank UI ${tests.length} cases PASS; offline Script API/form contract only; PC/Android/iOS client unverified.`);
