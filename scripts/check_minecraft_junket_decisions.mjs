// Real reducers/runtime with a deterministic scheduler and fake UI transport.
// This proves routing/concurrency, not Bedrock client rendering or touch UX.
import assert from "node:assert/strict";
import { GameManager } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/game_manager.js";
import { QUALITY_GAMES } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/quality_cases.js";
import { createCompleteRuntime } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/complete_runtime.js";
import { installDecisionResume } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/decision_resume.js";
import { createRuntimeForms } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/venue_presentation.js";
const flush = async () => { for (let i=0;i<5;i++) await Promise.resolve(); };
function harness(id) {
  const game = QUALITY_GAMES.find(g => g.id === id), forms=[], handlers={}, jobs=new Map();
  let seq=0, tick=0;
  const players=Array.from({length:game.seats},(_,i)=>({id:`${id}-${i}`,name:`P${i+1}`,
    sendMessage() {},onScreenDisplay:{setActionBar(){}}}));
  const signal=name=>({subscribe(f){(handlers[name]??=[]).push(f);}});
  const world={getAllPlayers:()=>players,beforeEvents:{playerInteractWithEntity:signal('interact')},
    afterEvents:{playerLeave:signal('leave'),playerButtonInput:signal('button')}};
  const system={get currentTick(){return tick;},run:f=>system.runTimeout(f,1),
    runTimeout(f,n=1){const k=++seq;jobs.set(k,{f,at:tick+n});return k;},
    runInterval(f,n=1){const k=++seq;jobs.set(k,{f,at:tick+n,n});return k;},
    clearRun:k=>jobs.delete(k),afterEvents:{scriptEventReceive:signal('script')}};
  class Form {
    constructor(){this.buttons=[];this.fields=[];}
    title(v){this.name=v;return this;} body(v){this.text=v;return this;}
    button(v){this.buttons.push(v);return this;}
    textField(...v){this.fields.push(v);return this;} dropdown(...v){this.fields.push(v);return this;}
    toggle(...v){this.fields.push(v);return this;}
    show(player){return new Promise(resolve=>forms.push({player,form:this,resolve}));}
  }
  const manager=new GameManager(QUALITY_GAMES);
  const runtime=createCompleteRuntime({game,world,system,manager,ActionFormData:Form,ModalFormData:Form});
  async function advance(n=5){for(let i=0;i<n;i++){tick++;for(const [k,j] of [...jobs])if(j.at<=tick){
    if(j.n)j.at+=j.n;else jobs.delete(k); j.f();}await flush();}}
  function start(){for(const p of players)manager.join(id,id,p.id);if(game.registration)manager.start(id);runtime.resume();}
  async function reply(seat,response){const i=forms.findIndex(f=>f.player===players[seat]);assert.ok(i>=0,`${id}: menu seat${seat}`);
    forms.splice(i,1)[0].resolve(response);await flush();await advance();}
  return {game,forms,players,manager,runtime,world,system,Form,handlers,advance,start,reply,jobs};
}

for(const id of ['ace','grail','auction','fire','pig','light','mine','hope','clock','walker','garden','rabbit']){
  const h=harness(id);h.start();await h.advance(id==='light'?200:5);
  const current=h.manager.view(id), expected=current.decisionSeats;
  assert.deepEqual(h.forms.map(f=>h.players.indexOf(f.player)).sort(),[...expected].sort(),`${id}: exact decision owners`);
  const before=h.forms.length;h.runtime.resume();h.runtime.resume();await h.advance(5);
  assert.equal(h.forms.length,before,`${id}: repeated resume must not duplicate forms`);
  for(const f of h.forms.splice(0))f.resolve({canceled:true,cancelationReason:'UserClosed'});
  await flush();await h.advance(50);assert.equal(h.forms.length,0,`${id}: no cancel loop`);
}

