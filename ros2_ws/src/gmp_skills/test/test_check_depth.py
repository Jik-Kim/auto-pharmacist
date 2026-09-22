"""티칭한 원료 측정 목표로 이동하며 관측하고 성공한 경우에만 복귀한다."""
from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from gmp_skills.core.stations import StationTable
from gmp_skills.core.transfer import pose_matches
from test_skill_node_weigh_held import _load_skill_node


def depth_node(monkeypatch, material='A'):
    module = _load_skill_node(monkeypatch)
    node = object.__new__(module.SkillNode)
    node.stations = StationTable.from_yaml(
        Path(__file__).parents[2] / 'gmp_bringup/params/stations.yaml')
    node._require_scoop_extracted = lambda: None
    node._held_payload, node._held_material_id = 'scoop', material
    clock = [0.0]
    node._now_s = lambda: clock[0]
    monkeypatch.setattr(module.time, 'sleep', lambda dt: clock.__setitem__(0, clock[0] + dt))
    node._cancel_requested = lambda: False
    node._pose_matches = lambda actual, target: pose_matches(actual, target, 2.0, 2.0)
    node.gripper = SimpleNamespace(state=lambda _: dict(busy=False, grip_inferred=True))
    node.vel_scale, node.motion_timeout_s = 1.0, 30.0
    node.get_logger = lambda: SimpleNamespace(info=lambda _: None)
    params = {'safety.compliance_stx': [3000, 3000, 500, 200, 200, 200],
              'height_measurement.reference_station': 'material_1',
              'safety.compliance_settle_s': 0.5, 'safety.fz_max_n': 15.0}
    node.get_parameter = lambda key: SimpleNamespace(value=params[key])
    station = node.stations.for_material(material)
    state = {'pose': list(station.posx), 'force': 0.0}
    calls, feedback = [], []
    job = module.Job('scoop', {'material_id': material}, feedback=lambda *v: feedback.append(v))

    def move(target, scale):
        calls.append(('move', list(target), scale))
        state['pose'] = list(target)

    def observed_move(target, scale, cancel, timeout, observer, stop_requested):
        assert clock[0] >= params['safety.compliance_settle_s']
        calls.append(('measure', list(target), scale, timeout))
        assert not cancel()
        state.update(pose=[target[0], target[1], 150.0, *target[3:]], force=16.0)
        observer()
        if stop_requested():
            calls.append(('contact_stop',))
            return
        state.update(pose=list(target), force=18.0)
        observer()

    node.arm = SimpleNamespace(
        movel=move, movel_cancellable=observed_move,
        current_posx=lambda: list(state['pose']), tool_force=lambda: [0, 0, state['force'], 0, 0, 0],
        force_over=lambda threshold: state['force'] >= threshold,
        compliance_on=lambda _: calls.append(('on',)), compliance_off=lambda: calls.append(('off',)))
    return node, job, calls, feedback


@pytest.mark.parametrize('material,x', [('A', 344.0), ('B', 439.0), ('C', 537.0)])
def test_check_depth_uses_full_taught_target_and_returns_to_weigh_pose(monkeypatch, material, x):
    node, job, calls, feedback = depth_node(monkeypatch, material)
    result = node._do_scoop(job)
    weigh = [x, -298.0, 200.0, 90.0, -180.0, -90.0]
    assert calls == [('move', weigh, 1.0), ('on',),
                     ('measure', [x, -334.0, 120.0, 90.0, 160.0, -90.0], 1.0, 30.0),
                     ('contact_stop',),
                     ('off',), ('move', weigh, 1.0)]
    measurement = json.loads(result.pop('message'))
    assert measurement['frame'] == 'BASE'
    assert measurement['contact_tcp_posx'] == [x, -334, 150, 90, 160, -90]
    # 최초 접촉의 자세와 회전을 사용한다. 최종 목표 Z=120이나 고정 Z-20이 아니다.
    assert measurement['tip_position_mm'][2] == pytest.approx(90.1637303852)
    assert result == dict(contact_detected=True, max_contact_force_n=16.0, insertion_depth_mm=0.0)
    assert [f[0] for f in feedback] == ['APPROACH', 'DIP', 'LIFT']


