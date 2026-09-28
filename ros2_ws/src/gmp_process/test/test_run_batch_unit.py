"""실제 ProcessNode 메서드 + ROS 통신 대역. DDS/로봇 검증은 test_run_batch_ros.py."""
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace as NS

import pytest

PKG = Path(__file__).resolve().parents[1]


class Message:
    def __init__(self, **kw):
        self.header = NS(stamp=NS(sec=0, nanosec=0))
        self.batch_id = ''; self.material_id = ''; self.note = ''; self.station = ''
        self.items = []; self.product = ''; self.success = False
        self.items_done = 0; self.deviations = 0; self.message = ''
        self.__dict__.update(kw)


class Handle:
    def __init__(self):
        self.is_active = True; self.is_cancel_requested = False
        self.feedback = []; self.terminal = None
    def execute(self): pass
    def publish_feedback(self, f): self.feedback.append(deepcopy(f))
    def canceled(self): self.terminal = 'canceled'; self.is_active = False
    def succeed(self): self.terminal = 'succeeded'; self.is_active = False
    def abort(self): self.terminal = 'aborted'; self.is_active = False


def ready(result):
    f = Future(); f.set_result(result); return f


@pytest.fixture
def module(monkeypatch):
    """별도 이름으로 로드해 다른 ROS 테스트 모듈에 대역을 남기지 않는다."""
    class Base:
        def __init__(self, *a, **kw):
            self.params = {p.name: p.value for p in kw.get('parameter_overrides', [])}
            self.published = {}
        def declare_parameters(self, _, pairs):
            for key, value in pairs:
                self.params.setdefault(key, value)
        def get_parameter(self, k): return NS(value=self.params[k])
        def create_publisher(self, _type, key, _qos):
            self.published[key] = []
            return NS(publish=lambda m: self.published[key].append(deepcopy(m)))
        def create_client(self, *a, **kw): return NS()
        def create_service(self, *a, **kw): pass
        def create_subscription(self, *a, **kw): pass
        def create_timer(self, *a, **kw): pass
        def get_clock(self): return NS(now=lambda: NS(nanoseconds=1000000000, to_msg=lambda: NS(sec=1,nanosec=0)))
        def get_logger(self): return NS(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None)
    modules = {}
    def stub(name, **attrs):
        m = ModuleType(name); m.__dict__.update(attrs); modules[name] = m; return m
    ros = stub('rclpy', ok=lambda: True)
    act = stub('rclpy.action', ActionClient=lambda *a, **k: NS(), ActionServer=lambda *a, **k: NS(**k),
               CancelResponse=NS(ACCEPT=1, REJECT=0), GoalResponse=NS(ACCEPT=1, REJECT=0))
    stub('rclpy.callback_groups', ReentrantCallbackGroup=lambda: None)
    stub('rclpy.executors', MultiThreadedExecutor=Base)
    stub('rclpy.node', Node=Base)
    stub('rclpy.qos', DurabilityPolicy=NS(TRANSIENT_LOCAL=1), QoSProfile=lambda **kw: NS(**kw))
    stub('gmp_interfaces')
    msgs={}
    for f in (PKG.parent/'gmp_interfaces/msg').glob('*.msg'):
        attrs={}
        for line in f.read_text().splitlines():
            bits=line.split('#')[0].strip().split()
            if len(bits)==2 and '=' in bits[1]:
                k,v=bits[1].split('=',1)
                try: attrs[k]=int(v)
                except ValueError: pass
        msgs[f.stem]=type(f.stem,(Message,),attrs)
    stub('gmp_interfaces.msg', **msgs)
    interface=lambda: NS(Goal=Message,Result=Message,Feedback=Message,Request=type('Request',(Message,),{'ENTER':0,'EXIT':1}),Response=Message)
    stub('gmp_interfaces.action', **{f.stem:interface() for f in (PKG.parent/'gmp_interfaces/action').glob('*.action')})
    stub('gmp_interfaces.srv', **{f.stem:interface() for f in (PKG.parent/'gmp_interfaces/srv').glob('*.srv')})
    with monkeypatch.context() as mp:
        for name, value in modules.items(): mp.setitem(sys.modules,name,value)
        spec=importlib.util.spec_from_file_location('_run_batch_node_under_test',PKG/'gmp_process/nodes/process_node.py')
        mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def node(module):
    n=module.ProcessNode()
    n.smap=module.StationMap(scoops={'A':'scoop_1','B':'scoop_2'},materials={'A':'material_1','B':'material_2'})
    n._call_srv=lambda *a: Message(success=True)
    return n


