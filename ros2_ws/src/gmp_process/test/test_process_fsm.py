"""process_fsm 전이 테스트 — 스쿱·용기 안의 양을 추적하는 물리 오라클로 돌린다 (D-22 6단계 흐름).

Cell 오라클: 스쿱 풍량 20 g, 용기 풍량 30 g. scoop 마다 yields 에서 퍼올림량을 꺼내고, pour 는 fraction 만큼 옮기되
residual 만큼 스쿱에 남긴다. weigh_scoop 은 스쿱 총량, weigh 는 용기 총량·순량을 돌려준다.
"""
from gmp_dosing.core.dosing import DosingConfig
from gmp_dosing.core.scale import ScaleConfig, WeightModel
from gmp_process.core.process_fsm import ProcessFSM
from gmp_process.core.recipe import parse

SCOOP_TARE, CUP_TARE = 20.0, 30.0


def _fsm(min_resolvable_g=30.0):
    spec = parse({'product': 't', 'items': [{'material_id': 'A', 'target_g': 100, 'tol_pct': 5},
                                              {'material_id': 'B', 'target_g': 50, 'tol_pct': 5}]})
    return ProcessFSM(spec, DosingConfig(scoop_nominal_g=40), WeightModel(ScaleConfig(min_resolvable_g=min_resolvable_g)))


class Cell:
    def __init__(self, yields, residual=2.0, grip=None, qa='APPROVED', spill=False, cup_bias=0.0, invalid_first=0):
        self.yields, self.residual, self.qa, self.spill, self.cup_bias = list(yields), residual, qa, spill, cup_bias
        self.grip = grip or (lambda req, n: True)
        self.in_scoop = self.in_cup = 0.0
        self.n = {'grip': 0, 'carry': 0, 'scoop': 0, 'weigh_scoop': 0}
        self.invalid_left = invalid_first

    def __call__(self, req):
        k = req['kind']
        if k in ('grip', 'carry'):
            self.n[k] += 1
            ok = self.grip(req, self.n[k])
            if k == 'grip' and not req.get('close', True):
                self.in_scoop = 0.0                       # 스쿱 반납 → 잔량은 스쿱과 함께 랙으로
            return {'grip_inferred': ok}
        if k == 'scoop':
            self.n['scoop'] += 1
            amt = self.yields.pop(0) if self.yields else 0.0
            self.in_scoop += amt
            return {'contact_detected': amt > 0}
        if k == 'pour':
            f = 1.0 if self.spill else req['fraction']
            moved = max(0.0, self.in_scoop * f - self.residual) if f >= 1.0 else self.in_scoop * f
            self.in_cup += moved
            self.in_scoop -= moved
            return {}
        if k == 'weigh_scoop':
            self.n['weigh_scoop'] += 1
            if self.invalid_left > 0:
                self.invalid_left -= 1
                return {'gross_g': 0.0, 'valid': False}
            return {'gross_g': SCOOP_TARE + self.in_scoop, 'valid': True}
        if k == 'weigh':
            gross = CUP_TARE + self.in_cup + (self.cup_bias if self.in_cup > 0 else 0.0)   # bias 는 TARE 뒤에만
            return {'gross_g': gross, 'net_g': gross - req['tare_g'], 'valid': True}
        if k == 'wait_qa':
            return {'decision': self.qa}
        return {}


def run(fsm, oracle, max_steps=300):
    """oracle(req) -> 결과 dict. 전이를 끝까지 돌린다."""
    req, trace = fsm.start(), []
    while req and len(trace) < max_steps:
        trace.append((fsm.state, req['kind']))
        req = fsm.on_result(req, oracle(req))
    return trace


def kinds_for(trace, state):
    return [k for s, k in trace if s == state]


def test_happy_path_six_steps():
    cell = Cell(yields=[100, 50])
    fsm = _fsm()
    trace = run(fsm, cell)
    assert fsm.state == 'DONE' and len(fsm.results) == 2 and not fsm.deviations
    a, b = fsm.results
    assert a.scoop_tare_g == SCOOP_TARE and a.scooped_g == 100 and a.residual_g == 2 and a.actual_g == 98
    assert b.actual_g == 48 and fsm.verify_net_g == 146                     # 용기 = 스쿱 누적 (2차 검증)
    # 원료 1종의 순서: 스쿱 풍량 → 퍼올림 → 붓기 전 계량 → 붓기 → 붓기 후 계량 → 반납
    seq = [s for s, k in trace]
    i = seq.index('PICK_SCOOP')
    assert seq[i + 2:i + 8] == ['SCOOP_TARE', 'SCOOP', 'WEIGH_SCOOP', 'POUR', 'WEIGH_RESIDUAL', 'RETURN_SCOOP']
    assert trace[1] == ('PICK_CONTAINER', 'carry') and ('VERIFY', 'weigh') in trace and trace[-1] == ('FINISH', 'carry')


