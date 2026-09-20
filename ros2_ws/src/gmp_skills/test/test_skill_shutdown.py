"""ROS 문맥 없이 워커 종료 순서·대기 요청 해제를 검증한다."""
import queue
import threading
from types import SimpleNamespace

import pytest
from test_skill_node_weigh_held import _load_skill_node


def worker_node(monkeypatch):
    module = _load_skill_node(monkeypatch)
    monkeypatch.setattr(module.rclpy, 'ok', lambda: True)
    node = module.SkillNode.__new__(module.SkillNode)
    node._q = queue.Queue()
    node._job_lock = threading.Lock()
    node._stopping = threading.Event()
    node._worker_stopped = threading.Event()
    node._current = None
    node.mode = 'virtual'
    node._safety_latched = False
    node._safety_reason = ''
    node._ready = True
    node._cleanup_error = ''
    node.shutdown_timeout_s = 1
    node._poll_nudge = lambda: None
    node.event = lambda *args: None
    node.get_logger = lambda: SimpleNamespace(error=lambda _: None)
    node.calls = []
    node.arm = SimpleNamespace(
        stop_motion=lambda: node.calls.append(('stop', threading.get_ident())),
        compliance_off=lambda: node.calls.append(('release', threading.get_ident())))
    node._worker_thread = threading.Thread(target=node._worker, daemon=True)
    return node, module


def test_shutdown_cancels_current_drains_queue_and_cleans_in_worker(monkeypatch):
    node, module = worker_node(monkeypatch)
    started = threading.Event()

    def execute(job):
        started.set()
        assert node._stopping.wait(1)
        assert job.cancel
        raise RuntimeError('cancelled')

    node._do_test = execute
    active = module.Job('test', {})
    pending = module.Job('never', {})
    node._q.put(active)
    node._q.put(pending)
    node._worker_thread.start()
    assert started.wait(1)
    assert node.shutdown()
    assert active.done.is_set() and pending.done.is_set()
    assert pending.cancel and pending.error
    assert [name for name, _ in node.calls] == ['stop', 'release']
    assert all(tid == node._worker_thread.ident for _, tid in node.calls)
    assert node._submit('never').error
    assert node.shutdown()  # 중복 종료는 해제를 반복하지 않는다.
    assert len(node.calls) == 2


def test_stop_failure_still_attempts_force_release(monkeypatch):
    node, _ = worker_node(monkeypatch)

    def fail():
        raise RuntimeError('stop unavailable')

    node.arm.stop_motion = fail
    node._worker_thread.start()
    assert not node.shutdown()
    assert [name for name, _ in node.calls] == ['release']
    assert 'stop unavailable' in node._cleanup_error


def test_hung_worker_is_not_reported_clean(monkeypatch):
    node, _ = worker_node(monkeypatch)
    node._worker_thread = SimpleNamespace(join=lambda _: None)
    assert not node.shutdown()
    assert '시간 초과' in node._cleanup_error


def test_force_sampling_observer_interrupts_on_shutdown(monkeypatch):
    node, _ = worker_node(monkeypatch)
    node._stopping.set()
    with pytest.raises(RuntimeError, match='cancelled'):
        node._observe_force([0]*6)


def test_scoop_compliance_entry_failure_still_releases_without_retreat(monkeypatch):
    module = _load_skill_node(monkeypatch)
    calls = []

    def fail(_):
        calls.append('compliance_on')
        raise RuntimeError('entry failed')

    node = SimpleNamespace(
        _require_scoop_extracted=lambda: None,
        _held_payload='scoop', _held_material_id='A',
        gripper=SimpleNamespace(state=lambda _: {'busy': False, 'grip_inferred': True}),
        _now_s=lambda: 0, vel_scale=0.3,
        get_parameter=lambda _: SimpleNamespace(value=[1]*6),
        stations=SimpleNamespace(approach_mm=60, for_material=lambda _: SimpleNamespace(above=lambda _: [1]*6)),
        arm=SimpleNamespace(movel=lambda *_: calls.append('move'), current_posx=lambda: [1]*6,
                            compliance_on=fail, compliance_off=lambda: calls.append('release')))
    node.get_parameter = lambda key: SimpleNamespace(value=[1]*6 if key == 'safety.compliance_stx' else 3.0)
    with pytest.raises(RuntimeError, match='entry failed'):
        module.SkillNode._do_scoop(node, module.Job('scoop', {'material_id': 'A'}))
    assert calls == ['move', 'compliance_on', 'release']


def test_cancel_during_container_measurement_keeps_grip_and_pose(monkeypatch):
    module = _load_skill_node(monkeypatch)
    calls = []
    job = module.Job('weigh', {'tare_g': 0})

    def measure(*_):
        job.cancel = True
        raise RuntimeError('cancelled')

    node = SimpleNamespace(
        _require_scoop_extracted=lambda: None,
        get_parameter=lambda key: SimpleNamespace(value={'scale.simulated': True,
            'gripper.cup_width_mm': 30, 'gripper.force_n': 20}[key]),
        stations=SimpleNamespace(approach_mm=60, get=lambda _: SimpleNamespace(posx=[1]*6, above=lambda _: [2]*6)),
        arm=SimpleNamespace(movel=lambda target, _: calls.append(target)), vel_scale=0.3,
        gripper=SimpleNamespace(grip=lambda *_: (True, 30, True), release=lambda _: calls.append('release')),
        _measure_weight_reading=measure)
    with pytest.raises(RuntimeError, match='cancelled'):
        module.SkillNode._do_weigh(node, job)
    assert calls == [[2]*6, [1]*6, [2]*6]


def test_startup_pending_is_nonblocking_and_rejects_motion(monkeypatch):
    node, module = worker_node(monkeypatch)
    node._ready = False
    node._startup_job = module.Job('startup', {})
    node.check_startup()
    assert '자가진단' in node._submit('move').error
    assert node._q.empty()


def test_failed_startup_can_be_cleaned_by_main_owner(monkeypatch):
    node, module = worker_node(monkeypatch)
    node._ready = False
    node._startup_job = module.Job('startup', {}, error='failed')
    node._startup_job.done.set()
    with pytest.raises(RuntimeError, match='자가진단 실패'):
        node.check_startup()
    node._worker_thread.start()
    assert node.shutdown()
