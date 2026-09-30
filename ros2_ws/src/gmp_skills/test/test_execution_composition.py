"""호환 fixture를 통하지 않고 실제 노드 조립과 단일 워커를 검증한다."""
from pathlib import Path
import threading
from types import SimpleNamespace

import yaml

from test_skill_node_weigh_held import _load_skill_node


ROOT = Path(__file__).resolve().parents[3]


def test_node_constructs_same_ros_endpoints_and_one_worker(monkeypatch):
    params = yaml.safe_load((ROOT / 'src/gmp_bringup/params/common.yaml').read_text())['/**']['ros__parameters']
    params['stations_file'] = str(ROOT / 'src/gmp_bringup/params/stations.yaml')
    flat = {}

    def flatten(values, prefix=''):
        for key, value in values.items():
            if isinstance(value, dict):
                flatten(value, prefix + key + '.')
            else:
                flat[prefix + key] = value
    flatten(params)
    actions, services, threads = [], [], []
    logger = SimpleNamespace(info=lambda *_: None)

    class FakeNode:
        def __init__(self, name, **kwargs):
            assert name == 'skill_node'

        def get_parameter(self, name):
            return SimpleNamespace(value=flat[name])

        def get_logger(self):
            return logger

        def get_clock(self):
            return SimpleNamespace(now=lambda: SimpleNamespace(nanoseconds=0))

        def create_publisher(self, *args, **kwargs):
            return SimpleNamespace(publish=lambda *_: None)

        def create_service(self, kind, name, callback, **kwargs):
            services.append((name, callback))

        def create_subscription(self, *args, **kwargs):
            pass

        def create_client(self, *args, **kwargs):
            return object()

        def create_timer(self, *args, **kwargs):
            pass

    class FakeThread:
        def __init__(self, **kwargs):
            self.target = kwargs['target']
            self.started = False
            threads.append(self)

        def start(self):
            self.started = True

    module = _load_skill_node(monkeypatch, node_base=FakeNode)
    monkeypatch.setattr(module, 'DsrArm', lambda *a, **kw: SimpleNamespace())
    monkeypatch.setattr(module, 'Rg2Gripper', lambda *a, **kw: SimpleNamespace())
    monkeypatch.setattr(module, 'ActionServer', lambda node, kind, name, callback, **kw: actions.append((name, callback)))
    monkeypatch.setattr(module.threading, 'Thread', FakeThread)
    node = module.RealSkillNode()
    assert [name for name, _ in actions] == ['move_to_station', 'scoop', 'pour', 'return_material', 'weigh_container', 'weigh_held']
    assert [name for name, _ in services] == ['set_gripper', 'measure_force', 'recover_safety', 'safe_pose', 'emergency_stop', 'restore_grip']
    assert len(threads) == 1 and threads[0].started
    assert threads[0].target == node.execution.runtime._worker
    assert node.ctx.arm.cancel_requested == node.execution.runtime._cancel_requested
    assert node.ctx.gripper.cancel_requested == node.execution.runtime._cancel_requested
    for component in vars(node.execution).values():
        assert component.ctx is node.ctx
    assert node.ctx.state.q.get_nowait() is node.ctx.state.startup_job
    assert node.ctx.state.startup_job.kind == 'startup'
    # 취소 콜백이 실행 객체와 같은 현재 작업을 취소해야 한다.
    job = module.Job('scoop', {})
    node.ctx.state.current = job
    assert node._on_cancel(None) == module.CancelResponse.ACCEPT
    assert job.cancel


def test_real_composition_dispatch_and_shutdown_use_single_worker(monkeypatch):
    module = _load_skill_node(monkeypatch)
    from gmp_skills.execution import ExecutionContext, SkillExecution
    from gmp_skills.execution.context import SkillConfig, SkillState

    calls = []
    def record(name):
        calls.append((name, threading.get_ident()))
    ctx = ExecutionContext(
        parameter=lambda key: SimpleNamespace(value={'scale.period_s': .1, 'scale.simulated': False}[key]),
        clock=lambda: None, now=lambda: 0., logger=lambda: SimpleNamespace(error=lambda *_: None),
        event=lambda *_: None, ok=lambda: True,
        config=SkillConfig(mode='virtual', shutdown_timeout_s=1.),
        state=SkillState(ready=True),
        arm=SimpleNamespace(
            measure_force=lambda *a, **kw: (record('measure') or ([0.]*6, 0., 0., True)),
            stop_motion=lambda: record('stop'), compliance_off=lambda: record('release')))
    execution = SkillExecution(ctx)
    worker = threading.Thread(target=execution.runtime._worker)
    ctx.state.worker_thread = worker
    worker.start()
    job = module.Job('measure', {'samples': 3, 'settle_s': .1})
    try:
        ctx.state.q.put(job)
        assert job.done.wait(2)
        assert not job.error
        assert job.result == ([0.]*6, 0., 0., True, '')
    finally:
        assert execution.runtime.shutdown()
    assert [name for name, _ in calls] == ['measure', 'stop', 'release']
    assert {ident for _, ident in calls} == {worker.ident}
    assert worker.ident != threading.get_ident()
    assert ctx.state.worker_stopped.is_set()
    assert execution.runtime._submit('measure').error == 'skill_node shutdown'


def test_runtime_keeps_return_history_until_scoop_handler_runs(monkeypatch):
    """9/30 실물: 실행기가 scoop 작업 시작 전에 returned_material 을 지워 반환 끝 → 재스쿱 연결이
    언제나 거부됐다(「반환 후 재스쿱 연결 경로 미구현」 3회). 연결 판정은 핸들러가 한다."""
    _load_skill_node(monkeypatch)
    from gmp_skills.execution import ExecutionContext, SkillExecution
    from gmp_skills.execution.context import SkillConfig, SkillState
    from gmp_skills.execution.runtime import Job

    ctx = ExecutionContext(
        parameter=lambda key: SimpleNamespace(value={'scale.period_s': .1, 'scale.simulated': False}[key]),
        clock=lambda: None, now=lambda: 0., logger=lambda: SimpleNamespace(error=lambda *_: None),
        event=lambda *_: None, ok=lambda: True,
        config=SkillConfig(mode='virtual', shutdown_timeout_s=1.),
        state=SkillState(ready=True),
        arm=SimpleNamespace(stop_motion=lambda: None, compliance_off=lambda: None))
    execution = SkillExecution(ctx)
    seen = []
    execution.runtime.handlers['scoop'] = lambda job: seen.append(ctx.state.returned_material)
    execution.runtime.handlers['pour'] = lambda job: seen.append(ctx.state.returned_material)
    worker = threading.Thread(target=execution.runtime._worker)
    ctx.state.worker_thread = worker
    worker.start()
    try:
        for kind in ('scoop', 'pour'):
            ctx.state.returned_material = 'C'
            job = Job(kind, {'material_id': 'C'})
            ctx.state.q.put(job)
            assert job.done.wait(2) and not job.error, job.error
    finally:
        assert execution.runtime.shutdown()
    assert seen == ['C', '']          # scoop 은 이력을 받고, 다른 작업은 종전대로 지운 뒤 시작한다