def recipe(batch='B1', target=40):
    return Message(batch_id=batch,product='test',items=[Message(material_id='A',target_g=target,tol_pct=5)])


def accept(node, r=None):
    assert node._goal_batch(Message(recipe=r or recipe())) == 1
    h=Handle(); node._accept_batch(h); return h


def test_action_server_uses_existing_name_and_callbacks(node):
    assert node.batch_server.goal_callback == node._goal_batch
    assert node.batch_server.cancel_callback == node._cancel_batch


def test_fixed_scoop_parameter_is_wired_to_dosing_config(module):
    """운영 YAML의 고정 스쿱 플래그가 ProcessFSM까지 전달돼야 #270 분기가 실제로 켜진다."""
    n = module.ProcessNode(parameter_overrides=[NS(name='dosing.fixed_scoop', value=True)])
    assert n.dosing_cfg.fixed_scoop is True


def test_reservation_rejects_parallel_action_and_service(node):
    with ThreadPoolExecutor(8) as pool:
        results=list(pool.map(lambda i: node._goal_batch(Message(recipe=recipe('B'+str(i)))),range(8)))
    assert results.count(1)==1
    assert not node._srv_submit(Message(recipe=recipe('SVC')),Message()).accepted


@pytest.mark.parametrize('r',[recipe(target=0),recipe(target=float('nan')),Message(items=[]),
    Message(items=[Message(material_id='unknown',target_g=40,tol_pct=5)]),
    Message(items=recipe().items*2)])
def test_bad_recipe_never_reserves_slot(node,r):
    assert node._goal_batch(Message(recipe=r))==0
    assert not node._reserved and node.fsm is None


@pytest.mark.parametrize('flag',['_safety_stop','_pause','_nudge_paused','_execution_uncertain'])
def test_gate_rejects_order(node,flag):
    setattr(node,flag,True)
    assert node._goal_batch(Message(recipe=recipe()))==0


def test_empty_id_is_unique_and_used_id_rejected(node):
    with node._order_lock: node._reserve_batch(recipe(''))
    first=node._reserved_recipe[1]
    node._reserved=False
    with node._order_lock: node._reserve_batch(recipe(''))
    assert first!=node._reserved_recipe[1]
    node._reserved=False;node._used_batch_ids.add('B1')
    assert node._goal_batch(Message(recipe=recipe()))==0


def test_cancel_before_execute_never_dispatches_skill(node):
    h=accept(node)
    assert node._cancel_batch(h)==1
    h.is_cancel_requested=True
    node._dispatch=lambda _: pytest.fail('취소 후 스킬을 실행했다')
    result=node._execute_batch(h)
    assert (result.result,result.success,h.terminal)==('ABORTED',False,'canceled')
    assert node.fsm.mode=='ERROR' and not node._reserved
    assert any(e.code=='BATCH_END' for e in node.published['event'])


def test_wrong_goal_and_finished_goal_cannot_cancel(node):
    h=accept(node)
    assert node._cancel_batch(Handle())==0
    node._batch_done.set()
    assert node._cancel_batch(h)==0
    assert not node._batch_cancel.is_set()


