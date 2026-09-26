"""시험 공정 상태 전이 검사. DDS·실물 동작 검증은 아니다.

흐름·정지 사유·일탈 처분은 실제 공정(gmp_process process_fsm·process_node)과 같아야 한다 —
/hmi_test 화면이 실제 셀과 다른 것을 보여 주면 시험이 HMI 를 잘못 길들인다.
"""
import copy
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest

from test_v3_backend import backend, Message, RosStub

INTERFACES = Path(__file__).resolve().parents[2] / 'gmp_interfaces/msg'
_CONSTANT = re.compile(r'^\s*u?int\d+\s+([A-Z_][A-Z0-9_]*)\s*=\s*(-?\d+)', re.M)
RUNNING, PAUSED, DEVIATION, ERROR, DONE = 1, 2, 3, 4, 5


def contract_constants(name):
    """실제 .msg 의 상수. 손으로 적으면 계약이 바뀔 때 스텁만 조용히 어긋난다 —
    OVERFILL 이 계약 0 인데 스텁 1 인 채로 통과하던 적이 있다(#266 검토)."""
    text = (INTERFACES / f'{name}.msg').read_text(encoding='utf-8')
    return {key: int(value) for key, value in _CONSTANT.findall(text)}


@pytest.fixture
def process(backend, monkeypatch):
    monkeypatch.setattr(RosStub, 'create_service', lambda self, *args: args, raising=False)
    monkeypatch.setattr(RosStub, 'create_timer', lambda self, *args: args, raising=False)
    monkeypatch.setattr(RosStub, 'get_logger', lambda self: SimpleNamespace(info=lambda *_: None, warning=lambda *_: None))
    msg = sys.modules['gmp_interfaces.msg']
    for name in ('CellEvent', 'Deviation', 'DispenseResult', 'ScoopCycle'):
        for key, value in contract_constants(name).items():
            setattr(getattr(msg, name), key, value)
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
        Message(material_id=mid, target_g=float(amount), tol_pct=10.)
        for mid, amount in amounts.items()])
    accepted = process._goal_batch(Message(recipe=recipe)) == 1
    if not accepted:
        return SimpleNamespace(accepted=False, message='Goal rejected')
    started, message = process._start_batch(recipe)
    return SimpleNamespace(accepted=started, message=message)


def height(process, mid, value):
    process._height(Message(data=json.dumps({'material_id': mid, 'height_pct': value})))


def nudge(process):
    process._on_event(Message(code='NUDGE'))


def interlock(process, request, reason='REFILL'):
    return process._interlock(Message(request=request, reason=reason), reply())


def qa(process, decision):
    return process._qa(Message(deviation_id=process.pending.deviation_id, decision=decision,
                               operator_id='qa'), reply())


def advance(process, seconds):
    for _ in range(int(round(seconds * 10))):
        process._previous_tick -= .1
        process._tick()


def run_until(process, predicate, limit_s=60):
    for _ in range(int(limit_s * 10)):
        if predicate():
            return
        advance(process, .1)
    raise AssertionError(f'{limit_s}s 안에 조건 미충족 · mode={process.mode} step={process.step}')


def published(process, kind):
    return [m for m in process.published if type(m).__name__ == kind]


def events(process, code=None):
    return [m for m in published(process, 'CellEvent') if code is None or m.code == code]


def steps_seen(process):
    """STEP 이벤트로 본 단계 순서 (실제 process_node 도 같은 이벤트를 남긴다)."""
    return [e.text.split(' → ')[1] for e in events(process, 'STEP')]


def finish_set(process):
    run_until(process, lambda: process.nudge_waiting)
    assert process.mode == PAUSED and process.step == 'NUDGE_WAIT' and process.station == 'nudge_wait'
    nudge(process)
    assert process.mode == DONE


def test_defaults_and_direct_process_order_shortage(process):
    assert process.test_scoop_nominal_g == 79.0   # SOT D-35
    assert all(item['capacity_g'] == 1000 and item['remaining_g'] == 1000 for item in process.inventory.items.values())
    assert not order(process, A=1001).accepted
    assert process.inventory.batch_id == ''
    assert order(process, A=80, B=40).accepted
    assert process.inventory.items['A']['reserved_g'] == 80
    assert process.inventory.items['C']['reserved_g'] == 0
    before = copy.deepcopy(process.inventory.snapshot(False))
    assert not process._refill('A', Message(), reply()).success
    assert process.inventory.snapshot(False) == before


