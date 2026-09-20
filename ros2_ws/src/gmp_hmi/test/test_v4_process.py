"""시험 공정 상태 전이 검사. DDS·실물 동작 검증은 아니다."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from test_v3_backend import backend, Message, RosStub


@pytest.fixture
def process(backend, monkeypatch):
    monkeypatch.setattr(RosStub, 'create_service', lambda self, *args: args, raising=False)
    monkeypatch.setattr(RosStub, 'create_timer', lambda self, *args: args, raising=False)
    monkeypatch.setattr(RosStub, 'get_logger', lambda self: SimpleNamespace(info=lambda *_: None, warning=lambda *_: None))
    msg = sys.modules['gmp_interfaces.msg']
    msg.CellEvent.WARN = 1
    msg.CellEvent.ERROR = 2
    msg.Deviation.OVERFILL = 1
    msg.Deviation.VERIFY_MISMATCH = 2
    msg.Deviation.WRONG_TOOL = 3
    msg.ScoopCycle.WEIGH_METHOD_WORKPIECE = 0
    msg.ScoopCycle.COMPLETE = 0
    msg.DispenseResult.OK = 0
    msg.DispenseResult.OVER = 1
    path = Path(__file__).resolve().parents[1] / 'gmp_hmi/nodes/hmi_test_process.py'
    spec = importlib.util.spec_from_file_location('hmi_test_process_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    return module.HmiTestProcess()


def reply():
    return SimpleNamespace(accepted=False, granted=False, success=False, message='', batch_id='')


def order(process, **amounts):
    recipe = Message(batch_id='', product='시험', items=[
        Message(material_id=mid, target_g=float(amount), tol_pct=5.)
        for mid, amount in amounts.items()])
    accepted = process._goal_batch(Message(recipe=recipe)) == 1
    if not accepted:
        return SimpleNamespace(accepted=False, message='Goal rejected')
    started, message = process._start_batch(recipe)
    return SimpleNamespace(accepted=started, message=message)


def height(process, mid, value):
    process._height(Message(data=json.dumps({'material_id': mid, 'height_pct': value})))


def advance(process, seconds):
    for _ in range(int(seconds * 10)):
        process._previous_tick -= .1
        process._tick()


def test_defaults_and_direct_process_order_shortage(process):
    assert all(item['capacity_g'] == 1000 and item['remaining_g'] == 1000 for item in process.inventory.items.values())
    assert not order(process, A=1001).accepted
    assert process.inventory.batch_id == ''
    assert order(process, A=80, B=40).accepted
    assert process.inventory.items['A']['reserved_g'] == 80
    assert process.inventory.items['C']['reserved_g'] == 0
    before = copy.deepcopy(process.inventory.snapshot(False))
    assert not process._refill('A', Message(), reply()).success
    assert process.inventory.snapshot(False) == before


def test_height_holds_running_without_claiming_entry_and_requires_all_refills_exit(process):
    assert order(process, A=80, B=40).accepted
    advance(process, 1.3)
    height(process, 'C', 19)
    before = (process.phase, process.index, process.elapsed, copy.deepcopy(process.inventory.items))
    advance(process, 6)
    assert (process.phase, process.index, process.elapsed, process.inventory.items) == before
    assert process.mode == 1  # RUNNING with progress held; 안전 진입 허가를 의미하지 않는다.
    assert not process._refill('C', Message(), reply()).success
    assert process._interlock(Message(request=1, reason='REFILL'), reply()).granted
    assert process.mode == 2 and process.previous is not None
    height(process, 'A', 10)
    height(process, 'C', 100)
    assert process.inventory.blocked_materials == ['A', 'C']
    assert not process._interlock(Message(request=2, reason='REFILL'), reply()).granted
    assert process._refill('C', Message(), reply()).success
    assert process.mode == 2 and process.inventory.blocked_materials == ['A']
    assert not process._interlock(Message(request=2, reason='REFILL'), reply()).granted
    assert process._refill('A', Message(), reply()).success
    assert process.inventory.items['A']['reserved_g'] == 80
    advance(process, 5)
    assert process.mode == 2 and process.elapsed == before[2]
    assert process._interlock(Message(request=2, reason='REFILL'), reply()).granted
    advance(process, 9)
    assert process.mode == 5 and process.step == 'DONE'
    assert process.inventory.items['A']['remaining_g'] == 920
    assert process.inventory.items['B']['remaining_g'] == 960
    assert process.inventory.items['C']['remaining_g'] == 1000
    cycles = [message for message in process.published if type(message).__name__ == 'ScoopCycle']
    assert [(cycle.material_id, cycle.attempt, cycle.delivered_g) for cycle in cycles] == [
        ('A', 1, 40.0), ('A', 2, 40.0), ('B', 1, 40.0)]
    assert cycles[1].actual_before_g == 40.0
    assert all(cycle.weigh_pose_id == 'workbench' for cycle in cycles)
    assert process.station == 'passbox_done'


def test_height_hold_does_not_skip_qa_or_transfer(process):
    process.params['scenario'] = 'overfill'
    assert order(process, A=40, B=40).accepted
    advance(process, 9)
    assert process.mode == 3 and process.pending is not None
    height(process, 'C', 0)
    qa = Message(deviation_id=process.pending.deviation_id, decision=1, operator_id='qa')
    assert not process._qa(qa, reply()).accepted
    assert process._interlock(Message(request=1, reason='REFILL'), reply()).granted
    assert process._refill('C', Message(), reply()).success
    assert process.mode == 2
    assert process._interlock(Message(request=2, reason='REFILL'), reply()).granted
    assert process.mode == 3
    assert process._qa(qa, reply()).accepted
    assert process.phase == 'finish'
    height(process, 'B', 10)
    advance(process, 5)
    assert process.mode == 1 and process.phase == 'finish'
    assert process._interlock(Message(request=1, reason='REFILL'), reply()).granted
    assert process._refill('B', Message(), reply()).success
    assert process._interlock(Message(request=2, reason='REFILL'), reply()).granted
    advance(process, 2)
    assert process.mode == 5


def test_idle_height_blocks_even_unselected_and_only_refill_clears(process):
    height(process, 'C', 20)
    assert process.inventory.blocked_materials == []
    height(process, 'C', 19.9)
    assert not order(process, A=40, B=40).accepted
    height(process, 'C', 80)
    assert not order(process, A=40, B=40).accepted
    assert process._refill('A', Message(), reply()).success
    assert not order(process, A=40, B=40).accepted
    assert process._refill('C', Message(), reply()).success
    assert order(process, A=40, B=40).accepted


def test_malformed_height_does_not_clear_existing_latch(process):
    height(process, 'A', 1)
    for value in ('bad json', '[]', '{"material_id":"A","height_pct":null}',
                  '{"material_id":"UNKNOWN","height_pct":100}', '{"material_id":"A","height_pct":101}'):
        process._height(Message(data=value))
    assert process.inventory.blocked_materials == ['A']
    assert not order(process, B=40).accepted
