"""용기 스테이션의 관절 구성 선택·직선 접근 회귀 검증."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from gmp_skills.core.stations import StationTable
from test_skill_node_weigh_held import _load_skill_node


@pytest.fixture
def setup(monkeypatch):
    module = _load_skill_node(monkeypatch)
    node = module.SkillNode.__new__(module.SkillNode)
    node.stations = StationTable.from_yaml(
        Path(__file__).resolve().parents[2] / 'gmp_bringup/params/stations.yaml')
    # 이전 sol 선택 정책을 쓰는 설정도 지원한다. 운영 티칭 경로는 별도 시험한다.
    for name in ('workbench', 'passbox_empty', 'passbox_done', 'reject_bin'):
        extra = node.stations.get(name).extra
        extra.pop('approach_posj')
        extra['solution_space'] = 3
    node.mode = 'real'
    node.vel_scale = .2
    node.motion_timeout_s = 30
    node.pose_xyz_tolerance = node.pose_rotation_tolerance = 2
    node.joint_tolerance = 1
    node._station_id = 'safe'
    node._held_payload = 'empty'
    node._held_material_id = ''
    node._pending_scoop_extract = node._scoop_extract_uncertain = False
    node._cartesian_ready = True
    node._motion_anchor = None
    node._now_s = lambda: 0
    state = dict(busy=False, width_mm=100, grip_inferred=False)
    node.gripper = SimpleNamespace(state=lambda _: state, open_width_mm=100, grip_margin_mm=2)
    node.calls = []
    node.arm = SimpleNamespace(pose=[300, 0, 450, 0, 180, 0], joints=[0]*6, sol=0)
    node.arm.current_posx = lambda: list(node.arm.pose)
    node.arm.current_posj = lambda: list(node.arm.joints)
    node.arm.solution_space = lambda: node.arm.sol
    node.after_move = lambda: None

    def linear(target, scale, cancel, timeout):
        if cancel():
            raise RuntimeError('cancelled')
        node.calls.append(('L', list(target)))
        node.arm.pose = list(target)
        node.after_move()

    def joint_x(target, sol, scale, cancel, timeout):
        if cancel():
            raise RuntimeError('cancelled')
        node.calls.append(('JX', list(target), sol))
        node.arm.pose = list(target)
        node.arm.sol = sol
        node.after_move()

    node.arm.movel_cancellable = linear
    node.arm.movejx_cancellable = joint_x
    job = module.Job('move', dict(station_id='workbench', approach=1))
    return node, job, module, state


@pytest.mark.parametrize('station', ['workbench', 'passbox_empty', 'passbox_done', 'reject_bin'])
@pytest.mark.parametrize('approach', [0, 1])
@pytest.mark.parametrize('mode', ['real', 'virtual'])
def test_select_solution_above_then_linear_descent(setup, station, approach, mode):
    node, job, _, _ = setup
    node.mode = mode
    job.args.update(station_id=station, approach=approach)
    node._do_move(job)
    assert node.calls[0] == ('JX', node.stations.get(station).above(60), 3)
    assert [c[0] for c in node.calls] == (['JX', 'L'] if approach else ['JX'])
    assert node._motion_anchor.station == station


def test_same_station_lift_never_flips_wrist(setup):
    node, job, _, _ = setup
    node._do_move(job)
    node.calls.clear()
    job.args['approach'] = 0
    node._do_move(job)
    assert [c[0] for c in node.calls] == ['L']


def test_low_pose_with_wrong_solution_rejected_without_motion(setup):
    node, job, _, _ = setup
    node.arm.pose = list(node.stations.get('workbench').posx)
    with pytest.raises(RuntimeError, match='관절 구성'):
        node._do_move(job)
    assert not node.calls
    assert node._motion_anchor is None


def test_source_exit_before_destination_joint_move(setup):
    node, job, _, _ = setup
    node._do_move(job)
    node.calls.clear()
    job.args.update(station_id='passbox_done')
    node._do_move(job)
    assert [c[0] for c in node.calls] == ['L', 'JX', 'L']
    assert node.calls[0][1] == node.stations.get('workbench').exit()


@pytest.mark.parametrize('fault', ['cancel', 'wrong_sol', 'lost_grip'])
def test_failed_joint_approach_never_descends(setup, fault):
    node, job, _, state = setup

    def fault_after_move():
        if fault == 'cancel':
            job.cancel = True
        elif fault == 'wrong_sol':
            node.arm.sol = 2
        else:
            state['busy'] = True

    node.after_move = fault_after_move
    with pytest.raises(RuntimeError):
        node._do_move(job)
    assert [c[0] for c in node.calls] == ['JX']
    assert node._motion_anchor is None and node._held_payload == 'unknown'


def test_manual_movement_invalidates_departure(setup):
    node, job, _, _ = setup
    node._do_move(job)
    node.calls.clear()
    node.arm.joints[0] += 10
    job.args['station_id'] = 'reject_bin'
    with pytest.raises(RuntimeError, match='출발 이력'):
        node._do_move(job)
    assert not node.calls


def test_nudge_route_stays_disabled(setup):
    node, job, _, _ = setup
    job.args['station_id'] = 'passbox_done'
    node._do_move(job)
    node.calls.clear()
    job.args['station_id'] = 'nudge_wait'
    with pytest.raises(ValueError, match='비활성'):
        node._do_move(job)
    assert not node.calls


def test_weigh_approach_uses_original_cancel_flag(setup):
    node, job, _, _ = setup
    job.args = {'tare_g': 0}
    node.after_move = lambda: setattr(job, 'cancel', True)
    node.get_parameter = lambda _: SimpleNamespace(value=False)
    with pytest.raises(RuntimeError, match='cancelled'):
        node._do_weigh(job)
    assert [c[0] for c in node.calls] == ['JX']


@pytest.mark.parametrize('sol', [True, -1, 8, 255, 3.0, '3', None])
def test_invalid_solution_is_configuration_error(sol):
    with pytest.raises(ValueError, match='solution_space'):
        StationTable({'stations': {'safe': {'posx': [0]*6}, 'workbench': {
            'posx': [0]*6, 'solution_space': sol, 'approach_mm': 50, 'exit_mm': 150}}})


@pytest.mark.parametrize('height,exit_height', [('50', 150), (True, 150), (0, 150),
                                               (50, 30), (50, None), (float('nan'), 150)])
def test_bad_solution_clearance_is_rejected(height, exit_height):
    with pytest.raises(ValueError):
        StationTable({'stations': {'safe': {'posx': [0]*6}, 'workbench': {
            'posx': [0]*6, 'solution_space': 3,
            'approach_mm': height, 'exit_mm': exit_height}}})


def test_legacy_transfer_cannot_override_solution_policy():
    from test_transfer import teaching_data
    data = teaching_data()
    data['stations']['passbox_done'].update(solution_space=3, approach_mm=50, exit_mm=150)
    with pytest.raises(ValueError, match='중복 등록'):
        StationTable(data)


def test_container_weigh_uses_joint_entry_and_finishes_above(setup):
    node, job, _, state = setup
    job.args = {'tare_g': 0}
    values = {'scale.simulated': True, 'gripper.cup_width_mm': 60, 'gripper.force_n': 20}
    node.get_parameter = lambda name: SimpleNamespace(value=values[name])
    node.arm.movel = lambda target, scale: node.arm.movel_cancellable(
        target, scale, lambda: job.cancel, 30)
    node.gripper.grip = lambda *_: (True, 60, True)
    node.gripper.release = lambda *_: True
    reading = object()
    node._measure_weight_reading = lambda *_: reading
    assert node._do_weigh(job) is reading
    assert [c[0] for c in node.calls] == ['JX', 'L', 'L', 'L', 'L']
    assert node.arm.pose == node.stations.get('workbench').above(60)
