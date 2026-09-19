from types import SimpleNamespace
import queue
import threading

import pytest

from gmp_skills.core.stations import StationTable
from gmp_skills.core.transfer import MotionAnchor
from test_skill_node_weigh_held import _load_skill_node
from test_transfer import teaching_data


@pytest.fixture
def setup(monkeypatch):
    module = _load_skill_node(monkeypatch)
    node = module.SkillNode.__new__(module.SkillNode)
    node.stations = StationTable(teaching_data())
    node.vel_scale = 0.2
    node.motion_timeout_s = 30
    node.transfer_joint_vel = 10
    node.transfer_joint_acc = 20
    node.pose_xyz_tolerance = node.pose_rotation_tolerance = 2
    node.joint_tolerance = 1
    node._station_id = 'workbench'
    node._held_payload = 'cup'
    node._pending_scoop_extract = node._scoop_extract_uncertain = False
    node._cartesian_ready = True
    node._now_s = lambda: 0
    node.feedback_state = {'busy': False, 'width_mm': 60, 'grip_inferred': True}
    node.gripper = SimpleNamespace(state=lambda _: node.feedback_state,
                                   open_width_mm=100, grip_margin_mm=2)
    node.calls = []
    pose = list(node.stations.get('workbench').posx)
    node.arm = SimpleNamespace(pose=pose, joints=[1]*6)
    node.arm.current_posx = lambda: list(node.arm.pose)
    node.arm.current_posj = lambda: list(node.arm.joints)
    route = node.stations.transfers[('workbench', 'passbox_done')]

    def linear(target, scale, cancel, timeout):
        if cancel():
            raise RuntimeError('cancelled')
        node.calls.append(('L', list(target)))
        node.arm.pose = list(target)
        if tuple(target) == route.exit_posx:
            node.arm.joints = list(route.exit_posj)
        node.after_move()

    def joint(target, scale, cancel, timeout, **kwargs):
        if cancel():
            raise RuntimeError('cancelled')
        node.calls.append(('J', list(target), scale, kwargs))
        node.arm.joints = list(target)
        if tuple(target) == route.waypoints_posj[-1]:
            node.arm.pose = node.stations.get('passbox_done').above(60)
        node.after_move()

    node.after_move = lambda: None
    node.arm.movel_cancellable = linear
    node.arm.movej_cancellable = joint
    node._motion_anchor = MotionAnchor('workbench', 1, tuple(pose), (1,)*6)
    job = module.Job('move', {'station_id': 'passbox_done', 'approach': 1, 'vel_scale': 0.2})
    return node, job, module


@pytest.mark.parametrize('approach,expected', [(0, ['L', 'J', 'J']), (1, ['L', 'J', 'J', 'L'])])
def test_transfer_order_and_above_at_semantics(setup, approach, expected):
    node, job, _ = setup
    job.args['approach'] = approach
    assert node._do_move(job) == 'passbox_done'
    assert [c[0] for c in node.calls] == expected
    assert node.calls[1][2:] == (0.2, {'joint_vel': 10, 'joint_acc': 20})
    assert node._motion_anchor.station == 'passbox_done'
    assert node._motion_anchor.approach == approach


def test_already_at_exit_skips_duplicate_retreat(setup):
    node, job, _ = setup
    data = teaching_data()
    source_above = node.stations.get('workbench').above(60)
    data['transfers'][0].update(exit_posx=source_above, exit_posj=[2]*6)
    node.stations = StationTable(data)
    node.arm.pose, node.arm.joints = source_above, [2]*6
    node._motion_anchor = MotionAnchor('workbench', 0, tuple(source_above), (2,)*6)
    node._do_move(job)
    assert [c[0] for c in node.calls] == ['J', 'J', 'L']


@pytest.mark.parametrize('fault', ['disabled', 'speed', 'payload', 'stale', 'manual', 'branch', 'unknown'])
def test_bad_departure_never_sends_motion_or_falls_back(setup, fault):
    node, job, _ = setup
    if fault == 'disabled':
        data = teaching_data()
        data['transfers'][0]['enabled'] = False
        node.stations = StationTable(data)
    elif fault == 'speed':
        node.transfer_joint_vel = 0
    elif fault == 'payload':
        node._held_payload = 'scoop'
    elif fault == 'stale':
        node.feedback_state['busy'] = True
    elif fault == 'manual':
        node.arm.pose[0] += 10
    elif fault == 'branch':
        node.arm.joints[5] += 360
    else:
        node._motion_anchor = None
    with pytest.raises((RuntimeError, ValueError)):
        node._do_move(job)
    assert node.calls == []
    assert node._motion_anchor is None
    assert node._held_payload == 'unknown'


@pytest.mark.parametrize('stage', [0, 1, 2, 3, 4])
def test_cancel_at_any_boundary_prevents_next_segment(setup, stage):
    node, job, _ = setup
    job.cancel = stage == 0
    node.after_move = lambda: setattr(job, 'cancel', len(node.calls) == stage)
    with pytest.raises(RuntimeError, match='cancelled'):
        node._do_move(job)
    assert len(node.calls) == stage
    assert node._motion_anchor is None
    assert node._held_payload == 'unknown'


