// 순수 DOM 스텁 검사. 실제 브라우저 렌더링 검사가 아니다.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../static/hmi.js'),'utf8');
const elements=new Map();
function element(){return {value:'',checked:false,textContent:'',hidden:false,disabled:false,
 classList:{toggle(){}},before(){},showModal(){this.open=true;},close(){this.open=false;},querySelector(){return this.small||(this.small=element());}};}
const $=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
const context=vm.createContext({$,document:{createElement:element},session:{authenticated:true},source:{demo:false},
 fresh:true,inFlight:false,can:()=>true,snapshot:{},command:async()=>{}});
vm.runInContext(code.slice(code.indexOf('let recoveryGeneration='),code.indexOf('function render(s)')),context);
const render=s=>{context.snapshot=s;context.renderSafetyPopup(s);};
render({state:{mode:'PAUSED',note:'NUDGE'}});
assert.match($('safetyModeTitle').textContent,/진입 허가 아님/);
render({state:{mode:'PAUSED'},interlock:{entry_granted:true}});
assert.match($('safetyModeTitle').textContent,/인터락 진입 허가/);
const safety={state:{mode:'ERROR'},safety_recovery:{active:true,phase:'stopped',generation:1,robot_state:5,reason:'stop'},diagnostics:{services:{request_safety_recovery:true}}};
render(safety);
assert.match($('safetyModeTitle').textContent,/로봇 안전정지/);
assert.equal($('requestRecovery').disabled,false);
assert.equal($('recoveryDialog').open,true);
safety.safety_recovery.generation=2;safety.safety_recovery.robot_state=-1;render(safety);
assert.equal($('requestRecovery').disabled,true);
for(const value of [2,4,6,-1]){safety.safety_recovery.robot_state=value;render(safety);assert.equal($('requestRecovery').disabled,true);}
for(const value of [1,3,5,8,9,10]){safety.safety_recovery.robot_state=value;render(safety);assert.equal($('requestRecovery').disabled,false);}
for(const phase of ['pending','uncertain']){safety.safety_recovery.phase=phase;render(safety);assert.equal($('requestRecovery').disabled,true);}
safety.safety_recovery.phase='recovered';safety.safety_recovery.active=false;render(safety);
assert.match($('safetyModeTitle').textContent,/배치 재개 아님/);
context.fresh=false;render(safety);assert.equal($('requestRecovery').disabled,true);
assert.match($('recoveryBlockReasons').innerHTML,/최신 공정 상태/);
safety.diagnostics.services.request_safety_recovery=false;render(safety);
assert.match($('recoveryBlockReasons').innerHTML,/C 복구 서비스가 연결되지/);
assert.equal($('recoveryBlocked').hidden,false);
assert.doesNotMatch($('recoveryPhaseLabel').textContent,/recovered/);
const html=fs.readFileSync(path.join(__dirname,'../templates/index.html'),'utf8');
assert.doesNotMatch(html,/id="recoveryExpected"|id="recoveryConfirmed"/);
assert.match(html,/조치 완료 · 안전정지 해제 요청/);
console.log('PASS one-button popup: auto-open, allowed states, unknown/pending/stale/service guards');