@pytest.mark.parametrize('fault', ['missing_target', 'cancel', 'compliance_failure', 'motion_failure', 'bad_force', 'wrong_rotation'])
def test_depth_failure_does_not_continue_or_retreat(monkeypatch, fault):
    node, job, calls, _ = depth_node(monkeypatch)
    if fault == 'missing_target':
        node.stations.for_material('A').extra.pop('measure_posx')
    elif fault == 'cancel':
        job.cancel = True
    elif fault == 'compliance_failure':
        def fail_compliance(stx):
            raise RuntimeError('task_compliance_ctrl failed: return=-1')
        node.arm.compliance_on = fail_compliance
    elif fault == 'motion_failure':
        def fail(*args, **kwargs):
            raise RuntimeError('motion failed')
        node.arm.movel_cancellable = fail
    elif fault == 'bad_force':
        node.arm.tool_force = lambda: None
    elif fault == 'wrong_rotation':
        node.arm.force_over = lambda _: False
        original = node.arm.movel_cancellable
        def wrong(*args, **kwargs):
            original(*args, **kwargs)
            node.arm.current_posx = lambda: [344, -334, 120, 0, 0, 0]
        node.arm.movel_cancellable = wrong
    with pytest.raises((RuntimeError, ValueError)):
        node._do_check_depth(job)
    assert sum(c[0] == 'move' for c in calls) == (0 if fault in ('missing_target', 'cancel') else 1)
    if fault not in ('missing_target', 'cancel'):
        assert calls[-1] == ('off',)
    if fault == 'compliance_failure':
        assert not any(c[0] == 'measure' for c in calls)


def test_cancel_during_compliance_settle_releases_without_motion(monkeypatch):
    node, job, calls, _ = depth_node(monkeypatch)
    node._cancel_requested = lambda: node._now_s() >= 0.1
    with pytest.raises(RuntimeError, match='cancelled'):
        node._do_check_depth(job)
    assert [c[0] for c in calls] == ['move', 'on', 'off']


@pytest.mark.parametrize('duration', [0, -1, float('nan'), float('inf'), 31])
def test_invalid_compliance_settle_rejected_before_motion(monkeypatch, duration):
    node, job, calls, _ = depth_node(monkeypatch)
    original = node.get_parameter
    node.get_parameter = lambda key: (SimpleNamespace(value=duration)
        if key == 'safety.compliance_settle_s' else original(key))
    with pytest.raises(ValueError):
        node._do_check_depth(job)
    assert calls == []


def test_no_contact_has_no_surface_height(monkeypatch):
    node, job, _, _ = depth_node(monkeypatch)
    node.arm.force_over = lambda _: False
    result = node._do_check_depth(job)
    assert not result['contact_detected']
    assert json.loads(result['message'])['tip_position_mm'] is None


def test_contact_is_logged_even_if_motion_later_times_out(monkeypatch):
    node, job, calls, _ = depth_node(monkeypatch)
    logs = []
    node.get_logger = lambda: SimpleNamespace(info=logs.append)
    move = node.arm.movel_cancellable

    def timeout(*args, **kwargs):
        move(*args, **kwargs)
        raise TimeoutError('motion timed out')

    node.arm.movel_cancellable = timeout
    with pytest.raises(TimeoutError):
        node._do_check_depth(job)
    assert len(logs) == 1
    assert logs[0].startswith('[SURFACE_CONTACT_BASE] ')
    assert json.loads(logs[0].split('] ', 1)[1])['contact_tcp_posx'][2] == 150
    assert calls[-1] == ('off',)


@pytest.mark.parametrize('client_cancel', [False, True])
def test_scoop_internal_timeout_aborts_unless_client_requested_cancel(monkeypatch, client_cancel):
    module = _load_skill_node(monkeypatch)
    node = object.__new__(module.SkillNode)
    node._submit = lambda *args, **kwargs: SimpleNamespace(
        error='motion timed out', cancel=True, result=None)
    endings = []
    goal = SimpleNamespace(request=SimpleNamespace(material_id='A', attempt=1),
        is_cancel_requested=client_cancel,
        succeed=lambda: endings.append('success'),
        canceled=lambda: endings.append('canceled'),
        abort=lambda: endings.append('aborted'))
    result = node._exec_scoop(goal)
    assert not result.success
    assert result.message == 'motion timed out'
    assert endings == ['canceled' if client_cancel else 'aborted']