def test_failure_invalidates_state_and_retry_cannot_resume(setup):
    node, job, _ = setup

    def failure():
        raise TimeoutError('motion timed out')

    node.after_move = failure
    with pytest.raises(TimeoutError):
        node._do_move(job)
    assert len(node.calls) == 1
    with pytest.raises(ValueError):
        node._do_move(job)
    assert len(node.calls) == 1


@pytest.mark.parametrize('fault', ['orientation', 'exit_branch', 'grip_lost'])
def test_intermediate_checks_prevent_following_segment(setup, fault):
    node, job, _ = setup

    def corrupt():
        if fault == 'orientation' and len(node.calls) == 3:
            node.arm.pose[4] += 20
        elif fault == 'exit_branch' and len(node.calls) == 1:
            node.arm.joints[0] += 20
        elif fault == 'grip_lost' and len(node.calls) == 1:
            node.feedback_state['grip_inferred'] = False

    node.after_move = corrupt
    with pytest.raises(RuntimeError):
        node._do_move(job)
    assert len(node.calls) == (3 if fault == 'orientation' else 1)


def test_unregistered_entry_into_protected_destination_is_rejected(setup):
    node, job, _ = setup
    node._station_id = 'safe'
    node._motion_anchor = None
    with pytest.raises(ValueError, match='보호 대상'):
        node._do_move(job)
    assert node.calls == []


def test_same_station_above_to_at_keeps_linear_motion(setup):
    node, job, _ = setup
    job.args['approach'] = 0
    node._do_move(job)
    node.calls.clear()
    job.args['approach'] = 1
    node._do_move(job)
    assert [c[0] for c in node.calls] == ['L']


def test_empty_route_can_be_anchored_without_moving_after_manual_teaching(setup):
    node, job, _ = setup
    data = teaching_data()
    data['transfers'].append(dict(data['transfers'][0], source='passbox_done', destination='nudge_wait',
                                  payload='empty', exit_posx=[300, 0, 160, 90, 90, 0]))
    node.stations = StationTable(data)
    node._station_id = ''
    node._motion_anchor = None
    node.arm.pose = list(node.stations.get('passbox_done').posx)
    node.arm.joints = [1]*6
    node._do_move(job)
    assert node.calls == []
    assert node._motion_anchor.station == 'passbox_done'
    assert node._held_payload == 'unknown'


def test_payload_requires_successful_grip_at_taught_station(setup):
    node, _, module = setup
    node.gripper.grip = lambda *_: (True, 60, True)
    job = module.Job('grip', {'close': True, 'width_mm': 60, 'force_n': 20, 'timeout_s': 3})
    node._do_grip(job)
    assert node._held_payload == 'cup'
    node.arm.pose[0] += 30
    node._do_grip(job)
    assert node._held_payload == 'unknown'


@pytest.mark.parametrize('width', [60, float('nan'), None])
def test_release_flag_alone_does_not_prove_empty_gripper(setup, width):
    node, _, _ = setup
    node._held_payload = 'empty'
    node.feedback_state.update(width_mm=width, grip_inferred=False)
    with pytest.raises(RuntimeError):
        node._require_transfer_payload('empty')


def test_empty_gripper_route_runs_after_successful_release(setup):
    node, job, module = setup
    data = teaching_data()
    data['transfers'][0]['payload'] = 'empty'
    node.stations = StationTable(data)
    node.gripper.release = lambda _: True
    node.gripper.width_mm = lambda: 100
    node.feedback_state.update(width_mm=100, grip_inferred=False)
    node._do_grip(module.Job('grip', {'close': False, 'timeout_s': 3}))
    assert node._held_payload == 'empty'
    node._do_move(job)
    assert [c[0] for c in node.calls] == ['L', 'J', 'J', 'L']


def test_failed_release_cannot_start_empty_route(setup):
    node, _, module = setup
    node.gripper.release = lambda _: False
    node.gripper.width_mm = lambda: 100
    node._do_grip(module.Job('grip', {'close': False, 'timeout_s': 3}))
    assert node._held_payload == 'unknown'


def test_worker_clears_anchor_when_cancel_arrives_during_final_record(setup, monkeypatch):
    node, job, module = setup
    node._job_lock = threading.Lock()
    node._q = queue.Queue()
    node._q.put(job)
    turns = iter([True, False])
    monkeypatch.setattr(module.rclpy, 'ok', lambda: next(turns))
    record = node._record_arrival

    def cancel_during_record(*args):
        node._on_cancel(None)
        record(*args)

    node._record_arrival = cancel_during_record
    node._worker()
    assert job.done.is_set() and job.cancel
    assert node._current is None
    assert node._motion_anchor is None
    assert node._held_payload == 'unknown'


@pytest.mark.parametrize('kind', ['scoop', 'pour', 'weigh', 'weigh_held', 'safe'])
def test_other_motion_skills_invalidate_previous_anchor(setup, monkeypatch, kind):
    node, _, module = setup
    node._job_lock = threading.Lock()
    node._q = queue.Queue()
    job = module.Job(kind, {})
    node._q.put(job)
    turns = iter([True, False])
    monkeypatch.setattr(module.rclpy, 'ok', lambda: next(turns))
    anchors = []
    setattr(node, '_do_' + kind, lambda _: anchors.append(node._motion_anchor))
    node._worker()
    assert anchors == [None]
    if kind == 'weigh':
        assert node._held_payload == 'unknown'
