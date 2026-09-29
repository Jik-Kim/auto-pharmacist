import copy
from pathlib import Path

import pytest

from gmp_skills.core.stations import StationTable
from gmp_skills.core.transfer import (MotionAnchor, joints_match, joints_match_or_wrist_flipped,
                                      pose_matches, validate_start)


def teaching_data():
    # 단위 테스트 전용 좌표. 실물 티칭값이 아니다.
    return {'approach_mm': 60.0, 'stations': {
        'safe': {'posx': [0, 0, 400, 0, 180, 0], 'posj': [0]*6},
        'workbench': {'posx': [100, 0, 100, 0, 180, 0]},
        'passbox_done': {'posx': [300, 0, 100, 90, 90, 0]},
        'nudge_wait': {'posx': [400, 0, 100, 0, 180, 0]},
    }, 'transfers': [{
        'source': 'workbench', 'destination': 'passbox_done', 'payload': 'cup', 'enabled': True,
        'start_at_posj': [1]*6, 'start_above_posj': [2]*6,
        'exit_posx': [80, 0, 200, 0, 180, 0], 'exit_posj': [3]*6,
        'waypoints_posj': [[4]*6, [5]*6],
    }]}


def test_pose_comparison_uses_rotation_not_euler_subtraction():
    assert pose_matches([0, 0, 0, 0, 180, 0], [0, 0, 0, 90, 180, 90], 2, 1)
    assert not pose_matches([0, 0, 0, 0, 180, 0], [0, 0, 0, 0, 160, 0], 2, 1)
    assert not pose_matches([3, 0, 0, 0, 180, 0], [0, 0, 0, 0, 180, 0], 2, 1)
    assert not joints_match([360]*6, [0]*6, 1)


@pytest.mark.parametrize('delta', [-360, 360])
def test_j6_branch_change_is_not_wrapped(delta):
    assert not joints_match([0, 0, 0, 0, 0, delta], [0]*6, 1)


def test_wrist_flipped_solution_matches_same_tcp():
    # 9/29 실측: 같은 A 계량 자세(TCP)의 두 관절 해 — 붓기 뒤에는 손목이 뒤집힌 해로 온다
    normal = [-46.0, 7.6, 84.2, -0.05, 88.2, -225.8]
    flipped = [-46.0, 7.6, 84.6, 179.95, -87.8, -45.8]
    assert not joints_match(flipped, normal, 1)
    assert joints_match_or_wrist_flipped(flipped, normal, 1)
    assert joints_match_or_wrist_flipped(normal, normal, 1)


@pytest.mark.parametrize('actual', [
    [0, 0, 0, 180, -30, -180],      # J4·J6 반대 방향 — J6 이 360° 다르게 감긴 해
    [0, 0, 0, 180, 30, 180],        # J5 부호가 그대로
    [5, 0, 0, 180, -30, 180],       # 팔(J1)이 다르다
    [0, 0, 0, 0, 30, 360],          # 360° 감김은 여전히 거부
])
def test_wrist_flip_does_not_accept_other_solutions(actual):
    assert not joints_match_or_wrist_flipped(actual, [0, 0, 0, 0, 30, 0], 1)


@pytest.mark.parametrize('field,value', [
    ('start_above_posj', [0]*5),
    ('exit_posj', [float('nan')]*6), ('waypoints_posj', []),
    ('waypoints_posj', [[True]*6]), ('enabled', 'false'), ('payload', 'scoop'),
    ('exit_posx', [80, 0, 200, 0, 160, 0]),
])
def test_invalid_teaching_rejected(field, value):
    data = teaching_data()
    data['transfers'][0][field] = value
    with pytest.raises(ValueError):
        StationTable(data)


def test_disabled_route_allows_empty_teaching_but_rejects_execution():
    data = teaching_data()
    data['transfers'] = [dict(source='workbench', destination='passbox_done',
                              enabled=False, payload='cup')]
    route = StationTable(data).transfers[('workbench', 'passbox_done')]
    with pytest.raises(ValueError, match='비활성'):
        validate_start(route, None, [0]*6, [0]*6, 'cup', 2, 2, 1)


def test_duplicate_route_rejected():
    data = teaching_data()
    data['transfers'].append(copy.deepcopy(data['transfers'][0]))
    with pytest.raises(ValueError, match='중복'):
        StationTable(data)


