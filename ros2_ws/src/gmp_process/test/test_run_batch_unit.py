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
        def __init__(self, *a, **kw): self.params = {}; self.published = {}
        def declare_parameters(self, _, pairs): self.params.update(pairs)
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
