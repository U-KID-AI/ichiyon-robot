import { TEMPERANCE_TURN_TICKS } from "./temperance.js";
import { createTemperanceVenue, TEMPERANCE_RECEPTION } from "./temperance_venue.js";
const TABLE="temperance", LABEL={arrange:"カードを並べる",target:"石を増減する対象者",choose:"カードを選ぶ",finished:"終了"};
const CHOICES=["＋1","−1","＋10","−10","＋2R","−2R"], VALUES=[1,-1,2,-2,3,-3];
export function createTemperanceRuntime({world,system,ActionFormData,ModalFormData,manager,installer}) {
  const venue=createTemperanceVenue({world}), busy=new Set();
  let deadline,air,reveal, generation=0, presenting=false;
  const table=()=>manager.view(TABLE);
  const tell=(p,m)=>{try{p?.sendMessage(`[ブルー・テンパランス] ${m}`);}catch{}};
  const members=t=>world.getAllPlayers().filter(p=>t.players.includes(p.id));
  const notify=(t,m)=>members(t).forEach(p=>tell(p,m));
  const status=t=>!t.state?"相手を待っています。":`ラウンド${t.state.round} / ${LABEL[t.state.phase]}\n石 ${t.state.stones.join(" : ")} / 体力 ${t.state.hp.map(n=>Math.max(0,n)).join(" : ")}`;
  function cancelTimers(){for(const id of [deadline,air,reveal]) if(id!==undefined) system.clearRun(id); deadline=air=reveal=undefined; ++generation;presenting=false;}
  function finish(t,reason){cancelTimers();manager.close(TABLE);notify(t,reason);try{venue.end(t);}catch(e){notify(t,e.message);} }
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
    tell(actor,t.state.phase==="choose"?"手番です。会場のカードを直接操作して選んでください。":"手番です。受付かカードを操作して入力画面を開いてください。");
  }
  function act(p,t,value){
    if(presenting)throw new Error("公開演出中です。");
    const next=manager.act(p.id,t.token,t.state.phase,value);
    if(deadline!==undefined)system.clearRun(deadline);
    if(t.state.phase==="choose") {
      presenting=true;visuals(next,true);
      notify(next,`${next.state.last.target+1}Pの石 ${next.state.last.amount>=0?"+":""}${next.state.last.amount} / ${status(next)}`);
      for(const member of members(next)){try{member.playSound("random.levelup");member.onScreenDisplay.setTitle("カード公開",{subtitle:`${next.state.last.target+1}P ${next.state.last.amount>=0?"+":""}${next.state.last.amount}`,stayDuration:45,fadeInDuration:5,fadeOutDuration:5});}catch{}}
      const token=next.token,g=generation;
      reveal=system.runTimeout(()=>{reveal=undefined;if(g!==generation||table()?.token!==token)return;presenting=false;const current=table();if(complete(current))return;visuals(current);arm(current);scheduleAir(current);},60);
    }else{visuals(next);arm(next);}
  }
  async function open(p){
    if(busy.has(p.id))return;busy.add(p.id);
    try{
      if(installer.installing())throw new Error("会場設置中です。");
      const mine=manager.forPlayer(p.id);if(mine&&mine.gameId!==TABLE)throw new Error("別のゲームに参加しています。");
      const t=table();
      const form=new ActionFormData().title("ブルー・テンパランス").body(`${t?status(t):"2人対戦。1・10・2Rのカードを正逆混在で伏せ、相手が対象者とカードを選びます。石の差で天秤が動き、体力が尽きた側が敗北。各入力は300秒以内。"}`);
      if(mine?.state&&mine.state.actor===mine.players.indexOf(p.id))form.button(LABEL[mine.state.phase]).button("退出して中止");
      else form.button(mine?"退出して中止":t?.state?"閉じる":"参加する");
      const response=await form.show(p);if(response.canceled)return;
      if((table()?.token??null)!==(t?.token??null)||(manager.forPlayer(p.id)?.token??null)!==(mine?.token??null))throw new Error("画面が古くなりました。");
      if(mine&&(!mine.state||mine.state.actor!==mine.players.indexOf(p.id)||response.selection===1)){finish(mine,"参加者が退出したため中止しました。");return;}
      if(!mine){if(t?.state)return;venue.ensure();const joined=manager.join(TABLE,TABLE,p.id);notify(joined,status(joined));if(joined.state){cancelTimers();try{venue.start(joined);arm(joined);}catch(e){finish(joined,e.message);}}return;}
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
    if(!e.target.hasTag(TEMPERANCE_RECEPTION)&&!e.target.hasTag("junket:temperance_control"))return;
    e.cancel=true;const t=manager.forPlayer(e.player.id), slot=e.target.getDynamicProperty("junket:temperance_slot");
    system.run(()=>{if(Number.isInteger(slot)&&t?.gameId===TABLE&&t.state?.phase==="choose"){try{act(e.player,t,slot);}catch(error){tell(e.player,error.message);}}else void open(e.player);});
  });
  world.afterEvents.playerLeave.subscribe(e=>{const mine=manager.forPlayer(e.playerId);if(mine?.gameId===TABLE)finish(mine,"参加者が切断したため中止しました。");busy.delete(e.playerId);});
  system.afterEvents.scriptEventReceive.subscribe(e=>{
    if(e.id!=="junket:temperance"||e.sourceEntity?.typeId!=="minecraft:player")return;
    const p=e.sourceEntity,command=e.message.trim();
    if(command==="menu"){void open(p);return;}
    if(command==="leave"){if(manager.forPlayer(p.id)?.gameId===TABLE)finish(table(),"参加者が退出したため中止しました。");return;}
    if(command==="install"){
      if(manager.tables.size){tell(p,"ゲーム参加者がいる間は設置できません。");return;}
      void installer.install(p,"temperance").then(m=>{venue.ensure();tell(p,m);}).catch(error=>tell(p,error.message));return;
    }
    try{if(command==="goto")venue.go(p);else if(command==="repair"&&!table()){venue.ensure();tell(p,"専用オブジェクトを補完しました。");}}catch(error){tell(p,error.message);}
  });
  return {open,venue};
}
