#!/usr/bin/env python3
"""실제 ROS 시험 launch용 HTTP → RunBatch 액션/서비스 → 토픽 → SQLite 검증기.

외부망/실제 /cell 조작을 막기 위해 loopback:5002, /hmi_test 및 시험 노드만 허용.
관리자 비밀번호는 GMP_HMI_ADMIN_PASSWORD 환경변수로만 입력한다. 로그에는 출력하지 않는다.
검증용 새 launch: test_initial_g:='[158.0,1000.0,1000.0]' item_duration_s:=2.0
만충 용량은 1,000g이며 최초 A만 158g으로 시작하여 실제 부족 차단을 재현한다
(recipe-01 이 A 79g 을 쓰면 79g 이 남아 recipe-02 의 A 158g 을 못 채운다).
"""
import argparse
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from urllib import error, parse, request


class CheckFailed(RuntimeError):
    pass


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CheckFailed('시험 서버의 HTTP redirect는 허용하지 않습니다.')


class RosHttpCheck:
    transport = 'ROS_2_DDS'
    def __init__(self, base_url='http://127.0.0.1:5002'):
        url = parse.urlsplit(base_url)
        if (url.scheme != 'http' or url.hostname not in ('localhost','127.0.0.1') or url.port != 5002
                or url.path not in ('','/') or url.query or url.fragment or url.username or url.password):
            raise CheckFailed('허용 주소는 http://127.0.0.1:5002 또는 http://localhost:5002입니다.')
        self.base = base_url.rstrip('/')
        self.opener = request.build_opener(request.ProxyHandler({}),NoRedirect(),request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf, self.passed, self.batches = '', [], []
        self.admin = os.environ.get('GMP_HMI_ADMIN_USER','admin')
        self.password = os.environ.get('GMP_HMI_ADMIN_PASSWORD','')
        self.suffix = secrets.token_hex(3)
        self.operator, self.qa, self.viewer = ['check_'+role+'_'+self.suffix for role in ('operator','qa','viewer')]
        self.test_password = secrets.token_urlsafe(24)

    def report(self, text):
        self.passed.append(text)
        print('PASS  '+text,flush=True)

    def http(self, method, path, data=None, expected=200, raw=False, csrf=True):
        body = None if data is None else json.dumps(data,ensure_ascii=False).encode()
        headers = {'Content-Type':'application/json'}
        if csrf and self.csrf: headers['X-CSRF-Token'] = self.csrf
        req = request.Request(self.base+path,data=body,headers=headers,method=method)
        try:
            response = self.opener.open(req,timeout=20)
        except error.HTTPError as exc:
            response = exc
        with response:
            payload = response.read()
            if response.status != expected:
                raise CheckFailed(f'{method} {path}: expected {expected}, received {response.status}: '+payload.decode(errors='replace')[:500])
            if raw: return payload, dict(response.headers)
            result = json.loads(payload)
            if isinstance(result,dict) and result.get('csrf_token'): self.csrf = result['csrf_token']
            return result

    def get(self,path): return self.http('GET',path)
    def guard(self):
        result = self.get('/status')
        if result.get('diagnostics',{}).get('namespace') != '/hmi_test':
            raise CheckFailed('쓰기 차단: HMI가 /hmi_test 네임스페이스가 아닙니다.')
        return result
    def post(self,path,data):
        self.guard()
        result = self.http('POST',path,data)
        if result.get('ok') is False: raise CheckFailed(f'{path}: {result}')
        return result
    def login(self, username, password):
        session = self.get('/auth/session')
        self.csrf = session['csrf_token']
        if session.get('authenticated'):
            self.http('POST','/auth/logout',{})
            self.csrf = self.get('/auth/session')['csrf_token']
        response = self.http('POST','/auth/login',{'username':username,'password':password})
        if not response.get('ok'): raise CheckFailed('로그인 실패: '+username)
        self.csrf = self.get('/auth/session')['csrf_token']

    @staticmethod
    def wait(label, predicate, timeout=65):
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                result=predicate()
                if result: return result
            except (error.URLError,ConnectionError,TimeoutError): pass
            time.sleep(.1)
        raise CheckFailed(label+' 시간 초과')

    @staticmethod
    def ros(*args):
        if not shutil.which('ros2'): raise CheckFailed('ROS Jazzy와 workspace setup.bash를 source하세요.')
        p=subprocess.run(['ros2',*args],capture_output=True,text=True,timeout=15,check=False)
        if p.returncode: raise CheckFailed('ROS CLI 실패: '+(p.stderr or p.stdout))
        return p.stdout.strip()
    def graph(self):
        nodes=set(self.ros('node','list').splitlines())
        need={'/hmi_test/'+n for n in ('hmi_test_process','hmi_web_node','record_node')}
        if not need<=nodes: raise CheckFailed('시험 노드 누락: '+str(need-nodes))
        if self.ros('action','type','/hmi_test/run_batch') != 'gmp_interfaces/action/RunBatch':
            raise CheckFailed('run_batch 타입 불일치')
        for name,kind in [('qa_decision','QaDecision'),('interlock','InterlockRequest')]:
            if self.ros('service','type','/hmi_test/'+name)!='gmp_interfaces/srv/'+kind: raise CheckFailed(name+' 타입 불일치')
        for name,kind in [('state','CellState'),('weight','WeightReading'),('scoop_cycle','ScoopCycle'),('dispense_result','DispenseResult'),('deviation','Deviation'),('event','CellEvent'),('gripper_state','GripperState')]:
            if self.ros('topic','type','/hmi_test/'+name)!='gmp_interfaces/msg/'+kind: raise CheckFailed(name+' 타입 불일치')
        for name in ('test_inventory','test_height'):
            if self.ros('topic','type','/hmi_test/'+name) != 'std_msgs/msg/String':
                raise CheckFailed(name+' 타입 불일치')
        for mid in ('A','B','C'):
            if self.ros('service','type','/hmi_test/test_refill_'+mid) != 'std_srvs/srv/Trigger':
                raise CheckFailed('시험 개별 보충 서비스 타입 불일치: '+mid)

    def direct_order_rejected(self, requirements=None):
        # HMI를 우회해도 같은 공정 재고/높이 차단이 적용되어야 한다.
        requirements = requirements or {'A':158.0,'B':79.0}
        data={'recipe':{'product':'BYPASS_TEST','items':[
            {'material_id':mid,'target_g':float(amount),'tol_pct':10.0}
            for mid,amount in requirements.items()]}}
        output=self.ros('action','send_goal','/hmi_test/run_batch','gmp_interfaces/action/RunBatch',json.dumps(data))
        if not re.search(r'goal(?: was)? rejected',output,re.IGNORECASE):
            raise CheckFailed('시험 공정의 직접 RunBatch Goal 거부를 확인하지 못함: '+output)

    def direct_refill_rejected(self, material_id='A'):
        output=self.ros('service','call','/hmi_test/test_refill_'+material_id,'std_srvs/srv/Trigger','{}')
        if not re.search(r'\bsuccess\s*[:=]\s*(?:False|false)\b',output):
            raise CheckFailed('운전 중 공정의 직접 보충 거부를 확인하지 못함: '+output)

    def stock(self):
        inv=self.guard().get('inventory',{})
        if inv.get('mode')!='test_process' or not inv.get('enforced') or not inv.get('fresh'):
            raise CheckFailed('최신 시험 공정 재고가 연결되지 않았습니다')
        return {item['material_id']:item for item in inv['items']}

    def amounts(self):
        return {mid:it['remaining_g'] for mid,it in self.stock().items()}

    def refill(self, material_id):
        before=self.amounts()
        self.post('/test/refill',{'material_id':material_id,'confirmed_full':True})
        self.wait('개별 만충 재고 토픽 반영',lambda:
            abs(self.stock()[material_id]['remaining_g']-1000.0)<1e-6 and
            not self.stock()[material_id]['height_low_latched'])
        after=self.amounts()
        if any(abs(after[mid]-value)>1e-6 for mid,value in before.items() if mid!=material_id):
            raise CheckFailed('개별 보충이 다른 원료 잔량을 변경함')

    def height(self, material_id, percent):
        self.post('/test/height',{'material_id':material_id,'height_pct':percent})
        self.wait('높이 신호 수신',lambda:self.stock()[material_id].get('height_pct')==percent)

    def blocked(self, materials):
        return set(self.guard()['inventory'].get('blocked_materials',[])) == set(materials)

    def order_rejected(self, recipe='recipe-01'):
        before=self.amounts()
        batch=self.guard()['state'].get('batch_id')
        count=len(self.get('/history'))
        rejected=self.http('POST','/order',{'recipe':recipe},expected=400)
        if rejected.get('ok') is not False:
            raise CheckFailed('부족 주문이 HMI 서버에서 거부되지 않음')
        if self.guard()['state'].get('batch_id')!=batch or len(self.get('/history'))!=count or self.amounts()!=before:
            raise CheckFailed('거부된 주문이 배치·기록·재고를 변경함')

    def scenario(self,value):
        self.guard()
        output=self.ros('param','set','/hmi_test/hmi_test_process','scenario',value)
        if 'successful' not in output.lower(): raise CheckFailed('시험 시나리오 변경 실패: '+output)
    def mode(self,want,batch=None):
        state=self.guard().get('state',{})
        return state if state.get('mode')==want and (batch is None or state.get('batch_id')==batch) else None
    def record(self,batch,result='DONE',count=3):
        response=self.get('/batch/'+parse.quote(batch,safe=''))
        if response.get('result')!=result or len(response.get('items',[]))!=count: return None
        if count and (len(response.get('scoop_cycles',[]))<count or not response.get('weights')): return None
        return response
    def order(self,scenario,recipe='recipe-01'):
        self.login(self.operator,self.test_password)
        self.scenario(scenario)
        result=self.post('/order',{'recipe':recipe,'actor':'SPOOFED_ACTOR'})
        batch=result['batch_id']
        self.batches.append(batch)
        self.wait('RUNNING '+batch,lambda:self.mode('RUNNING',batch))
        return batch
    def pending(self,batch,kind):
        self.wait('QA 상태',lambda:self.mode('DEVIATION',batch))
        return self.wait('일탈 '+kind,lambda:next((d for d in self.guard().get('deviations',[]) if d.get('batch_id')==batch and d.get('kind')==kind and d.get('decision')=='PENDING'),None))
    def decide(self,batch,pending,decision):
        self.login(self.qa,self.test_password)
        self.post('/qa',{'batch_id':batch,'deviation_id':pending['deviation_id'],'decision':decision,'actor':'SPOOFED_ACTOR'})
        if decision==2:
            state=self.guard().get('state',{})
            if state.get('mode')=='DONE': raise CheckFailed('폐기 반송 완료 전에 DONE이 발행됨')
        self.wait('최종 완료',lambda:self.mode('DONE',batch))

    def extra_checks(self):
        """Transport-specific checks, overridden only by the explicit non-DDS harness."""

    def run(self):
        if len(self.password)<10: raise CheckFailed('GMP_HMI_ADMIN_PASSWORD를 10자 이상으로 설정하세요.')
        self.wait('HTTP 기동',lambda:self.get('/auth/session'))
        self.http('GET','/status',expected=401)
        self.login(self.admin,self.password)
        self.guard(); self.graph()
        self.wait('시험 재고 수신',lambda:self.guard().get('inventory',{}).get('fresh'))
        stock=self.stock()
        self.wait('ROS RunBatch·서비스 발견',lambda:
            self.guard()['diagnostics'].get('actions',{}).get('run_batch') and
            all(self.guard()['diagnostics']['services'].get(k) for k in ('qa_decision','interlock')))
        if self.guard().get('state',{}).get('mode')!='IDLE' or self.get('/history'):
            raise CheckFailed('새 DB의 시험 세션이 필요합니다. launch를 종료 후 재실행하세요.')
        if any(abs(stock[mid]['capacity_g']-1000.0)>1e-6 or
               abs(stock[mid]['remaining_g']-amount)>1e-6 for mid,amount in [('A',158),('B',1000),('C',1000)]):
            raise CheckFailed("검증용 초기 재고가 필요합니다. 새 launch에 test_initial_g:='[158.0,1000.0,1000.0]' 를 지정하세요. 만충은 1,000g 그대로입니다.")
        self.report('시험 네임스페이스·노드3·RunBatch 액션1·서비스5·토픽9 및 인증 전 접근 차단')
        catalog={r['name']:r for r in self.get('/recipes')}
        expected={'recipe-01':{'A':79,'B':79,'C':79},'recipe-02':{'A':158,'B':79},'recipe-03':{'A':79,'B':79,'C':158}}
        for name,items in expected.items():
            if name not in catalog or {it['material_id']:it['target_g'] for it in catalog[name]['items']}!=items:
                raise CheckFailed('레시피 원료/목표량 불일치: '+name)
            if any(it['tol_pct']!=10.0 for it in catalog[name]['items']): raise CheckFailed('허용 오차 불일치: '+name)
        self.report('레시피3종 목표량(D-35 79/158g)·원료 A/B/C·허용오차10% 및 recipe-02 C 생략')
        for role,name in [('operator',self.operator),('qa',self.qa),('viewer',self.viewer)]:
            self.http('POST','/users',{'username':name,'password':self.test_password,'role':role,'active':True},expected=201)
        self.http('POST','/settings',{},expected=403,csrf=False)
        self.login(self.viewer,self.test_password)
        self.get('/history')
        for path,data in [('/order',{'recipe':'recipe-01'}),('/users',{}),('/test/refill',{'material_id':'A','confirmed_full':True}),('/test/height',{'material_id':'A','height_pct':10})]:
            self.http('POST',path,data,expected=403)
        self.login(self.operator,self.test_password)
        self.http('POST','/qa',{'decision':1},expected=403)
        self.http('POST','/test/refill',{'material_id':'A','confirmed_full':True},expected=403,csrf=False)
        self.http('POST','/test/refill',{'material_id':'A'},expected=400)
        self.http('POST','/test/refill',{'material_id':'D','confirmed_full':True},expected=400)
        self.http('POST','/test/height',{'material_id':'A','height_pct':101},expected=400)
        self.login(self.qa,self.test_password)
        self.http('POST','/order',{'recipe':'recipe-01'},expected=403)
        self.http('POST','/test/refill',{'material_id':'A','confirmed_full':True},expected=403)
        self.http('POST','/test/height',{'material_id':'A','height_pct':10},expected=403)
        self.report('역할별 권한·보충 확인 필수·미등록 원료·잘못된 높이·CSRF 차단')
        self.login(self.operator,self.test_password)
        self.height('A',20.0)
        if self.stock()['A']['height_low_latched']: raise CheckFailed('20% 경계를 부족으로 오판')
        first=self.order('normal')
        self.report('높이20%는 차단하지 않음 · recipe-01 주문 수락')
        self.wait('운전 중 보충 금지 상태',lambda:not self.guard()['inventory'].get('can_refill'))
        self.http('POST','/test/refill',{'material_id':'A','confirmed_full':True},expected=503)
        self.direct_refill_rejected()
        self.report('운전 중 개별 보충 HTTP·직접 ROS 요청 차단')
        self.post('/interlock',{'request':1,'reason':'INSPECTION'})
        state=self.wait('ENTER 안전 이동 후 PAUSED',lambda:self.mode('PAUSED',first))
        if state.get('station')!='test_safe': raise CheckFailed('가상 안전 위치 도착 전 진입 허가')
        self.wait('시험 보충 허용 상태',lambda:self.guard()['inventory'].get('can_refill'))
        self.refill('C')
        time.sleep(.3)
        if not self.mode('PAUSED',first): raise CheckFailed('보충만으로 자동 재개됨')
        self.post('/interlock',{'request':2,'reason':'INSPECTION'})
        self.wait('EXIT 재개',lambda:self.mode('RUNNING',first))
        self.report('ENTER 허가 후 개별 보충 · PAUSED 유지 · EXIT로만 재개')
        self.wait('정상 DONE',lambda:self.mode('DONE',first))
        rec=self.wait('정상 SQLite 기록',lambda:self.record(first))
        subjects={w.get('subject') for w in rec['weights']}
        if not {'scoop','container'}<=subjects or not all(w.get('samples')==20 for w in rec['weights']):
            raise CheckFailed('subject/samples 저장 누락')
        self.wait('recipe-01 소비 반영',lambda:abs(self.stock()['A']['remaining_g']-79.0)<1e-6)
        self.report('WeightReading subject/samples·ScoopCycle·결과3·SQLite 완료 저장')
        self.order_rejected('recipe-02')
        self.direct_order_rejected()
        self.report('g 부족 → HMI HTTP·직접 ROS 주문 거부 · 배치/재고 불변')
        self.refill('A')
        self.report('A만1,000g 보충 · B/C 불변 · 다음 주문 가능')
        before_c=self.amounts()['C']
        second=self.order('normal','recipe-02')
        self.wait('recipe-02 DONE',lambda:self.mode('DONE',second))
        rec2=self.wait('recipe-02 원료2 기록',lambda:self.record(second,count=2))
        if {it['material_id'] for it in rec2['items']}!={'A','B'} or self.amounts()['C']!=before_c:
            raise CheckFailed('recipe-02에서 C가 처리 또는 차감됨')
        a_cycles=sorted((c for c in rec2['scoop_cycles'] if c['material_id']=='A'),key=lambda c:c['attempt'])
        if [(c['attempt'],c['actual_before_g'],c['delivered_g']) for c in a_cycles]!=[(1,0.0,79.0),(2,79.0,79.0)]:
            raise CheckFailed('158g 시험 스쿠핑의 79g×2 시도·누적량 기록 불일치')
        a_result=next(it for it in rec2['items'] if it['material_id']=='A')
        if a_result['attempts']!=2 or any(c['payload']['weigh_pose_id']!='workbench' for c in a_cycles):
            raise CheckFailed('158g 결과 attempts2 또는 공용 계량 위치 ID 불일치')
        self.report('158g 시험 분주 → 79g×2 시도·누적79g·attempts2·workbench 기록')
        third=self.order('normal','recipe-03')
        self.wait('recipe-03 DONE',lambda:self.mode('DONE',third))
        self.wait('recipe-03 원료3 기록',lambda:self.record(third))
        self.report('recipe-02 A/B만 처리·C 불변 및 recipe-03 정상 완료')
        fourth=self.order('overfill')
        pending=self.pending(fourth,'OVERFILL')
        self.decide(fourth,pending,1)
        rec4=self.wait('QA 승인 기록',lambda:self.record(fourth))
        if not any(d.get('decision')=='APPROVED' and d.get('operator_id')==self.qa for d in rec4['deviations']):
            raise CheckFailed('QA 판정자 또는 승인 기록 누락')
        self.report('OVERFILL → QA 승인 → 후속 원료 처리·로그인 판정자 기록')
        fifth=self.order('batch_out_of_spec','recipe-03')
        pending=self.pending(fifth,'BATCH_OUT_OF_SPEC')
        before_discard=self.amounts()
        self.decide(fifth,pending,2)
        self.wait('검증 일탈 폐기 기록',lambda:self.record(fifth,'DISCARDED'))
        if self.amounts()!=before_discard: raise CheckFailed('폐기 시 이미 사용한 재고가 복구됨')
        self.report('BATCH_OUT_OF_SPEC → QA 폐기 → 반송 완료 뒤 DISCARDED · 소비 재고 유지')
        before_wrong=self.amounts()
        sixth=self.order('wrong_tool','recipe-02')
        pending=self.pending(sixth,'WRONG_TOOL')
        self.decide(sixth,pending,2)
        self.wait('도구 일탈 폐기 기록',lambda:self.record(sixth,'DISCARDED',0))
        if self.amounts()!=before_wrong: raise CheckFailed('미투입 폐기에서 원료가 소비되거나 복구됨')
        self.report('WRONG_TOOL → QA 폐기 · 미사용 예약 해제·원료 소비 없음')
        # 계량 무효를 QA 가 승인해 투입량을 모르는 채 끝난 배치 (계약 v1.8 INVALID · SOT D-32).
        kpi_before=self.get('/kpi')
        seventh=self.order('weigh_invalid')
        pending=self.pending(seventh,'WEIGH_INVALID')
        self.decide(seventh,pending,1)
        rec7=self.wait('미측정 승인 완료 기록',lambda:self.record(seventh,'DONE_UNMEASURED'))
        invalid=[i for i in rec7['items'] if i.get('verdict')=='INVALID']
        if len(invalid)!=1: raise CheckFailed(f'INVALID 판정 원료가 1건이 아님: {len(invalid)}')
        if not any(e.get('code')=='BATCH_UNMEASURED' for e in rec7.get('events',[])):
            raise CheckFailed('BATCH_UNMEASURED 이벤트가 기록되지 않음')
        if not any(not w.get('valid') for w in rec7.get('weights',[])):
            raise CheckFailed('무효 계량(valid=false)이 기록되지 않음')
        self.report('계량 무효 → QA 승인 → verdict=INVALID·BATCH_UNMEASURED·DONE_UNMEASURED 기록')
        kpi=self.get('/kpi')
        if kpi.get('unmeasured_done',0)!=kpi_before.get('unmeasured_done',0)+1:
            raise CheckFailed('KPI 미측정 승인 완료 건수가 늘지 않음')
        if kpi.get('batch_success_pct') is None or kpi.get('run_complete_pct') is None:
            raise CheckFailed('KPI 두 지표(batch_success_pct·run_complete_pct)가 없음')
        if kpi['run_complete_pct']<kpi['batch_success_pct']-1e-6:
            raise CheckFailed('완주율이 계량 검증 완료율보다 작음 — 미측정이 합산되지 않음')
        self.report('KPI 두 지표 — 계량 검증 완료율(DONE만)·미측정 승인 완료·완주율(합)')
        before_empty=self.amounts()
        eighth=self.order('material_empty','recipe-02')
        pending=self.pending(eighth,'MATERIAL_EMPTY')
        self.decide(eighth,pending,2)
        self.wait('원료 소진 폐기 기록',lambda:self.record(eighth,'DISCARDED',0))
        if self.amounts()!=before_empty: raise CheckFailed('미투입 폐기에서 원료가 소비되거나 복구됨')
        self.report('MATERIAL_EMPTY(kind 5) → QA 폐기 · 원료 소비 없음')
        self.login(self.operator,self.test_password)
        self.height('A',19.9); self.height('B',19.9)
        self.wait('원료2개 높이 부족',lambda:self.blocked(['A','B']))
        self.order_rejected()
        self.direct_order_rejected({'A':1.0})
        self.height('A',90.0)
        if not self.stock()['A']['height_low_latched']: raise CheckFailed('높이 회복 신호만으로 부족이 해제됨')
        self.refill('A')
        if not self.blocked(['B']): raise CheckFailed('A 보충이 B 높이 부족까지 해제함')
        self.order_rejected()
        self.refill('B')
        if not self.blocked([]): raise CheckFailed('개별 보충 후 부족 해제 실패')
        self.report('높이19.9% 잠금·회복 신호로 해제 안 됨·복수 부족 개별 보충')
        seventh=self.order('normal')
        self.wait('스쿠핑 단계',lambda:self.guard()['state'].get('step')=='SCOOP')
        self.height('A',19.9)
        self.wait('운전 중 높이 차단',lambda:self.blocked(['A']))
        before=self.amounts()
        before_count=len(self.get('/batch/'+seventh).get('items',[]))
        before_index=self.guard()['state'].get('item_index')
        time.sleep(2.3)
        if self.amounts()!=before or len(self.get('/batch/'+seventh).get('items',[]))!=before_count or self.guard()['state'].get('item_index')!=before_index:
            raise CheckFailed('높이 부족 중 원료 소비 또는 다음 단계 진행')
        self.http('POST','/test/refill',{'material_id':'A','confirmed_full':True},expected=503)
        self.direct_refill_rejected()
        self.height('B',19.9)
        self.post('/interlock',{'request':1,'reason':'REFILL'})
        self.wait('높이 부족 ENTER',lambda:self.mode('PAUSED',seventh))
        self.wait('ENTER 보충 허용',lambda:self.guard()['inventory']['can_refill'])
        self.refill('A')
        denied=self.http('POST','/interlock',{'request':2,'reason':'REFILL'})
        if denied.get('ok') is not False or not self.mode('PAUSED',seventh) or not self.blocked(['B']):
            raise CheckFailed('B 부족이 남았는데 EXIT 재개됨')
        self.refill('B')
        time.sleep(.3)
        if not self.mode('PAUSED',seventh): raise CheckFailed('높이 부족 보충 후 EXIT 없이 재개')
        self.post('/interlock',{'request':2,'reason':'REFILL'})
        self.wait('높이 부족 해소 DONE',lambda:self.mode('DONE',seventh))
        self.wait('높이 부족 배치 완료 기록',lambda:self.record(seventh))
        self.report('운전 중 높이 부족 → 진행 차단 → ENTER·개별 보충·EXIT 후 완료')
        self.extra_checks()
        self.login(self.admin,self.password)
        history=self.get('/history')
        if not set(self.batches)<={b['batch_id'] for b in history}: raise CheckFailed('배치 이력 누락')
        audit=self.get('/audit')
        if not any(row.get('actor')==self.operator and row.get('action')=='ORDER' for row in audit): raise CheckFailed('작업자 감사 기록 누락')
        if any(row.get('actor')=='SPOOFED_ACTOR' for row in audit): raise CheckFailed('클라이언트 actor 위조 허용')
        csv_data,_=self.http('GET','/history.csv',raw=True)
        json_data,_=self.http('GET','/batch/'+first+'/download',raw=True)
        if first.encode() not in csv_data or json.loads(json_data).get('batch_id')!=first: raise CheckFailed('CSV/JSON 다운로드 내용 불일치')
        if self.get('/kpi').get('batches',0)<len(self.batches): raise CheckFailed('KPI 배치 집계 누락')
        self.wait('개별 보충 감사 기록',lambda:any(row.get('actor')==self.operator and row.get('action')=='TEST_REFILL' for row in self.get('/audit')))
        self.report(f'배치 이력{len(self.batches)}·KPI·개별 보충 감사 기록·actor 위조 방지·CSV/JSON 다운로드')
        summary={'result':'PASS','transport':self.transport,'checks':len(self.passed),'check_details':self.passed,'batches':self.batches,
                 'scope':'actual HMI/record/mock code + HTTP + SQLite; fake process only, no physical robot'}
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:5002')
    args=parser.parse_args()
    try: RosHttpCheck(args.base_url).run()
    except (CheckFailed,error.URLError,TimeoutError,subprocess.TimeoutExpired,ValueError,KeyError) as exc:
        print('FAIL  '+str(exc),file=sys.stderr); return 1
    return 0

if __name__=='__main__': raise SystemExit(main())