def test_prepour_check_prevents_overfill():
    """퍼낸 양(130) 이 목표(100) 보다 많으면 붓기 전 계량이 fraction 을 줄여 초과를 막는다 — 1차 폐루프."""
    cell = Cell(yields=[130, 50], residual=0.0)
    fsm = _fsm()
    fractions = []
    orig = cell.__call__
    def spy(req):
        if req['kind'] == 'pour':
            fractions.append(req['fraction'])
        return orig(req)
    run(fsm, spy)
    assert fsm.state == 'DONE' and not fsm.deviations
    assert abs(fractions[0] - 100 / 130) < 1e-6 and abs(fsm.results[0].actual_g - 100) < 1e-6


def test_under_then_correction_accumulates():
    cell = Cell(yields=[60, 40, 50])                    # A: 60 퍼서 58 투입(잔량 2) → UNDER → 40 더 퍼서 스쿱 42 → 40 투입 = 98 OK
    fsm = _fsm()
    trace = run(fsm, cell)
    a = fsm.results[0]
    # 첫 붓기의 잔량 2 g 이 두 번째 스쿱에 섞여 들어가도, 붓기 전후 계량 차이로 실제 투입량만 누적된다
    assert a.attempts == 2 and abs(a.actual_g - 98) < 1e-6 and a.scooped_g == 42 and fsm.state == 'DONE'
    assert kinds_for(trace, 'SCOOP').count('scoop') == 3 and not fsm.deviations


def test_overfill_goes_to_qa_and_discard_returns_scoop_first():
    cell = Cell(yields=[130, 50], spill=True, qa='DISCARDED')   # 붓기가 fraction 을 무시 → 128 g 투입 → OVER
    fsm = _fsm()
    trace = run(fsm, cell)
    assert fsm.deviations[0]['kind'] == 'OVERFILL' and fsm.deviations[0]['step'] == 'WEIGH_RESIDUAL'
    assert fsm.state == 'DISCARDED'
    # 스쿱을 든 채 일탈 → 스쿱 반납(move, grip open) 후 용기째 폐기함
    assert [k for s, k in trace if s == 'DISCARDED'] == ['move', 'grip', 'carry']


def test_verify_mismatch_goes_to_qa_then_finish():
    cell = Cell(yields=[100, 50], cup_bias=50.0)        # 용기에 50 g 이 더 있다 (스쿱 누적과 불일치)
    fsm = _fsm(min_resolvable_g=30.0)
    trace = run(fsm, cell)
    assert fsm.deviations == [{'kind': 'VERIFY_MISMATCH', 'step': 'VERIFY', 'count': 1, 'action': 'QA', 'material_id': 'B'}]
    assert fsm.state == 'DONE' and trace[-1] == ('FINISH', 'carry')     # QA 승인 → 그대로 완료품


def test_invalid_scoop_weigh_retries():
    cell = Cell(yields=[100, 50], invalid_first=1)       # 첫 weigh_scoop 만 무효
    fsm = _fsm()
    trace = run(fsm, cell)
    assert kinds_for(trace, 'SCOOP_TARE') == ['weigh_scoop'] * 3 and fsm.state == 'DONE'   # A: 무효+재계량, B: 1회
    assert not fsm.deviations and fsm.results[0].invalid == 1


def test_grip_fail_retries_then_forced():
    cell = Cell(yields=[], grip=lambda req, n: False)
    fsm = _fsm()
    run(fsm, cell)
    assert [d['kind'] for d in fsm.deviations] == ['GRIP_FAIL'] * 4 and fsm.state == 'ERROR'


def test_container_grip_fail_retries_at_pick_container():
    cell = Cell(yields=[100, 50], grip=lambda req, n: not (req['kind'] == 'carry' and n == 1))
    fsm = _fsm()
    trace = run(fsm, cell)
    assert fsm.deviations == [{'kind': 'GRIP_FAIL', 'step': 'PICK_CONTAINER', 'count': 1, 'action': 'RETRY',
                               'material_id': None}]
    assert trace[:3] == [('SELF_CHECK', 'measure'), ('PICK_CONTAINER', 'carry'), ('PICK_CONTAINER', 'carry')]
    assert fsm.tare_g == CUP_TARE and fsm.state == 'DONE' and len(fsm.results) == 2


def test_material_empty_refill_resumes_scoop():
    cell = Cell(yields=[0, 0, 0, 0, 100, 50])           # 4번 빈 스쿱 → REFILL → 보충 후 재개
    fsm = _fsm()
    trace = run(fsm, cell)
    kinds = [d['kind'] for d in fsm.deviations]
    assert kinds == ['SCOOP_EMPTY'] * 4 and fsm.deviations[-1]['action'] == 'REFILL'
    assert ('PAUSED', 'wait_interlock') in trace and fsm.state == 'DONE' and len(fsm.results) == 2
