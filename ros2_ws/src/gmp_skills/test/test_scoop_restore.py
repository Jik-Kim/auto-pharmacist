"""스쿱 복원은 읽기 검증만 수행하고 불확실한 상태에서는 이력을 만들지 않는다."""
import threading
from types import SimpleNamespace

import pytest

from gmp_skills.core.transfer import pose_matches
from test_skill_node_weigh_held import _load_skill_node


def restore_node(monkeypatch):
    module = _load_skill_node(monkeypatch)
    node = object.__new__(module.SkillNode)
    pose = [344.0, -298.0, 200.0, 90.0, -180.0, -90.0]
    station = SimpleNamespace(station_id='material_1', posx=pose)
    state = dict(width_mm=24.8, fresh=True, busy=False, grip_inferred=True,
                 safety_triggered=False, slip=False)
    node.mode = 'real'
    node.gripper = SimpleNamespace(backend='modbus', state=lambda _: state)
    # 이동·개폐 메서드는 제공하지 않는다. 호출되면 테스트가 실패한다.
    node.arm = SimpleNamespace(current_posx=lambda: pose,
                              initialize=lambda: None, self_check=lambda *_: (True, 'OK'))
    node.stations = SimpleNamespace(for_material=lambda mid: station if mid == 'A' else (_ for _ in ()).throw(KeyError(mid)))
    node._pose_matches = lambda actual, expected: pose_matches(actual, expected, 2.0, 2.0)
    node._poll_safety = lambda **_: None
    clock = [1.0]
    node._now_s = lambda: clock[0]
    node.state_poll_s = 0.1
    node.get_parameter = lambda _: SimpleNamespace(value=0.3)
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    node._last_robot_state = 1
    node._safety_latched = False
    node._safety_revision = 0
    node._return_rescoop_blocked = False
    node._job_lock = threading.Lock()
    node._stopping = threading.Event()
    node._held_payload = 'unknown'
    node._held_material_id = ''
    node._configured = False
    node.get_logger = lambda: SimpleNamespace(info=lambda _: None)
    job = module.Job('startup', dict(expect_tool='tool', expect_tcp='tcp',
                                     restore_material_id='A', restore_operator_id='operator',
                                     restore_confirmed=True))
    return node, job, state


def test_startup_restores_confirmed_scoop_without_motion(monkeypatch):
    node, job, _ = restore_node(monkeypatch)
    assert node._do_startup(job)[0]
    assert node._configured
    assert (node._held_payload, node._held_material_id, node._station_id) == ('scoop', 'A', 'material_1')
    assert node._cartesian_ready and node._motion_anchor is None
    assert not node._pending_scoop_extract and not node._scoop_extract_uncertain


@pytest.mark.parametrize('fault', [
    'unconfirmed', 'missing_operator', 'wrong_material', 'wrong_pose', 'stale',
    'no_grip', 'safety', 'slip', 'bad_width', 'moving', 'latched', 'cancel',
    'stopping', 'alarm_during_read', 'return_blocked', 'virtual',
])
def test_failed_restore_never_enables_startup(monkeypatch, fault):
    node, job, state = restore_node(monkeypatch)
    if fault == 'unconfirmed':
        job.args['restore_confirmed'] = False
    elif fault == 'missing_operator':
        job.args['restore_operator_id'] = ''
    elif fault == 'wrong_material':
        job.args['restore_material_id'] = 'B'
    elif fault == 'wrong_pose':
        node.arm.current_posx = lambda: [0.0] * 6
    elif fault == 'stale':
        state.update(fresh=False, busy=True, grip_inferred=False)
    elif fault == 'no_grip':
        state['grip_inferred'] = False
    elif fault == 'safety':
        state['safety_triggered'] = True
    elif fault == 'slip':
        state['slip'] = True
    elif fault == 'bad_width':
        state['width_mm'] = float('nan')
    elif fault == 'moving':
        node._last_robot_state = 2
    elif fault == 'latched':
        node._safety_latched = True
    elif fault == 'cancel':
        job.cancel = True
    elif fault == 'stopping':
        node._stopping.set()
    elif fault == 'alarm_during_read':
        def read(_):
            node._safety_revision += 1
            return state
        node.gripper.state = read
    elif fault == 'return_blocked':
        node._return_rescoop_blocked = True
    elif fault == 'virtual':
        node.mode = 'virtual'
    with pytest.raises((ValueError, RuntimeError, KeyError, TimeoutError)):
        node._do_startup(job)
    assert not node._configured
    assert node._held_payload == 'unknown' and node._held_material_id == ''


def test_default_startup_does_not_restore(monkeypatch):
    node, job, _ = restore_node(monkeypatch)
    job.args.update(restore_material_id='', restore_operator_id='', restore_confirmed=False)
    assert node._do_startup(job)[0]
    assert node._held_payload == 'unknown'


def test_failed_self_check_does_not_restore(monkeypatch):
    node, job, _ = restore_node(monkeypatch)
    node.arm.self_check = lambda *_: (False, 'tool mismatch')
    assert not node._do_startup(job)[0]
    assert not node._configured and node._held_payload == 'unknown'


def test_restore_waits_for_first_fresh_sample(monkeypatch):
    node, job, state = restore_node(monkeypatch)
    readings = []
    def read(_):
        readings.append(True)
        return dict(state, fresh=False, busy=True, grip_inferred=False) if len(readings) < 3 else state
    node.gripper.state = read
    assert node._do_startup(job)[0]
    assert len(readings) == 4  # 대기 2회·최신 확인·자세 재확인 후 최종 센서 확인
    assert node._held_material_id == 'A'


@pytest.mark.parametrize('fault', ['cancel', 'alarm', 'pose_changed'])
def test_sensor_wait_never_overrides_changed_conditions(monkeypatch, fault):
    node, job, state = restore_node(monkeypatch)
    calls = []
    def read(_):
        calls.append(True)
        if len(calls) == 1:
            if fault == 'cancel':
                job.cancel = True
            elif fault == 'alarm':
                node._safety_revision += 1
            else:
                node.arm.current_posx = lambda: [0.0] * 6
            return dict(state, fresh=False)
        return state
    node.gripper.state = read
    with pytest.raises(RuntimeError):
        node._do_startup(job)
    assert not node._configured and node._held_payload == 'unknown'


def test_fresh_safety_fault_is_not_waited_out(monkeypatch):
    node, job, state = restore_node(monkeypatch)
    state['safety_triggered'] = True
    start = node._now_s()
    with pytest.raises(RuntimeError, match='safety_triggered'):
        node._do_startup(job)
    assert node._now_s() == start
