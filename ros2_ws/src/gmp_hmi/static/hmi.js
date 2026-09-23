/* 공정 의미를 추정하지 않고 API 필드를 표시한다. 서버 문자열은 모두 escape한다. */
const $=id=>document.getElementById(id),source=window.hmiSource;
const escapeHtml=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number=(v,d=1)=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toFixed(d);
const clock=t=>t==null?'—':new Date(t*1000).toLocaleTimeString('ko-KR',{hour12:false});
const dateTime=t=>t==null?'—':new Date(t*1000).toLocaleString('ko-KR',{hour12:false});
const badge=(text,type='neutral')=>`<span class="badge ${type}">${escapeHtml(text)}</span>`;
const verdict=v=>badge(v||'진행',v==='OK'||v==='DONE'||v==='AUTO_RECOVERED'?'good':v==='ERROR'||v==='DISCARDED'||v==='INVALID'?'bad':v==='OVER'||v==='UNDER'||v==='PENDING'?'warn':'info');
let cancelBatchId='';
let session={authenticated:false,user:null},userEdit='',currentSettings=null,alarmRows=[],alarmBusy=false;
let snapshot={},fresh=false,inFlight=false,selectedBatch='',detailTab='items',detailData=null,detailVersion=0,recordBusy=false,page='operation',lastWeights=[],selectedRecipe=null,recipeVersion=0,lastBatchMode='',acknowledgedShortages=new Set(),refillMaterial='';
const modes={IDLE:'주문 대기',RUNNING:'운전 중',PAUSED:'일시 정지',DEVIATION:'QA 판정 대기',ERROR:'공정 오류',DONE:'배치 완료',DISCARDED:'QA 폐기 종료',FINISH_PENDING:'완료 확인 대기'};
function arrangeOperationColumns(){
 const operation=$('operation'),columns=operation?[...operation.children].filter(el=>el.classList.contains('column')):[];
 if(!operation||operation.dataset.fourColumns==='true'||columns.length!==3)return;
 const [,center]=columns,results=$('results')?.closest('section');
 if(!results)return;
 const resultsColumn=document.createElement('div');resultsColumn.className='column';
 operation.insertBefore(resultsColumn,center);resultsColumn.append(results);
 operation.dataset.fourColumns='true';
}
arrangeOperationColumns();
function arrangeRecordOverview(){
 const kpis=$('kpis'),history=$('history'),historyCard=history?.closest('section');
 if(!kpis||!history||!historyCard||historyCard.classList.contains('history-overview-card'))return;
 historyCard.classList.add('history-overview-card');
 historyCard.insertBefore(kpis,history);
}
arrangeRecordOverview();
function table(headers,rows){return rows.length?`<table><thead><tr>${headers.map(h=>`<th>${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`:'<div class="empty">표시할 기록이 없습니다.</div>';}
const itemRows=rows=>rows.map(r=>[escapeHtml(r.material_id),number(r.target_g),number(r.actual_g),number(r.error_pct,2),verdict(r.verdict),escapeHtml(r.attempts)]);
function visibleTargetBand(s,rows,subject){
 const b=s.target_band;
 if(!b||subject!=='container'||!s.state?.batch_id||b.batch_id!==s.state.batch_id||!rows.length)return null;
 if(![b.lower_g,b.target_g,b.upper_g].every(Number.isFinite))return null;
 if(rows.some(w=>w.subject!=='container'||w.observed_batch_id!==b.batch_id))return null;
 return b;
}
function renderBatchControls(s){
 const c=s.batch_control||{};
 $('cancelHint').textContent=c.can_cancel?'현재 배치에 취소 요청 가능 · 일시정지와 다릅니다':c.reason||'이 HMI에서 수락받은 진행 중 배치가 없습니다';
 $('inventoryContract').hidden=source.demo||s.inventory?.mode==='test_process';
 $('inventoryContract').textContent=s.integration?.inventory?.message||'운영 재고·보충 계약 미정 · 실제 재고에 반영하지 않습니다';
 const b=s.target_band,rows=filteredWeights(s.weights||[]),shown=visibleTargetBand(s,rows,$('weightSubject').value);
 $('targetBandHint').textContent=b?`용기 최종 목표 ${number(b.target_g)} g · 허용 ${number(b.lower_g)}–${number(b.upper_g)} g · ${shown?'초록 점선/영역 · 중간 계량 판정 아님':'용기를 선택하고 동일 배치의 측정값을 기다리세요'}`:'수락 레시피가 없어 목표 기준 미확인 · 선택 메뉴만 바꿔 목표를 만들지 않습니다';
}
async function loadRestartRecords(){
 if(source.demo){$('restartList').textContent='데모는 영구 기록이 없습니다';return;}
 try{const r=await source.request('/restart-state');$('restartList').innerHTML=table(['배치','마지막 단계','시각','기록'],r.records.map(b=>[escapeHtml(b.batch_id),escapeHtml(b.checkpoint?.step||'기록 없음'),escapeHtml(dateTime(b.checkpoint?.t)),`<button type="button" data-restart-batch="${escapeHtml(b.batch_id)}">기록 보기</button>`]));}
 catch(e){handleAuthError(e);$('restartList').textContent='조회 실패 · '+e.message;}
}
$('refreshRestart').onclick=loadRestartRecords;
$('restartList').onclick=e=>{const b=e.target.closest('[data-restart-batch]');if(b){showPage('records');selectBatch(b.dataset.restartBatch);}};
$('openCancelBatch').onclick=()=>{cancelBatchId=snapshot.state?.batch_id||'';$('cancelBatchText').textContent='대상 배치: '+cancelBatchId;$('cancelBatchConfirmed').checked=false;$('cancelBatchDialog').showModal();gate();};
$('closeCancelBatch').onclick=()=>$('cancelBatchDialog').close();
$('cancelBatchConfirmed').onchange=gate;
$('cancelBatchForm').onsubmit=e=>{e.preventDefault();$('cancelBatchDialog').close();$('cancelMsg').hidden=false;command('/batch/cancel',{batch_id:cancelBatchId,confirmed:$('cancelBatchConfirmed').checked},'cancelMsg');};
function draw(ws){lastWeights=ws;const c=$('chart'),dpr=window.devicePixelRatio||1,w=c.clientWidth||500,h=c.clientHeight||190;c.width=w*dpr;c.height=h*dpr;const ctx=c.getContext('2d');ctx.scale(dpr,dpr);const left=38,top=12,right=w-12,bottom=h-25,finite=ws.filter(v=>Number.isFinite(v.net_g)),band=visibleTargetBand(snapshot,ws,$('weightSubject').value),min=Math.min(0,...finite.map(v=>v.net_g),...(band?[band.lower_g]:[])),max=Math.max(1,...finite.map(v=>v.net_g),...(band?[band.upper_g]:[]))*1.12;ctx.font='10px Arial';ctx.lineWidth=1;if(!ws.length){ctx.fillStyle='#8795a8';ctx.fillText('계량 수신 대기',w/2-35,h/2);return;}for(let i=0;i<=4;i++){const y=top+(bottom-top)*i/4;ctx.strokeStyle='#e7edf5';ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.fillStyle='#71819a';ctx.fillText(number(max-(max-min)*i/4,0),2,y+4);}const x=i=>left+i*(right-left)/Math.max(ws.length-1,1),y=v=>bottom-(v-min)/(max-min)*(bottom-top);if(band){ctx.fillStyle='rgba(24,150,100,.12)';ctx.fillRect(left,y(band.upper_g),right-left,y(band.lower_g)-y(band.upper_g));ctx.strokeStyle='#15845a';ctx.setLineDash([5,4]);ctx.beginPath();ctx.moveTo(left,y(band.target_g));ctx.lineTo(right,y(band.target_g));ctx.stroke();ctx.setLineDash([]);ctx.fillStyle='#15734f';ctx.fillText('최종 목표 '+number(band.target_g)+' g',left+5,y(band.target_g)-5);}ctx.strokeStyle='#1957ed';ctx.lineWidth=2.4;ctx.beginPath();let connect=false;ws.forEach((v,i)=>{if(!v.valid||!Number.isFinite(v.net_g)){connect=false;return;}if(i>0&&(ws[i-1].subject!==v.subject||ws[i-1].observed_batch_id!==v.observed_batch_id))connect=false;connect?ctx.lineTo(x(i),y(v.net_g)):ctx.moveTo(x(i),y(v.net_g));connect=true;});ctx.stroke();ws.forEach((v,i)=>{if(v.valid&&Number.isFinite(v.net_g)){ctx.fillStyle='#1957ed';ctx.beginPath();ctx.arc(x(i),y(v.net_g),2,0,Math.PI*2);ctx.fill();}else{const py=Number.isFinite(v.net_g)?y(v.net_g):bottom;ctx.strokeStyle='#d72440';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(x(i)-4,py-4);ctx.lineTo(x(i)+4,py+4);ctx.moveTo(x(i)-4,py+4);ctx.lineTo(x(i)+4,py-4);ctx.stroke();}});ctx.fillStyle='#71819a';ctx.fillText('1',left,bottom+18);ctx.fillText(String(ws.length),right-12,bottom+18);}
// process_fsm.py 의 State 값과 1:1 이다. 상태가 늘거나 이름이 바뀌면 여기도 같이 고친다 —
// 사전에 없는 상태는 화면에 영문 원시 문자열로 그대로 노출된다 (hmi.js:193 steps[st.step]).
const steps={IDLE:'주문 대기',SELF_CHECK:'자가 점검',PICK_CONTAINER:'빈 용기 준비',TARE:'용기 영점 측정',PICK_SCOOP:'스쿱 준비',SCOOP:'원료 채취',SCOOP_TARE:'빈 스쿱 기준 계량',WEIGH_SCOOP:'붓기 전 스쿱 계량',RETURN_MATERIAL:'초과 원료 반환',POUR:'원료 투입',WEIGH_RESIDUAL:'잔류량 확인',RETURN_SCOOP:'스쿱 반환',VERIFY:'최종 검증',FINISH:'완성 용기 배출',NUDGE_WAIT:'세트 완료 · 넛지 대기',DONE:'정상 완료',DISCARDED:'폐기 완료',PAUSED:'일시 정지',DEVIATION:'QA 판정 대기',CLEANUP:'오류 정리 중 · 원료·스쿱 반환',ERROR:'공정 오류'};
function shortage(){const inv=snapshot.inventory;if(!selectedRecipe||!inv||inv.mode==='unconfigured')return [];return selectedRecipe.items.filter(it=>{const item=inv.items?.find(x=>x.material_id===it.material_id);const amount=inv.enforced?item?.available_g:item?.remaining_g;return inv.enforced?(!item||!Number.isFinite(amount)||amount+1e-8<it.target_g):(item&&Number.isFinite(amount)&&amount<it.target_g);}).map(it=>it.material_id);}
function heightBlocked(){return [...new Set([...(snapshot.inventory?.blocked_materials||[]),...(snapshot.inventory?.items||[]).filter(i=>i.height_low_latched).map(i=>i.material_id)])];}
function can(role){return session.authenticated&&(session.user?.role==='admin'||role.includes(session.user?.role));}
function gate(){
 if(!fresh){$('entryStatus').textContent='현재 진입 허가 확인 안 됨 · 최신 공정 상태가 필요합니다.';$('entryStatus').className='response';}
 const enabled=fresh&&!inFlight&&!snapshot.safety_recovery?.active;
 const cancellable=fresh&&!inFlight&&can(['operator'])&&snapshot.batch_control?.can_cancel===true;
 $('openCancelBatch').disabled=!cancellable;
 $('sendCancelBatch').disabled=!cancellable||cancelBatchId!==snapshot.state?.batch_id||!$('cancelBatchConfirmed').checked;
 $('refreshRestart').disabled=!session.authenticated||inFlight;
 document.querySelectorAll('#lockForm button').forEach(b=>b.disabled=!enabled||!can(['operator']));
 document.querySelectorAll('#deviations button').forEach(b=>b.disabled=!enabled||!can(['qa'])||heightBlocked().length>0);
 $('orderForm').querySelector('button').disabled=!enabled||snapshot.diagnostics?.actions?.run_batch!==true||!can(['operator'])||snapshot.batch_control?.submitting===true||!canStartOrder(snapshot.state)||!$('recipe').value||!selectedRecipe||((source.demo||snapshot.inventory?.enforced)&&shortage().length>0)||(snapshot.inventory?.enforced&&!snapshot.inventory.fresh)||snapshot.inventory?.order_allowed===false||heightBlocked().length>0;
 $('saveSettings').disabled=!can([])||inFlight;
 document.querySelectorAll('#userForm input,#userForm select,#saveUser').forEach(el=>el.disabled=!can([]));
 $('userName').disabled=!can([])||Boolean(userEdit);
 const inv=snapshot.inventory||{};
 document.querySelectorAll('[data-refill]').forEach(b=>{const item=inv.items?.find(i=>i.material_id===b.dataset.refill);b.disabled=!enabled||!can(['operator'])||!inv.fresh||!inv.can_refill||!item?.refill_ready;});
 if($('refillConfirm').open){const item=inv.items?.find(i=>i.material_id===refillMaterial);$('confirmRefill').disabled=!enabled||!can(['operator'])||!inv.fresh||!inv.can_refill||!item?.refill_ready||!$('confirmFull').checked;}
 $('showStockAlert').hidden=!(heightBlocked().length||(canStartOrder(snapshot.state)&&shortage().length));
 if(source.demo){$('nudgeDemo').hidden=!(snapshot.state?.mode==='PAUSED'&&snapshot.state?.pause_reason==='NUDGE'&&!snapshot.state?.demo_entry_granted);$('nudgeDemo').disabled=!enabled||!can(['operator']);$('injectHeight').disabled=!enabled||!can(['operator']);}

}
async function loadRecipe(){const version=++recipeVersion;selectedRecipe=null;$('recipeDetail').textContent='레시피 구성 조회 중';try{const r=await source.recipe($('recipe').value);if(version!==recipeVersion)return;if(!r||r.error)throw Error('레시피 구성을 확인할 수 없습니다.');selectedRecipe=r;$('recipeDetail').innerHTML=`<div class="recipe-title"><span>${escapeHtml(r.product||r.name)}</span><span>총 ${number(r.total_g,0)} g</span></div><div class="recipe-chips">${r.items.map(i=>`<span class="recipe-chip">${escapeHtml(i.material_id)} <b>${number(i.target_g,0)} g</b></span>`).join('')}</div><small>투입 순서 ${r.items.map(i=>escapeHtml(i.material_id)).join(' → ')} · 허용 오차 ${r.items.map(i=>escapeHtml(i.material_id)+' ±'+number(i.tol_pct,1)+'%').join(' / ')}</small>`;}catch(e){if(version===recipeVersion)$('recipeDetail').textContent='레시피 구성 조회 실패 · 선택 파일을 확인하세요.';}renderInventory(snapshot);gate();}
function renderInventory(s){
 const inv=s.inventory||{mode:'unconfigured',items:[]};
 $('inventoryMode').textContent=inv.mode==='test_process'?(inv.fresh?'시험 공정 재고':'재고 수신 대기'):inv.mode==='demo'?'데모 재고':inv.mode==='session_estimate'?'세션 추정':'미설정';
 $('inventoryMode').className='badge '+(inv.mode==='unconfigured'?'neutral':'info');
 $('inventoryNote').textContent=inv.note||'원료통 기준량과 초기 재고가 설정되면 잔량을 표시합니다.';
 if(inv.mode==='unconfigured'||!inv.items?.length){$('inventory').innerHTML='<div class="inventory-unknown">잔량을 확인할 수 없습니다.<br>초기 재고와 원료통 기준량 설정이 필요합니다.</div>';}
 else{$('inventory').innerHTML=inv.items.map(i=>{
  const pct=Number.isFinite(i.percent)?Math.max(0,Math.min(100,i.percent)):null,height=Number.isFinite(i.height_pct)?number(i.height_pct,1)+'%':i.height_status||'측정 대기';
  return `<div class="inventory-row ${i.low||i.height_low_latched?'low':''}" data-material="${escapeHtml(i.material_id)}"><div class="inventory-row-head"><strong>원료 ${escapeHtml(i.material_id)}</strong><span><small>추정 ${number(i.remaining_g,0)} / ${number(i.capacity_g,0)} g</small><b>${number(i.percent,0)}%</b>${inv.enforced?`<small class="stock-reservation">예약 ${number(i.reserved_g,0)} g · 주문 가능 ${number(i.available_g,0)} g</small>`:''}</span></div><div class="inventory-track" role="meter" aria-label="원료 ${escapeHtml(i.material_id)} 추정 잔량" aria-valuemin="0" aria-valuemax="100" ${pct==null?'':`aria-valuenow="${pct}"`}><span style="width:${pct??0}%"></span></div><div class="inventory-height"><small>높이 ${escapeHtml(height)}</small>${i.height_low_latched?badge('높이 부족 · 보충 필요','warn'):''}${inv.refill_supported?`<button id="refill-${escapeHtml(i.material_id)}" class="secondary small-button" data-refill="${escapeHtml(i.material_id)}">${escapeHtml(i.material_id)} 보충 완료</button>`:''}</div></div>`;
 }).join('');}
 const lacks=shortage(),blocked=heightBlocked(),low=(inv.items||[]).filter(i=>i.low).map(i=>i.material_id),reasons=[];
 if(inv.order_block_reason&&!blocked.length)reasons.push(inv.order_block_reason);
 if(inv.enforced&&!inv.fresh)reasons.push('재고 수신이 지연되어 새 주문·보충을 차단했습니다.');
 if(blocked.length)reasons.push(`원료 ${blocked.join(' · ')} 높이 부족 · 해당 원료의 만충 확인 전까지 진행할 수 없습니다.`);
 if(lacks.length)reasons.push(`다음 주문 기준 원료 ${lacks.join(' · ')}의 g 잔량이 부족합니다.`);
 else if(low.length)reasons.push(`원료 ${low.join(' · ')} 추정 잔량이 낮습니다. 높이 측정값과는 별개입니다.`);
 $('stockWarning').hidden=!reasons.length;$('stockWarning').textContent=reasons.join(' ');
 renderStockAlert();
}
function stockAlerts(){
 const items=snapshot.inventory?.items||[],alerts=heightBlocked().map(id=>({key:'height:'+id,material:id,reason:'높이 20% 미만 신호가 들어왔습니다. 보충 완료 전까지 진행을 차단합니다.'}));
 if(canStartOrder(snapshot.state))for(const id of shortage())if(!alerts.some(a=>a.material===id)){
  const target=selectedRecipe?.items.find(it=>it.material_id===id)?.target_g,item=items.find(it=>it.material_id===id);
  alerts.push({key:'grams:'+id,material:id,reason:`선택 레시피 필요량 ${number(target,0)}g / 주문 가능 ${number(item?.available_g??item?.remaining_g,0)}g. 새 주문을 차단합니다.`});
 }
 return alerts;
}
function renderStockAlert(force=false){
 const alerts=stockAlerts(),active=new Set(alerts.map(a=>a.key));for(const key of acknowledgedShortages)if(!active.has(key))acknowledgedShortages.delete(key);
 $('stockAlertItems').innerHTML=alerts.map(a=>`<article class="deviation"><h3>원재료 ${escapeHtml(a.material)} 부족 알림</h3><p>${escapeHtml(a.reason)}</p></article>`).join('');
 if(!alerts.length){if($('stockAlert').open)$('stockAlert').close();return;}
 if(fresh&&session.authenticated&&(force||alerts.some(a=>!acknowledgedShortages.has(a.key)))&&!$('refillConfirm').open&&!document.querySelector('.utility-dialog[open]')){
  if(!$('stockAlert').open)$('stockAlert').showModal();alerts.forEach(a=>acknowledgedShortages.add(a.key));
 }
}
function openRefill(materialId){
 const inv=snapshot.inventory,item=inv?.items?.find(i=>i.material_id===materialId);
 if(!fresh||inFlight||!can(['operator'])||!inv?.fresh||!inv.can_refill||!item?.refill_ready)return;
 refillMaterial=materialId;$('confirmFull').checked=false;
 $('refillConfirmTitle').textContent=`원료 ${materialId} 만충 보충 확인`;
 $('refillConfirmText').textContent=`${source.demo?'브라우저 데모':'ROS 시험'} 원료 ${materialId}만 ${number(item.capacity_g,0)}g·100%로 갱신합니다. 다른 원료의 잔량과 부족 상태는 유지됩니다. 높이는 재측정 대기로 표시하고 공정은 자동 재개하지 않습니다.`;
 $('refillConfirmLabel').textContent=`원료 ${materialId}를 만충 보충한 것으로 확인합니다 (${number(item.capacity_g,0)}g 기준).`;
 $('refillConfirm').showModal();gate();
}
$('inventory').onclick=e=>{const b=e.target.closest('[data-refill]');if(b)openRefill(b.dataset.refill);};
$('confirmFull').onchange=gate;
$('refillConfirmForm').onsubmit=async e=>{e.preventDefault();if($('confirmRefill').disabled)return;const id=refillMaterial;$('refillConfirm').close();$('refillMsg').hidden=false;await command('/test/refill',{material_id:id,confirmed_full:true},'refillMsg');};
$('cancelRefill').onclick=()=>$('refillConfirm').close();
$('closeStockAlert').onclick=()=>$('stockAlert').close();
$('showStockAlert').onclick=()=>renderStockAlert(true);

function latestBatchResults(s){const batch=s.state?.batch_id,latest=new Map();for(const r of s.results||[])if(r.batch_id===batch)latest.set(r.material_id,r);return [...latest.values()];}
function completionState(s){const st=s.state||{},terminal=st.mode==='DONE'&&['DONE','DISCARDED'].includes(st.step),pending=st.mode==='DONE'&&!terminal,discarded=st.step==='DISCARDED'||(s.deviations||[]).some(d=>d.batch_id===st.batch_id&&d.decision==='DISCARDED');return {terminal,pending,discarded,mode:pending?'FINISH_PENDING':terminal&&discarded?'DISCARDED':st.mode};}
function canStartOrder(st){return st?.mode==='IDLE'||st?.mode==='ERROR'||(st?.mode==='DONE'&&['DONE','DISCARDED'].includes(st?.step));}
function renderProgress(s){const recipe=s.active_recipe,st=s.state||{},items=recipe?.items||[],results=latestBatchResults(s);$('itemProgress').innerHTML=items.length?items.map((it,i)=>{const r=results.find(r=>r.material_id===it.material_id),discarded=st.step==='DISCARDED'||(s.deviations||[]).some(d=>d.batch_id===st.batch_id&&d.decision==='DISCARDED'),devs=(s.deviations||[]).filter(d=>d.batch_id===st.batch_id&&d.material_id===it.material_id),pending=devs.some(d=>d.decision==='PENDING'),complete=r&&(r.verdict==='OK'||devs.some(d=>d.decision==='APPROVED')),current=i===st.item_index&&!['DONE','ERROR'].includes(st.mode),state=pending?'hold':complete?'done':current?(st.mode==='PAUSED'?'hold':'current'):'';const label=pending?'QA 대기':complete?'완료':current&&st.mode==='PAUSED'?'정지':r?.verdict==='UNDER'?'추가 투입':r?.verdict==='INVALID'?'재계량':current?'처리 중':r?'결과 확인':discarded||st.mode==='ERROR'?'미처리':'대기';return `<div class="progress-item ${state}"><strong>${escapeHtml(it.material_id)}</strong>${label}</div>`;}).join(''):`<p class="hint">${st.batch_id?'배치 레시피가 확인되면 원료별 진행을 표시합니다.':'주문 후 원료별 진행 상태를 표시합니다.'}</p>`;}
function renderDiagnostics(s){const d=s.diagnostics||{},topics=d.topics||{},actions=d.actions||{},services=d.services||{};$('diagHint').textContent=source.demo?'데모 데이터 · ROS 미연결':fresh?'상태 수신 중':'상태 수신 대기';$('diagNote').textContent=source.demo?'아래 값은 화면 검증용 예시입니다. 실제 ROS 통신 확인은 실시간 화면에서 진행하세요.':`네임스페이스 ${d.namespace||'—'} · 액션·서비스는 서버 발견 여부, 토픽은 HMI 수신 횟수와 마지막 수신 경과시간입니다.`;const names={state:'공정 상태',weight:'계량값',gripper:'그리퍼',dispense_result:'분주 결과',deviation:'일탈',scoop_cycle:'스쿱 시도',event:'공정 이벤트'},actionNames={run_batch:'배치 실행'},serviceNames={qa_decision:'QA 판정',interlock:'진입·복귀'};if(s.inventory?.enforced){names.test_inventory='시험 재고';for(const id of ['A','B','C'])serviceNames['test_refill_'+id]='시험 '+id+' 개별 보충';}const rows=Object.entries(names).map(([k,label])=>{const t=topics[k]||{};return [escapeHtml(label),'토픽',t.count?badge('수신 '+t.count+'회','info'):t.age_s!=null?badge('수신','info'):badge('미수신'),t.age_s==null?'—':number(t.age_s,1)+'초 전'];});rows.push(...Object.entries(actionNames).map(([k,label])=>[label,'액션',actions[k]===true?badge('서버 발견','good'):actions[k]===false?badge('서버 없음','warn'):badge('확인 전'),'—']));rows.push(...Object.entries(serviceNames).map(([k,label])=>[label,'서비스',services[k]===true?badge('서버 발견','good'):services[k]===false?badge('서버 없음','warn'):badge('확인 전'),'—']));$('diagnostics').innerHTML=table(['항목','방식','상태','마지막 수신'],rows);}
function renderCompletion(s){
 const st=s.state||{},status=completionState(s);$('completion').hidden=(!status.terminal&&!status.pending)||!fresh;
 if(status.pending){$('completion').className='completion pending';$('completion').innerHTML=`<strong>완료 확인 대기</strong><span>${escapeHtml(st.batch_id)} · 종료 상태가 먼저 도착했습니다. 용기 배출·폐기 등 물리적 작업 완료를 확인할 때까지 새 주문을 보낼 수 없습니다.</span>`;}
 else if(status.terminal){const results=latestBatchResults(s),sum=results.reduce((n,r)=>n+(Number(r.actual_g)||0),0);$('completion').className='completion'+(status.discarded?' discarded':'');$('completion').innerHTML=`<strong>${status.discarded?'QA 폐기 · 배치 종료':'✓ 배치 완료'}</strong><span>${escapeHtml(st.batch_id)} · 결과 수신 ${results.length}종 / 사용량 합계 ${number(sum)} g${status.discarded?' · 사용한 원료는 재고로 복원되지 않습니다.':' · 배치 상세는 기록·통계에서 확인하세요.'}</span>`;}
 const key=st.batch_id+':'+st.mode+':'+st.step+':'+status.discarded;if(status.terminal&&key!==lastBatchMode)refreshRecords();lastBatchMode=key;
}
let recoveryGeneration=null;
const recoveryButton=document.createElement('button');
recoveryButton.id='openRecovery';recoveryButton.className='session-button warn';recoveryButton.textContent='안전 복구';
$('utilityButtons').appendChild(recoveryButton);
recoveryButton.onclick=()=>{$('recoveryDialog').showModal();renderRecovery(snapshot);};
$('closeRecovery').onclick=()=>$('recoveryDialog').close();
function renderRecovery(s){
 const r=s.safety_recovery||{},supported=s.diagnostics?.services?.request_safety_recovery===true;
 const allowed=[1,3,5,8,9,10].includes(r.robot_state);
 if(session.authenticated&&r.active&&r.generation!==recoveryGeneration&&!$('recoveryDialog').open)$('recoveryDialog').showModal();
 recoveryGeneration=r.generation;
 recoveryButton.disabled=!session.authenticated;
 const phaseNames={unobserved:'안전 상태 미확인',stopped:'안전정지 감지',pending:'복구 응답 대기',uncertain:'복구 결과 미확인',manual_required:'현장 수동 조치 필요',failed:'복구 미완료',recovered:'로봇 복구 확인 · 배치 재개 아님'};
 const stateNames={1:'대기 · STANDBY',2:'이동 중 · MOVING',3:'안전 전원 차단 · SAFE_OFF',4:'티칭 · TEACHING',5:'안전정지 · SAFE_STOP',6:'비상정지 · EMERGENCY_STOP',8:'복구 모드 · RECOVERY',9:'안전정지 2 · SAFE_STOP2',10:'안전 전원 차단 2 · SAFE_OFF2'};
 $('recoveryPhaseLabel').textContent=phaseNames[r.phase]||'안전 상태 미확인';
 const observed=stateNames[r.robot_state];
 $('recoveryStatus').textContent=`마지막 관측: ${observed?observed+' (코드 '+r.robot_state+')':'미확인'}\n${r.reason||'안전 이벤트를 아직 받지 못했습니다. 정상 상태를 의미하지 않습니다.'}${r.phase&&r.phase!=='unobserved'&&r.message?'\n'+r.message:''}`;
 $('recoveryRequestId').textContent=r.request_id?'요청 ID: '+r.request_id:'';
 $('requestRecovery').disabled=!fresh||inFlight||!can(['operator'])||!supported||!r.active||!allowed||['pending','uncertain'].includes(r.phase);
 $('retryRecovery').hidden=r.phase!=='uncertain';$('retryRecovery').disabled=!fresh||inFlight||!can(['operator'])||!supported;
 const reasons=[];
 if(!session.authenticated)reasons.push('로그인이 필요합니다.');
 else if(!can(['operator']))reasons.push('운전자 또는 관리자 권한이 필요합니다.');
 if(!fresh)reasons.push('최신 공정 상태가 수신되지 않았습니다.');
 if(!supported)reasons.push('C 복구 서비스가 연결되지 않았습니다. 계약 설치·서버 실행·ROS 연결을 확인하세요.');
 if(inFlight)reasons.push('다른 요청을 처리 중입니다.');
 if(r.phase==='pending')reasons.push('복구 응답을 기다리고 있습니다.');
 if(r.phase==='uncertain')reasons.push('이전 결과가 미확인입니다. 아래 동일 요청 재확인을 사용하세요.');
 if(!allowed)reasons.push('복구 가능한 로봇 상태가 확인되지 않았습니다. 상태를 추측해서 요청하지 않습니다.');
 if(!r.active&&r.phase==='recovered')reasons.push('복구가 확인되었습니다. 기존 배치는 자동 재개되지 않습니다.');
 else if(!r.active)reasons.push('해제할 안전정지가 확인되지 않았습니다.');
 $('recoveryBlocked').hidden=reasons.length===0;
 $('recoveryBlockReasons').innerHTML=reasons.map(reason=>`<li>${reason}</li>`).join('');
 const help={1:'대기 상태를 다시 확인하고 안전 차단 해제를 요청합니다. 배치 재개는 아닙니다.',3:'현장 원인 제거 및 필요한 펜던트 조치를 확인한 뒤 요청하세요.',5:'안전정지 리셋 후 대기 상태 확인을 요청합니다.',8:'사람이 필요한 자세 교정·펜던트 조치를 완료한 뒤 요청하세요.',9:'복구 모드 진입 후 수동 조치가 필요합니다. 자동 자세 이동은 없습니다.',10:'복구 모드 진입 후 수동 조치가 필요합니다. 자동 자세 이동은 없습니다.'};
 $('recoveryStateHelp').textContent=help[r.robot_state]||'비상정지·이동·티칭·미확인은 원격 복구 요청 대상이 아닙니다. 현장 조치 후 새로운 상태 수신이 필요합니다.';
}
$('recoveryForm').onsubmit=async e=>{e.preventDefault();renderRecovery(snapshot);if($('requestRecovery').disabled)return;const r=snapshot.safety_recovery;const data={expected_state:r.robot_state,operator_confirmed:true,generation:r.generation};$('requestRecovery').disabled=true;await command('/recover',data,'recoveryMsg');renderRecovery(snapshot);};
$('retryRecovery').onclick=async()=>{if($('retryRecovery').disabled)return;await command('/recover',{request_id:snapshot.safety_recovery?.request_id},'recoveryMsg');renderRecovery(snapshot);};
function renderSafetyPopup(s){
 const popup=$('safetyModePopup'),st=s.state||{},r=s.safety_recovery||{},triggered=s.gripper?.safety_triggered===true,paused=fresh&&st.mode==='PAUSED';
 popup.hidden=!r.active&&r.phase!=='recovered'&&!triggered&&!paused;
 renderRecovery(s);if(popup.hidden)return;
 popup.classList.toggle('critical',Boolean(r.active||triggered));
 $('safetyModeTitle').textContent=r.active?'로봇 안전정지 · 복구 확인 필요':r.phase==='recovered'?'로봇 복구 확인 · 배치 재개 아님':triggered?'그리퍼 안전 입력 감지':s.interlock?.entry_granted===true?'인터락 진입 허가 응답 수신':'공정 일시 정지 · 진입 허가 아님';
 $('safetyModeText').textContent=r.active||r.phase==='recovered'?`${r.reason} · ${r.message} · 상단 안전 복구에서 상세 확인`:triggered?'장치를 확인하세요. 필요 시 비상정지 버튼을 누르세요.':`${st.note||'일시 정지 상태'} · ${st.station||'위치 미확인'}`;
 popup.querySelector('small').textContent=r.active||r.phase==='recovered'?'복구 ≠ 배치 재개/구역 진입 허가. 다음 작업 전 안전 자세·현장 재설정 필요.':'PAUSED만으로 안전 자세 또는 구역 진입을 보장하지 않습니다.';
}
function render(s){if(!source.demo){const test=s.diagnostics?.namespace==='/hmi_test',stub=s.diagnostics?.transport==='inprocess';$('environment').textContent=stub?'가짜 ROS 대역 시험':test?'ROS 통신 시험':'실시간 연결';$('footerMode').textContent=stub?'HTTP · SQLite 실제 검증 / ROS 대역 사용 · DDS 미실행':test?'ROS 실제 통신 · 계량값은 시험 생성값 · 로봇 미연결':'공정 관측 · 요청 · 기록 조회';}snapshot=s;const st=s.state||{},f=s.freshness||{};fresh=f.state_age_s!=null&&f.state_age_s<=(f.stale_after_s??3);const completion=completionState(s),mode=fresh?completion.mode:'OFFLINE';$('mode').textContent=fresh?(modes[mode]||mode):st.mode?'상태 수신 중단':'상태 수신 대기';$('mode').className='state-pill '+mode;$('batch').textContent=st.batch_id||'—';$('item').textContent=st.batch_id&&st.item_index!=null?(Math.min(st.item_index+1,s.active_recipe?.items?.length||st.item_index+1)+(s.active_recipe?.items?.length?' / '+s.active_recipe.items.length:'')):'—';$('step').textContent=completion.pending?'물리적 완료 확인 대기':completion.terminal&&completion.discarded?'폐기 완료':steps[st.step]||st.step||'—';$('step').title=st.step||'';$('station').textContent=st.station||'—';$('note').textContent=fresh?(completion.pending?'용기 배출·폐기 등 물리적 작업 완료 신호를 기다립니다. 새 주문이 차단되어 있습니다.':((st.mode==='PAUSED'?(st.pause_reason==='NUDGE'?'접촉 감지 정지 · ':['REFILL','HEIGHT_LOW'].includes(st.pause_reason)?'원료 보충 대기 · ':''):'')+(st.note||'공정 상태 수신 중'))):'마지막 값입니다. 공정 상태가 다시 수신될 때까지 요청을 보낼 수 없습니다.';$('connection').textContent=fresh?'상태 수신 '+clock(st.t):'상태 미수신 · '+clock(st.t);
 const g=s.gripper||{},gripFresh=f.gripper_age_s!=null&&f.gripper_age_s<=(f.stale_after_s??3);$('width').textContent=number(g.width_mm);$('backend').textContent=g.backend||'—';$('force').textContent=number(g.force_n)+' N';$('busy').textContent=!gripFresh?'수신 대기 / 마지막 값':g.busy?'동작 중':'대기';$('grip').textContent=!gripFresh?'최신 여부 미확인':g.backend==='modbus'?(g.grip?'파지 감지':'파지 미감지'):(g.grip?'파지 추정':'미파지 추정');$('grip').className='badge '+(gripFresh&&g.grip?'good':'neutral');
 const ws=filteredWeights(s.weights||[]),latest=ws.at(-1);$('weightSubjectLabel').textContent=subjectName(latest?.subject);$('weight').textContent=latest?.valid?number(latest.net_g):'—';$('valid').textContent=latest?(latest.valid?'✓ 측정 유효':'× 무효 측정'):'측정 대기';$('valid').className='badge '+(latest?(latest.valid?'good':'bad'):'neutral');$('weightTime').textContent=clock(latest?.t);$('weightStation').textContent='측정 위치 '+(latest?.station||'—');draw(ws);$('results').innerHTML=table(['원료','목표 g','실측 g','오차 %','판정','시도'],itemRows(s.results||[]));
 const pending=(s.deviations||[]).filter(d=>d.decision==='PENDING'&&d.requires_decision);$('pendingCount').textContent='대기 '+pending.length+'건';$('deviations').innerHTML=pending.length?pending.map(d=>{const r=[...(s.results||[])].reverse().find(r=>r.batch_id===d.batch_id&&r.material_id===d.material_id);return `<article class="deviation"><h3>! ${escapeHtml(d.kind)}</h3><p>일탈 ${escapeHtml(d.deviation_id)} · 배치 ${escapeHtml(d.batch_id)}</p><p>${escapeHtml(d.detail)}</p>${r?`<p>원료 ${escapeHtml(d.material_id)} · 목표 ${number(r.target_g)} g / 실측 ${number(r.actual_g)} g</p>`:''}<div class="button-pair"><button data-dev="${escapeHtml(d.deviation_id)}" data-batch="${escapeHtml(d.batch_id)}" data-decision="1">승인 · 계속</button><button class="danger" data-dev="${escapeHtml(d.deviation_id)}" data-batch="${escapeHtml(d.batch_id)}" data-decision="2">폐기</button></div></article>`;}).join(''):'<div class="empty">✓ 판정 대기 일탈이 없습니다.</div>';renderInventory(s);renderProgress(s);renderDiagnostics(s);renderCompletion(s);renderSafetyPopup(s);renderCycle(s);renderNetwork();renderBatchControls(s);$('alarmCount').textContent=pending.length+heightBlocked().length+(fresh?0:1)+(st.mode==='ERROR'?1:0);gate();}
async function tick(){try{if(session.authenticated){render(await source.status());}}catch(e){fresh=false;$('completion').hidden=true;$('mode').textContent=e.status===401?'로그인 필요':'서버 연결 끊김';$('mode').className='state-pill OFFLINE';$('connection').textContent='서버 응답 미확인';$('note').textContent='마지막 수신값입니다. 연결이 복구되면 자동 갱신됩니다.';handleAuthError(e);renderNetwork();gate();}finally{setTimeout(tick,500);}}
function response(id,text,type=''){const el=$(id);el.textContent=text;el.className='response '+type;}
async function command(path,data,msgId){if(!fresh||inFlight)return;inFlight=true;gate();response(msgId,'요청 중…');try{const payload={...data};delete payload.actor;const r=await source.post(path,payload);response(msgId,(r.ok?'요청 수락 · ':'거부 / 미확인 · ')+(r.message||'')+(r.batch_id?' · '+r.batch_id:''),r.ok?'success':'error');render(await source.status());await refreshRecords();}catch(e){handleAuthError(e);response(msgId,e.status===403?'이 조작에 필요한 권한이 없습니다.':e.message+' · 처리 여부를 확인한 뒤 다시 요청하세요.','error');}finally{inFlight=false;gate();}}
$('orderForm').onsubmit=e=>{e.preventDefault();command('/order',Object.fromEntries(new FormData(e.target)),'orderMsg');};
$('lockForm').onsubmit=e=>{e.preventDefault();command('/interlock',{...Object.fromEntries(new FormData(e.target)),request:e.submitter.value},'lockMsg');};
$('deviations').onclick=e=>{const b=e.target.closest('button[data-dev]');if(b)command('/qa',{batch_id:b.dataset.batch,deviation_id:b.dataset.dev,decision:Number(b.dataset.decision)},'qaMsg');};
function kpiCard(title,value,unit,note,help=''){return `<div class="kpi"><h3>${escapeHtml(title)}${help?` <span class="help" tabindex="0" title="${escapeHtml(help)}">?</span>`:''}</h3><strong>${value}</strong> <span class="unit">${unit}</span><p class="hint">${escapeHtml(note)}</p></div>`;}
async function refreshRecords(){if(recordBusy||!session.authenticated)return;recordBusy=true;try{const filters=reportFilters(),[h,k]=await Promise.all([source.history(filters),source.kpi(filters)]);const cards=kpiCard('배치 성공률',number(k.batch_success_pct),'%',k.batches?'종료 배치 '+k.batches+'건 기준':'집계 대상 없음')+kpiCard('자동복구율',number(k.auto_recovery_pct),'%',k.deviations?'전체 일탈 '+k.deviations+'건 기준':'집계 대상 없음')+kpiCard('누적 운전시간',k.batches?number(k.run_time_s/60):'—','분','종료 배치 운전시간 합계')+kpiCard('강제 개입',number(k.forced_interventions,0),'회','INTERVENTION_FORCED 기록')+kpiCard('MTBI',k.forced_interventions?number(k.mtbi_s/60):'—','분',k.forced_interventions?'운전시간 ÷ 강제 개입':'개입 0회 · 계산 대상 없음','Mean Time Between Interventions. 기록된 종료 배치의 운전시간을 강제 개입 횟수로 나눈 값입니다. 로봇 가동률이나 고장 간격이 아닙니다.');$('kpis').innerHTML=cards;$('history').innerHTML=table(['배치','제품','시작','결과','원료','일탈','사이클 s'],h.map(b=>[`<button data-id="${escapeHtml(b.batch_id)}">${escapeHtml(b.batch_id)}</button>`,escapeHtml(b.product),escapeHtml(dateTime(b.started_at)),verdict(b.result),escapeHtml(b.n_items),escapeHtml(b.n_dev),number(b.cycle_s,0)]));$('recordStatus').textContent='갱신 '+clock(Date.now()/1000)+' · '+h.length+'건';if(selectedBatch&&!h.some(b=>b.batch_id===selectedBatch)){selectedBatch='';detailData=null;}if(!selectedBatch&&h.length)selectedBatch=h.find(b=>b.result)?.batch_id||h[0].batch_id;if(selectedBatch)await selectBatch(selectedBatch);else{detailData=null;renderDetail();}await refreshAudit();}catch(e){handleAuthError(e);$('recordStatus').textContent='기록 조회 실패 · '+e.message;}finally{recordBusy=false;}}
async function selectBatch(id){selectedBatch=id;const version=++detailVersion;$('detailTitle').textContent='배치 상세 · '+id;try{const b=await source.batch(id);if(version!==detailVersion)return;detailData=b;renderDetail();document.querySelectorAll('#history tr').forEach(r=>r.classList.toggle('selected',r.querySelector('button')?.dataset.id===id));}catch(e){if(version===detailVersion){detailData=null;$('detail').textContent='상세 조회 실패. 배치를 다시 선택하세요.';$('detailResult').textContent='';$('detailTime').textContent='';}}}
function renderDetail(){const b=detailData;$('detailTitle').textContent='배치 상세'+(b?' · '+b.batch_id:'');$('detailResult').innerHTML=b?verdict(b.result):'';$('detailTime').textContent=b?'시작 '+dateTime(b.started_at)+' · 종료 '+dateTime(b.finished_at):'배치를 선택하세요.';$('downloadBatch').disabled=!b;if(!b){$('detail').innerHTML='<div class="empty">배치 기록이 없습니다.</div>';return;}const rows=b[detailTab]||[];if(detailTab==='items')$('detail').innerHTML=table(['원료','목표 g','실측 g','오차 %','판정','시도'],itemRows(rows));if(detailTab==='weights')$('detail').innerHTML=table(['시각','대상','위치','총량 g','영점 g','순량 g','유효'],rows.map(w=>[escapeHtml(clock(w.t)),escapeHtml(subjectName(w.subject)),escapeHtml(w.station),number(w.gross_g),number(w.tare_g),number(w.net_g),badge(w.valid?'유효':'무효',w.valid?'good':'bad')]));if(detailTab==='deviations')$('detail').innerHTML=table(['일탈','원료','유형','판정','담당자','상세'],rows.map(d=>[escapeHtml(d.deviation_id),escapeHtml(d.material_id),escapeHtml(d.kind),verdict(d.decision),escapeHtml(d.operator_id),escapeHtml(d.detail)]));if(detailTab==='scoop_cycles')$('detail').innerHTML=cycleTable(rows);if(detailTab==='events')$('detail').innerHTML=table(['시각','수준','코드','내용'],rows.map(e=>[escapeHtml(clock(e.t)),escapeHtml(e.level),escapeHtml(e.code),escapeHtml(e.text)]));}
$('history').onclick=e=>{const b=e.target.closest('button[data-id]');if(b)selectBatch(b.dataset.id);};
document.querySelectorAll('[data-detail]').forEach(b=>b.onclick=()=>{detailTab=b.dataset.detail;document.querySelectorAll('[data-detail]').forEach(t=>{t.classList.toggle('active',t===b);t.setAttribute('aria-selected',t===b);});renderDetail();});
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>showPage(b.dataset.page));
$('refreshRecords').onclick=refreshRecords;
$('recipe').onchange=loadRecipe;
if(source.demo){
 $('nudgeDemo').onclick=async()=>{if(!fresh||inFlight)return;inFlight=true;gate();try{const r=await source.nudge();response('lockMsg',r.message,r.ok?'success':'error');render(await source.status());}finally{inFlight=false;gate();}};
 $('demoBar').hidden=false;$('environment').textContent='DEMO';$('footerMode').textContent='데모 모드 · 예시 데이터 · 실제 장치 미연결';
 const reset=()=>{source.reset($('scenario').value);acknowledgedShortages.clear();selectedBatch='';detailData=null;$('refillMsg').hidden=true;['orderMsg','qaMsg','lockMsg'].forEach(id=>response(id,'요청 전'));source.status().then(render);refreshRecords();};
 $('scenario').onchange=reset;$('resetDemo').onclick=reset;
 $('injectHeight').onclick=async()=>{if(!fresh||inFlight)return;const r=await source.injectHeight($('heightMaterial').value,Number($('heightValue').value));$('refillMsg').hidden=false;response('refillMsg',r.message,r.ok?'success':'error');render(await source.status());};
}

function subjectName(subject){return subject==='scoop'?'스쿱':subject==='container'?'용기':'대상 미상';}
function filteredWeights(rows){const subject=$('weightSubject').value;return subject==='all'?rows:rows.filter(w=>subject==='unknown'?!w.subject||!['scoop','container'].includes(w.subject):w.subject===subject);}
$('weightSubject').onchange=()=>render(snapshot);
function renderNetwork(){const text=source.demo?'데모 · 미측정':source.rttMs==null?'—':number(source.rttMs,0)+' ms';$('httpRtt').textContent=text;$('httpFailures').textContent=String(source.failures||0);}
const outcomeNames={0:'완료',1:'빈 스쿱',2:'계량 무효',3:'붓기 실패',4:'중단',5:'원료 반환',6:'원료 반환 실패'};
function cycleTable(rows){return rows.length?rows.map(c=>`<details class="cycle-record"><summary>${escapeHtml(c.batch_id)} · 원료 ${escapeHtml(c.material_id)} · 시도 ${escapeHtml(c.attempt)} ${badge(outcomeNames[c.outcome]||c.outcome,c.valid?'good':'warn')} <b>${number(c.delivered_g)} g</b></summary><p class="hint">종료 기록 ${dateTime(c.t)} · 계량 자세 ${escapeHtml(c.weigh_pose_id)} · ${number(c.duration_s)}초</p>${table(['빈 스쿱 순량','붓기 전','붓기 후','투입량'],[[number(c.scoop_tare?.net_g)+' g',number(c.pre_pour?.net_g)+' g',number(c.post_pour?.net_g)+' g',number(c.delivered_g)+' g']])}${table(['접촉 검출','최대 접촉력','삽입 깊이','파지 폭'],[[c.contact_detected?'검출':'미검출',number(c.max_contact_force_n)+' N',number(c.insertion_depth_mm)+' mm',number(c.grip_width_mm)+' mm']])}</details>`).join(''):'<div class="empty">스쿱 시도 기록이 없습니다.</div>';}
function renderCycle(s){const granted=fresh&&['PAUSED','DEVIATION'].includes(s.state?.mode)&&(source.demo?s.state?.demo_entry_granted===true:s.interlock?.entry_granted===true);$('entryStatus').textContent=granted?(source.demo?'데모 ENTER 허가 예시 · 실제 장치 미연결':'최근 ENTER 허가 응답 확인 · 현장 안전 상태 확인 필요'):'현재 진입 허가 확인 안 됨 · PAUSED만으로 진입하지 마세요.';$('entryStatus').className='response '+(granted?'success':'');const c=(s.scoop_cycles||[]).at(-1);$('cycleSummary').className='';$('cycleSummary').innerHTML=c?`<div class="force-summary"><div><small>최근 시도 최대 접촉력</small><strong>${number(c.max_contact_force_n)} <span>N</span></strong></div><div><small>삽입 깊이</small><strong>${number(c.insertion_depth_mm)} <span>mm</span></strong></div></div><p class="hint">${escapeHtml(c.batch_id)} · 원료 ${escapeHtml(c.material_id)} · 시도 ${escapeHtml(c.attempt)} · ${escapeHtml(outcomeNames[c.outcome]||c.outcome)} · ${clock(c.t)}</p>`:'<div class="empty">종료된 스쿱 시도 수신 대기</div>';}
function dateFilter(startId,endId){const start=$(startId).value,end=$(endId).value,filter={};if(start)filter.start=new Date(start).getTime()/1000;if(end)filter.end=new Date(end).getTime()/1000;if(start&&end&&filter.end<=filter.start)throw Error('종료 시각은 시작 시각보다 늦어야 합니다.');return filter;}
function reportFilters(){return {...dateFilter('reportStart','reportEnd'),result:$('reportResult').value,query:$('reportQuery').value.trim()};}
function alarmFilters(){return {...dateFilter('alarmStart','alarmEnd'),level:$('alarmLevel').value,query:$('alarmQuery').value.trim()};}
async function refreshAudit(){try{const filter={...dateFilter('reportStart','reportEnd'),actor:$('auditActor').value.trim(),action:$('auditAction').value.trim(),query:$('auditQuery').value.trim()},a=await source.audit(filter);$('audit').innerHTML=table(['시각','작업자','조작','대상','상세'],a.map(r=>[escapeHtml(dateTime(r.t)),escapeHtml(r.actor),escapeHtml(r.action),escapeHtml(r.target),escapeHtml(r.detail)]));}catch(e){handleAuthError(e);$('audit').innerHTML=`<div class="empty">${escapeHtml(e.message)}</div>`;}}
async function refreshAlarms(){if(alarmBusy||!session.authenticated)return;alarmBusy=true;try{const filter=alarmFilters(),events=await source.events(filter),devs=(snapshot.deviations||[]).filter(d=>d.decision==='PENDING').map(d=>({t:d.t||d.raised_at,level:'WARN',code:d.kind,text:d.detail,batch_id:d.batch_id,origin:'현재 일탈'})),local=[];if(!fresh)local.push({t:Date.now()/1000,level:'ERROR',code:'HMI_STATE_STALE',text:'공정 상태가 최신이 아닙니다. 표시값과 조작 가능 여부를 확인하세요.',origin:'HMI 현재 상태'});if(snapshot.state?.mode==='ERROR')local.push({t:snapshot.state.t,level:'ERROR',code:'CELL_ERROR',text:snapshot.state.note,origin:'현재 공정'});const match=e=>(!filter.start||e.t>=filter.start)&&(!filter.end||e.t<filter.end)&&(!filter.level||e.level===filter.level)&&(!filter.query||JSON.stringify(e).toLowerCase().includes(filter.query.toLowerCase()));alarmRows=[...events.map(e=>({...e,origin:'기록 이벤트'})),...devs.filter(match),...local.filter(match)].sort((a,b)=>(b.t||0)-(a.t||0));$('alarmList').innerHTML=table(['시각','수준','출처','코드','배치','내용'],alarmRows.map(r=>[escapeHtml(dateTime(r.t)),badge(r.level,r.level==='ERROR'?'bad':r.level==='WARN'?'warn':'info'),escapeHtml(r.origin),escapeHtml(r.code),escapeHtml(r.batch_id||'—'),escapeHtml(r.text)]));$('alarmStatus').textContent='갱신 '+clock(Date.now()/1000)+' · 현재 경고와 검색된 이벤트 '+alarmRows.length+'건 · 현장 안전 상태는 별도로 확인하세요.';}catch(e){handleAuthError(e);$('alarmStatus').textContent='알람 조회 실패 · '+e.message;}finally{alarmBusy=false;}}
function showPage(next){
 page=next==='records'?'records':'operation';
 const operation=page==='operation';
 $('operation').hidden=!operation;$('operationDetails').hidden=!operation;$('alarms').hidden=!operation;$('records').hidden=operation;
 document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
 if(operation){refreshAlarms();requestAnimationFrame(()=>draw(filteredWeights(snapshot.weights||[])));}else refreshRecords();
}
function closeUtilityDialogs(){document.querySelectorAll('.utility-dialog[open]').forEach(d=>d.close());}
function openUtilityDialog(name){if(!session.authenticated){$('loginOverlay').hidden=false;return;}closeUtilityDialogs();$(name).showModal();if(name==='settings')loadSettings();else loadUsers();}
document.querySelectorAll('[data-dialog]').forEach(b=>b.onclick=()=>openUtilityDialog(b.dataset.dialog));
document.querySelectorAll('[data-close-dialog]').forEach(b=>b.onclick=()=>$(b.dataset.closeDialog).close());

function showSession(){if(!session.authenticated){closeUtilityDialogs();for(const id of ['stockAlert','refillConfirm'])if($(id).open)$(id).close();}const user=session.user;$('loginOverlay').hidden=session.authenticated;$('sessionButton').textContent=session.authenticated?`${user.username}${source.demo?' · 데모':' · 로그아웃'}`:'로그인';$('adminSession').textContent=source.demo?'데모 권한 미리보기 · 실제 인증/계정 생성 아님':user?user.username+' · '+user.role:'로그인 필요';gate();}
function handleAuthError(e){if(e.status===401){session={authenticated:false,user:null};fresh=false;showSession();source.session().then(s=>{$('setupNote').hidden=!s.setup_required;}).catch(()=>{});}else if(e.status===403){response('userMsg','관리자 권한이 필요합니다.','error');}}
async function initialize(){try{session=await source.session();$('setupNote').hidden=!session.setup_required;showSession();if(session.authenticated){await source.catalog();$('recipe').innerHTML=source.recipes.map(r=>{const name=typeof r==='string'?r:r.name,detail=source.recipeCache?.find(x=>x.name===name);return `<option value="${escapeHtml(name)}">${escapeHtml(detail?.product||name)}</option>`;}).join('');await loadRecipe();await refreshRecords();await loadSettings();await refreshAlarms();}}catch(e){response('loginMsg','세션 확인 실패 · '+e.message,'error');$('loginOverlay').hidden=source.demo;}}
$('loginForm').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;response('loginMsg','로그인 중…');try{session=await source.login(Object.fromEntries(new FormData(e.target)));if(!session.authenticated)throw Error(session.message||'로그인에 실패했습니다.');$('loginPassword').value='';source.recipeCache=null;await initialize();}catch(err){response('loginMsg',err.message,'error');}finally{button.disabled=false;}};
$('sessionButton').onclick=async()=>{if(!session.authenticated){$('loginOverlay').hidden=false;return;}try{await source.logout();session={authenticated:false,user:null};snapshot={};fresh=false;showSession();await source.session();}catch(e){handleAuthError(e);}};
$('demoRole').onchange=async()=>{source.setRole($('demoRole').value);session=await source.session();showSession();if($('administration').open)loadUsers();};
async function loadSettings(){if(!session.authenticated)return;try{currentSettings=await source.settings();$('materialSettings').innerHTML=table(['원료 ID','원료통 기준량 (g)','추정 초기량 (g)'],currentSettings.inventory_material_ids.map((id,i)=>[escapeHtml(id),`<input aria-label="${escapeHtml(id)} 원료통 기준량" data-capacity="${i}" type="number" min="0" step="0.1" value="${Number(currentSettings.inventory_capacity_g[i])}" required>`,`<input aria-label="${escapeHtml(id)} 초기 재고" data-initial="${i}" type="number" min="-1" step="0.1" value="${Number(currentSettings.inventory_initial_g[i])}" required>`]));$('settingLow').value=currentSettings.inventory_low_pct;$('settingStale').value=currentSettings.ui_stale_after_s;document.querySelectorAll('#settingsForm input').forEach(el=>el.disabled=!can([]));response('settingsMsg',source.demo?'데모 메모리 설정 · 실제 서버에 저장되지 않습니다.':currentSettings.note||'HMI 표시용 설정입니다.');gate();}catch(e){handleAuthError(e);response('settingsMsg',e.message,'error');}}
$('settingsForm').onsubmit=async e=>{e.preventDefault();if(!can([])||!currentSettings)return;const data={inventory_material_ids:currentSettings.inventory_material_ids,inventory_capacity_g:[...document.querySelectorAll('[data-capacity]')].map(e=>Number(e.value)),inventory_initial_g:[...document.querySelectorAll('[data-initial]')].map(e=>Number(e.value)),inventory_low_pct:Number($('settingLow').value),ui_stale_after_s:Number($('settingStale').value)};if(data.inventory_initial_g.some((v,i)=>v>=0&&data.inventory_capacity_g[i]>0&&v>data.inventory_capacity_g[i])){response('settingsMsg','초기량은 원료통 기준량을 넘을 수 없습니다.','error');return;}try{const r=await source.saveSettings(data);response('settingsMsg',r.message||'HMI 설정을 저장했습니다. 공정 노드의 재고나 로봇 제어값은 바뀌지 않습니다.','success');render(await source.status());}catch(err){handleAuthError(err);response('settingsMsg',err.message,'error');}};
async function loadUsers(){if(!session.authenticated)return;if(!can([])){$('userList').innerHTML='<div class="empty">계정 관리에는 관리자 권한이 필요합니다.</div>';gate();return;}try{const users=await source.users();$('userList').innerHTML=table(['계정','역할','상태','편집'],users.map(u=>[escapeHtml(u.username),escapeHtml(u.role),badge(u.active?'활성':'비활성',u.active?'good':'neutral'),`<button class="secondary small-button" data-user="${escapeHtml(u.username)}" data-role="${escapeHtml(u.role)}" data-active="${Boolean(u.active)}">수정</button>`]));}catch(e){handleAuthError(e);$('userList').innerHTML=`<div class="empty">${escapeHtml(e.message)}</div>`;}gate();}
$('userList').onclick=e=>{const b=e.target.closest('button[data-user]');if(!b)return;userEdit=b.dataset.user;$('userFormTitle').textContent='계정 수정 · '+userEdit;$('userName').value=userEdit;$('userPassword').value='';$('userRole').value=b.dataset.role;$('userActive').checked=b.dataset.active==='true';gate();};
$('resetUser').onclick=()=>{userEdit='';$('userForm').reset();$('userActive').checked=true;$('userFormTitle').textContent='계정 추가';gate();};
$('userForm').onsubmit=async e=>{e.preventDefault();if(!can([]))return;const data={username:$('userName').value.trim(),role:$('userRole').value,active:$('userActive').checked};if($('userPassword').value)data.password=$('userPassword').value;if(userEdit)delete data.username;try{const r=await source.saveUser(userEdit,data);$('userPassword').value='';response('userMsg',r.message||'계정을 저장했습니다.','success');await loadUsers();}catch(err){handleAuthError(err);response('userMsg',err.message,'error');}};
$('refreshUsers').onclick=loadUsers;
$('reportFilters').onsubmit=e=>{e.preventDefault();refreshRecords();};$('auditFilters').onsubmit=e=>{e.preventDefault();refreshAudit();};$('alarmFilters').onsubmit=e=>{e.preventDefault();refreshAlarms();};$('refreshAlarms').onclick=refreshAlarms;
function saveFile(name,content,type){const url=URL.createObjectURL(new Blob([content],{type})),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function downloadUrl(url){const link=document.createElement('a');link.href=url;link.download='';link.click();}
function csvCell(v){const text=String(v??'');return '"'+(/^[=+\-@]/.test(text)?"'"+text:text).replaceAll('"','""')+'"';}
$('downloadCsv').onclick=async()=>{try{const filter=reportFilters();if(!source.demo){downloadUrl(source.csvUrl(filter));return;}const rows=await source.history(filter),cols=['batch_id','product','started_at','finished_at','result','n_items','n_dev','cycle_s'];saveFile('hmi_demo_batches.csv','\ufeff'+[cols.join(','),...rows.map(r=>cols.map(c=>csvCell(r[c])).join(','))].join('\r\n'),'text/csv;charset=utf-8');}catch(e){$('recordStatus').textContent=e.message;}};
$('downloadBatch').onclick=()=>{if(!detailData)return;if(source.demo)saveFile(detailData.batch_id+'.json',JSON.stringify(detailData,null,2),'application/json');else downloadUrl(source.batchUrl(detailData.batch_id));};

window.addEventListener('resize',()=>draw(lastWeights));gate();initialize();tick();setInterval(()=>{if(page==='records')refreshRecords();if(page==='operation')refreshAlarms();},5000);