@pytest.mark.parametrize('wait_kind',['wait_qa','wait_interlock','wait_nudge','gate'])
def test_cancel_interrupts_human_waits(node,module,wait_kind):
    h=accept(node)
    node.fsm=NS(mode='RUNNING',state='WAIT',idx=0)
    entered=threading.Event()
    def waiting():
        entered.set()
        with pytest.raises(module.BatchCancelled):
            if wait_kind=='gate':node._pause=True;node._gate()
            else:node._dispatch({'kind':wait_kind})
    with ThreadPoolExecutor(1) as p:
        f=p.submit(waiting);assert entered.wait(1)
        assert node._cancel_batch(h)==1
        f.result(timeout=2)


def test_cancel_does_not_release_slot_until_active_skill_finishes(node,module):
    h=accept(node)
    result_future=Future(); requested=threading.Event()
    skill=NS(accepted=True,get_result_async=lambda:result_future,
             cancel_goal_async=lambda:requested.set())
    node.act['move']=NS(wait_for_server=lambda **kw:True, send_goal_async=lambda _:ready(skill))
    node._dispatch=lambda _:node._call_act('move',Message())
    with ThreadPoolExecutor(1) as pool:
        f=pool.submit(node._execute_batch,h)
        while not node._thread:time.sleep(.001)
        assert node._cancel_batch(h)==1;h.is_cancel_requested=True
        assert requested.wait(1)
        assert not f.done()
        assert node._goal_batch(Message(recipe=recipe('NEW')))==0
        result_future.set_result(NS(result=Message(success=True)))
        assert f.result(timeout=2).result=='ABORTED'
    assert h.terminal=='canceled'


def test_timeout_cannot_report_success_or_admit_new_batch(node):
    h=accept(node);node.params['skill_timeout_s']=.03
    skill=NS(accepted=True,get_result_async=lambda:Future(),cancel_goal_async=lambda:None)
    node.act['move']=NS(wait_for_server=lambda **kw:True,send_goal_async=lambda _:ready(skill))
    node._dispatch=lambda _:node._call_act('move',Message())
    result=node._execute_batch(h)
    assert result.result=='ERROR' and node._execution_uncertain
    assert node._goal_batch(Message(recipe=recipe('NEXT')))==0


def test_late_skill_acceptance_is_canceled(node,module):
    h=accept(node);node.params['server_wait_s']=.01
    pending=Future();calls=[]
    node.act['move']=NS(wait_for_server=lambda **kw:True,send_goal_async=lambda _:pending)
    with pytest.raises(module.SkillError):node._call_act('move',Message())
    pending.set_result(NS(accepted=True,cancel_goal_async=lambda:calls.append('cancel')))
    assert calls==['cancel'] and node._execution_uncertain


def test_safety_stop_stays_fatal_even_after_recovery_flag_clears(node):
    h=accept(node)
    node._on_safety_stop(Message(text='{}'))
    node._safety_stop=False  # 전송 순서가 빨라 복구 이벤트가 먼저 반영된 상황
    node._dispatch=lambda _:pytest.fail('안전정지 배치를 재개했다')
    result=node._execute_batch(h)
    assert result.result=='ERROR' and h.terminal=='aborted'


def test_actual_fsm_done_feedback_results_and_next_order(node):
    from test_process_fsm import Cell
    cell=Cell([40],residual=0)
    def dispatch(req):
        out=cell(req)
        if req['kind'] in ('weigh','weigh_scoop'):
            out.update(tare_g=req.get('tare_g',0.0),net_g=out['gross_g']-req.get('tare_g',0.0),std_g=0.0,samples=4,station='workbench')
        return out
    node._dispatch=dispatch
    h=accept(node);r=node._execute_batch(h)
    assert (r.result,r.success,r.items_done,r.deviations)==('DONE',True,1,0)
    assert h.terminal=='succeeded'
    assert any(f.last_result.material_id=='A' for f in h.feedback)
    assert all(f.state.batch_id=='B1' for f in h.feedback)
    assert node._goal_batch(Message(recipe=recipe('B2')))==1


