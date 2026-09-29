"""ROS 메시지만 사용하며 노드·드라이버·DDS는 기동하지 않는 수동 순서 검사."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manual_scoop_pour_nudge import ScoopPourNudge, PlaceNudge


def sequence(monkeypatch, fail_at=None):
    node = object.__new__(ScoopPourNudge)
    trace = []

    def record(*entry):
        trace.append(entry)
        if len(trace) == fail_at:
            raise RuntimeError('스킬 실패')
        return NS(success=True, payload='empty')

    node.prepare = lambda: None
    node.check = lambda: None
    node.values = {'gripper.cup_width_mm': 30, 'gripper.open_width_mm': 100,
                   'gripper.scoop_search_width_mm': 0}
    node.scoops = {'A': 'scoop_1', 'B': 'scoop_2', 'C': 'scoop_3'}
    node.safe_client = NS(call_async=lambda req: record('safe', req.reason))
    node.restore_client = NS(call_async=lambda req: record('restore', req.expected_payload))
    node.wait = lambda result: result
    node.move = lambda station, approach: record('move', station, approach)
    node.grip = lambda close, width: record('grip', close)
    node.action = lambda name, goal: record(name, getattr(goal, 'material_id', ''))
    monkeypatch.setattr(PlaceNudge, 'run', lambda self: record('place_nudge_safe'))
    return node, trace


def test_sequence_returns_every_scoop_then_carries_cup(monkeypatch):
    node, trace = sequence(monkeypatch)
    node.run()
    assert [entry for entry in trace if entry[0] in ('weigh_held', 'scoop', 'pour')] == [
        entry for material in 'ABC'
        for entry in [('weigh_held', ''), ('scoop', material), ('pour', '')]]
    for index, entry in enumerate(trace):
        if entry[0] == 'pour':
            assert trace[index + 1][0] == 'move'
            assert trace[index + 1][1].startswith('scoop_')
            assert trace[index + 2] == ('grip', False)
    assert trace[-5:] == [('move', 'workbench', 0), ('move', 'workbench', 1),
                          ('grip', True), ('move', 'workbench', 0), ('place_nudge_safe',)]


def test_each_failure_stops_all_following_steps(monkeypatch):
    node, successful = sequence(monkeypatch)
    node.run()
    for index in range(1, len(successful) + 1):
        node, trace = sequence(monkeypatch, fail_at=index)
        with pytest.raises(RuntimeError, match='스킬 실패'):
            node.run()
        assert trace == successful[:index]


@pytest.mark.parametrize('status, success', [(6, True), (5, True), (4, False)])
def test_action_rejects_failed_or_cancelled_result(status, success):
    node = object.__new__(ScoopPourNudge)
    node.check = lambda: None
    node.get_logger = lambda: NS(info=lambda message: None)
    node.wait = lambda future: future
    handle = NS(accepted=True, get_result_async=lambda: NS(
        status=status, result=NS(success=success, message='실패')))
    node.actions = {'scoop': NS(send_goal_async=lambda goal: handle)}
    with pytest.raises(RuntimeError, match='scoop 실패'):
        node.action('scoop', NS())


def test_grip_success_without_payload_stops():
    node = object.__new__(ScoopPourNudge)
    node.check = lambda: None
    node.values = {'gripper.force_n': 20.0}
    node.args = NS(timeout=10.0)
    node.wait = lambda future: future
    node.grip_client = NS(call_async=lambda request: NS(
        success=True, grip_inferred=False, message='파지 안 됨'))
    with pytest.raises(RuntimeError, match='파지/열기 실패'):
        node.grip(True, 0.0)
