"""process_node 통합 시험 — 가짜 skill_node 를 상대로 레시피 1건을 끝까지 돌린다.

FSM 단위 시험(test_process_fsm.py)은 전이표를 본다. 이 시험은 그 위의 **배선**을 본다 —
요청 dict 가 계약 Action/Service 로 제대로 나가고, 결과가 제대로 해석되고, 토픽 5종이 나오는가.
로봇·힘제어·실제 분해능은 여기서 검증되지 않는다.

ROS 가 안 깔린 곳에서는 통째로 건너뛴다 (core 단위 시험은 그대로 돈다).
"""
import os
import re
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

from gmp_interfaces.msg import (CellEvent, CellState, Deviation, DispenseResult, ScoopCycle,  # noqa: E402
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
        self.events = []
        self.create_subscription(CellState, 'state', self.states.append, latched)
        self.create_subscription(WeightReading, 'weight', self.weights.append, 20)
        self.create_subscription(ScoopCycle, 'scoop_cycle', self.cycles.append, 50)
        self.create_subscription(DispenseResult, 'dispense_result', self.results.append, 50)
        self.create_subscription(Deviation, 'deviation',  self.devs.append,
                                 QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.create_subscription(CellEvent, 'event', self.events.append, 100)


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
    fake.attend(proc)                   # 세트 끝마다 건드려 주는 사람 — 배치가 DONE 까지 가게 한다 (D-23)
    try:
        yield proc, fake, col
    finally:
        fake.stop_attending()
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


def test_scoop_grip_commands_the_search_width_not_the_expected_width(cell):
    """스쿱 파지 명령폭은 원료와 무관한 탐색 폭이다 — 기대 폭(WRONG_TOOL 판정용)을 명령폭으로 쓰면
    그보다 가는 손잡이는 접촉조차 못 해 GRIP_FAIL 로 빠진다 (A 리뷰, PR #165)."""
    proc, fake, col = cell
    search = float(proc.p('gripper.scoop_search_width_mm'))
    cup = float(proc.p('gripper.cup_width_mm'))
    _submit(col, [('A', 100.0, 5.0), ('C', 50.0, 5.0)])       # 기대 폭이 서로 다른 두 원료 (15.5 / 28)
    assert _wait_done(proc) == 'DONE', proc.note

    closes = [c for c in fake.calls if c.startswith('grip:close:')]
    scoop_closes = [c for c in closes if c != f'grip:close:{cup:.0f}']
    assert scoop_closes, closes
    assert set(scoop_closes) == {f'grip:close:{search:.0f}'}, \
        f'원료마다 다른 폭으로 명령하고 있다: {scoop_closes}'


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


def _wait_state_note(col, pattern, timeout=20.0):
    """발행된 CellState 중 note 앞머리가 pattern 에 맞는 것이 있었는가."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if any(re.match(pattern, s.note or '') for s in col.states):
            return True
        time.sleep(0.02)
    return False


def _wait_mode(proc, mode, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.fsm and proc.fsm.mode == mode:
            return True
        time.sleep(0.02)
    return False


def _why(proc):
    """실패 메시지에 붙일 현재 상태 — 타이밍 문제는 이것 없이는 못 쫓는다."""
    f = proc.fsm
    return (f'mode={f and f.mode} state={f and f.state} pause={proc._pause} '
            f'nudge={proc._nudge_paused} exit_set={proc._interlock_exit.is_set()} '
            f'pending={proc._pending_dev() and proc._pending_dev().deviation_id} note={proc.note!r}')


def test_qa_rejects_wrong_deviation_id_then_approves(cell):
    """QA 판정은 **대기 중인 그 일탈**에만 붙는다. 승인하면 같은 deviation_id 로 재발행한다."""
    proc, fake, col = cell
    # 붓을 때 스쿱 투입량의 2배가 약통에 들어간다(흘림·편향 모사). 스쿱 계량(WEIGH_SCOOP·WEIGH_RESIDUAL)은
    # 정상으로 보이므로 배치 끝 VERIFY ① 이 BATCH_OUT_OF_SPEC 으로 잡는다 (D-22 ①). 깊이 계약(v1.5) 뒤로는
    # 정상 스쿱 경로에서 OVERFILL 이 나지 않는다 — 스쿱량이 남은 양+허용오차를 넘으면 붓기 전에 반환하기 때문.
    fake.transfer = 2.0
    _submit(col, [('A', 10.0, 5.0)])
    assert _wait_mode(proc, 'DEVIATION'), proc.fsm.state

    dev = proc._pending_dev()
    assert dev.kind == Deviation.BATCH_OUT_OF_SPEC and dev.requires_decision
    assert dev.deviation_id.startswith('D-B-')

    bad = _qa(col, 'D-없는-배치-9', Deviation.APPROVED)
    assert not bad.accepted and dev.deviation_id in bad.message

    ok = _qa(col, dev.deviation_id, Deviation.APPROVED)
    assert ok.accepted
    assert _wait_done(proc) == 'DONE', _why(proc)

    same = [d for d in col.devs if d.deviation_id == dev.deviation_id]
    assert len(same) == 2, '판정 후 같은 ID 로 재발행해야 record 가 upsert 한다'
    assert same[-1].decision == Deviation.APPROVED and same[-1].operator_id == 'qa_kim'


def test_qa_discard_sends_the_cup_to_reject_bin(cell):
    """폐기 판정이면 스쿱을 먼저 반납하고 용기째 폐기함으로 간다 (_qa_step 분기).

    일탈은 붓기 **전**에 나야 한다 — 스쿱을 쥔 채 폐기로 들어가야 "스쿱 먼저 반납"이 시험되고, 아직 아무
    원료도 넣지 않았으니 분주 결과가 없어야 한다. scoop_gain 을 크게 주면 min_fraction 깊이로도 남은 양을
    넘겨 반환만 반복하다 TIMEOUT 으로 QA 대기에 들어간다.
    """
    proc, fake, col = cell
    fake.scoop_gain = 4.0
    _submit(col, [('A', 10.0, 5.0)])
    assert _wait_mode(proc, 'DEVIATION'), _why(proc)
    assert proc._pending_dev().kind == Deviation.TIMEOUT and proc.fsm.state == 'DEVIATION'
    assert fake.held, '스쿱을 쥔 채 QA 를 기다려야 한다'

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
    assert _wait_mode(proc, 'PAUSED'), f'판정 뒤에는 사람이 나올 때까지(EXIT) 멈춘다 — {_why(proc)}'
    assert _lock(col, InterlockRequest.Request.EXIT).granted

    # VERIFY ① 규격 이탈(transfer=2.0)을 승인했으니 남은 것은 완주다
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


def test_refill_wait_puts_reason_at_head_of_note(cell):
    """REFILL 대기 중 CellState.note **앞머리**에 정지 사유가 실린다 (#191).

    HMI 는 note 앞머리로 정지 사유를 가른다 (gmp_hmi pause_context.pause_reason 이 `^REFILL\\b`).
    사유를 안 실으면 대기 내내 note 가 비어 HMI 의 REFILL 분기가 영영 안 뜬다 — 그게 #191 이다.
    `_pause_reason()` 은 이 경로에 관여하지 않는다 (REFILL 대기 중 `_pause` 는 False 다).
    """
    from gmp_interfaces.srv import InterlockRequest
    proc, fake, col = cell
    fake.empty = 4                                # SCOOP_EMPTY ×3 재시도 → 4회째 MATERIAL_EMPTY → REFILL
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_mode(proc, 'PAUSED'), proc.fsm.state
    assert proc.fsm.deviations[-1]['action'] == 'REFILL', proc.fsm.deviations

    # **폴링으로 먼저 기다린다.** `_wait_mode` 가 True 가 되는 시점(`on_result` 안, REFILL 판정 즉시)과
    # note 가 실리는 시점(`_run_loop` 스레드가 그 다음 `safe` 를 dispatch 할 때)이 달라서, 여기서
    # `proc.note` 를 바로 읽으면 스레드 스케줄링 창에 빈 값으로 걸린다 (정합성 검토: 14회 중 4회 실패).
    # 발행까지 돼야 HMI 가 본다 — proc.note 만 맞고 CellState 에 안 실리면 소용없다.
    assert _wait_state_note(col, r'^REFILL\b'), [t.note for t in col.states[-5:]]
    # 발행됐으면 그 값은 EXIT 전까지 유지된다 — 이제 동기적으로 읽어도 안전하다
    assert re.match(r'^REFILL\b', proc.note), f'note 앞머리가 REFILL 이어야 한다: {proc.note!r}'
    assert not proc._pause, 'REFILL 대기는 인터락 정지가 아니다 — _pause 가 서면 안 된다'

    assert _lock(col, InterlockRequest.Request.EXIT).granted
    assert _wait_done(proc) == 'DONE', f'{proc.fsm.state} / {proc.note}'
    assert proc.note == '', f'대기가 끝나면 사유를 내린다: {proc.note!r}'


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


def test_rescoop_after_return_is_blocked_once_with_a_clear_reason(cell):
    """실물은 반환 뒤 Scoop 을 전부 거부한다 (v1.5.1 · PR #43, 연결 경로 #64 미구현).
    재시도해도 같은 이유로 거부되므로 FORCE_LIMIT 2건이 아니라 사유 1건으로 끝나야 한다."""
    proc, fake, col = cell
    fake.block_rescoop = True                     # 반환이 성공하면 이후 Scoop 을 실물처럼 거부한다
    fake.scoop_gain = 4.0                         # 한 번에 목표+허용오차를 넘겨 퍼 → 붓기 전 반환
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_done(proc) == 'ERROR', _why(proc)

    assert any(c.startswith('return_material:') for c in fake.calls), fake.calls
    after_return = fake.calls[fake.calls.index(
        next(c for c in fake.calls if c.startswith('return_material:'))) + 1:]
    assert len([c for c in after_return if c.startswith('scoop:')]) == 1, \
        f'거부될 걸 알면서 재시도했다: {after_return}'

    devs = [d for d in proc.fsm.deviations if d['step'] == 'SCOOP']
    assert len(devs) == 1 and devs[0]['action'] == 'FORCED', proc.fsm.deviations
    assert '반환 후 재스쿱 차단' in devs[0]['detail'] and '연결 경로 미구현' in devs[0]['detail']
    assert any(c.startswith('safe:') for c in fake.calls)      # 끝에 안전 자세로 간다


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


# ── NUDGE 게이트 (추가 기능 7 · D-21) ────────────────────────────────────
def _wait_until(fn, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return True
        time.sleep(0.02)
    return False


def test_nudge_pauses_and_second_nudge_resumes(cell):
    """건드리면 정지, 다시 건드리면 재개 (D-21). FSM 은 이 정지를 모른다."""
    proc, fake, col = cell
    fake.delay['move'] = 0.15                     # 요청 사이에 끼어들 틈을 만든다
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_until(lambda: proc.fsm and proc.fsm.mode == 'RUNNING')

    fake.nudge()
    assert _wait_mode(proc, 'PAUSED'), proc.fsm.mode
    assert 'NUDGE' in proc.note, proc.note
    step_at_pause = proc.fsm.state

    time.sleep(0.4)
    assert proc.fsm.state == step_at_pause, '정지 중에는 다음 요청으로 넘어가지 않는다'
    assert proc.fsm.mode == 'PAUSED'

    fake.delay.clear()
    fake.nudge()
    assert _wait_done(proc) == 'DONE', f'{proc.fsm.state} / {proc.note}'
    assert not proc.fsm.deviations, 'NUDGE 정지는 일탈이 아니다'


def test_nudge_does_not_cut_a_skill_in_flight(cell):
    """스킬 중간에는 끊지 않는다 — 블로킹 movel 은 취소가 안 되고(I-004) 그 구간에선 감지도 안 된다."""
    proc, fake, col = cell
    fake.delay['scoop'] = 0.6
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_until(lambda: any(c.startswith('scoop:') for c in fake.calls))

    fake.nudge()                                  # 퍼올리는 중에 건드린다
    assert _wait_mode(proc, 'PAUSED')
    # 스쿱은 중간에 끊기지 않고 끝났다 — 그래서 다음 계량까지 가 있다
    assert any(c.startswith('scoop:') for c in fake.calls)
    assert not any(c.startswith('safe:') for c in fake.calls), 'NUDGE 는 안전 자세로 보내지 않는다'

    fake.delay.clear()
    fake.nudge()
    assert _wait_done(proc) == 'DONE', proc.note


def test_two_nudges_inside_one_skill_cancel_out(cell):
    """스킬 도는 동안 정지·재개가 다 지나가면 아예 멈추지 않는다.

    게이트를 Event 로 기다렸다면 두 번째 NUDGE 가 세운 신호가 남아 **다음** 정지를 즉시 풀어 버린다.
    그래서 불린을 폴링한다 — 남는 신호가 없다.
    """
    proc, fake, col = cell
    fake.delay['scoop'] = 0.5
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_until(lambda: any(c.startswith('scoop:') for c in fake.calls))

    fake.nudge(); fake.nudge()                    # 정지했다 바로 재개
    assert _wait_done(proc) == 'DONE', proc.note
    assert not proc._nudge_paused
    assert not [e for e in col.events if e.code == 'PAUSE'], '스킬이 끝났을 때는 이미 풀려 있어야 한다'


def test_nudge_while_qa_pending_keeps_qa_open(cell):
    """판정 대기 중에는 mode 를 덮지 않는다 — 덮으면 _srv_qa 가 영영 거부한다 (인터락과 같은 함정)."""
    proc, fake, col = cell
    fake.transfer = 2.0
    _submit(col, [('A', 10.0, 5.0)])
    assert _wait_mode(proc, 'DEVIATION')
    dev = proc._pending_dev()

    fake.nudge()
    time.sleep(0.3)
    assert proc.fsm.mode == 'DEVIATION', 'NUDGE 가 QA 대기 상태를 덮으면 안 된다'
    assert _qa(col, dev.deviation_id, Deviation.APPROVED).accepted

    assert _wait_mode(proc, 'PAUSED'), f'판정 뒤에는 NUDGE 정지가 드러난다 — {_why(proc)}'
    fake.nudge()
    assert _wait_done(proc) == 'DONE', proc.note   # 일탈은 VERIFY ① 한 건 — 승인했으니 완주


def test_nudge_and_interlock_both_must_clear(cell):
    """NUDGE 로 멈춘 뒤 사람이 인터락으로 들어왔다 — 둘 다 풀려야 움직인다."""
    from gmp_interfaces.srv import InterlockRequest
    proc, fake, col = cell
    fake.delay['move'] = 0.15
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_until(lambda: proc.fsm and proc.fsm.mode == 'RUNNING')

    fake.nudge()
    assert _wait_mode(proc, 'PAUSED')
    # NUDGE 정지는 그 자리에 선 것뿐 — 안전 자세가 아니므로 ENTER 는 멱등 처리로 빠지면 안 된다
    r = _lock(col, InterlockRequest.Request.ENTER)
    assert r.granted and not r.message.startswith('이미'), r.message
    assert any(c.startswith('safe:') for c in fake.calls), 'ENTER 는 안전 자세로 보내야 한다'

    fake.nudge()                                   # NUDGE 만 풀었다
    time.sleep(0.3)
    assert proc.fsm.mode == 'PAUSED', '인터락이 남아 있으면 계속 멈춰 있어야 한다'

    fake.delay.clear()
    assert _lock(col, InterlockRequest.Request.EXIT).granted
    assert _wait_done(proc) == 'DONE', f'{proc.fsm.state} / {proc.note}'


def test_nudge_disabled_is_ignored(cell):
    """safety.nudge_enabled=false 면 무시한다 — skill_node 와 같은 스위치."""
    proc, fake, col = cell
    proc.set_parameters([Parameter('safety.nudge_enabled', value=False)])
    fake.delay['move'] = 0.1
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_until(lambda: proc.fsm and proc.fsm.mode == 'RUNNING')
    fake.nudge()
    assert _wait_done(proc) == 'DONE', proc.note
    assert not proc._nudge_paused


def test_exit_during_nudge_pause_is_ignored_and_does_not_leak(cell):
    """NUDGE 정지 중에 누른 EXIT 는 무시된다 — 신호가 남으면 다음 REFILL 대기가 보충 없이 풀린다 (넛지 리뷰 1번)."""
    from gmp_interfaces.srv import InterlockRequest
    proc, fake, col = cell
    fake.delay['move'] = 0.15
    fake.empty = 4                                 # 나중에 SCOOP_EMPTY ×4 → REFILL 대기
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_until(lambda: proc.fsm and proc.fsm.mode == 'RUNNING')
    fake.nudge()
    assert _wait_mode(proc, 'PAUSED')

    r = _lock(col, InterlockRequest.Request.EXIT, 'REFILL')        # ENTER 없이 EXIT 만 (실수)
    assert r.granted and r.message.startswith('대기 중이 아니다'), r.message
    assert not proc._interlock_exit.is_set(), 'EXIT 신호가 남았다'

    fake.nudge()                                   # 재개 → 원료 소진 → 보충 대기
    assert _wait_until(lambda: proc.fsm and any(d['action'] == 'REFILL' for d in proc.fsm.deviations))
    time.sleep(0.8)
    assert proc.fsm.mode == 'PAUSED' and proc._refill_waiting, _why(proc)
    assert _lock(col, InterlockRequest.Request.EXIT, 'REFILL').message == 'resume'
    assert _wait_done(proc) == 'DONE', _why(proc)


def test_safe_pose_bypasses_the_nudge_gate(cell):
    """NUDGE 로 멈춘 채 원료가 떨어지면 두 번째 nudge 없이 안전 자세로 물러난다 (넛지 리뷰 2번).

    안전 자세로 가는 이동은 정지보다 우선한다 — 사람이 보충하러 들어와야 하기 때문이다.
    NUDGE 정지 자체는 살아 있어서, 보충(EXIT) 뒤 다음 로봇 동작 앞에서 다시 잡힌다.
    """
    from gmp_interfaces.srv import InterlockRequest
    proc, fake, col = cell
    fake.empty = 4
    fake.delay['scoop'] = 0.5                      # 4번째(마지막) 빈 스쿱 도중에 건드릴 틈
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_until(lambda: sum(c.startswith('scoop:') for c in fake.calls) >= 4)
    fake.nudge()                                   # 4번째 스쿱이 도는 중 — 곧 MATERIAL_EMPTY → safe
    assert _wait_until(lambda: any(c.startswith('safe:') for c in fake.calls), 5.0), \
        f'nudge 정지 중에도 safe 는 나가야 한다 — {_why(proc)}'
    assert proc._nudge_paused, 'NUDGE 정지는 그대로 살아 있다'
    assert _wait_until(lambda: proc._refill_waiting, 5.0), _why(proc)

    assert _lock(col, InterlockRequest.Request.EXIT, 'REFILL').message == 'resume'   # 보충 완료
    time.sleep(0.5)
    assert proc.fsm.mode == 'PAUSED' and proc._nudge_paused, '보충 뒤에도 NUDGE 정지는 남아 있어야 한다'
    fake.delay.clear()
    fake.nudge()
    assert _wait_done(proc) == 'DONE', _why(proc)


def test_refill_wait_with_nudge_and_enter_needs_one_exit(cell):
    """REFILL 대기 + NUDGE + ENTER 가 겹쳐도 EXIT 는 한 번이면 된다 (넛지 리뷰 3번)."""
    from gmp_interfaces.srv import InterlockRequest
    proc, fake, col = cell
    fake.empty = 4
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_until(lambda: proc._refill_waiting), _why(proc)
    fake.nudge()                                   # 보충 대기 중에 건드렸다
    r = _lock(col, InterlockRequest.Request.ENTER, 'REFILL')
    assert r.granted and not r.message.startswith('이미'), r.message   # NUDGE 정지는 안전 자세가 아니므로 safe_pose
    assert _lock(col, InterlockRequest.Request.EXIT, 'REFILL').message == 'resume'
    time.sleep(0.5)
    assert not proc._pause and not proc._refill_waiting, _why(proc)      # EXIT 한 번으로 둘 다 풀렸다
    assert proc._nudge_paused and proc.fsm.mode == 'PAUSED'              # NUDGE 만 남았다
    fake.nudge()
    assert _wait_done(proc) == 'DONE', _why(proc)


def _lock(col, request, reason='TEST'):
    from gmp_interfaces.srv import InterlockRequest
    cli = col.create_client(InterlockRequest, 'interlock')
    assert cli.wait_for_service(timeout_sec=5.0)
    fut = cli.call_async(InterlockRequest.Request(request=request, reason=reason))
    t0 = time.time()
    while not fut.done() and time.time() - t0 < 10.0:
        time.sleep(0.02)
    assert fut.done()
    return fut.result()


def test_set_end_waits_at_nudge_wait_until_nudged(cell):
    """D-23: passbox_done 반송 → nudge_wait 이동 → NUDGE 대기. 그동안 주문은 거부, 건드리면 DONE 이고 다음 주문을 받는다."""
    proc, fake, col = cell
    fake.attendant = False                         # 아무도 안 건드린다
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_until(lambda: proc.fsm and proc.fsm.state == 'NUDGE_WAIT' and proc._nudge_waiting, 60.0), _why(proc)
    assert fake.station == 'nudge_wait' and proc.fsm.mode == 'PAUSED'
    moves = [c for c in fake.calls if c.startswith('move:')]
    assert any('passbox_done' in c for c in moves) and moves[-1].startswith('move:nudge_wait'), moves[-4:]
    assert 'NUDGE_WAIT' in proc.note
    r = _submit(col, [('A', 100.0, 5.0)])
    assert not r.accepted and 'NUDGE_WAIT' in r.message, r.message

    time.sleep(0.5)
    assert proc.fsm.state == 'NUDGE_WAIT', '건드리기 전에는 끝나지 않는다'
    fake.nudge()
    assert _wait_done(proc) == 'DONE' and proc.fsm.state == 'DONE', _why(proc)
    assert not proc._nudge_paused, '세트 끝의 NUDGE 는 정지 토글이 아니다'
    fake.attendant = True
    with fake.lock:                                # 가짜 셀을 다음 배치 상태로 — 첫 배치의 스쿱 잔량·용기 내용물이 남으면 계량이 어긋난다
        fake.in_cup, fake.held = 0.0, None
        fake.content.clear()
    r = _submit(col, [('A', 100.0, 5.0)])
    assert r.accepted, r.message
    assert _wait_done(proc) == 'DONE', f'{_why(proc)} | waiting={proc._nudge_waiting} paused={proc._nudge_paused} calls={fake.calls[-5:]}'
    assert any(e.code == 'SET_DONE' for e in col.events) and any(e.code == 'SET_NEXT' for e in col.events)


def test_discarded_batch_also_parks_at_nudge_wait(cell):
    """폐기도 세트의 끝 — reject_bin 뒤 nudge_wait 에서 기다리고, NUDGE 뒤 상태는 DISCARDED 로 남는다 (record_node 가 본다)."""
    proc, fake, col = cell
    fake.attendant = False
    fake.transfer = 2.0                            # 약통에 2배 → VERIFY ① BATCH_OUT_OF_SPEC → QA
    _submit(col, [('A', 10.0, 5.0)])
    assert _wait_mode(proc, 'DEVIATION'), _why(proc)
    dev = proc._pending_dev()
    assert _qa(col, dev.deviation_id, Deviation.DISCARDED).accepted
    assert _wait_until(lambda: proc._nudge_waiting, 30.0), _why(proc)
    assert fake.station == 'nudge_wait' and proc.fsm.state == 'NUDGE_WAIT'
    fake.nudge()
    assert _wait_done(proc) == 'DONE' and proc.fsm.state == 'DISCARDED', _why(proc)


def test_enter_during_nudge_wait_goes_to_safe_pose(cell):
    """NUDGE_WAIT 는 PAUSED 지만 안전 자세가 아니다 — ENTER 는 safe_pose 를 실제로 불러야 하고, EXIT 뒤 NUDGE 로 끝난다."""
    proc, fake, col = cell
    fake.attendant = False
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_until(lambda: proc._nudge_waiting, 60.0), _why(proc)
    from gmp_interfaces.srv import InterlockRequest
    n_safe = sum(c.startswith('safe:') for c in fake.calls)
    r = _lock(col, InterlockRequest.Request.ENTER, 'CHECK')
    assert r.granted and '이미 대기 중' not in r.message, r.message
    assert sum(c.startswith('safe:') for c in fake.calls) == n_safe + 1, 'safe_pose 를 불러야 한다'
    assert _lock(col, InterlockRequest.Request.EXIT).granted
    fake.nudge()
    assert _wait_done(proc) == 'DONE', _why(proc)


# ── 종료(Ctrl+C) 가 사람 대기를 끊을 때 (9/21) ─────────────────────────────
# 종료 전(9/21 이전 main) 에는 `shutdown()` 이 `_qa`·`_interlock_exit` 를 대신 세워 "판정이 온 것처럼"
# 깨웠다 — QA 미판정이 DISCARDED 로, 인터락 대기가 EXIT 받은 것처럼 지어져 나갔다. NUDGE_WAIT 는
# 반대로 아무도 안 깨워 `_stop` 폴링에 걸려 FORCE_LIMIT 일탈을 지어냈다. #163(RunBatch) 이 도입한
# `_check_batch_interrupt()`/`BatchCancelled` 가 세 경로 모두를 이미 정확히 잡아 ABORTED/ERROR 로
# 끝낸다는 것을 이 3건이 고정한다 — 회귀가 생기면 여기서 먼저 깨진다.
def test_shutdown_during_qa_wait_ends_the_batch_via_cancellation(cell):
    """QA 판정 대기 중 종료는 '거부(DISCARDED)'를 지어내지 않는다 — BatchCancelled 로 명확히 취소된다."""
    proc, fake, col = cell
    fake.transfer = 2.0                            # 약통에 2배 → VERIFY ① BATCH_OUT_OF_SPEC → QA
    _submit(col, [('A', 10.0, 5.0)])
    assert _wait_mode(proc, 'DEVIATION'), _why(proc)
    n_devs = len(proc.fsm.deviations)
    proc.shutdown(timeout=3.0)
    assert not proc._thread.is_alive(), 'QA 대기를 못 깨웠다'
    assert proc.fsm.state == 'ABORTED' and proc.fsm.mode == 'ERROR', _why(proc)
    assert len(proc.fsm.deviations) == n_devs, 'QA 미판정을 새 일탈로 지어내면 안 된다'


def test_shutdown_at_nudge_wait_ends_the_batch_without_a_fabricated_deviation(cell):
    """세트 끝 NUDGE 대기(제일 흔한 정상 종료 상황) 중 종료는 FORCE_LIMIT 일탈을 지어내면 안 된다."""
    proc, fake, col = cell
    fake.attendant = False
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_until(lambda: proc._nudge_waiting, 60.0), _why(proc)
    proc.shutdown(timeout=3.0)
    assert not proc._thread.is_alive(), 'NUDGE 대기를 못 깨웠다'
    assert proc.fsm.deviations == [], f'종료를 일탈로 지어냈다: {proc.fsm.deviations}'
    assert proc.fsm.state == 'ABORTED' and proc.fsm.mode == 'ERROR', _why(proc)


def test_shutdown_during_refill_wait_ends_the_batch_instead_of_faking_a_resume(cell):
    """보충 대기(PAUSED) 중 종료는 EXIT 가 온 것처럼 재개 상태로 지어내면 안 된다."""
    proc, fake, col = cell
    fake.empty = 4                                 # SCOOP_EMPTY ×3 재시도 → 4회째 REFILL
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_mode(proc, 'PAUSED'), proc.fsm.state
    proc.shutdown(timeout=3.0)
    assert not proc._thread.is_alive(), '인터락 대기를 못 깨웠다'
    assert proc.fsm.state == 'ABORTED' and proc.fsm.mode == 'ERROR', 'EXIT 가 온 것처럼 재개시키면 안 된다'


def _call(col, srv_type, name, request, timeout=10.0):
    cli = col.create_client(srv_type, name)
    assert cli.wait_for_service(timeout_sec=5.0), f'{name} 서버가 없다'
    fut = cli.call_async(request)
    t0 = time.time()
    while not fut.done() and time.time() - t0 < timeout:
        time.sleep(0.02)
    assert fut.done(), f'{name} 응답 없음'
    return fut.result()


def _recover(col, **kwargs):
    from gmp_interfaces.srv import RecoverSafety
    args = {'request_id': 'r1', 'operator_id': 'op', 'expected_state': 5, 'operator_confirmed': True}
    args.update(kwargs)
    return _call(col, RecoverSafety, 'request_safety_recovery', RecoverSafety.Request(**args))


def test_safety_stop_blocks_new_submission(cell):
    """로봇 안전 정지 중에는 배치를 시작하지 않는다 (docs/interfaces.md 8절)."""
    proc, fake, col = cell
    fake.safety_stop('joint limit', robot_state=5)
    assert _wait_until(lambda: proc._safety_stop), '이벤트를 못 받았다'
    r = _submit(col, [('A', 100.0, 5.0)])
    assert not r.accepted and 'joint limit' in r.message, r.message
    assert proc.fsm is None, '배치가 시작되지 않아야 한다'


def test_safety_stop_skips_force_limit_retry(cell):
    """안전 정지 중 스킬 실패는 FORCE_LIMIT 재시도 없이 바로 ERROR (재시도해도 skill_node 가 다시 거부할 뿐이다).

    일반 실패(test_skill_failure_becomes_force_limit_then_error)는 같은 요청을 한 번 더 부르고
    FORCE_LIMIT 일탈 2건을 남긴다 — 안전 정지는 재시도도, 일탈 기록도 남기지 않고 바로 끝낸다.
    """
    proc, fake, col = cell
    fake.delay['move'] = 0.2            # 안전 정지 이벤트가 들어갈 틈을 만든다
    fake.fail['move'] = 99
    _submit(col, [('A', 100.0, 5.0)])
    assert _wait_until(lambda: fake.calls), '첫 move 가 시작되지 않았다'
    fake.safety_stop('external torque')
    assert _wait_until(lambda: proc._safety_stop), '이벤트를 못 받았다'
    assert _wait_done(proc) == 'ERROR', _why(proc)
    assert not proc.fsm.deviations, proc.fsm.deviations


def test_safety_stop_during_qa_wait_ends_batch(cell):
    """QA 판정을 기다리는 중에 안전 정지가 오면 판정을 기다리지 않고 끝낸다."""
    proc, fake, col = cell
    fake.transfer = 2.0                              # 과투입 → OVERFILL → QA 대기
    _submit(col, [('A', 10.0, 5.0)])
    assert _wait_mode(proc, 'DEVIATION'), _why(proc)
    fake.safety_stop('collision while paused')
    assert _wait_done(proc) == 'ERROR', _why(proc)


def test_safety_recovery_success_unblocks_submission_not_resume(cell):
    """복구 성공(수동 조치 불필요)은 새 주문만 받는다 — 끝난 배치를 되살리지 않는다."""
    proc, fake, col = cell
    fake.safety_stop('collision')
    assert _wait_until(lambda: proc._safety_stop), '이벤트를 못 받았다'
    fake.safety_recovery(success=True, manual_required=False, robot_state=1, message='복구 확인')
    assert _wait_until(lambda: not proc._safety_stop), '차단이 안 풀렸다'
    r = _submit(col, [('A', 100.0, 5.0)])
    assert r.accepted, r.message                                               # 새 주문은 받는다
    assert _wait_until(lambda: proc.fsm and proc.fsm.mode == 'RUNNING'), _why(proc)   # 배치가 실제로 돈다


def test_safety_recovery_manual_required_keeps_block(cell):
    """복구 모드 진입만 한 결과(manual_required)는 차단을 유지한다 — 새 요청이 또 필요하다."""
    proc, fake, col = cell
    fake.safety_stop('joint limit')
    assert _wait_until(lambda: proc._safety_stop), '이벤트를 못 받았다'
    fake.safety_recovery(success=False, manual_required=True, robot_state=8, message='복구 모드 진입')
    time.sleep(0.3)
    assert proc._safety_stop, '수동 조치가 필요하면 차단을 유지해야 한다'


def test_request_safety_recovery_relays_to_skill(cell):
    """HMI → 여기 → skill_node/recover_safety 중계 (docs/interfaces.md 8절)."""
    proc, fake, col = cell
    fake.recover_result = (True, False, 1, '로봇 복구 확인')
    r = _recover(col, request_id='r7')
    assert r.success and not r.manual_required and r.robot_state == 1
    assert any(c == 'recover:r7' for c in fake.calls)


def test_request_safety_recovery_rejects_missing_fields_without_calling_skill(cell):
    """작업자 확인 없는 요청은 skill_node 를 부르지도 않고 거부한다."""
    proc, fake, col = cell
    r = _recover(col, request_id='', operator_confirmed=True)
    assert not r.success and r.manual_required
    assert not any(c.startswith('recover:') for c in fake.calls)


def test_late_recovery_event_cannot_clear_new_safety_stop(cell):
    import json
    proc, fake, col = cell
    fake.safety_stop('start', origin='recovery_request', request_id='old', operator_id='op')
    assert _wait_until(lambda: proc._safety_events.request == ('old', 'op'))
    old_revision = fake.safety_revision
    fake.safety_stop('new alarm')
    assert _wait_until(lambda: proc._safety_stop_reason == 'new alarm')
    msg = CellEvent(code='ROBOT_SAFETY_RECOVERY', text=json.dumps(dict(
        safety_session='fake-skill-session', safety_revision=old_revision,
        request_id='old', operator_id='op', success=True, manual_required=False, robot_state=1)))
    fake.pub_event.publish(msg)
    assert _wait_until(lambda: any(e.code == msg.code and e.text == msg.text for e in col.events))
    assert proc._safety_stop
    assert not _submit(col, [('A', 100.0, 5.0)]).accepted