def test_discard_result_is_not_success(node):
    from test_process_fsm import Cell
    cell=Cell([100,100,100],residual=0,qa='DISCARDED')
    def dispatch(req):
        out=cell(req)
        if req['kind'] in ('weigh','weigh_scoop'):
            out.update(tare_g=0.0,net_g=out['gross_g'],std_g=0.0,samples=4,station='material_1')
        return out
    node._dispatch=dispatch
    h=accept(node);r=node._execute_batch(h)
    assert r.result=='DISCARDED' and not r.success and r.deviations>0
    assert h.terminal=='succeeded'


def test_service_kept_and_action_blocked_during_service(node):
    block=threading.Event();entered=threading.Event()
    def dispatch(req): entered.set();block.wait(2);node._batch_cancel.set();return {}
    node._dispatch=dispatch
    assert node._srv_submit(Message(recipe=recipe()),Message()).accepted
    assert entered.wait(1)
    assert node._goal_batch(Message(recipe=recipe('B2')))==0
    block.set();node._thread.join(2)
    assert not node._reserved


def test_cancel_with_missing_skill_result_is_error_not_confirmed_cancel(node):
    h=accept(node);node.params['skill_timeout_s']=.04
    started=threading.Event()
    def get_result(): started.set();return Future()
    skill=NS(accepted=True,get_result_async=get_result,cancel_goal_async=lambda:None)
    node.act['move']=NS(wait_for_server=lambda **kw:True,send_goal_async=lambda _:ready(skill))
    node._dispatch=lambda _:node._call_act('move',Message())
    with ThreadPoolExecutor(1) as p:
        f=p.submit(node._execute_batch,h);assert started.wait(1)
        node._cancel_batch(h);h.is_cancel_requested=True
        r=f.result(timeout=2)
    assert r.result=='ERROR' and h.terminal=='aborted' and node._execution_uncertain


def test_service_wait_rechecks_cancel_before_sending(node,module):
    h=accept(node);calls=[]
    def available(**kw):node._batch_cancel.set();return True
    node.srv['grip']=NS(wait_for_service=available,call_async=lambda _:calls.append('called'))
    with pytest.raises(module.BatchCancelled):module.ProcessNode._call_srv(node,'grip',Message())
    assert not calls


def test_shutdown_interrupts_wait_even_when_event_already_set(node,module):
    node._stop.set();event=threading.Event();event.set()
    with pytest.raises(module.BatchCancelled):node._await(event,'QA')



def test_enter_between_acceptance_and_execution_is_not_cleared(node):
    accept(node)
    node._pause=True
    node._interlock_exit.set()
    node._reset_batch()
    assert node._pause and node._interlock_exit.is_set()


def test_service_worker_start_failure_does_not_leak_slot(node):
    node._start_reserved_batch=lambda:(_ for _ in ()).throw(RuntimeError('thread start failed'))
    assert not node._srv_submit(Message(recipe=recipe()),Message()).accepted
    assert not node._reserved and node._execution_uncertain


def test_final_record_failure_returns_error_and_blocks_orders(node):
    h=accept(node);node._batch_cancel.set();h.is_cancel_requested=True
    node._drain=lambda:(_ for _ in ()).throw(RuntimeError('record failure'))
    r=node._execute_batch(h)
    assert r.result=='ERROR' and h.terminal=='aborted' and node._execution_uncertain
    assert node._goal_batch(Message(recipe=recipe('NEXT')))==0


def test_nudge_wait_rejection_preserves_operator_instruction(node):
    node.fsm = NS(mode='PAUSED', state='NUDGE_WAIT')
    reply = node._srv_submit(Message(recipe=recipe()), Message())
    assert not reply.accepted
    assert reply.message == '세트 완료 — 로봇을 건드리면 다음 주문을 받는다 (NUDGE_WAIT)'


def test_idle_pause_reason_is_published_and_cleared(node):
    node._nudge_paused = True
    node._pub_state()
    assert '다시 건드리면' in node.published['state'][-1].note
    assert node._goal_batch(Message(recipe=recipe())) == 0
    node._nudge_paused = False
    node._pub_state()
    assert 'NUDGE 일시 정지' not in node.published['state'][-1].note
    node._pause = True
    node._pub_state()
    assert 'EXIT' in node.published['state'][-1].note