def test_steps_follow_real_process_order_and_set_end_waits_for_nudge(process):
    assert order(process, A=158, B=79).accepted
    run_until(process, lambda: process.nudge_waiting)
    per_scoop = ['SCOOP', 'WEIGH_SCOOP', 'POUR', 'WEIGH_RESIDUAL']
    assert steps_seen(process) == (
        ['SELF_CHECK', 'PICK_CONTAINER', 'TARE'] +
        ['PICK_SCOOP', 'SCOOP_TARE'] + per_scoop * 2 + ['RETURN_SCOOP'] +
        ['PICK_SCOOP', 'SCOOP_TARE'] + per_scoop + ['RETURN_SCOOP'] +
        ['VERIFY', 'FINISH', 'NUDGE_WAIT'])
    # 세트 끝 — 로봇은 섰고 주문은 받지 않는다. HMI 는 note 앞머리로 사유를 본다.
    assert process.mode == PAUSED and process.note.startswith('NUDGE_WAIT')
    assert [e.code for e in events(process) if e.code in ('SET_DONE', 'SET_NEXT')] == ['SET_DONE']
    assert not order(process, C=10).accepted
    assert '세트 완료' in events(process, 'TEST_ORDER_REJECTED')[-1].text
    advance(process, 5)
    assert process.mode == PAUSED and not process.batch_done.is_set()   # 사람이 건드리기 전에는 안 끝난다
    nudge(process)
    assert (process.mode, process.step, process.finish_result) == (DONE, 'DONE', 'DONE')
    assert process.batch_done.is_set()
    assert events(process, 'SET_NEXT') and events(process, 'BATCH_END')[-1].text == 'DONE / DONE / DONE'
    assert process.inventory.items['A']['remaining_g'] == 842
    assert process.inventory.items['B']['remaining_g'] == 921
    cycles = published(process, 'ScoopCycle')
    assert [(c.material_id, c.attempt, c.actual_before_g, c.delivered_g) for c in cycles] == [
        ('A', 1, 0.0, 79.0), ('A', 2, 79.0, 79.0), ('B', 1, 0.0, 79.0)]
    # 고정 스쿱(D-34) — 접촉은 「안 재봤다」, 스쿱 계량은 원료통 위 material_N, 전량 붓기
    assert all(not c.contact_detected and c.commanded_pour_fraction == 1.0 for c in cycles)
    assert [c.weigh_pose_id for c in cycles] == ['material_1', 'material_1', 'material_2']
    containers = [w for w in published(process, 'WeightReading') if w.subject == 'container']
    assert [w.net_g for w in containers] == [35.0, 237.0]   # TARE(빈 약통) · VERIFY(내용물)
    assert order(process, C=10).accepted                     # NUDGE 뒤에는 다음 주문을 받는다


def test_nudge_while_running_pauses_and_second_nudge_resumes(process):
    assert order(process, A=79).accepted
    run_until(process, lambda: process.step == 'SCOOP')
    nudge(process)
    held = (process.plan[0]['step'], process.elapsed, len(published(process, 'ScoopCycle')))
    assert process.mode == PAUSED and process.note == 'NUDGE 정지 — 다시 건드리면 재개'
    assert events(process, 'PAUSE')
    advance(process, 5)
    assert (process.plan[0]['step'], process.elapsed, len(published(process, 'ScoopCycle'))) == held
    nudge(process)
    assert process.mode == RUNNING and events(process, 'RESUME')
    finish_set(process)
    assert process.finish_result == 'DONE'


def test_nudge_while_idle_blocks_orders_until_touched_again(process):
    nudge(process)
    assert process.note.startswith('NUDGE 일시 정지')
    assert not order(process, A=10).accepted
    nudge(process)
    assert order(process, A=10).accepted


def test_material_empty_retries_then_waits_for_refill_not_qa(process):
    process.params['scenario'] = 'material_empty'
    assert order(process, A=79).accepted
    run_until(process, lambda: process.refill_waiting)
    deviations = published(process, 'Deviation')
    dev = contract_constants('Deviation')
    assert [d.kind for d in deviations] == [dev['SCOOP_EMPTY']] * 3 + [dev['MATERIAL_EMPTY']]
    # 보충은 개입이 아니다 — QA 판정 없이 자동 복구로 남는다 (process_node _publish_deviation)
    assert all(not d.requires_decision and d.decision == dev['AUTO_RECOVERED'] for d in deviations)
    assert len({d.deviation_id for d in deviations}) == 4
    assert process.pending is None and process.mode == PAUSED and process.step == 'PAUSED'
    assert process.note.startswith('REFILL')                   # HMI pause_context `^REFILL\b`
    assert process._can_refill() and process._refill('A', Message(), reply()).success
    advance(process, 3)
    assert process.mode == PAUSED                              # 보충만으로는 재개하지 않는다
    assert interlock(process, 1).granted                       # 이미 안전 자세 — 멱등
    assert interlock(process, 2).granted
    finish_set(process)
    cycles = published(process, 'ScoopCycle')
    cycle = contract_constants('ScoopCycle')
    assert [(c.attempt, c.outcome, c.valid) for c in cycles] == [
        (n, cycle['SCOOP_EMPTY'], False) for n in range(1, 5)] + [(5, cycle['COMPLETE'], True)]
    assert process.finish_result == 'DONE'


def test_qa_discard_returns_scoop_parks_and_is_done_only_after_nudge(process):
    process.params['scenario'] = 'overfill'
    assert order(process, A=40, B=40).accepted
    run_until(process, lambda: process.mode == DEVIATION)
    assert process.pending.kind == contract_constants('Deviation')['OVERFILL'] and process.holding_scoop
    assert qa(process, 2).accepted
    run_until(process, lambda: process.nudge_waiting)
    assert steps_seen(process)[-3:] == ['RETURN_SCOOP', 'DISCARDED', 'NUDGE_WAIT']
    assert DONE not in {m.mode for m in published(process, 'CellState')}   # 물리 종료 전에 DONE 을 내지 않는다
    nudge(process)
    assert (process.mode, process.step, process.finish_result) == (DONE, 'DISCARDED', 'DISCARDED')


