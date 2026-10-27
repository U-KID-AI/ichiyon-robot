import { createDecisionPrompts } from "./decision_prompts.js";
import { eligibleSpecialDecisions, specialDecisionWindow } from "./special_decisions.js";
import { isServiceGuide } from "./service_controls.js";
import { createRuntimeForms, requestCatalogReturn, showReceptionNavigation } from "./venue_presentation.js";
import { TEMPERANCE_TURN_TICKS } from "./temperance.js";
import { createTemperanceVenue, TEMPERANCE_RECEPTION } from "./temperance_venue.js";
const TABLE="temperance", LABEL={arrange:"カードを並べる",target:"石を増減する対象者",choose:"カードを選ぶ",finished:"終了"};
const CHOICES=["＋1","−1","＋10","−10","＋2R","−2R"], VALUES=[1,-1,2,-2,3,-3];
export function createTemperanceRuntime({world,system,ActionFormData,ModalFormData,manager,installer}) {
  const runtimeForms = createRuntimeForms({ ActionFormData, ModalFormData, world, system });
  ({ ActionFormData, ModalFormData } = runtimeForms);
  const venue=createTemperanceVenue({world}), busy=new Set();
  const disconnectTimers=new Map();
  let deadline,air,reveal, generation=0, presenting=false;
  const table=()=>manager.view(TABLE);
  const tell=(p,m)=>{try{p?.sendMessage(`[ブルー・テンパランス] ${m}`);}catch{}};
  const members=t=>world.getAllPlayers().filter(p=>t.players.includes(p.id));
  const allMembersOnline=t=>members(t).length===t.players.length;
  const notify=(t,m)=>members(t).forEach(p=>tell(p,m));
  const decisions=createDecisionPrompts({world,system,read:table,
    eligible:t=>(presenting||!allMembersOnline(t))?[]:(t.decisionSeats??eligibleSpecialDecisions(TABLE,t.state)),key:(t,seat)=>`${t.sessionToken}:${manager.forPlayer(t.players[seat])?.decisionKey??specialDecisionWindow(TABLE,t.state,seat)}:${t.decisionSerials?.[seat]??0}`,
    busy:id=>busy.has(id)||presenting,open:p=>open(p,false),notify:tell});

  const status=t=>!t.state?"相手を待っています。":`ラウンド${t.state.round} / ${LABEL[t.state.phase]}\n石 ${t.state.stones.join(" : ")} / 体力 ${t.state.hp.map(n=>Math.max(0,n)).join(" : ")}`;
  function cancelTimers(){for(const id of [deadline,air,reveal]) if(id!==undefined) system.clearRun(id); deadline=air=reveal=undefined; ++generation;presenting=false;}
  function finish(t,reason){runtimeForms.invalidate();busy.clear();decisions.clear();cancelTimers();manager.close(TABLE);notify(t,reason);try{venue.end(t);}catch(e){notify(t,e.message);} }
  function complete(t){if(t?.state?.phase!=="finished")return false;finish(t,t.state.winner===null?"引き分け。受付から再戦できます。":`${members(t).find(p=>p.id===t.players[t.state.winner])?.name??"相手"}の勝利（${t.state.reason}）。受付から再戦できます。`);return true;}
  function visuals(t,opened=false){try{venue.update(t,opened);}catch(e){notify(t,`会場表示: ${e.message}`);} }
  function scheduleAir(t,first=true){
    if(Math.abs(t.state.balance)!==101){if(air!==undefined)system.clearRun(air);air=undefined;return;}
    if(air!==undefined)return;
    const g=generation;
    air=system.runTimeout(()=>{
      air=undefined; if(g!==generation||!table())return;
      const next=manager.systemAct(TABLE,"air");if(complete(next))return;
      notify(next,"通気口が塞がれています。体力が減少しました。");if(!presenting)visuals(next);scheduleAir(next,false);
    },first?1:100);
  }
  function arm(t){
    if(deadline!==undefined)system.clearRun(deadline);
    const token=t.token,g=generation;
    deadline=system.runTimeout(()=>{if(g===generation&&table()?.token===token)complete(manager.systemAct(TABLE,"timeout"));},TEMPERANCE_TURN_TICKS);
    notify(t,status(t));
    const actor=members(t).find(p=>p.id===t.players[t.state.actor]);
    tell(actor,"手番です。操作メニューで選択してください。閉じた場合はゲーム操作パネルから再開できます。");
    decisions.schedule(t);
  }
  function act(p,t,value){
    if(presenting)throw new Error("公開演出中です。");
    if(!allMembersOnline(t))throw new Error("対戦相手の再接続を待っています。");
    const next=manager.actDecision?manager.actDecision(p.id,t,t.state.phase,value):manager.act(p.id,t.token,t.state.phase,value);
    if(deadline!==undefined)system.clearRun(deadline);
    if(t.state.phase==="choose") {
      presenting=true;visuals(next,true);
      notify(next,`${next.state.last.target+1}Pの石 ${next.state.last.amount>=0?"+":""}${next.state.last.amount} / ${status(next)}`);
      for(const member of members(next)){try{member.playSound("random.levelup");member.onScreenDisplay.setTitle("カード公開",{subtitle:`${next.state.last.target+1}P ${next.state.last.amount>=0?"+":""}${next.state.last.amount}`,stayDuration:45,fadeInDuration:5,fadeOutDuration:5});}catch{}}
      const token=next.token,g=generation;
      reveal=system.runTimeout(()=>{reveal=undefined;if(g!==generation||table()?.token!==token)return;presenting=false;const current=table();if(complete(current))return;visuals(current);arm(current);scheduleAir(current);},60);
    }else{visuals(next);arm(next);}
  }
  async function open(p,navigation=true){
    if(manager.maintenance){tell(p,"会場を確認中です。完了後に再操作してください。");return;}
    if(busy.has(p.id))return;busy.add(p.id);
    try{
      if(installer.installing())throw new Error("会場設置中です。");
      const mine=manager.forPlayer(p.id);if(mine&&mine.gameId!==TABLE)throw new Error("別のゲームに参加しています。");
      const t=table();
      const action=(!navigation&&mine?.state)?"act":await showReceptionNavigation({ActionFormData,player:p,gameId:TABLE,
        title:"ブルー・テンパランス",body:t?status(t):"参加者募集中です。",
        rules:"2人対戦。1・10・2Rのカードを正逆混在で伏せ、相手が対象者とカードを選びます。石の差で天秤が動き、体力が尽きた側が敗北。各入力は300秒以内。",
        joined:!!mine,canJoin:!mine&&!t?.state,
        canAct:!!mine?.state&&mine.state.actor===mine.players.indexOf(p.id)});
      if(!action)return;
      if((table()?.token??null)!==(t?.token??null)||(manager.forPlayer(p.id)?.token??null)!==(mine?.token??null))throw new Error("画面が古くなりました。");
      if(action==="catalog"){if(mine)tell(p,"退出を選んでからカタログへ戻ってください。");else await requestCatalogReturn(p, world);return;}
      if(action==="leave"&&mine){finish(mine,"参加者が退出したため中止しました。");return;}
      if(!mine){if(!["join","rematch"].includes(action)||t?.state)return;venue.ensure();const joined=manager.join(TABLE,TABLE,p.id);notify(joined,status(joined));if(joined.state){cancelTimers();try{venue.start(joined);arm(joined);}catch(e){finish(joined,e.message);}}return;}
      if(action!=="act")return;
      if(presenting)throw new Error("公開演出中です。");
      let value;
      if(mine.state.phase==="arrange"){
        const f=new ModalFormData().title("3枚を並べる：正逆を混ぜる");
        for(let i=0;i<3;i++)f.dropdown(`位置 ${i+1}`,CHOICES,{defaultValue:i*2+(i===2?1:0)});
        const r=await f.show(p);if(r.canceled)return;value=r.formValues?.map(i=>VALUES[i]);
      }else{
        const f=new ActionFormData().title(LABEL[mine.state.phase]);
        const labels=mine.state.phase==="target"?mine.players.map((id,i)=>`${i+1}P ${members(mine).find(p=>p.id===id)?.name??""}`):["カード1","カード2","カード3"];
        for(const label of labels)f.button(label);
        const r=await f.show(p);if(r.canceled)return;value=r.selection;
      }
      act(p,mine,value);
    }catch(e){tell(p,e.message);}finally{busy.delete(p.id);}
  }
  world.beforeEvents.playerInteractWithEntity.subscribe(e=>{
    if(isServiceGuide(world,e.target))return;
    if(!e.target.hasTag(TEMPERANCE_RECEPTION)&&!e.target.hasTag("junket:temperance_control"))return;
    e.cancel=true;const t=manager.forPlayer(e.player.id), slot=e.target.getDynamicProperty("junket:temperance_slot");
    system.run(()=>{if(Number.isInteger(slot)&&t?.gameId===TABLE&&t.state?.phase==="choose"){try{act(e.player,t,slot);}catch(error){tell(e.player,error.message);}}else void open(e.player);});
  });
  world.afterEvents.playerLeave.subscribe(e=>{
    runtimeForms.invalidate([e.playerId]);
    decisions.forget(e.playerId);
    busy.delete(e.playerId);
    const mine=manager.forPlayer(e.playerId);if(mine?.gameId!==TABLE)return;
    cancelTimers();
    const previous=disconnectTimers.get(e.playerId);
    if(previous!==undefined)system.clearRun(previous);
    const token=mine.token;
    const timer=system.runTimeout(()=>{
      disconnectTimers.delete(e.playerId);
      const live=manager.forPlayer(e.playerId);
      if(live?.token!==token)return;
      if(world.getAllPlayers().some(p=>p.id===e.playerId))return;
      finish(live,"参加者が切断したため中止しました。");
    },200);
    disconnectTimers.set(e.playerId,timer);
  });

  function waitForMissing(current){
    const session=manager.tables.get(current.id);
    for(const id of current.players){
      if(world.getAllPlayers().some(p=>p.id===id)||disconnectTimers.has(id))continue;
      disconnectTimers.set(id,system.runTimeout(()=>{
        disconnectTimers.delete(id);
        const live=manager.forPlayer(id);
        if(manager.tables.get(current.id)!==session||!live||world.getAllPlayers().some(p=>p.id===id))return;
        finish(live,"参加者が再接続しなかったため中止しました。");
      },200));
    }
  }
  function resume(playerId=null){
    if(playerId){
      const pending=disconnectTimers.get(playerId);
      if(pending!==undefined)system.clearRun(pending);
      disconnectTimers.delete(playerId);
    }
    cancelTimers();
    const current=table();
    if(!current)return;
    visuals(current);
    if(complete(current))return;
    if(!allMembersOnline(current)){waitForMissing(current);return;}
    arm(current);
    scheduleAir(current);
    const actor=members(current).find(p=>p.id===current.players[current.state.actor]);
    decisions.schedule(current);
  }
  system.afterEvents.scriptEventReceive.subscribe(e=>{
    if(e.id!=="junket:temperance"||e.sourceEntity?.typeId!=="minecraft:player")return;
    const p=e.sourceEntity,command=e.message.trim();
    if(command==="menu"){void open(p);return;}
    if(command==="leave"){if(manager.forPlayer(p.id)?.gameId===TABLE)finish(table(),"参加者が退出したため中止しました。");return;}
    if(command==="install"){
      if(manager.tables.size){tell(p,"ゲーム参加者がいる間は設置できません。");return;}
      try{venue.ensure();venue.verify();tell(p,"既存のブルー・テンパランス会場と操作物を検証・修復しました。");}
      catch(error){tell(p,error.message);}
      return;
    }
    try{if(command==="goto")venue.go(p);else if(command==="repair"&&!table()){venue.ensure();tell(p,"専用オブジェクトを補完しました。");}}catch(error){tell(p,error.message);}
  });
  async function openReception(player) {
    if (busy.has(player.id)) {
      tell(player, "別の操作画面を処理中です。画面やチャットを閉じて、受付ボタンを再操作してください。");
      return false;
    }
    await open(player);
    return true;
  }
  async function resetSession(current, reason = "会場をリセットしました。", { evacuated = false } = {}) {
    runtimeForms.invalidate();
    decisions.clear();
    cancelTimers();
    for (const timerId of disconnectTimers.values()) system.clearRun?.(timerId);
    disconnectTimers.clear();
    busy.clear();
    const resetTable = current ? { ...current, players: evacuated ? [] : [...current.players] } : { players: [], state: null };
    await venue.end(resetTable);
    if (current) notify(current, reason);
  }
  return {open,openReception,resetSession,venue,resume};
}