def test_generated_date_id_skips_explicit_used_id(node):
    node._used_batch_ids.add('B-19700101-001')
    with node._order_lock:
        node._reserve_batch(recipe(''))
    assert node._reserved_recipe[1] == 'B-19700101-002'


def test_refill_exit_between_safe_and_wait_is_not_lost(node):
    # idx 는 _pub_state 가 읽는다 — safe 가 정지 사유를 note 에 실으며 발행한다 (#191). 163행과 같은 규약.
    node.fsm = NS(mode='PAUSED', state='PAUSED', idx=0)
    assert node._dispatch({'kind': 'safe', 'reason': 'REFILL', 'then': 'wait_interlock'})['success']
    assert node._refill_waiting
    enter = node._srv_interlock(Message(request=0, reason='REFILL'), Message())
    assert enter.granted and enter.message.startswith('이미')
    assert node._srv_interlock(Message(request=1, reason='REFILL'), Message()).granted
    assert node._interlock_exit.is_set()
    node._dispatch({'kind': 'wait_interlock'})
    assert not node._refill_waiting and not node._interlock_exit.is_set()


def test_refill_wait_puts_reason_at_head_of_published_note(node):
    """REFILL 대기 중 CellState.note 앞머리에 FSM 이 만든 사유가 실린다 (#191).

    HMI(gmp_hmi core/pause_context.pause_reason)는 note **앞머리**로 정지 사유를 가른다 —
    `^REFILL\\b`. 고치기 전에는 이 구간 내내 note 가 비어 있어서(INTERLOCK 도 아니었다)
    HMI 의 REFILL 분기가 영영 안 떴다.
    """
    node.fsm = NS(mode='PAUSED', state='PAUSED', idx=0)
    node._dispatch({'kind': 'safe', 'reason': 'REFILL', 'then': 'wait_interlock'})
    assert node.note.startswith('REFILL '), node.note
    assert node.published['state'][-1].note.startswith('REFILL '), node.published['state'][-1].note
    assert not node._pause, 'REFILL 대기는 인터락 정지가 아니다'

    # 사유를 하드코딩하지 않으므로 FSM 이 다른 사유를 보내면 그대로 흐른다 (HEIGHT_LOW 등, #192)
    node._dispatch({'kind': 'safe', 'reason': 'HEIGHT_LOW', 'then': 'wait_interlock'})
    assert node.published['state'][-1].note.startswith('HEIGHT_LOW '), node.published['state'][-1].note

    # 대기가 끝나면 내린다
    node._interlock_exit.set()
    node._dispatch({'kind': 'wait_interlock'})
    assert node.note == '' and node.published['state'][-1].note == '', node.published['state'][-1].note


def test_safe_without_interlock_wait_does_not_touch_note(node):
    """`then` 이 wait_interlock 이 아닌 safe(예: ERROR 로 가는 길)는 note 를 건드리지 않는다."""
    node.fsm = NS(mode='RUNNING', state='SCOOP', idx=0)
    node.note = ''
    node._dispatch({'kind': 'safe', 'reason': 'RECOVERY'})
    assert node.note == '' and not node._refill_waiting


def test_refill_enter_does_not_grant_on_paused_mode_alone(node):
    node.fsm = NS(mode='PAUSED', state='PAUSED')
    node.params['server_wait_s'] = 0.01
    reply = node._srv_interlock(Message(request=0, reason='REFILL'), Message())
    assert not reply.granted


# ── 세트 끝 주문 예약 (9/28 조장 제기·사용자 결정 A안) ─────────────────────
# 종전에는 NUDGE_WAIT 중 주문을 거부해 운영자가 「주문 → 거부 → 넛지 → 다시 주문」을 해야 했다.
# 이제 RunBatch 는 세트 끝 구간(FINISH·폐기 반송·NUDGE_WAIT)의 주문을 **한 건 예약**하고, 직전 배치가
# **넛지로 끝났을 때만** 시작한다 — 넛지가 곧 완성품 회수 확인이다(D-23). 그 밖의 끝(취소·안전
# 정지·오류)이면 칸이 비었다는 근거가 없으므로 예약을 시작하지 않고 ABORTED 로 돌려준다.