// Reversed order simultaneous responses remain valid, repeated answers do not.
for(const id of ['fire','garden','grail']){
  const h=harness(id);h.start();await h.advance();
  const before=h.players.map(p=>h.manager.forPlayer(p.id));
  const request=id==='fire'?['choice',{number:1,heat:1}]:id==='garden'?['choose','double']:['arrange',{holyIndex:0}];
  h.manager.actDecision(h.players[1].id,before[1],...request);
  const first=id==='grail'?['arrange',{trueIndex:0}]:request;
  h.manager.actDecision(h.players[0].id,before[0],...first);
  assert.throws(()=>h.manager.actDecision(h.players[0].id,before[0],...first),/状況/);
  h.manager.close(id);for(const p of h.players)h.manager.join(id,id,p.id);
  assert.throws(()=>h.manager.actDecision(h.players[1].id,before[1],...request),/状況/,'old match rejected');
}

// Actual menu answers, not direct object operations, advance simultaneous Fire.
{
  const h=harness('fire');h.start();await h.advance();
  assert.equal(h.forms.length,2);await h.reply(1,{selection:0});await h.reply(0,{selection:1});
  assert.notEqual(h.manager.view('fire').state.phase,'choice');
}
// Mine's previously missing ticker must reach the next source phase unaided.
{
  const h=harness('mine');h.start();await h.advance();
  await h.reply(1,{selection:0});
  await h.reply(1,{selection:0});
  await h.reply(0,{selection:0});
  await h.reply(0,{selection:0});
  const state=h.manager.view('mine').state;assert.equal(state.phase,'reveal');
  await h.advance(state.phaseRemaining+1);
  assert.notEqual(h.manager.view('mine').state.phase,'reveal');
}
// Reset settles pending forms, late client answers cannot mutate a new match.
// Auction payment runs its repeated source damage clock before opening the
// next legal bidder menu; it must not wait for a non-existent physical button.
{
  const h=harness('auction');h.start();await h.advance();
  for(let i=0;i<4;i++){
    const seat=h.manager.view('auction').state.actor;
    await h.reply(seat,{selection:i});
  }
  const state=h.manager.view('auction').state;
  assert.equal(state.phase,'payment');assert.equal(h.forms.length,0);
  await h.advance(state.phaseRemaining+6);
  assert.equal(h.manager.view('auction').state.phase,'bid');
  assert.equal(h.forms.length,1);
  assert.ok(h.manager.view('auction').state.damage.some(d=>d>1700));
}
// Reset settles pending forms, late client answers cannot mutate a new match.
{
  const h=harness('clock');h.start();await h.advance();const old=h.forms.shift();
  const snapshot=h.manager.close('clock');await h.runtime.resetSession(snapshot);
  for(const p of h.players)h.manager.join('clock','clock',p.id);
  const before=h.manager.serialize();old.resolve({selection:0});await flush();
  assert.deepEqual(h.manager.serialize(),before);
}
// No permissions/inventory/NPC required: two deliberate crouch presses recall.
{
  const h=harness('walker');h.start();let opened=0;
  installDecisionResume({...h,runtimes:new Map([['walker',{open(p){assert.equal(p,h.players[0]);opened++;}}]])});
  const fire=()=>h.handlers.button[0]({player:h.players[0],button:'Sneak',newButtonState:'Pressed'});
  fire();await h.advance(2);fire();await h.advance(2);assert.equal(opened,1);
  fire();await h.advance(2);fire();await h.advance(2);assert.equal(opened,1,'debounce');
}
// Busy retry is bounded and respects reset; canceled forms never retry.
{
  const h=harness('ace');let calls=0;
  class Busy extends h.Form{show(){calls++;return Promise.resolve({canceled:true,cancelationReason:'UserBusy'});}}
  const wrap=createRuntimeForms({ActionFormData:Busy,...h});
  const promise=new wrap.ActionFormData().show(h.players[0]);await flush();await h.advance(40);await promise;
  assert.equal(calls,3);
}
console.log('PASS menu routing for12shared games; simultaneous reply/replay/session fences; Mine ticker; reset; mobile-compatible recall; bounded busy retry (mock UI, not client play)');