def test_weigh_invalid_approval_ends_unmeasured(process):
    process.params['scenario'] = 'weigh_invalid'
    assert order(process, A=158, B=79).accepted
    run_until(process, lambda: process.mode == DEVIATION)
    assert qa(process, 1).accepted
    finish_set(process)
    results = published(process, 'DispenseResult')
    assert (results[0].material_id, results[0].verdict) == ('A', contract_constants('DispenseResult')['INVALID'])
    assert process.finish_result == 'DONE_UNMEASURED'
    assert events(process, 'BATCH_UNMEASURED') and events(process, 'DISPENSE_UNMEASURED')
    assert process.inventory.items['A']['remaining_g'] == 1000              # 모르는 양은 차감하지 않는다


def test_nudge_during_qa_wait_pauses_after_decision(process):
    process.params['scenario'] = 'overfill'
    assert order(process, A=40, B=40).accepted
    run_until(process, lambda: process.mode == DEVIATION)
    nudge(process)
    assert process.mode == DEVIATION                     # 판정 대기 중에는 mode 를 덮지 않는다
    assert qa(process, 1).accepted
    assert process.mode == PAUSED and process.note.startswith('NUDGE')
    nudge(process)
    assert process.mode == RUNNING
    finish_set(process)


def test_cancel_marks_aborted_like_real_process(process):
    assert order(process, A=40).accepted
    advance(process, 1)
    process._abort('RunBatch 취소 — 배치 자동 재개 없음')
    assert (process.mode, process.step) == (ERROR, 'ABORTED')
    assert events(process, 'BATCH_CANCELLED') and process.batch_done.is_set()
    assert process.inventory.items['A']['reserved_g'] == 0
    assert order(process, A=40).accepted                 # 실제처럼 ERROR 뒤 새 주문을 받는다


def test_exit_without_waiting_is_ignored_like_real_process(process):
    response = interlock(process, 2)
    assert response.granted and '무시' in response.message


def test_height_holds_running_without_claiming_entry_and_requires_all_refills_exit(process):
    assert order(process, A=80, B=40).accepted
    run_until(process, lambda: process.step == 'SCOOP')
    height(process, 'C', 19)
    before = (process.step, process.index, process.elapsed, copy.deepcopy(process.inventory.items))
    advance(process, 6)
    assert (process.step, process.index, process.elapsed, process.inventory.items) == before
    assert process.mode == RUNNING  # 진행만 붙든다. 안전 진입 허가를 의미하지 않는다.
    assert not process._refill('C', Message(), reply()).success
    assert interlock(process, 1).granted
    assert process.mode == PAUSED and process.previous is not None
    assert process.note.startswith('인터락 ENTER')   # HMI pause_context 가 INTERLOCK 으로 가른다
    height(process, 'A', 10)
    height(process, 'C', 100)
    assert process.inventory.blocked_materials == ['A', 'C']
    assert not interlock(process, 2).granted
    assert process._refill('C', Message(), reply()).success
    assert process.mode == PAUSED and process.inventory.blocked_materials == ['A']
    assert not interlock(process, 2).granted
    assert process._refill('A', Message(), reply()).success
    assert process.inventory.items['A']['reserved_g'] == 80
    advance(process, 5)
    assert process.mode == PAUSED and process.elapsed == before[2]
    assert interlock(process, 2).granted
    finish_set(process)
    assert process.inventory.items['A']['remaining_g'] == 920
    assert process.inventory.items['B']['remaining_g'] == 960
    assert process.inventory.items['C']['remaining_g'] == 1000
    cycles = published(process, 'ScoopCycle')
    assert [(cycle.material_id, cycle.attempt, cycle.delivered_g) for cycle in cycles] == [
        ('A', 1, 79.0), ('A', 2, 1.0), ('B', 1, 40.0)]
    assert cycles[1].actual_before_g == 79.0


def test_height_hold_does_not_skip_qa_or_transfer(process):
    process.params['scenario'] = 'overfill'
    assert order(process, A=40, B=40).accepted
    run_until(process, lambda: process.mode == DEVIATION)
    height(process, 'C', 0)
    decision = Message(deviation_id=process.pending.deviation_id, decision=1, operator_id='qa')
    assert not process._qa(decision, reply()).accepted
    assert interlock(process, 1).granted
    assert process._refill('C', Message(), reply()).success
    assert process.mode == PAUSED
    assert interlock(process, 2).granted
    assert process.mode == DEVIATION
    assert process._qa(decision, reply()).accepted
    run_until(process, lambda: process.step == 'FINISH')
    height(process, 'B', 10)
    advance(process, 5)
    assert process.mode == RUNNING and process.step == 'FINISH'
    assert interlock(process, 1).granted
    assert process._refill('B', Message(), reply()).success
    assert interlock(process, 2).granted
    finish_set(process)


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