def _at_set_end(node, state='NUDGE_WAIT', mode='PAUSED'):
    """배치 1 이 세트 끝에 있는 모양 — 루프가 살아 있고 슬롯을 쥐고 있다."""
    node.fsm = NS(mode=mode, state=state, idx=0)
    node._thread = NS(is_alive=lambda: True)
    node._reserved = True
    node._nudge_waiting = state == 'NUDGE_WAIT'
    node._batch_handle = Handle()


def _batch1_ended(node, outcome='DONE', nudged=True):
    ended = outcome if outcome in ('DONE', 'DISCARDED', 'ABORTED') else 'ERROR'
    node.fsm = NS(mode='DONE' if ended in ('DONE', 'DISCARDED') else 'ERROR', state=ended, idx=0)
    node._thread = NS(is_alive=lambda: False)
    node._reserved = False
    node._batch_handle = None
    node._set_next, node._batch_outcome = nudged, outcome


def accept_queued(node, r):
    goal = Message(recipe=r)
    assert node._goal_batch(goal) == 1
    h = Handle(); h.request = goal          # rclpy 는 goal_callback 에 준 요청을 handle.request 로 둔다
    node._accept_batch(h)
    return h


def codes(node):
    return [e.code for e in node.published['event']]


@pytest.mark.parametrize('state,mode', [('NUDGE_WAIT', 'PAUSED'), ('FINISH', 'RUNNING'), ('DISCARDED', 'DONE')])
def test_set_end_order_is_queued_not_rejected(node, state, mode):
    _at_set_end(node, state, mode)
    batch1 = node._batch_handle
    h = accept_queued(node, recipe('B2'))
    assert node._queued[1] == 'B2' and node._queued_handle is h
    assert node._batch_handle is batch1, '예약이 실행 중 배치의 handle 을 덮으면 안 된다'
    assert 'ORDER_QUEUED' in codes(node)


def test_nudge_wait_note_names_the_queued_order(node):
    _at_set_end(node)
    accept_queued(node, recipe('B2'))
    assert node.note.startswith('NUDGE_WAIT —') and 'B2' in node.note, node.note   # HMI 는 앞머리로 사유를 가른다


def test_service_and_mid_batch_orders_are_still_rejected(node):
    _at_set_end(node)
    reply = node._srv_submit(Message(recipe=recipe('SVC')), Message())
    assert not reply.accepted and 'NUDGE_WAIT' in reply.message       # 서비스는 예약을 걸어 둘 곳이 없다
    node.fsm = NS(mode='RUNNING', state='SCOOP', idx=0)
    assert node._goal_batch(Message(recipe=recipe('B2'))) == 0       # 세트 끝이 아니면 종전대로


def test_only_one_order_is_queued_and_it_cannot_be_overtaken(node):
    _at_set_end(node)
    accept_queued(node, recipe('B2'))
    assert node._goal_batch(Message(recipe=recipe('B3'))) == 0
    _batch1_ended(node)
    assert node._goal_batch(Message(recipe=recipe('B3'))) == 0, '예약 주문보다 먼저 슬롯을 잡으면 안 된다'


def test_queued_order_is_promoted_after_nudge(node):
    _at_set_end(node)
    h = accept_queued(node, recipe('B2'))
    _batch1_ended(node, 'DONE', nudged=True)
    assert node._promote_queued(h) == ''
    assert node._batch_handle is h and node._reserved and node._reserved_recipe[1] == 'B2'
    assert node._queued is None and node._queued_handle is None


