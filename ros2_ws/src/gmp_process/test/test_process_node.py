"""process_node 통합 시험 — 가짜 skill_node 를 상대로 레시피 1건을 끝까지 돌린다.

FSM 단위 시험(test_process_fsm.py)은 전이표를 본다. 이 시험은 그 위의 **배선**을 본다 —
요청 dict 가 계약 Action/Service 로 제대로 나가고, 결과가 제대로 해석되고, 토픽 5종이 나오는가.
로봇·힘제어·실제 분해능은 여기서 검증되지 않는다.

ROS 가 안 깔린 곳에서는 통째로 건너뛴다 (core 단위 시험은 그대로 돈다).
"""
import os
import sys
import threading
import time

import pytest

rclpy = pytest.importorskip('rclpy', reason='ROS 2 환경이 아니다')
pytest.importorskip('gmp_interfaces', reason='gmp_interfaces 가 빌드되지 않았다')

sys.path.insert(0, os.path.dirname(__file__))

from rclpy.executors import MultiThreadedExecutor            # noqa: E402
from rclpy.node import Node                                  # noqa: E402
from rclpy.parameter import Parameter                        # noqa: E402

from gmp_interfaces.msg import (CellState, Deviation, DispenseResult, ScoopCycle,  # noqa: E402
                                Recipe, RecipeItem, WeightReading)
from gmp_interfaces.srv import SubmitOrder                    # noqa: E402

from fake_skill_node import FakeSkillNode                     # noqa: E402
from gmp_process.nodes.process_node import ProcessNode        # noqa: E402

STATIONS = os.path.join(os.path.dirname(__file__), '..', '..', 'gmp_bringup', 'params', 'stations.yaml')


class Collector(Node):
    def __init__(self):
        super().__init__('collector')
        from rclpy.qos import DurabilityPolicy, QoSProfile
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.states, self.weights, self.cycles, self.results, self.devs = [], [], [], [], []
        self.create_subscription(CellState, 'state', self.states.append, latched)
        self.create_subscription(WeightReading, 'weight', self.weights.append, 20)
        self.create_subscription(ScoopCycle, 'scoop_cycle', self.cycles.append, 50)
        self.create_subscription(DispenseResult, 'dispense_result', self.results.append, 50)
        self.create_subscription(Deviation, 'deviation',  self.devs.append,
                                 QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL))


@pytest.fixture
def cell():
    rclpy.init()
    proc = ProcessNode(parameter_overrides=[
        Parameter('stations_file', value=os.path.abspath(STATIONS)),
        Parameter('scale.samples', value=4), Parameter('scale.settle_s', value=0.0),
        Parameter('server_wait_s', value=10.0), Parameter('skill_timeout_s', value=15.0),
    ])
    fake = FakeSkillNode()
    col = Collector()
    ex = MultiThreadedExecutor(num_threads=6)
    for n in (proc, fake, col):
        ex.add_node(n)
    t = threading.Thread(target=ex.spin, daemon=True)
    t.start()
    try:
        yield proc, fake, col
    finally:
        proc.shutdown()                 # 실행 루프를 먼저 세운다 — 죽은 노드로 발행하지 않게
        ex.shutdown()
        for n in (proc, fake, col):
            n.destroy_node()
        rclpy.shutdown()
        t.join(timeout=2.0)


def _submit(col, items, product='테스트정'):
    cli = col.create_client(SubmitOrder, 'submit_order')
    assert cli.wait_for_service(timeout_sec=10.0), 'submit_order 서버가 없다'
    recipe = Recipe(product=product,
                    items=[RecipeItem(material_id=m, target_g=t, tol_pct=p) for m, t, p in items])
    fut = cli.call_async(SubmitOrder.Request(recipe=recipe))
    t0 = time.time()
    while not fut.done() and time.time() - t0 < 10.0:
        time.sleep(0.02)
    assert fut.done(), 'submit_order 응답 없음'
    return fut.result()