def test_shipped_nudge_route_is_enabled_and_preserves_teaching():
    params = Path(__file__).resolve().parents[2] / 'gmp_bringup' / 'params'
    table = StationTable.from_yaml(params / 'stations.yaml')
    assert set(table.transfers) == {('passbox_done', 'nudge_wait')}
    assert all(r.enabled for r in table.transfers.values())
    empty = table.transfers[('passbox_done', 'nudge_wait')]
    assert empty.exit_posx == (705.0, 77.0, 330, 180, -90, -90)
    assert not empty.start_at_posj
    assert empty.start_from == 'exit' and empty.arrival == 'at'
    assert empty.waypoints_posj == ((14.57, 35.24, 63.40, -0.12, 81.36, 104.70),)


def test_above_only_route_can_enable_without_source_at_teaching():
    data = teaching_data()
    row = data['transfers'][0]
    row.update(start_from='above', arrival='at')
    del row['start_at_posj']
    route = StationTable(data).transfers[('workbench', 'passbox_done')]
    assert route.enabled and not route.start_at_posj


def test_exit_route_does_not_require_legacy_joint_checks():
    data = teaching_data()
    row = data['transfers'][0]
    row.update(start_from='exit', arrival='at')
    del row['start_at_posj']
    del row['start_above_posj']
    route = StationTable(data).transfers[('workbench', 'passbox_done')]
    assert route.start_from == 'exit' and not route.start_above_posj
    del row['exit_posj']
    assert not StationTable(data).transfers[('workbench', 'passbox_done')].exit_posj


@pytest.mark.parametrize('field,value', [('start_from', 'at'), ('arrival', 'direct'),
                                         ('start_from', None), ('arrival', None)])
def test_unknown_departure_or_arrival_policy_is_rejected(field, value):
    data = teaching_data()
    data['transfers'][0][field] = value
    with pytest.raises(ValueError, match='start_from/arrival'):
        StationTable(data)


def test_relative_exit_tracks_reference_without_changing_orientation():
    data = teaching_data()
    row = data['transfers'][0]
    del row['exit_posx']
    row['exit_offset_mm'] = 200
    data['stations']['workbench']['posx'] = [123, 45, 67, 90, -90, -90]
    route = StationTable(data).transfers[('workbench', 'passbox_done')]
    assert route.exit_posx == (123, 45, 267, 90, -90, -90)


def test_relative_and_absolute_exit_cannot_conflict():
    data = teaching_data()
    data['transfers'][0]['exit_offset_mm'] = 200
    with pytest.raises(ValueError, match='동시에'):
        StationTable(data)


def test_legacy_pick_route_requires_migration():
    data = teaching_data()
    data['transfers'][0]['source_pose_key'] = 'pick_posx'
    with pytest.raises(ValueError, match='통합'):
        StationTable(data)


@pytest.mark.parametrize('change', ['pose', 'joint', 'payload', 'unknown'])
def test_departure_checks_tcp_history_and_payload(change):
    table = StationTable(teaching_data())
    route = table.transfers[('workbench', 'passbox_done')]
    pose, joints = list(table.get('workbench').posx), [1.0]*6
    anchor = MotionAnchor('workbench', 1, tuple(pose), tuple(joints))
    payload = 'cup'
    if change == 'pose':
        pose[0] += 10
    elif change == 'joint':
        joints[0] += 10
    elif change == 'payload':
        payload = 'scoop'
    else:
        anchor = None
    with pytest.raises(ValueError):
        validate_start(route, anchor, pose, joints, payload, 2, 2, 1)


def test_departure_uses_recorded_tcp_instead_of_old_taught_pose():
    route = StationTable(teaching_data()).transfers[('workbench', 'passbox_done')]
    pose = (100, 0, 330, 0, 180, 0)
    anchor = MotionAnchor(station='workbench', approach=0, pose=pose, joints=(99,)*6)
    validate_start(route, anchor, pose, [99]*6, 'cup', 2, 2, 1)


@pytest.mark.parametrize('delta', [-360, 360])
def test_departure_rejects_j6_branch_change_from_recorded_anchor(delta):
    route = StationTable(teaching_data()).transfers[('workbench', 'passbox_done')]
    pose = (100, 0, 100, 0, 180, 0)
    anchor = MotionAnchor('workbench', 1, pose, (0,)*6)
    with pytest.raises(ValueError, match='관절각'):
        validate_start(route, anchor, pose, (0, 0, 0, 0, 0, delta),
                       'cup', 2, 2, 1)