@pytest.mark.parametrize('outcome,nudged', [('ABORTED', False), ('ERROR', False), ('DONE', False), ('ABORTED', True)])
def test_queued_order_is_dropped_unless_batch_ended_by_nudge(node, outcome, nudged):
    """넛지 없이 끝났으면(취소·오류) 회수 확인이 없다 — 예약을 시작하지 않는다."""
    _at_set_end(node)
    h = accept_queued(node, recipe('B2'))
    _batch1_ended(node, outcome, nudged)
    r = node._execute_batch(h)
    assert r.result == 'ABORTED' and not r.success and h.terminal == 'aborted', r.message
    assert '넛지 없이' in r.message
    assert not node._reserved and node._queued is None
    assert 'ORDER_DROPPED' in codes(node)
    assert node._goal_batch(Message(recipe=recipe('B3'))) == 1, '버린 예약이 슬롯을 막으면 안 된다'


def test_queued_order_can_be_canceled_while_batch1_still_waits(node):
    _at_set_end(node)
    h = accept_queued(node, recipe('B2'))
    assert node._cancel_batch(h) == 1
    h.is_cancel_requested = True
    r = node._execute_batch(h)                      # 배치 1 이 아직 넛지 대기여도 바로 돌아온다
    assert h.terminal == 'canceled' and r.result == 'ABORTED'
    assert node._reserved and node._batch_handle is not h, '배치 1 의 슬롯은 그대로다'
    assert node._queued is None


def test_queued_order_runs_after_real_nudge_wait(node):
    """실제 루프로: 배치 1 이 NUDGE_WAIT 에 서면 주문을 예약하고, 넛지 하나로 배치 1 종료 → 배치 2 완료."""
    from test_process_fsm import Cell
    cells = {'B1': Cell([40], residual=0), 'B2': Cell([40], residual=0)}   # 배치마다 새 용기·스쿱
    real = node._dispatch

    def dispatch(req):
        if req['kind'] == 'wait_nudge':
            return real(req)                        # 넛지 대기는 실제 노드 코드로
        out = cells[node.batch_id](req)
        if req['kind'] in ('weigh', 'weigh_scoop'):
            out.update(tare_g=req.get('tare_g', 0.0), net_g=out['gross_g'] - req.get('tare_g', 0.0),
                       std_g=0.0, samples=4, station='workbench')
        return out
    node._dispatch = dispatch
    h1 = accept(node)
    pool = ThreadPoolExecutor(2)
    try:
        f1 = pool.submit(node._execute_batch, h1)
        deadline = time.monotonic() + 5
        while not node._nudge_waiting and time.monotonic() < deadline:
            time.sleep(0.01)
        assert node._nudge_waiting and node.fsm.state == 'NUDGE_WAIT', (node.fsm.state, node.note)
        h2 = accept_queued(node, recipe('B2'))
        f2 = pool.submit(node._execute_batch, h2)
        time.sleep(0.3)
        assert not f2.done() and node.fsm.state == 'NUDGE_WAIT', '넛지 전에는 시작하지 않는다'
        node._on_event(Message(code='NUDGE'))
        r1 = f1.result(5)
        deadline = time.monotonic() + 5              # 배치 2 도 세트 끝에서 다시 넛지를 기다린다
        while not (node.batch_id == 'B2' and node._nudge_waiting) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert node.batch_id == 'B2' and node._nudge_waiting, (node.batch_id, node.fsm.state)
        node._on_event(Message(code='NUDGE'))
        r2 = f2.result(5)
    finally:
        node._stop.set()                            # 단언이 깨져도 넛지 대기에 걸린 스레드를 푼다
        pool.shutdown(wait=True)
    assert (r1.result, r2.result) == ('DONE', 'DONE'), (r1.message, r2.message)
    assert h1.terminal == h2.terminal == 'succeeded'
    assert h2.feedback and all(f.state.batch_id == 'B2' for f in h2.feedback)
    assert any('예약 주문 B2 시작' in e.text for e in node.published['event'] if e.code == 'SET_NEXT')
    assert not node._reserved and node._queued is None