def _wait_done(proc, timeout=60.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.fsm and proc.fsm.mode in ('DONE', 'ERROR'):
            time.sleep(0.5)          # 마지막 발행이 나갈 틈
            return proc.fsm.mode
        time.sleep(0.05)
    return 'TIMEOUT'


def test_batch_runs_to_completion(cell):
    """원료 2종 레시피가 일탈 없이 FINISH 까지 간다 — D-22 6단계 × 재시도."""
    proc, fake, col = cell
    res = _submit(col, [('A', 100.0, 5.0), ('B', 80.0, 8.0)])
    assert res.accepted, res.message
    assert res.batch_id.startswith('B-')

    assert _wait_done(proc) == 'DONE', f'끝나지 않았다: {proc.fsm.state} / {proc.note}'
    assert proc.fsm.state == 'DONE'
    assert not proc.fsm.deviations, proc.fsm.deviations

    # 원료 2종이 각각 허용 오차 안에서 끝났다
    assert len(proc.fsm.results) == 2
    for r in proc.fsm.results:
        assert r.verdict == 'OK', (r.material_id, r.verdict, r.actual_g)

    # 발행 — 원료마다 시도 1건씩 ScoopCycle, 원료마다 DispenseResult 1건
    assert len(col.cycles) == sum(r.attempts for r in proc.fsm.results), [c.attempt for c in col.cycles]
    assert len(col.results) == 2
    assert not col.devs
    assert {c.material_id for c in col.cycles} == {'A', 'B'}


def test_skill_call_sequence(cell):
    """계약대로 부르는가 — 전용 스쿱 해석(D-24)·계량 주체 구분(I-007 c)·carry 조합(D-18)."""
    proc, fake, col = cell
    _submit(col, [('B', 80.0, 8.0)])
    assert _wait_done(proc) == 'DONE', proc.note

    calls = fake.calls
    assert calls[0] == 'measure_force'                       # SELF_CHECK
    # 원료 B 는 stations.yaml 에서 scoop_2 로 풀린다 (FSM 은 'scoop' 이라고만 말한다)
    assert any(c == 'move:scoop_2:1' for c in calls), calls
    assert not any(c.startswith('move:scoop:') for c in calls), '원료 → 스쿱 해석이 안 됐다'
    # carry 는 MoveToStation 4쌍 + SetGripper 2회로 조합된다 (D-18)
    assert 'move:passbox_empty:0' in calls and 'move:passbox_done:0' in calls
    # 계량 횟수 — 용기는 2회(TARE·VERIFY), 스쿱은 **빈 스쿱 1회 + 시도마다 2회**(붓기 전·후).
    # 빈 스쿱은 원료마다 한 번만 잰다 (D-22) — 시도마다 3회가 아니다
    attempts = proc.fsm.results[0].attempts
    assert calls.count('weigh_container') == 2
    assert calls.count('weigh_held') == 1 + 2 * attempts

    subjects = [w.subject for w in col.weights]
    assert subjects.count('container') == 2
    assert subjects.count('scoop') == 1 + 2 * attempts


def test_rejects_unknown_material(cell):
    """전용 스쿱이 없는 원료는 주문 단계에서 거부한다 — 배치 중간에 서지 않게."""
    proc, fake, col = cell
    res = _submit(col, [('Z', 50.0, 5.0)])
    assert not res.accepted
    assert 'Z' in res.message


def test_scoop_cycle_marks_missing_wrench(cell):
    """계량 스킬이 6축 wrench 를 안 주므로 valid=false 로 남긴다 (I-008) — 0 을 참값처럼 두지 않는다."""
    proc, fake, col = cell
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_done(proc) == 'DONE', proc.note

    c = col.cycles[-1]
    assert c.valid and c.outcome == ScoopCycle.COMPLETE
    assert c.delivered_g > 0.0
    assert c.scoop_tare.valid and c.pre_pour.valid and c.post_pour.valid
    assert not (c.tare_wrench_valid or c.pre_pour_wrench_valid or c.post_pour_wrench_valid)
    assert c.weigh_pose_id == 'material_1'


def test_skill_failure_becomes_force_limit_then_error(cell):
    """스킬 실패는 FORCE_LIMIT 일탈이 된다 — 1회 재시도하고, 또 실패하면 ERROR (process_flow 8절).

    무한 재시도로 서 있는 것이 최악이다. RULES 의 (1, RETRY, FORCED) 가 상한을 준다.
    """
    proc, fake, col = cell
    fake.fail['pour'] = 99                      # 계속 실패시킨다
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_done(proc) == 'ERROR'

    kinds = [d['kind'] for d in proc.fsm.deviations]
    assert kinds == ['FORCE_LIMIT', 'FORCE_LIMIT'], kinds      # RETRY 1회 → FORCED
    assert [d['action'] for d in proc.fsm.deviations] == ['RETRY', 'FORCED']
    assert fake.calls.count('pour:1.000') == 2                 # 같은 요청을 한 번만 더 부른다
    assert any(c.startswith('safe:') for c in fake.calls)      # 끝에 안전 자세로 간다
    assert col.devs and col.devs[0].kind == Deviation.FORCE_LIMIT
    assert '기울임' in col.devs[0].detail                       # 실패 사유가 일탈에 남는다


def test_missing_skill_server_does_not_hang(cell):
    """서버가 아예 없으면(예: A 가 weigh_held 를 아직 안 올렸을 때) 기다리다 죽지 않고 일탈로 끝난다."""
    proc, fake, col = cell
    fake.destroy_node()                          # 스킬 서버를 통째로 내린다
    proc.set_parameters([Parameter('server_wait_s', value=1.0)])
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_done(proc, timeout=40.0) == 'ERROR'
    assert proc.fsm.deviations, '일탈 없이 끝났다'


def _qa(col, deviation_id, decision, operator='qa_kim'):
    from gmp_interfaces.srv import QaDecision
    cli = col.create_client(QaDecision, 'qa_decision')
    assert cli.wait_for_service(timeout_sec=5.0)
    fut = cli.call_async(QaDecision.Request(deviation_id=deviation_id, decision=decision,
                                            operator_id=operator))
    t0 = time.time()
    while not fut.done() and time.time() - t0 < 5.0:
        time.sleep(0.02)
    assert fut.done()
    return fut.result()


def _wait_mode(proc, mode, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.fsm and proc.fsm.mode == mode:
            return True
        time.sleep(0.02)
    return False


def test_qa_rejects_wrong_deviation_id_then_approves(cell):
    """QA 판정은 **대기 중인 그 일탈**에만 붙는다. 승인하면 같은 deviation_id 로 재발행한다."""
    proc, fake, col = cell
    fake.transfer = 2.0                          # 부으면 퍼낸 양의 2배가 들어간다 → 과투입
    _submit(col, [('A', 10.0, 5.0)])
    assert _wait_mode(proc, 'DEVIATION'), proc.fsm.state

    dev = proc._pending_dev()
    assert dev.kind == Deviation.OVERFILL and dev.requires_decision
    assert dev.deviation_id.startswith('D-B-')

    bad = _qa(col, 'D-없는-배치-9', Deviation.APPROVED)
    assert not bad.accepted and dev.deviation_id in bad.message

    ok = _qa(col, dev.deviation_id, Deviation.APPROVED)
    assert ok.accepted

    # 과투입을 승인했으니 배치 끝 VERIFY 에서 규격 이탈로 한 번 더 묻는다 (D-22 ①) — 그것도 승인한다
    assert _wait_mode(proc, 'DEVIATION')
    second = proc._pending_dev()
    assert second.kind == Deviation.BATCH_OUT_OF_SPEC
    assert _qa(col, second.deviation_id, Deviation.APPROVED).accepted
    assert _wait_done(proc) == 'DONE'

    same = [d for d in col.devs if d.deviation_id == dev.deviation_id]
    assert len(same) == 2, '판정 후 같은 ID 로 재발행해야 record 가 upsert 한다'
    assert same[-1].decision == Deviation.APPROVED and same[-1].operator_id == 'qa_kim'


def test_qa_discard_sends_the_cup_to_reject_bin(cell):
    """폐기 판정이면 스쿱을 먼저 반납하고 용기째 폐기함으로 간다 (_qa_step 분기)."""
    proc, fake, col = cell
    fake.transfer = 2.0
    _submit(col, [('A', 10.0, 5.0)])
    assert _wait_mode(proc, 'DEVIATION')

    _qa(col, proc._pending_dev().deviation_id, Deviation.DISCARDED)
    assert _wait_done(proc) == 'DONE'
    assert proc.fsm.state == 'DISCARDED'
    assert 'move:reject_bin:1' in fake.calls, fake.calls[-12:]
    assert not col.results, '폐기된 배치는 분주 결과를 남기지 않는다'


def test_interlock_enter_pauses_and_exit_resumes(cell):
    """ENTER → safe_pose 로 스킬이 끊긴다 → PAUSED. EXIT 로 같은 요청을 다시 부르고 완주한다."""
    proc, fake, col = cell
    fake.delay['move'] = 0.25                    # 이동 중에 끼어들 틈을 만든다
    _submit(col, [('A', 100.0, 5.0)])

    from gmp_interfaces.srv import InterlockRequest
    cli = col.create_client(InterlockRequest, 'interlock')
    assert cli.wait_for_service(timeout_sec=5.0)

    def call(request, reason):
        fut = cli.call_async(InterlockRequest.Request(request=request, reason=reason))
        t0 = time.time()
        while not fut.done() and time.time() - t0 < 10.0:
            time.sleep(0.02)
        assert fut.done()
        return fut.result()

    time.sleep(0.3)
    assert call(InterlockRequest.Request.ENTER, 'REFILL').granted
    assert _wait_mode(proc, 'PAUSED'), proc.fsm.mode
    assert any(c.startswith('safe:') for c in fake.calls)

    fake.delay.clear()
    assert call(InterlockRequest.Request.EXIT, 'REFILL').granted
    assert _wait_done(proc) == 'DONE', f'{proc.fsm.state} / {proc.note}'
    assert not proc.fsm.deviations, '인터락이 끊은 것은 일탈이 아니다'


def test_exit_without_pause_is_ignored(cell):
    """아무도 안 기다리는데 EXIT 를 받아 두면 다음 보충 대기가 저절로 풀린다 — 그래서 무시한다."""
    proc, fake, col = cell
    from gmp_interfaces.srv import InterlockRequest
    cli = col.create_client(InterlockRequest, 'interlock')
    assert cli.wait_for_service(timeout_sec=5.0)
    fut = cli.call_async(InterlockRequest.Request(request=InterlockRequest.Request.EXIT, reason='실수'))
    t0 = time.time()
    while not fut.done() and time.time() - t0 < 5.0:
        time.sleep(0.02)
    assert fut.result().granted
    assert not proc._interlock_exit.is_set()


def _lock(col, request, reason='REFILL'):
    from gmp_interfaces.srv import InterlockRequest
    cli = col.create_client(InterlockRequest, 'interlock')
    assert cli.wait_for_service(timeout_sec=5.0)
    fut = cli.call_async(InterlockRequest.Request(request=request, reason=reason))
    t0 = time.time()
    while not fut.done() and time.time() - t0 < 20.0:
        time.sleep(0.02)
    assert fut.done()
    return fut.result()


def test_enter_during_qa_wait_keeps_qa_open(cell):
    """QA 대기 중 ENTER 가 와도 QA 를 계속 받는다. 판정 뒤에는 EXIT 까지 멈췄다가 완주한다.

    ENTER 가 mode 를 PAUSED 로 덮으면 _srv_qa(mode==DEVIATION 만 허용)가 영영 거부하고 루프는 QA 만
    기다리는 교착이 된다 — PR #18 리뷰 1번.
    """
    from gmp_interfaces.srv import InterlockRequest
    proc, fake, col = cell
    fake.transfer = 2.0
    _submit(col, [('A', 10.0, 5.0)])
    assert _wait_mode(proc, 'DEVIATION')
    dev = proc._pending_dev()

    assert _lock(col, InterlockRequest.Request.ENTER).granted
    assert proc.fsm.mode == 'DEVIATION', '인터락이 QA 대기 상태를 덮으면 안 된다'
    assert 'QA' in proc.note
    assert _lock(col, InterlockRequest.Request.ENTER).message.startswith('이미')   # 두 번 눌러도 멱등

    assert _qa(col, dev.deviation_id, Deviation.APPROVED).accepted
    assert _wait_mode(proc, 'PAUSED'), '판정 뒤에는 사람이 나올 때까지(EXIT) 멈춘다'
    assert _lock(col, InterlockRequest.Request.EXIT).granted

    # 과투입 승인 → VERIFY 규격 이탈도 승인 → 완주
    assert _wait_mode(proc, 'DEVIATION')
    assert _qa(col, proc._pending_dev().deviation_id, Deviation.APPROVED).accepted
    assert _wait_done(proc) == 'DONE', f'{proc.fsm.state} / {proc.note}'


def test_forced_deviation_is_not_auto_recovered(cell):
    """FORCED(강제 개입)로 끝난 일탈은 AUTO_RECOVERED 가 아니다 — 자동 복구율이 부풀지 않게 (리뷰 2번)."""
    proc, fake, col = cell
    fake.fail['pour'] = 99
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_done(proc) == 'ERROR'
    decisions = [(d.detail.split(' · ')[1], d.decision) for d in col.devs]
    forced = getattr(Deviation, 'FORCED', Deviation.PENDING)      # 계약 v1.2.1(PR #19) 전 빌드는 PENDING 폴백
    assert decisions == [('RETRY', Deviation.AUTO_RECOVERED), ('FORCED', forced)], decisions


def test_qa_rejects_invalid_decision_value(cell):
    """승인(1)·폐기(2) 외의 판정값은 거부한다 — 0 을 보냈다고 폐기로 흘러가면 안 된다 (리뷰 3번)."""
    proc, fake, col = cell
    fake.transfer = 2.0
    _submit(col, [('A', 10.0, 5.0)])
    assert _wait_mode(proc, 'DEVIATION')
    dev = proc._pending_dev()

    for bad in (Deviation.PENDING, Deviation.AUTO_RECOVERED, 7):
        r = _qa(col, dev.deviation_id, bad)
        assert not r.accepted and str(bad) in r.message, (bad, r.message)
    assert proc.fsm.mode == 'DEVIATION' and dev.decision == Deviation.PENDING   # 아무것도 안 바뀌었다
    assert _qa(col, dev.deviation_id, Deviation.DISCARDED).accepted
    assert _wait_done(proc) == 'DONE' and proc.fsm.state == 'DISCARDED'


def test_refill_wait_then_enter_resumes_with_one_exit(cell):
    """원료 소진 → REFILL 대기(PAUSED) 중 사람이 ENTER 를 또 누르고 EXIT 한 번 → 재개·완주 (리뷰 P2)."""
    from gmp_interfaces.srv import InterlockRequest
    proc, fake, col = cell
    fake.empty = 4                                # SCOOP_EMPTY ×3 재시도 → 4회째 MATERIAL_EMPTY → REFILL
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_mode(proc, 'PAUSED'), proc.fsm.state
    kinds = [d['kind'] for d in proc.fsm.deviations]
    assert kinds == ['SCOOP_EMPTY'] * 4 and proc.fsm.deviations[-1]['action'] == 'REFILL'

    r = _lock(col, InterlockRequest.Request.ENTER)
    assert r.granted and r.message.startswith('이미'), r.message      # 이미 안전 자세 — safe_pose 재호출 없음
    assert _lock(col, InterlockRequest.Request.EXIT).granted
    assert _wait_done(proc) == 'DONE', f'{proc.fsm.state} / {proc.note} / _pause={proc._pause}'
    # 빈 스쿱 시도 4건은 SCOOP_EMPTY 로, 성공 1건은 COMPLETE 로 남는다 — 하나도 안 잃는다
    outcomes = [c.outcome for c in col.cycles]
    assert outcomes.count(ScoopCycle.SCOOP_EMPTY) == 4 and outcomes.count(ScoopCycle.COMPLETE) >= 1, outcomes


def test_scoop_skill_failure_does_not_lose_scoop_cycle(cell):
    """스쿱 스킬이 실패(FORCE_LIMIT)해 같은 요청을 다시 부를 때 앞 시도 기록이 덮여 사라지면 안 된다 (리뷰 P2)."""
    proc, fake, col = cell
    fake.fail['scoop'] = 1                        # 첫 담그기만 실패 → RETRY → 성공
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_done(proc) == 'DONE', proc.note
    assert [d['kind'] for d in proc.fsm.deviations] == ['FORCE_LIMIT']
    outcomes = [c.outcome for c in col.cycles]
    assert outcomes[0] == ScoopCycle.ABORTED and ScoopCycle.COMPLETE in outcomes, outcomes
    assert len(col.cycles) == 1 + sum(r.attempts for r in proc.fsm.results)


def test_submit_rejects_invalid_numbers_and_duplicates(cell):
    """주문 검증은 core/recipe.parse 단일 출처 — 0·음수·NaN·중복 원료는 주문에서 거부한다 (리뷰 P2)."""
    proc, fake, col = cell
    for items, key in (([('A', 0.0, 5.0)], '양수'), ([('A', -10.0, 5.0)], '양수'), ([('A', 100.0, 0.0)], '양수'),
                       ([('A', float('nan'), 5.0)], '유한'), ([('A', 100.0, float('inf'))], '유한'),
                       ([('A', 100.0, 5.0), ('A', 50.0, 5.0)], '중복'), ([], 'items')):
        r = _submit(col, items)
        assert not r.accepted and key in r.message, (items, r.message)
    assert proc.fsm is None                       # 아무 배치도 시작되지 않았다


def test_scoop_cycle_attempt_numbers_are_unique_per_material(cell):
    """빈 스쿱 재시도도 시도다 — ScoopCycle.attempt 는 원료별로 1,2,3… 한 번씩만 나온다."""
    proc, fake, col = cell
    fake.empty = 2                                 # SCOOP_EMPTY ×2 → 3번째 성공
    _submit(col, [('A', 100.0, 5.0), ('B', 80.0, 8.0)])
    assert _wait_done(proc) == 'DONE', proc.note
    a = [c.attempt for c in col.cycles if c.material_id == 'A']
    b = [c.attempt for c in col.cycles if c.material_id == 'B']
    assert a == list(range(1, len(a) + 1)) and a[:2] == [1, 2], a
    assert b == list(range(1, len(b) + 1)), b
    assert [c.outcome for c in col.cycles if c.material_id == 'A'][:2] == [ScoopCycle.SCOOP_EMPTY] * 2
