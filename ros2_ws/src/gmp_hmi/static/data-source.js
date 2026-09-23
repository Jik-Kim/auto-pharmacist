/* 화면과 데이터 연결 분리. DemoSource는 HTTP/ROS 호출 없이 브라우저 메모리만 사용한다. */
(() => {
'use strict';
const config=window.HMI_CONFIG||{},copy=v=>JSON.parse(JSON.stringify(v)),now=()=>Date.now()/1000;
const queryString=filter=>{const q=new URLSearchParams();Object.entries(filter||{}).forEach(([k,v])=>{if(v!==''&&v!=null)q.set(k,v);});return q.size?'?'+q.toString():'';};
class HttpSource {
 constructor(){this.demo=false;this.recipes=config.recipes||[];this.recipeCache=null;this.csrf='';this.rttMs=null;this.failures=0;}
 async request(path,data,method){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000),started=performance.now();
  try{
   const options={cache:'no-store',credentials:'same-origin',signal:controller.signal};
   if(data!==undefined){options.method=method||'POST';options.headers={'Content-Type':'application/json','X-CSRF-Token':this.csrf};options.body=JSON.stringify(data);}
   const r=await fetch(path,options),body=await r.json();
   if(body.csrf_token)this.csrf=body.csrf_token;
   if(!r.ok){const e=Error(body.message||body.error||`HTTP ${r.status}`);e.status=r.status;throw e;}
   this.rttMs=performance.now()-started;this.failures=0;return body;
  }catch(e){this.failures++;throw e;}finally{clearTimeout(timer);}
 }
 async catalog(){this.recipeCache=await this.request('/recipes');this.recipes=this.recipeCache.map(r=>r.name);return this.recipes;}
 async recipe(name){if(!this.recipeCache)this.recipeCache=await this.request('/recipes');return this.recipeCache.find(r=>r.name===name)||null;}
 session(){return this.request('/auth/session')}
 login(data){return this.request('/auth/login',data)}
 logout(){return this.request('/auth/logout',{})}
 users(){return this.request('/users')}
 saveUser(name,data){return this.request('/users'+(name?'/'+encodeURIComponent(name):''),data)}
 settings(){return this.request('/settings')}
 saveSettings(data){return this.request('/settings',data)}
 status(){return this.request('/status')}
 history(filter){return this.request('/history'+queryString({...filter,limit:500}))}
 audit(filter){return this.request('/audit'+queryString(filter))}
 events(filter){return this.request('/events'+queryString(filter))}
 kpi(filter){return this.request('/kpi'+queryString(filter))}
 batch(id){return this.request('/batch/'+encodeURIComponent(id))}
 post(path,data){return this.request(path,data)}
 csvUrl(filter){return '/history.csv'+queryString(filter)}
 batchUrl(id){return '/batch/'+encodeURIComponent(id)+'/download'}
}
const RECIPES=[
 {name:'recipe-01',product:'레시피 1',items:[['A',40],['B',40],['C',40]]},
 {name:'recipe-02',product:'레시피 2',items:[['A',80],['B',40]]},
 {name:'recipe-03',product:'레시피 3',items:[['A',40],['B',40],['C',80]]},
].map(r=>({...r,total_g:r.items.reduce((sum,item)=>sum+item[1],0),items:r.items.map(([material_id,target_g])=>({material_id,target_g,tol_pct:5}))}));
const RECIPE=RECIPES[0];
class DemoSource {
 constructor(){this.demo=true;this.recipes=RECIPES.map(r=>r.name);this.recipeCache=copy(RECIPES);this.user={username:'DEMO-ADMIN',role:'admin',active:true};this.demoUsers=[this.user];this.reset('qa');}
 async catalog(){return this.recipes;}
 async recipe(name){return copy(RECIPES.find(r=>r.name===name)||null);}
 reset(scenario='qa'){
  this.scenario=scenario;this.sequence=1;this.discardAt=null;this.entered=false;this.auditRows=[];this.batches=[];this.pending=[];this.results=[];this.weights=[];this.scoopCycles=[];this.localSettings={inventory_material_ids:['A','B','C'],inventory_capacity_g:[1000,1000,1000],inventory_initial_g:[1000,1000,1000],inventory_low_pct:20,ui_stale_after_s:3,scope:'demo_memory_only',note:'데모 메모리에만 유지 · 실제 설정 아님'};this.elapsed=0;this.lastTick=now();this.lastSample=-1;this.debited=new Set();this.reservations={};this.inventory=['A','B','C'].map(material_id=>({material_id,capacity_g:1000,initial_g:1000,consumed_g:0,height_pct:null,height_low_latched:false,height_status:'측정 대기'}));this.current=null;this.activeRecipe=null;
  this.state={mode:'IDLE',step:'IDLE',batch_id:'',item_index:0,station:'safe',note:'레시피를 선택하고 주문을 제출하세요.',t:now()};
  if(scenario!=='empty')this.seedHistory();
  if(!['idle','empty','low_grams','height_low'].includes(scenario)){
   this.startBatch('B-001','OP-01');
   if(scenario==='qa'){
    this.elapsed=18;this.addResult(0,40);this.addResult(1,44);this.state={...this.state,mode:'DEVIATION',step:'WEIGH_RESIDUAL',item_index:1,note:'원료 B 과다 투입 · QA 판정을 기다립니다.'};
    this.weights=[{t:now(),net_g:44,gross_g:59,tare_g:15,std_g:.4,valid:true,station:'workbench',subject:'scoop',samples:30}];
    this.pending=[{deviation_id:'DEV-001',batch_id:'B-001',material_id:'B',kind:'OVERFILL',detail:'목표 40g / 실측 44g. 과다 투입분을 포함해 원료 B 44g이 차감되었습니다.',requires_decision:true,decision:'PENDING',t:now()}];
   }else{
    this.elapsed=10;this.updateProcess();
    if(scenario==='error')this.finish('ERROR','파지 재시도 한도 초과 · 담당자 확인 필요');
    if(scenario==='refill'||scenario==='contact')this.pause(scenario==='refill'?'REFILL':'NUDGE');
   }
  }
  if(scenario==='low_grams')this.inventory.find(i=>i.material_id==='A').initial_g=20;
  if(scenario==='height_low'){this.reportHeight('A',15);this.reportHeight('C',10);}
  this.lastTick=now();this.sync();
 }
 seedHistory(){
  for(let i=0;i<4;i++){const end=now()-600-i*500,start=end-300-i*15,b=this.makeBatch('B-'+String(100-i).padStart(3,'0'),start);b.finished_at=end;b.result=i===3?'ERROR':'DONE';b.items=RECIPE.items.map(it=>({...it,batch_id:b.batch_id,actual_g:it.target_g,error_pct:0,verdict:'OK',attempts:1,t:start+15}));b.weights=[{t:start+30,gross_g:55,tare_g:15,net_g:40,valid:true,station:'workbench',subject:'scoop',samples:30}];b.events=[{t:start,level:'INFO',code:'BATCH_START',text:'예시 배치 시작'},{t:end,level:i===3?'ERROR':'INFO',code:i===3?'INTERVENTION_FORCED':'BATCH_END',text:'이전 운전 예시'}];b.scoop_cycles=RECIPE.items.map(it=>this.cycle(b.batch_id,it.material_id,it.target_g,start+40));if(i===1)b.deviations=[{deviation_id:'DEV-H1',batch_id:b.batch_id,material_id:'B',kind:'WEIGH_INVALID',decision:'AUTO_RECOVERED',operator_id:'',detail:'재계량으로 자동 복구',raised_at:start+100}];this.batches.push(b);}
 }
 makeBatch(id,t,recipe=RECIPE){return {batch_id:id,product:recipe.product,started_at:t,finished_at:null,result:null,items:[],weights:[],deviations:[],events:[],scoop_cycles:[],note:''};}
 startBatch(id,actor,recipe=RECIPE){
  this.discardAt=null;this.elapsed=0;this.lastTick=now();this.lastSample=-1;this.entered=false;this.debited=new Set();this.pending=[];this.results=[];this.weights=[];this.scoopCycles=[];this.activeRecipe=copy(recipe);
  this.reservations=Object.fromEntries(recipe.items.map(it=>[it.material_id,it.target_g]));
  const id0=recipe.items[0].material_id;this.state={mode:'RUNNING',step:'PICK_SCOOP',batch_id:id,item_index:0,station:'scoop_'+(id0.charCodeAt(0)-64),note:`원료 ${id0} 처리 시작`,t:now()};
  this.current=this.makeBatch(id,now(),recipe);this.current.events.push({t:now(),level:'INFO',code:'BATCH_START',text:'데모 배치 시작'});this.batches.unshift(this.current);this.record(actor,'HMI_ORDER',id,recipe.name);
 }
 addResult(index,actual){
  const it=this.activeRecipe.items[index];if(this.debited.has(it.material_id))return;this.debited.add(it.material_id);
  const inv=this.inventory.find(x=>x.material_id===it.material_id);inv.consumed_g+=actual;delete this.reservations[it.material_id];
  const attempts=Math.max(1,Math.ceil(it.target_g/40));
  this.results.push({batch_id:this.state.batch_id,material_id:it.material_id,target_g:it.target_g,actual_g:actual,error_pct:(actual-it.target_g)/it.target_g*100,verdict:actual>it.target_g*1.05?'OVER':'OK',attempts,t:now()});
  for(let attempt=1;attempt<=attempts;attempt++)this.scoopCycles.push(this.cycle(this.state.batch_id,it.material_id,actual/attempts,now(),attempt,actual/attempts*(attempt-1)));
  this.current.events.push({t:now(),level:'INFO',code:'DISPENSE_RESULT',text:`원료 ${it.material_id} ${actual}g 사용`});
 }
 updateProcess(){
  if(this.heightBlocked().length){this.holdHeight();return;}
  const items=this.activeRecipe.items,finishStart=items.length*9,endAt=finishStart+9;
  for(let i=0;i<items.length;i++)if(this.elapsed>=i*9+7)this.addResult(i,items[i].target_g);
  if(this.elapsed>=endAt){this.finish('DONE',items.map(it=>it.material_id).join(' · ')+' 분주 및 최종 검증 완료. 다음 주문을 제출할 수 있습니다.');return;}
  if(this.elapsed>=finishStart){this.state.step=this.elapsed<finishStart+5?'VERIFY_FINAL':'FINISH';this.state.station=this.elapsed<finishStart+5?'workbench':'passbox_done';this.state.item_index=items.length-1;this.state.note=this.elapsed<finishStart+5?'최종 계량 확인 중':'완성 용기를 배출 트레이로 이동 중';}
  else{const i=Math.floor(this.elapsed/9),phase=this.elapsed%9,id=items[i].material_id,n=id.charCodeAt(0)-64;this.state.item_index=i;this.state.step=phase<2?'PICK_SCOOP':phase<3?'SCOOP_TARE':phase<4?'SCOOP':phase<5?'WEIGH_SCOOP':phase<6?'POUR':phase<7?'WEIGH_RESIDUAL':'RETURN_SCOOP';this.state.station=phase<2||phase>=7?'scoop_'+n:phase<4?'material_'+n:'workbench';this.state.note=`원료 ${id} 처리 중 · 데모는 약 ${endAt}초에 완료됩니다.`;}
  const slot=Math.floor(this.elapsed*2);if(slot!==this.lastSample){this.lastSample=slot;const target=this.elapsed>=finishStart?this.results.reduce((sum,r)=>sum+r.actual_g,0):items[this.state.item_index].target_g,net=Math.round((target+Math.sin(this.elapsed*2)*.3)*10)/10;this.weights.push({t:now(),net_g:net,gross_g:net+15,tare_g:15,std_g:.4,valid:true,station:this.state.station,subject:this.elapsed>=finishStart?'container':'scoop',samples:30});this.weights=this.weights.slice(-100);}
 }
 advance(){const t=now(),dt=Math.max(0,t-this.lastTick);this.lastTick=t;if(this.discardAt!==null){if(this.state.mode==='PAUSED'||this.scenario==='offline'||this.heightBlocked().length)this.discardAt+=dt;else if(t>=this.discardAt)this.finish('DISCARDED','QA 폐기 작업 완료 · 사용한 원료는 재고로 복원하지 않습니다.');}else if(this.state.mode==='RUNNING'&&this.scenario!=='offline'){if(this.heightBlocked().length)this.holdHeight();else{this.elapsed+=dt;this.updateProcess();}}this.sync();}
 finish(result,note){if(this.current?.finished_at!=null)return;this.discardAt=null;this.reservations={};this.state.mode=result==='ERROR'?'ERROR':'DONE';this.state.step=result==='ERROR'?'ERROR':'DONE';this.state.note=note;this.state.station=result==='DISCARDED'?'reject_bin':result==='DONE'?'passbox_done':'safe';this.current.finished_at=now();this.current.result=result;this.current.note=note;this.current.events.push({t:now(),level:result==='ERROR'?'ERROR':'INFO',code:result==='ERROR'?'INTERVENTION_FORCED':result==='DISCARDED'?'BATCH_DISCARDED':'BATCH_END',text:note});this.sync();}
 pause(reason,grantEntry=reason!=='NUDGE'){
  if(this.state.mode!=='PAUSED')this.saved=copy(this.state);this.entered=grantEntry;
  this.state={...this.state,mode:'PAUSED',step:'PAUSED',pause_reason:reason,demo_entry_granted:grantEntry};
  this.state.note=!grantEntry?(reason==='HEIGHT_LOW'?'높이 부족으로 공정 대기 · ENTER 허가 후 해당 원료를 보충하세요.':'접촉 정지 · 진입 허가 아님. 데모 다시 접촉으로 재개하세요.'):'데모 진입 허가 · 해당 원료 보충 완료 후 EXIT로 재개하세요.';
 }
 heightBlocked(){return this.inventory.filter(i=>i.height_low_latched).map(i=>i.material_id);}
 holdHeight(){if(['RUNNING','DEVIATION'].includes(this.state.mode))this.state.note='높이 부족으로 다음 공정 대기 · ENTER 허가 후 해당 원료를 보충하세요.';}
 reportHeight(materialId,pct){
  const i=this.inventory.find(i=>i.material_id===materialId);if(!i||typeof pct!=='number'||!Number.isFinite(pct)||pct<0||pct>100)return {ok:false,message:'높이값은 0~100%입니다.'};
  i.height_pct=pct;i.height_status='데모 높이 신호';if(pct<20){i.height_low_latched=true;this.holdHeight();}
  this.record(this.user.username,'HMI_DEMO_HEIGHT',materialId,`높이 ${pct}% · 만충 확인 전까지 부족 유지`);return {ok:true,message:`원료 ${materialId} 높이 ${pct}% 입력${i.height_low_latched?' · 보충 완료 필요':''}`};
 }
 async injectHeight(materialId,pct){this.advance();if(!['operator','admin'].includes(this.user.role))return {ok:false,message:'조작 권한이 없습니다.'};if(this.scenario==='offline')return {ok:false,message:'상태 수신이 중단되었습니다.'};return this.reportHeight(materialId,pct);}
 async nudge(){this.advance();if(this.entered||this.state.mode!=='PAUSED'||this.state.pause_reason!=='NUDGE')return {ok:false,message:'접촉 정지 상태에서만 다시 접촉으로 재개할 수 있습니다.'};if(this.heightBlocked().length)return {ok:false,message:'높이 부족 원료를 먼저 보충하세요.'};this.state=this.saved;this.lastTick=now();this.record('DEMO','HMI_DEMO_NUDGE',this.state.batch_id,'데모 다시 접촉으로 운전 재개');return {ok:true,message:'데모 다시 접촉 · 운전을 재개합니다.'};}
 record(actor,action,target,detail){this.auditRows.unshift({t:now(),actor,action,target,detail});}
 sync(){if(this.current){this.current.items=copy(this.results);this.current.weights=copy(this.weights);this.current.scoop_cycles=copy(this.scoopCycles);this.current.deviations=copy(this.pending);}}
 inventoryStatus(){
  const fresh=this.scenario!=='offline',canRefill=['IDLE','DONE'].includes(this.state.mode)||(this.state.mode==='PAUSED'&&this.entered);
  return {mode:'demo',enforced:true,fresh,refill_supported:true,can_refill:canRefill,refill_ready:fresh,blocked_materials:this.heightBlocked(),order_allowed:fresh&&!this.heightBlocked().length,order_block_reason:this.heightBlocked().length?'높이 부족 원료의 만충 확인이 필요합니다.':'',items:this.inventory.map(i=>{
   const remaining=Math.max(0,i.initial_g-i.consumed_g),reserved=this.reservations[i.material_id]||0;
   return {...i,remaining_g:remaining,reserved_g:reserved,available_g:Math.max(0,remaining-reserved),percent:remaining/i.capacity_g*100,low:remaining/i.capacity_g*100<this.localSettings.inventory_low_pct,refill_ready:fresh,valid:true};
  }),note:'g 잔량은 1,000g 만충 설정값에서 사용량을 뺀 추정치입니다. 로봇 높이는 별도 신호이며, 데모에서는 예시값만 입력합니다.'};
 }
 async refill(materialId,confirmedFull){
  this.advance();if(!['operator','admin'].includes(this.user.role))return {ok:false,message:'조작 권한이 없습니다.'};
  const inv=this.inventoryStatus(),i=this.inventory.find(i=>i.material_id===materialId);
  if(!i||confirmedFull!==true)return {ok:false,message:'원료를 선택하고 만충 보충을 확인하세요.'};
  if(!inv.fresh||!inv.can_refill)return {ok:false,message:'최신 상태에서 정지 및 데모 진입 허가 후 보충하세요.'};
  const before=Math.max(0,i.initial_g-i.consumed_g);i.initial_g=i.capacity_g+i.consumed_g;i.height_pct=null;i.height_status='보충 확인 · 재측정 대기';i.height_low_latched=false;
  this.record(this.user.username,'HMI_DEMO_REFILL',materialId,`${before}g → ${i.capacity_g}g, 개별 만충 확인`);
  return {ok:true,message:`원료 ${materialId}만 ${i.capacity_g}g·100%로 반영했습니다.${this.state.mode==='PAUSED'?' PAUSED 유지 · 다른 부족 해소 후 EXIT로 재개하세요.':''}`};
 }
 async status(){this.advance();const age=this.scenario==='offline'?30:0;this.state.t=now()-age;return copy({state:this.state,active_recipe:this.activeRecipe,inventory:this.inventoryStatus(),results:this.results,weights:this.weights,deviations:this.pending,scoop_cycles:this.scoopCycles,events:this.current?.events||[],gripper:{width_mm:32.4,grip:this.state.mode==='RUNNING',backend:'virtual',force_n:20,busy:false},freshness:{state_age_s:age,gripper_age_s:age,stale_after_s:this.localSettings.ui_stale_after_s},diagnostics:{namespace:'/cell',actions:{run_batch:true},services:{qa_decision:true,interlock:true},topics:Object.fromEntries(['state','weight','gripper','dispense_result','deviation','scoop_cycle','event'].map(k=>[k,{age_s:age,count:k==='dispense_result'?this.results.length:k==='deviation'?this.pending.length:1}]))},now:now()});}
 async history(filter={}){this.advance();return copy(this.filteredBatches(filter).map(b=>({...b,n_items:b.items.length,n_dev:b.deviations.length,cycle_s:b.finished_at===null?null:b.finished_at-b.started_at})));}
 async batch(id){this.advance();const b=this.batches.find(x=>x.batch_id===id);if(!b)throw Error('배치 없음');return copy(b);}
 async audit(filter={}){return copy(this.auditRows.filter(r=>this.matches(r,filter)&&(!filter.actor||r.actor.includes(filter.actor))&&(!filter.action||r.action.includes(filter.action))));}
 async kpi(filter={}){this.advance();const list=this.filteredBatches(filter),ended=list.filter(b=>b.finished_at!==null),devs=list.flatMap(b=>b.deviations),forced=list.flatMap(b=>b.events).filter(e=>e.code==='INTERVENTION_FORCED').length,run=ended.reduce((s,b)=>s+b.finished_at-b.started_at,0);return {batches:ended.length,batch_success_pct:ended.length?ended.filter(b=>b.result==='DONE').length/ended.length*100:null,unmeasured_done:ended.filter(b=>b.result==='DONE_UNMEASURED').length,unmeasured_done_pct:ended.length?ended.filter(b=>b.result==='DONE_UNMEASURED').length/ended.length*100:null,run_complete_pct:ended.length?ended.filter(b=>b.result==='DONE'||b.result==='DONE_UNMEASURED').length/ended.length*100:null,deviations:devs.length,auto_recovery_pct:devs.length?devs.filter(d=>d.decision==='AUTO_RECOVERED').length/devs.length*100:null,run_time_s:run,forced_interventions:forced,mtbi_s:forced?run/forced:run};}
 cycle(batch_id,material_id,delivered,t=now(),attempt=1,actualBefore=0){
  const reading=n=>({t,station:'workbench',subject:'scoop',net_g:n,gross_g:n+35,tare_g:35,std_g:.3,samples:30,valid:true});
  return {t,batch_id,material_id,attempt,target_g:(this.activeRecipe||RECIPE).items.find(i=>i.material_id===material_id)?.target_g||delivered,actual_before_g:actualBefore,scoop_tare:reading(0),pre_pour:reading(delivered+4),post_pour:reading(4),commanded_pour_fraction:.8,delivered_g:delivered,weigh_method:2,weigh_pose_id:'workbench',tool_name:'scoop',tcp_name:'scoop_tcp',contact_detected:true,max_contact_force_n:8.2,insertion_depth_mm:30,grip_width_mm:32.4,tare_wrench:[0,0,-.3,0,0,0],pre_pour_wrench:[.1,.2,-2.6,0,.1,0],post_pour_wrench:[.1,.1,-.34,0,0,0],tare_wrench_std:[.01,.01,.02,.01,.01,.01],pre_pour_wrench_std:[.01,.01,.02,.01,.01,.01],post_pour_wrench_std:[.01,.01,.02,.01,.01,.01],tare_wrench_samples:30,pre_pour_wrench_samples:30,post_pour_wrench_samples:30,tare_wrench_valid:true,pre_pour_wrench_valid:true,post_pour_wrench_valid:true,reference_valid:false,reference_delivered_g:0,reference_std_g:0,reference_source:'',outcome:0,valid:true,duration_s:8};
 }
 matches(row,filter){const t=row.t??row.started_at??row.raised_at;return (!filter.start||t>=Number(filter.start))&&(!filter.end||t<Number(filter.end))&&(!filter.query||JSON.stringify(row).toLowerCase().includes(filter.query.toLowerCase()));}
 filteredBatches(filter={}){return this.batches.filter(b=>this.matches(b,filter)&&(!filter.result||(filter.result==='RUNNING'?!b.result:b.result===filter.result)));}
 async events(filter={}){const rows=this.batches.flatMap(b=>b.events.map(e=>({...e,batch_id:b.batch_id})));return copy(rows.filter(r=>this.matches(r,filter)&&(!filter.level||r.level===filter.level)).sort((a,b)=>b.t-a.t));}
 async session(){return {authenticated:true,user:copy(this.user),csrf_token:'DEMO-NO-HTTP',setup_required:false};}
 async login(){return this.session();}
 async logout(){return this.session();}
 setRole(role){this.user={username:'DEMO-'+role.toUpperCase(),role,active:true};}
 async users(){if(this.user.role!=='admin')throw Error('관리자 권한이 필요합니다.');return copy(this.demoUsers);}
 async saveUser(name,data){if(this.user.role!=='admin')throw Error('관리자 권한이 필요합니다.');const username=name||data.username;if(!/^[A-Za-z0-9_.-]{1,64}$/.test(username))throw Error('계정 이름은 영문·숫자·밑줄·점·하이픈 1~64자입니다.');const existing=this.demoUsers.find(u=>u.username===username);if(!name&&existing)throw Error('이미 존재하는 계정입니다.');if(!name&&((data.password||'').length<10||(data.password||'').length>128))throw Error('비밀번호는 10~128자로 입력하세요.');const user={username,role:data.role,active:data.active};if(existing)Object.assign(existing,user);else this.demoUsers.push(user);this.record(this.user.username,'HMI_USER_UPDATE',username,'데모 계정 변경');return {ok:true,message:'데모 메모리에 반영했습니다. 실제 계정은 생성되지 않습니다.'};}
 async settings(){return copy(this.localSettings);}
 async saveSettings(data){
  if(this.user.role!=='admin')throw Error('관리자 권한이 필요합니다.');
  if(!['IDLE','DONE','ERROR'].includes(this.state.mode))throw Error('운전 또는 일시 정지 중에는 초기 재고 기준을 변경할 수 없습니다.');
  if(JSON.stringify(data.inventory_material_ids)!==JSON.stringify(['A','B','C'])||data.inventory_capacity_g.some(v=>v!==1000))throw Error('V4 원료 A/B/C 만충 기준은 각각 1,000g입니다.');
  this.localSettings={...this.localSettings,...copy(data)};
  // 표시용 설정 변경은 재고/높이 래치를 해제하지 않는다. 만충 확인은 개별 보충 API만 사용한다.
  this.record(this.user.username,'HMI_SETTINGS','local','데모 표시 설정 변경 · 현재 재고 유지');return {...copy(this.localSettings),ok:true,message:'데모 표시 설정을 저장했습니다. 현재 재고·높이 부족 상태는 유지됩니다.'};
 }
 async post(path,d){this.advance();if(this.scenario==='offline')return {ok:false,message:'상태 수신이 중단되었습니다.'};d.actor=this.user.username;const allowed=['/qa','/collection-confirm'].includes(path)?['qa','admin']:['operator','admin'];if(!allowed.includes(this.user.role))return {ok:false,message:'현재 데모 역할에는 조작 권한이 없습니다.'};
  if(path==='/collection-confirm'){
   if(!d.passbox_done_empty||!d.reject_bin_empty)return {ok:false,message:'완성품 패스박스와 폐기함을 모두 비웠음을 확인하세요.'};
   this.record(d.actor,'HMI_COLLECTION_CONFIRMED','collection','데모 회수 확인 · 적재 카운터 연동 없음');return {ok:true,message:'데모 회수 확인을 기록했습니다.'};
  }
  if(path==='/test/refill')return this.refill(d.material_id,d.confirmed_full);
  if(path==='/order'){
   if(!['IDLE','DONE'].includes(this.state.mode)||(this.state.mode==='DONE'&&this.state.step!=='DONE')||this.entered)return {ok:false,message:'실행 또는 진입 중인 배치가 있습니다.'};const recipe=RECIPES.find(r=>r.name===d.recipe);if(!recipe)return {ok:false,message:'알 수 없는 레시피입니다.'};
   const inv=this.inventoryStatus(),shortages=recipe.items.filter(it=>{const item=inv.items.find(x=>x.material_id===it.material_id);return !item||item.available_g<it.target_g;});
   if(this.heightBlocked().length)return {ok:false,message:'원료 '+this.heightBlocked().join(' · ')+' 높이 부족 · 만충 확인 후 주문하세요.'};
   if(shortages.length)return {ok:false,message:'원료 '+shortages.map(x=>x.material_id).join(' · ')+' 재고 부족. 해당 원료 보충 후 주문하세요.'};
   const id='B-DEMO-'+(++this.sequence);this.scenario='running';this.startBatch(id,d.actor,recipe);return {ok:true,message:`데모 주문 접수 · 약 ${recipe.items.length*9+9}초 후 완료`,batch_id:id};
  }
  if(path==='/qa'){
   if(this.heightBlocked().length)return {ok:false,message:'높이 부족 원료의 보충 완료 후 QA 판정을 진행하세요.'};
   if(!['1','2'].includes(String(d.decision)))return {ok:false,message:'알 수 없는 판정입니다.'};const dev=this.pending.find(x=>x.deviation_id===d.deviation_id&&x.decision==='PENDING');if(!dev||this.state.mode!=='DEVIATION')return {ok:false,message:'판정 대기 일탈이 없습니다.'};dev.decision=String(d.decision)==='1'?'APPROVED':'DISCARDED';dev.operator_id=d.actor;dev.decided_at=now();if(dev.decision==='APPROVED'){this.state.mode='RUNNING';this.state.step='RETURN_SCOOP';this.state.note='QA 승인 · 다음 원료 처리로 진행합니다.';this.lastTick=now();}else{this.state.mode='RUNNING';this.state.step='DISCARD_FINISH';this.state.station='reject_bin';this.state.note='QA 폐기 수락 · 용기 폐기 완료를 기다립니다.';this.discardAt=now()+2;}this.record(d.actor,dev.decision==='APPROVED'?'HMI_QA_APPROVE':'HMI_QA_DISCARD',d.deviation_id,'데모 판정 요청');this.sync();return {ok:true,message:'데모 판정 접수'};
  }
  if(path==='/interlock'){
   if(!['1','2'].includes(String(d.request)))return {ok:false,message:'알 수 없는 요청입니다.'};if(String(d.request)==='1'){if(this.entered)return {ok:false,message:'이미 데모 진입이 허가되었습니다.'};this.pause(d.reason||'REFILL',true);}else{if(!this.entered)return {ok:false,message:'처리된 진입 요청이 없습니다.'};if(this.heightBlocked().length)return {ok:false,message:'높이 부족 원료 '+this.heightBlocked().join(' · ')+'의 보충 완료가 필요합니다.'};this.state=this.saved;this.entered=false;this.lastTick=now();}this.record(d.actor,String(d.request)==='1'?'HMI_INTERLOCK_ENTER':'HMI_INTERLOCK_EXIT',this.state.batch_id,d.reason);return {ok:true,message:String(d.request)==='1'?'데모 진입 허가':'데모 재개 요청 수락'};
  }
  return {ok:false,message:'지원하지 않는 요청'};
 }
}
window.hmiSource=config.demo===true?new DemoSource():new HttpSource();
})();
