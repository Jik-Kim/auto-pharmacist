"""process_fsm 전이 테스트 — 스쿱·용기 안의 양을 추적하는 물리 오라클로 돌린다 (D-22 6단계 흐름).

Cell 오라클: 스쿱 풍량 20 g, 용기 풍량 30 g. scoop 마다 yields 에서 퍼올림량을 꺼내고, pour 는 fraction 만큼 옮기되
residual 만큼 스쿱에 남긴다. weigh_scoop 은 스쿱 총량, weigh 는 용기 총량·순량을 돌려준다.
"""
import pytest

from gmp_dosing.core.dosing import DosingConfig, decide
from gmp_dosing.core.scale import ScaleConfig, WeightModel
from gmp_process.core.process_fsm import ProcessFSM, ToolFingerprint
from gmp_process.core.recipe import parse

SCOOP_TARE, CUP_TARE = 20.0, 30.0


def _fsm(fingerprint=None):
    spec = parse({'product': 't', 'items': [{'material_id': 'A', 'target_g': 100, 'tol_pct': 5},
                                              {'material_id': 'B', 'target_g': 50, 'tol_pct': 5}]})
    return ProcessFSM(spec, DosingConfig(scoop_nominal_g=40), WeightModel(ScaleConfig()),
                      fingerprint=fingerprint or ToolFingerprint())


class Cell:
    def __init__(self, yields, residual=2.0, grip=None, qa='APPROVED', spill=False, cup_bias=0.0, invalid_first=0,
                width_mm=None, cup_invalid_first=0, zero_drift_n=0.0):
        self.yields, self.residual, self.qa, self.spill, self.cup_bias = list(yields), residual, qa, spill, cup_bias
        self.grip = grip or (lambda req, n: True)
        self.width_mm = width_mm                          # 폭 지문 테스트용 — 정지 폭을 고정값으로 돌려준다
        self.in_scoop = self.in_cup = 0.0
        self.n = {'grip': 0, 'carry': 0, 'scoop': 0, 'weigh_scoop': 0, 'return_material': 0}
        self.invalid_left = invalid_first
        self.cup_invalid_left = cup_invalid_first   # 용기 계량(TARE·VERIFY) 무효 횟수
        self.zero_drift_n = zero_drift_n            # VERIFY 직전 영점이 이만큼 움직인 것으로 답한다
        self.n_measure = 0

    def __call__(self, req):
        k = req['kind']
        if k == 'measure':
            self.n_measure += 1
            # 1회차 SELF_CHECK(자가진단), 2회차 TARE 직전(영점 기준), 3회차부터 VERIFY 직전 재확인.
            # 기준과 대조는 둘 다 workbench ABOVE 라 자세가 같다 — 그래서 차이가 곧 영점 이동이다.
            return {'fz_mean_n': 0.0 if self.n_measure <= 2 else self.zero_drift_n, 'valid': True}
        if k in ('grip', 'carry'):
            self.n[k] += 1
            ok = self.grip(req, self.n[k])
            if k == 'grip' and not req.get('close', True):
                self.in_scoop = 0.0                       # 스쿱 반납 → 잔량은 스쿱과 함께 랙으로
            res = {'grip_inferred': ok}
            if self.width_mm is not None:
                res['final_width_mm'] = self.width_mm
            return res
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
        if k == 'return_material':
            self.n[k] += 1
            self.in_scoop = 0.0
            return {'success': True}
        if k == 'weigh_scoop':
            self.n['weigh_scoop'] += 1
            if self.invalid_left > 0:
                self.invalid_left -= 1
                return {'gross_g': 0.0, 'valid': False}
            return {'gross_g': SCOOP_TARE + self.in_scoop, 'valid': True}
        if k == 'weigh':
            if self.cup_invalid_left > 0:
                self.cup_invalid_left -= 1
                return {'gross_g': 0.0, 'net_g': 0.0, 'valid': False}
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
    assert trace[1] == ('PICK_CONTAINER', 'carry') and ('VERIFY', 'weigh') in trace
    # 세트 끝: passbox_done 반송 → nudge_wait 이동 → NUDGE 대기 → DONE (D-23·D-24)
    assert trace[-3:] == [('FINISH', 'carry'), ('NUDGE_WAIT', 'move'), ('NUDGE_WAIT', 'wait_nudge')]


def test_oversize_scoop_returns_to_material_before_rescoop():
    """퍼낸 양(130)이 목표+허용오차(105)를 넘으면 약통에 붓지 않고 반환한다."""
    cell = Cell(yields=[130, 100, 50], residual=0.0)
    fsm = _fsm()
    fractions = []
    orig = cell.__call__
    def spy(req):
        if req['kind'] == 'pour':
            fractions.append(req['fraction'])
        return orig(req)
    trace = run(fsm, spy)
    assert fsm.state == 'DONE' and not fsm.deviations
    assert [k for s, k in trace if s == 'RETURN_MATERIAL'] == ['return_material']
    assert fractions and all(f == 1.0 for f in fractions)
    # 반환은 약통에 아무것도 넣지 않았으므로 붓기 시도(attempts)가 아니다. 반환 횟수는 따로 센다.
    assert fsm.results[0].attempts == 1 and fsm.results[0].returns == 1
    assert abs(fsm.results[0].actual_g - 100) < 1e-6


def test_under_then_correction_accumulates():
    cell = Cell(yields=[60, 40, 50])                    # A: 60 퍼서 58 투입(잔량 2) → UNDER → 40 더 퍼서 스쿱 42 → 40 투입 = 98 OK
    fsm = _fsm()
    trace = run(fsm, cell)
    a = fsm.results[0]
    # 첫 붓기의 잔량 2 g 이 두 번째 스쿱에 섞여 들어가도, 붓기 전후 계량 차이로 실제 투입량만 누적된다
    assert a.attempts == 2 and abs(a.actual_g - 98) < 1e-6 and a.scooped_g == 42 and fsm.state == 'DONE'
    assert kinds_for(trace, 'SCOOP').count('scoop') == 3 and not fsm.deviations


def test_repeated_oversize_returns_then_times_out_without_pour():
    """반환을 세 번 성공해도 적정 스쿱이 안 나오면 재투입하지 않고 QA로 멈춘다."""
    cell = Cell(yields=[130, 130, 130], qa='DISCARDED')
    fsm = _fsm()
    trace = run(fsm, cell)
    assert fsm.deviations[0]['kind'] == 'TIMEOUT' and fsm.deviations[0]['step'] == 'RETURN_MATERIAL'
    assert fsm.state == 'DISCARDED'
    assert not any(k == 'pour' for _, k in trace)
    assert [k for s, k in trace if s == 'RETURN_MATERIAL'] == ['return_material'] * 3
    # 스쿱을 든 채 일탈 → 스쿱 반납(move, grip open) 후 용기째 폐기함 → 폐기도 세트의 끝이라 nudge_wait 에서 대기
    assert [k for s, k in trace if s == 'DISCARDED'] == ['move', 'grip', 'carry']
    assert trace[-2:] == [('NUDGE_WAIT', 'move'), ('NUDGE_WAIT', 'wait_nudge')]


def test_return_failure_goes_safe_without_retry_or_repour():
    """반환 실패 뒤에는 held material 이력을 믿을 수 없으므로 즉시 ERROR 안전 경로다."""
    cell = Cell(yields=[130])
    fsm = _fsm()
    req, trace = fsm.start(), []
    while req['kind'] != 'return_material':
        trace.append((fsm.state, req['kind']))
        req = fsm.on_result(req, cell(req))

    safe = fsm.on_result(req, {'success': False, 'message': '반환 자세 미티칭'})
    assert safe == {'kind': 'safe', 'then': None, 'reason': 'RECOVERY'}
    assert fsm.state == 'ERROR' and fsm.mode == 'ERROR'
    assert fsm.deviations[-1]['kind'] == 'FORCE_LIMIT'
    assert fsm.deviations[-1]['step'] == 'RETURN_MATERIAL'
    assert fsm.deviations[-1]['action'] == 'FORCED'
    assert not any(k == 'pour' for _, k in trace)


def test_rescoop_after_return_fails_once_without_retry():
    """실물 skill_node 는 반환 중 세운 플래그로 이후 Scoop 을 전부 거부한다 (v1.5.1 · PR #43).
    플래그가 풀리지 않으니 재시도는 같은 이유로 또 거부된다 — FORCE_LIMIT 2건을 쌓지 말고
    사유를 남기고 한 번에 끝내야 한다 (연결 경로는 A 몫, #64)."""
    cell = Cell(yields=[130])
    fsm = _fsm()
    req = fsm.start()
    while not (req['kind'] == 'scoop' and req.get('after_return')):
        req = fsm.on_result(req, cell(req))

    safe = fsm.skill_failed(req, 'scoop 실패: 반환 후 재스쿱 연결 경로 미구현: 자동 Scoop을 차단합니다')
    assert safe == {'kind': 'safe', 'then': None, 'reason': 'RECOVERY'}
    assert fsm.state == 'ERROR' and fsm.mode == 'ERROR'
    assert len(fsm.deviations) == 1, fsm.deviations        # 재시도분(2건째)이 없다
    d = fsm.deviations[-1]
    assert (d['kind'], d['step'], d['action']) == ('FORCE_LIMIT', 'SCOOP', 'FORCED')
    assert d['detail'].startswith('반환 후 재스쿱 차단 — '), d['detail']
    assert '연결 경로 미구현' in d['detail']                # 스킬이 준 진짜 사유도 남는다


def test_normal_scoop_failure_still_retries_once():
    """반환과 무관한 스쿱 실패는 기존대로 1회 재시도한다 — 위 분기가 일반 경로를 삼키면 안 된다."""
    cell = Cell(yields=[100])
    fsm = _fsm()
    req = fsm.start()
    while req['kind'] != 'scoop':
        req = fsm.on_result(req, cell(req))
    assert not req.get('after_return')

    retry = fsm.skill_failed(req, '담그기 중 힘 상한')
    assert retry == req and fsm.state == 'SCOOP'           # 같은 요청을 한 번 더
    assert fsm.deviations[-1]['action'] == 'RETRY'


class _DepthCell(Cell):
    """요청한 깊이(fraction)만큼 퍼올리는 셀 — 실물처럼 마지막 스쿱이 부분 스쿱이 된다."""
    NOMINAL_G = 40.0

    def __call__(self, req):
        if req['kind'] == 'scoop':
            self.n['scoop'] += 1
            self.in_scoop += self.NOMINAL_G * req.get('fraction', 1.0)
            return {'contact_detected': True}
        return super().__call__(req)


def _demo_spec():
    return parse({'product': 'demo', 'items': [
        {'material_id': 'A', 'target_g': 200.0, 'tol_pct': 5.0},
        {'material_id': 'B', 'target_g': 150.0, 'tol_pct': 5.0},
        {'material_id': 'C', 'target_g': 100.0, 'tol_pct': 5.0}]})


def test_데모_레시피가_일탈_없이_완주한다():
    """#189 회귀 — `max_attempts` 는 ceil(목표량 ÷ 스쿱 1회량) 이상이어야 한다.

    3 이면 한 원료 상한이 3×40 = 120 g 이라 A(200)·B(150)이 TIMEOUT 으로 못 끝낸다.
    이 시험이 깨지면 `common.yaml` 의 `dosing.max_attempts` 나 `scoop_nominal_g` 가
    레시피와 어긋난 것이다.
    """
    fsm = ProcessFSM(_demo_spec(), DosingConfig(max_attempts=8, scoop_nominal_g=40.0),
                     WeightModel(ScaleConfig()), max_returns=3)
    run(fsm, _DepthCell(yields=[], residual=0.0))
    assert fsm.deviations == [], fsm.deviations
    assert fsm.state == 'DONE'
    assert [round(r.actual_g) for r in fsm.results] == [200, 150, 100]
    assert [r.attempts for r in fsm.results] == [5, 4, 3]     # ceil(목표 ÷ 40)


def test_붓기_상한이_모자라면_TIMEOUT_으로_못_끝낸다():
    """#189 가 있던 상태를 고정한다 — 값이 다시 내려가면 이 시험이 알려 준다."""
    fsm = ProcessFSM(_demo_spec(), DosingConfig(max_attempts=3, scoop_nominal_g=40.0),
                     WeightModel(ScaleConfig()), max_returns=3)
    run(fsm, _DepthCell(yields=[], residual=0.0))
    assert [d['kind'] for d in fsm.deviations].count('TIMEOUT') == 2      # A·B
    assert [round(r.actual_g) for r in fsm.results][:2] == [120, 120]     # 3 × 40 이 상한


def test_반환_상한은_붓기_상한과_분리돼_있다():
    """#189 — 한 상수로 묶여 있으면 큰 레시피 때문에 붓기 상한을 올릴 때 반환 허용도 같이 오른다."""
    fsm = ProcessFSM(_demo_spec(), DosingConfig(max_attempts=8), WeightModel(ScaleConfig()),
                     max_returns=3)
    assert fsm.max_returns == 3 and fsm.dosing_cfg.max_attempts == 8


class _DepthCell(Cell):
    """요청한 깊이(fraction)만큼 퍼올리는 셀 — 첫 깊이의 효과를 본다."""
    def __init__(self, *a, nominal_g=40.0, **kw):
        super().__init__(*a, **kw)
        self.nominal_g, self.fractions = nominal_g, []

    def __call__(self, req):
        if req['kind'] == 'scoop':
            self.n['scoop'] += 1
            f = req.get('fraction', 1.0)
            self.fractions.append(round(f, 3))
            self.in_scoop += self.nominal_g * f
            return {'contact_detected': True}
        return super().__call__(req)


def _one_item(target_g, tol_pct=5.0):
    return parse({'product': 'x', 'items': [{'material_id': 'A', 'target_g': target_g, 'tol_pct': tol_pct}]})


def test_221_첫_담그기_깊이가_목표량을_반영한다():
    """#221 — 첫 SCOOP 이 1.0 고정이라 작은 목표에서 곧장 초과 반환이 났다.

    목표 30 g 에 1회량 40 g 을 그대로 푸면 남은 목표 + 허용오차(1.5)를 넘어 `RETURN_MATERIAL`
    로 되돌린다. 깊이를 목표에 맞추면 그 낭비가 사라진다. #216 이 1회량을 65 g 으로 올리면
    같은 일이 더 큰 목표에서도 생긴다.
    """
    fsm = ProcessFSM(_one_item(30.0), DosingConfig(max_attempts=8, scoop_nominal_g=40.0),
                     WeightModel(ScaleConfig()))
    cell = _DepthCell(yields=[], residual=0.0, nominal_g=40.0)
    run(fsm, cell)
    assert cell.fractions[0] == 0.75, cell.fractions      # 30 ÷ 40
    assert fsm.results[0].returns == 0, cell.fractions    # 첫 사이클이 헛돌지 않는다
    assert fsm.state == 'DONE'


def test_221_목표가_1회량보다_크면_전량이다():
    """큰 목표는 종전과 같다 — 첫 깊이 1.0."""
    fsm = ProcessFSM(_one_item(200.0), DosingConfig(max_attempts=8, scoop_nominal_g=40.0),
                     WeightModel(ScaleConfig()))
    cell = _DepthCell(yields=[], residual=0.0, nominal_g=40.0)
    run(fsm, cell)
    assert cell.fractions[0] == 1.0, cell.fractions
    assert fsm.state == 'DONE' and fsm.results[0].returns == 0


def test_221_첫_깊이도_min_fraction_하한을_지킨다():
    """하한 아래 요청은 A 의 profile 이 거부한다 — `decide()` 와 같은 식을 쓴다.

    하한에 눌린 요청을 조용히 올려 과다 채취하는 문제는 `decide()` 쪽이고 B 소관이다 (#221).
    첫 사이클만 다른 규칙을 쓰면 그 문제가 두 곳으로 갈라지므로 식을 같게 둔다.
    """
    cfg = DosingConfig(max_attempts=8, scoop_nominal_g=40.0)
    fsm = ProcessFSM(_one_item(3.0, tol_pct=50.0), cfg, WeightModel(ScaleConfig()))
    cell = _DepthCell(yields=[], residual=0.0, nominal_g=40.0)
    run(fsm, cell)
    # 하한값을 리터럴로 박지 않는다 — `min_fraction` 기본값이 바뀌면 이 시험이 **설정 변경만으로**
    # 깨져서, 정작 검사하려던 「하한에 눌린다」는 성질은 보지 못한 채 숫자만 고치게 된다.
    # 3 ÷ 40 = 0.075 로 하한보다 작다는 것이 전제이므로 그것도 같이 단언한다.
    assert 3.0 / cfg.scoop_nominal_g < cfg.min_fraction, cfg
    assert cell.fractions[0] == cfg.min_fraction, (cell.fractions, cfg.min_fraction)


def test_verify_규격이탈은_BATCH_OUT_OF_SPEC():
    """① 제품 판정 — 용기 순량이 레시피 총 목표량에서 벗어나면 규격 이탈이다.
    레시피 A 100 + B 50 = 150 g, 허용치 Σ(target×tol) = 7.5 g. 용기에 50 g 이 더 있다."""
    cell = Cell(yields=[100, 50], cup_bias=50.0)
    fsm = _fsm()
    trace = run(fsm, cell)
    d, = fsm.deviations
    assert {k: d[k] for k in ('kind', 'step', 'count', 'action', 'material_id')} == {
        'kind': 'BATCH_OUT_OF_SPEC', 'step': 'VERIFY', 'count': 1, 'action': 'QA', 'material_id': 'B'}
    assert '①규격' in d['detail'] and '②회계' in d['detail'], d['detail']
    assert fsm.state == 'DONE' and ('FINISH', 'carry') in trace          # QA 승인 → 그대로 완료품


def test_verify_회계불일치는_관측만_하고_판정하지_않는다():
    """② 폐지 (9/22 사용자·조장 확정) — 제품이 규격 안이면 회계가 어긋나도 배치는 안 멈춘다.

    종전에는 이 상황이 `VERIFY_MISMATCH` 였다. 지금은 **일탈이 아니다** — 값은 `verify_detail`
    에 관측으로만 남는다. 이것이 「배치 기록 교차검증을 포기한다」의 구체적 모습이다:
    제품은 합격인데 원료별 투입 기록이 5 g 틀린 배치가 그대로 완료품으로 나간다.
    """
    cell = Cell(yields=[100, 50], cup_bias=5.0)         # 규격(±7.5) 안, 회계는 5 g 어긋남
    fsm = _fsm()
    run(fsm, cell)
    assert fsm.deviations == [] and fsm.state == 'DONE', fsm.deviations
    assert '②회계 +5.0 (관측, 판정 안 함)' in fsm.verify_detail, fsm.verify_detail


def test_verify_직전_영점이_움직이면_재측정하고_한계를_넘으면_WEIGH_INVALID():
    """용기를 들기 전 빈 그리퍼 영점을 다시 재서 계량 오염을 거른다.

    NUDGE 는 정지·재개 장치일 뿐 계량 유효성과 연결돼 있지 않다 — 사람이 건드려 생긴 계단이
    NUDGE 임계를 넘든 못 넘든 오염된 값이 그대로 장부에 들어간다. 이 검사가 그 구멍을 막는다.
    """
    cell = Cell(yields=[100, 50], zero_drift_n=2.0)      # 한계 0.1 N 을 크게 넘는다
    fsm = _fsm()
    run(fsm, cell)
    assert [(d['kind'], d['step']) for d in fsm.deviations] == [('WEIGH_INVALID', 'VERIFY')]
    assert '영점 이동' in fsm.deviations[0]['detail'], fsm.deviations[0]['detail']
    assert cell.n_measure == 1 + 1 + 3, cell.n_measure   # SELF_CHECK 1 + TARE 영점 1 + VERIFY 재측정 3회


def test_verify_직전_영점이_한계_안이면_그대로_잰다():
    cell = Cell(yields=[100, 50], zero_drift_n=0.05)     # 한계 0.1 N 안
    fsm = _fsm()
    run(fsm, cell)
    assert fsm.deviations == [] and fsm.state == 'DONE'
    assert '영점이동 +0.050 N' in fsm.verify_detail, fsm.verify_detail


def test_영점_기준과_대조는_같은_자세에서_잰다():
    """tool_force 는 자세 의존이라 다른 자세끼리 비교하면 자세 차이가 영점 이동으로 둔갑한다.

    기준은 TARE 직전(carry 가 workbench ABOVE·그리퍼 열림으로 끝난 자리), 대조는 VERIFY 직전에
    같은 workbench ABOVE 로 옮긴 뒤. SELF_CHECK 의 measure 는 자가진단 전용이라 기준이 아니다.
    """
    cell = Cell(yields=[100, 50])
    fsm = _fsm()
    trace = run(fsm, cell)
    assert ('TARE', 'measure') in trace, trace          # 기준 — carry 직후 그 자리에서
    i = trace.index(('VERIFY', 'move'))
    assert trace[i:i + 3] == [('VERIFY', 'move'), ('VERIFY', 'measure'), ('VERIFY', 'weigh')], trace[i:i + 3]


def test_verify_통과해도_판정_근거를_남긴다():
    cell = Cell(yields=[100, 50])                       # 편향 없음
    fsm = _fsm()
    run(fsm, cell)
    assert fsm.deviations == [] and fsm.state == 'DONE'
    # 일탈이 없어도 수치는 남는다 — process_node 가 CellEvent 로 발행한다
    assert fsm.verify_detail.startswith('net ') and '①규격' in fsm.verify_detail, fsm.verify_detail


def test_invalid_scoop_weigh_retries():
    cell = Cell(yields=[100, 50], invalid_first=1)       # 첫 weigh_scoop 만 무효
    fsm = _fsm()
    trace = run(fsm, cell)
    assert kinds_for(trace, 'SCOOP_TARE') == ['weigh_scoop'] * 3 and fsm.state == 'DONE'   # A: 무효+재계량, B: 1회
    # 유효해지면 카운터가 0 으로 돌아간다 — 다음 단계의 무효와 합산되지 않는다 (#213 결정 2)
    assert fsm.results[0].invalid == 0 and fsm.results[0].invalid_step == ''
    assert not fsm.deviations


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
                               'detail': '', 'material_id': None}]
    assert trace[:3] == [('SELF_CHECK', 'measure'), ('PICK_CONTAINER', 'carry'), ('PICK_CONTAINER', 'carry')]
    assert fsm.tare_g == CUP_TARE and fsm.state == 'DONE' and len(fsm.results) == 2


def test_wrong_tool_scoop_width_mismatch_goes_to_qa_and_discards():
    """A 자리에 C(28mm) 스쿱이 잘못 꽂혀 있으면 퍼내기 전에 QA 로 멈춘다(교차오염 의심, 0회 즉시 QA)."""
    fp = ToolFingerprint(scoop_widths_mm={'A': 15.5, 'B': 18.0}, tolerance_mm=1.0)
    cell = Cell(yields=[100, 50], width_mm=28.0, qa='DISCARDED')
    fsm = _fsm(fingerprint=fp)
    trace = run(fsm, cell)
    assert fsm.deviations[0] == {'kind': 'WRONG_TOOL', 'step': 'PICK_SCOOP', 'count': 1, 'action': 'QA',
                                 'detail': '폭 28.0mm (기대 15.5±1.0mm)', 'material_id': 'A'}
    assert kinds_for(trace, 'SCOOP_TARE') == []          # 저울질도 못 가보고 걸렸다
    assert fsm.state == 'DISCARDED'


def test_wrong_tool_container_width_mismatch_approved_resumes_dosing():
    """규격 다른 용기를 QA 가 승인하면(완료품이 아니라) 정상적으로 TARE 부터 이어간다."""
    fp = ToolFingerprint(cup_width_mm=60.0, tolerance_mm=1.0)
    cell = Cell(yields=[100, 50], width_mm=45.0)         # scoop_widths_mm 을 안 줬으니 스쿱 쪽은 검사하지 않는다
    fsm = _fsm(fingerprint=fp)
    trace = run(fsm, cell)
    assert fsm.deviations[0] == {'kind': 'WRONG_TOOL', 'step': 'PICK_CONTAINER', 'count': 1, 'action': 'QA',
                                 'detail': '폭 45.0mm (기대 60.0±1.0mm)', 'material_id': None}
    assert trace[3] == ('TARE', 'weigh') and fsm.tare_g == CUP_TARE   # SELF_CHECK·PICK_CONTAINER·DEVIATION 다음
    assert fsm.state == 'DONE' and len(fsm.results) == 2  # 승인 후 평소대로 두 원료 다 담아 완료


def test_wrong_tool_skips_check_when_width_feedback_is_negative():
    """DIO 백엔드는 폭 피드백이 없어 성공해도 final_width_mm=-1 을 돌려준다(grip_inferred 는 DI 로 추론) —
    정상 파지를 WRONG_TOOL 로 오판하면 안 된다 (A 리뷰, PR #165)."""
    fp = ToolFingerprint(scoop_widths_mm={'A': 15.5, 'B': 18.0}, tolerance_mm=1.0)
    cell = Cell(yields=[100, 50], width_mm=-1.0)
    fsm = _fsm(fingerprint=fp)
    run(fsm, cell)
    assert fsm.state == 'DONE' and not fsm.deviations


def test_wrong_tool_detects_narrower_actual_width_too():
    """기대보다 더 가는 손잡이(교차오염의 반대 방향)도 잡는다 — 비교 자체는 방향에 무관하다."""
    fp = ToolFingerprint(scoop_widths_mm={'A': 15.5, 'B': 18.0}, tolerance_mm=1.0)
    cell = Cell(yields=[100, 50], width_mm=5.0, qa='DISCARDED')   # 기대(15.5)보다 훨씬 가는 손잡이
    fsm = _fsm(fingerprint=fp)
    run(fsm, cell)
    assert fsm.deviations[0]['kind'] == 'WRONG_TOOL'
    assert fsm.deviations[0]['detail'] == '폭 5.0mm (기대 15.5±1.0mm)'


def test_wrong_tool_scoop_approved_resumes_scooping_not_skip():
    """PICK_SCOOP 의 WRONG_TOOL 을 승인하면 이 스쿱으로 실제로 퍼서 투입한다 — 원료를 빈 결과로 건너뛰면
    안 된다 (A 리뷰, PR #165 — 예전엔 빈 ItemRun 을 결과에 남기고 RETURN_SCOOP 로 건너뛰었다)."""
    fp = ToolFingerprint(scoop_widths_mm={'A': 15.5}, tolerance_mm=1.0)
    cell = Cell(yields=[100], width_mm=28.0, qa='APPROVED')
    fsm = _fsm(fingerprint=fp)
    req = fsm.start()
    while req['kind'] != 'wait_qa':
        req = fsm.on_result(req, cell(req))
    assert fsm.deviations[-1]['kind'] == 'WRONG_TOOL' and fsm.deviations[-1]['step'] == 'PICK_SCOOP'
    nxt = fsm.on_result(req, cell(req))
    assert fsm.state == 'SCOOP_TARE' and nxt['kind'] == 'weigh_scoop'
    assert not fsm.results, '승인 즉시 원료를 건너뛰면 안 된다 — 아직 아무것도 못 퍼냈다'


def test_wrong_tool_container_width_mismatch_discarded():
    """QA 가 거부하면 이미 workbench 에 내려놓은 빈 통을 다시 들어 폐기함으로 보낸다(스쿱 반납 단계 없음)."""
    fp = ToolFingerprint(cup_width_mm=60.0, tolerance_mm=1.0)
    cell = Cell(yields=[], width_mm=45.0, qa='DISCARDED')
    fsm = _fsm(fingerprint=fp)
    trace = run(fsm, cell)
    assert fsm.deviations[0]['kind'] == 'WRONG_TOOL' and fsm.deviations[0]['step'] == 'PICK_CONTAINER'
    assert [k for s, k in trace if s == 'DISCARDED'] == ['carry']   # 스쿱을 쥔 적이 없어 move·grip 이 없다
    assert fsm.state == 'DISCARDED' and not fsm.results


def test_material_empty_refill_resumes_scoop():
    """#111 A안 — 재시도 3회는 `SCOOP_EMPTY`, **보충으로 넘어가는 4회째는 `MATERIAL_EMPTY`**.

    「한 번 못 펐다」와 「원료통이 비었다」는 다른 사실이고, 기록에도 다르게 남아야 한다.
    고치기 전에는 kind 가 끝까지 SCOOP_EMPTY 이고 action 만 REFILL 로 올라가서,
    BRD·흐름도·HMI 가 전제하는 MATERIAL_EMPTY 를 아무도 내지 않았다.
    """
    cell = Cell(yields=[0, 0, 0, 0, 100, 50])           # 4번 빈 스쿱 → REFILL → 보충 후 재개
    fsm = _fsm()
    trace = run(fsm, cell)
    kinds = [d['kind'] for d in fsm.deviations]
    assert kinds == ['SCOOP_EMPTY'] * 3 + ['MATERIAL_EMPTY'], kinds
    assert fsm.deviations[-1]['action'] == 'REFILL'
    assert '보충' in fsm.deviations[-1]['detail'], fsm.deviations[-1]['detail']
    assert fsm.deviations[-1]['count'] == 1, 'MATERIAL_EMPTY 카운터는 따로 센다'
    assert ('PAUSED', 'wait_interlock') in trace and fsm.state == 'DONE' and len(fsm.results) == 2


def test_material_empty_repeats_when_refill_did_not_help():
    """#111 — 채웠는데 또 비면 `SCOOP_EMPTY` 로 되돌아가지 않는다.

    SCOOP_EMPTY 카운터가 상한에 멈춰 있으므로 이후 접촉 실패는 계속 MATERIAL_EMPTY 다.
    「채웠는데 또 비었다」가 그대로 기록돼야 사람이 같은 통을 다시 들여다본다.
    """
    cell = Cell(yields=[0] * 8 + [100, 50])            # 보충 뒤에도 4번 더 빈 스쿱
    fsm = _fsm()
    run(fsm, cell)
    kinds = [d['kind'] for d in fsm.deviations]
    assert kinds[:3] == ['SCOOP_EMPTY'] * 3, kinds
    assert set(kinds[3:]) == {'MATERIAL_EMPTY'}, kinds
    assert all(d['action'] == 'REFILL' for d in fsm.deviations[3:])
    assert [d['count'] for d in fsm.deviations[3:]] == list(range(1, len(kinds) - 2)), kinds


def test_prepour_boundary_uses_original_target_tolerance_after_prior_delivery():
    fsm = _fsm()
    req = fsm.start()
    cell = Cell(yields=[100])
    while fsm.state != 'WEIGH_SCOOP':
        req = fsm.on_result(req, cell(req))
    fsm.cur.actual_g = 60.0
    # 남은 40 g + 전체 목표 100 g의 5% = 45 g. 남은 양의 5%가 아니다.
    for amount, expected in [(45.0, 'pour'), (45.01, 'return_material')]:
        fsm.state = 'WEIGH_SCOOP'
        result = fsm.on_result(req, {'valid': True, 'gross_g': SCOOP_TARE + amount})
        assert result['kind'] == expected
        assert fsm.cur.actual_g == 60.0


def test_set_end_parks_at_nudge_wait_and_mode_blocks_orders():
    """세트 끝 — 반송 뒤 nudge_wait 로 이동하는 동안 RUNNING, 서서 기다릴 때 PAUSED(주문 거부), NUDGE 뒤 DONE."""
    fsm = _fsm()
    cell = Cell(yields=[100, 50])
    req, seen = fsm.start(), []
    while req:
        if req['kind'] == 'move' and req.get('station') == 'nudge_wait':
            seen.append(('move', fsm.state, fsm.mode))
        if req['kind'] == 'wait_nudge':
            seen.append(('wait', fsm.state, fsm.mode))
        req = fsm.on_result(req, cell(req))
    assert seen == [('move', 'NUDGE_WAIT', 'RUNNING'), ('wait', 'NUDGE_WAIT', 'PAUSED')]
    assert (fsm.state, fsm.mode) == ('DONE', 'DONE') and len(fsm.results) == 2



class DepthCell(Cell):
    """담그기 깊이 힌트를 물리로 반영하는 오라클 — 퍼올림량 = fraction × 공칭 × gain.

    기본 Cell 은 yields 를 그대로 돌려줘 fraction 을 무시한다. 반환 뒤 재스쿱이 더 얕게
    푸는지는 그 오라클로 볼 수 없어 이 클래스를 쓴다. gain 은 원료 밀도·삽입 오차다.
    """

    def __init__(self, gain=1.0, nominal=40.0, **kw):
        super().__init__(yields=[], **kw)
        self.gain, self.nominal = gain, nominal
        self.fractions = []          # (material_id, fraction) — 원료가 2종이라 섞으면 안 된다

    def __call__(self, req):
        if req['kind'] == 'scoop':
            self.fractions.append((req['material_id'], req['fraction']))
            amt = req['fraction'] * self.nominal * self.gain
            self.in_scoop += amt
            return {'contact_detected': amt > 0}
        return super().__call__(req)


def depths(cell, material_id):
    return [f for m, f in cell.fractions if m == material_id]


def test_rescoop_after_return_digs_shallower_not_deeper():
    """초과로 되돌린 뒤 같은 깊이로 다시 푸면 초과가 그대로 재현된다 — 더 얕게 요청해야 한다."""
    cell = DepthCell(gain=1.6, residual=0.0)           # 힌트보다 60 % 더 퍼지는 원료
    fsm = _fsm()
    run(fsm, cell)
    assert fsm.results[0].returns == 1, '반환이 일어나야 하는 조건이다'
    a = depths(cell, 'A')
    assert a[-1] < a[-2], a                            # 반환 직전 깊이보다 얕게 다시 푼다


def test_return_does_not_consume_a_pour_attempt():
    """반환이 붓기 시도를 먹으면 마지막 한 스쿱을 남기고 TIMEOUT 으로 끝난다 (PR #26 회귀)."""
    cell = DepthCell(gain=1.5, residual=0.0)          # 한 번은 초과해 반환을 거치는 조건
    fsm = _fsm()
    run(fsm, cell)
    a = fsm.results[0]
    assert a.returns == 1, '반환을 거치는 경로여야 한다'
    assert fsm.state == 'DONE', [d['kind'] for d in fsm.deviations]
    assert a.attempts <= fsm.dosing_cfg.max_attempts and abs(a.actual_g - 100) < 1e-6


def test_rescoop_depth_compounds_when_already_shallow():
    """이미 얕게 펐는데 또 초과하면 그 얕은 깊이에서 더 줄여야 한다.

    보정을 비율 그대로 쓰면 깊이가 한 값에 멈춰(0.5 → 0.5 → 0.5) 반환만 반복하다
    TIMEOUT 으로 끝난다 — PR #33 리뷰에서 A 가 잡은 결함이다.
    """
    cell = DepthCell(gain=2.0, residual=0.0)           # 힌트의 2배로 퍼지는 원료
    fsm = _fsm()
    run(fsm, cell)
    a = depths(cell, 'A')
    assert fsm.results[0].returns == 1 and a == [1.0, 0.5, 0.25], a
    assert fsm.state == 'DONE' and not fsm.deviations, [d['kind'] for d in fsm.deviations]
    assert abs(fsm.results[0].actual_g - 100) < 1e-6


def test_invalid_tare_reweighs_and_does_not_keep_the_bad_value():
    """빈 용기 계량이 무효면 그 값을 tare 로 받지 않고 다시 잰다 (VERIFY 와 같은 규칙).

    무효 tare 를 그대로 쓰면 VERIFY 의 net = gross − tare_g 가 어긋나 ①·②가 둘 다 틀린다.
    """
    cell = Cell(yields=[100, 50], cup_invalid_first=1)
    fsm = _fsm()
    trace = run(fsm, cell)
    assert fsm.state == 'DONE' and not fsm.deviations
    assert fsm.tare_g == CUP_TARE                      # 무효값 0.0 이 아니라 재계량한 값이 들어간다
    # measure 는 영점 기준(같은 자세) — 그 뒤 무효 1회 재계량으로 weigh 가 2번이다
    assert kinds_for(trace, 'TARE') == ['measure', 'weigh', 'weigh']
    assert fsm.verify_net_g == 146                     # 순량이 정상 경로와 같다 (happy path 와 동일)


def _cleanup_trace(cell, fsm):
    """정리 경로에서 나간 요청을 (kind, station) 으로 뽑는다."""
    out = []
    for st, k in run(fsm, cell):
        if st == 'CLEANUP':
            out.append(k)
    return out


def test_213_weigh_residual_무효는_미측정으로_세고_누산하지_않는다():
    """#213 5번 1단계 — 이미 부은 뒤라 되돌릴 게 없고 **투입량만 모른다**.

    0 을 더하면 「안 들어갔다」가 되어 거짓이다. 누산을 건너뛰고 미측정으로 센다 —
    그래서 `actual_g` 는 실제보다 작고, 그 사실이 `unmeasured` 와 detail 에 남는다.
    """
    cell = Cell(yields=[100, 50])
    fsm = _fsm()
    tap_n = [0]
    orig = cell.__call__

    def tap(req):
        if req['kind'] == 'weigh_scoop' and fsm.state == 'WEIGH_RESIDUAL':
            tap_n[0] += 1
            if tap_n[0] <= 3:                        # 첫 사이클의 잔량 계량만 무효로 (재시도 2회 + 3회째)
                return {'gross_g': 0.0, 'valid': False}
        return orig(req)

    run(fsm, tap)
    d = next(x for x in fsm.deviations if x['step'] == 'WEIGH_RESIDUAL')
    # 투입 뒤라 되돌릴 게 없으므로 **QA** 다 (#213 결정 3). 재계량은 이미
    # max_invalid_retries 가 끝냈으므로 정책표는 즉시 처분만 한다 (결정 1).
    assert (d['kind'], d['action']) == ('WEIGH_INVALID', 'QA')
    assert '미측정 1회' in d['detail'], d['detail']
    # QA 승인으로 배치가 이어지므로 그 원료는 results 에 담긴다 (결정 3).
    r = fsm.results[0]
    assert r.unmeasured == 1, r.unmeasured
    assert r.actual_g < r.target_g          # 미측정분이 빠져 실제보다 작다
    # ⚠️ `decide()` 를 못 거쳐 verdict 가 **비어 있다.** FSM 은 여기까지만 안다 —
    # 「모름」을 어떻게 발행할지는 `process_node._publish_result` 가 정하고, `unmeasured` 가
    # 서 있으므로 **INVALID** 로 나간다 (계약 v1.8 · #108). 발행 쪽 고정은
    # `test_process_node.py` 의 `test_108_투입량_불명은_INVALID_로_나간다` 가 한다.
    assert r.verdict == '', r.verdict


def test_213_verify_무효는_최종계량_미측정으로_남는다():
    """#213 5번 1단계 — `verify_net_g` 를 0.0 으로 남기지 않는다.

    고치기 전에는 QA 승인 시 배치 기록에 순량 0.0 이 찍힌 채 완성품으로 나갔다.
    이제 `verify_unmeasured` 와 detail 이 「모른다」를 명시한다.
    """
    cell = Cell(yields=[100, 50], cup_invalid_first=0)
    fsm = _fsm()
    orig = cell.__call__

    def tap(req):
        if req['kind'] == 'weigh' and fsm.state == 'VERIFY':
            return {'gross_g': 0.0, 'net_g': 0.0, 'valid': False}
        return orig(req)

    run(fsm, tap)
    d = fsm.deviations[-1]
    assert (d['kind'], d['step']) == ('WEIGH_INVALID', 'VERIFY')
    assert fsm.verify_unmeasured is True
    assert '최종 계량 미측정' in fsm.verify_detail, fsm.verify_detail
    assert '판정 불가' in fsm.verify_detail, fsm.verify_detail


def test_213_cleanup_투입전_세_단계의_요청_순서를_고정한다():
    """#213 4번 — 손에 뭐가 있느냐로 정리 경로가 갈린다 (9/22 조장 확인).

    `TARE` 빈 그리퍼 → 정리 없음 · `SCOOP_TARE` 빈 스쿱 → 반환 없이 스쿱만 반납 ·
    `WEIGH_SCOOP` 원료 든 스쿱 → 원료통 반환 후 스쿱 반납. 중간 경유는 `material_N`(AT) 다.
    빈 스쿱을 원료통에 기울이는 동작(SCOOP_TARE 의 RETURN_MATERIAL)은 넣지 않는다.
    """
    # TARE — 정리 없음
    fsm = _fsm()
    assert _cleanup_trace(Cell(yields=[100, 50], cup_invalid_first=3), fsm) == []
    d, = fsm.deviations
    assert (d['kind'], d['step'], d['action']) == ('WEIGH_INVALID', 'TARE', 'FORCED')
    assert '정리 없음' in d['detail'], d['detail']
    assert fsm.state == 'ERROR'

    # SCOOP_TARE — 반환 없이 스쿱만 반납
    fsm = _fsm()
    assert _cleanup_trace(Cell(yields=[100, 50], invalid_first=3), fsm) == ['move', 'move', 'grip']
    d, = fsm.deviations
    assert (d['kind'], d['step'], d['action']) == ('WEIGH_INVALID', 'SCOOP_TARE', 'FORCED')
    assert 'return_material' not in d['detail'], d['detail']   # 빈 스쿱을 기울이지 않는다
    assert fsm.state == 'ERROR'


def test_213_cleanup_weigh_scoop_은_원료를_먼저_반환한다():
    """#213 4번 — `WEIGH_SCOOP` 은 스쿱에 원료가 있으므로 반환이 맨 앞에 온다."""
    cell = Cell(yields=[100, 50], invalid_first=3)
    cell.invalid_left = 0                       # SCOOP_TARE 는 통과시키고
    fsm = _fsm()
    trace = []
    orig = cell.__call__

    def tap(req):
        # WEIGH_SCOOP 두 번을 무효로 돌려준다
        if req['kind'] == 'weigh_scoop' and fsm.state == 'WEIGH_SCOOP':
            tap.n += 1
            if tap.n <= 3:
                return {'gross_g': 0.0, 'valid': False}
        return orig(req)
    tap.n = 0
    for st, k in run(fsm, tap):
        if st == 'CLEANUP':
            trace.append(k)
    assert trace == ['return_material', 'move', 'move', 'grip'], trace
    d = fsm.deviations[-1]
    assert (d['kind'], d['step'], d['action']) == ('WEIGH_INVALID', 'WEIGH_SCOOP', 'FORCED')
    assert 'return_material' in d['detail'], d['detail']
    assert fsm.state == 'ERROR'


def test_invalid_tare_up_to_limit_raises_weigh_invalid():
    """`max_invalid_retries` 를 넘으면(총 3회 무효) WEIGH_INVALID 일탈로 멈춘다 — 무효 tare 로 배치를 시작하지 않는다."""
    cell = Cell(yields=[100, 50], cup_invalid_first=3)
    fsm = _fsm()
    run(fsm, cell)
    assert [(d['kind'], d['step']) for d in fsm.deviations] == [('WEIGH_INVALID', 'TARE')]
    assert fsm.state == 'ERROR' and fsm.tare_g == 0.0


# ── 고정 스쿱 (9/23 조장 결정: 공칭 85 · min_fraction 0.10 · 레시피 85/85/170 ±10 %) ──────
# 시연은 **끝까지 담그는 고정 스쿱**이라 `depth_fraction` 이 실제로 안 먹는다. 그때 무슨 일이
# 벌어지는지를 고정한다 — 깊이 제어가 붙거나 `fixed_scoop` 분기가 들어오면 **먼저 깨져야** 한다.

NOMINAL_85, MINFRAC_10 = 85.0, 0.10


def _fixed_scoop_run(target, tol, per_scoop, first=None):
    """매번 같은 양을 퍼는 스쿱으로 원료 1종을 끝까지 돌린다 — 깊이 요청은 무시된다."""
    spec = parse({'product': 'demo', 'items': [{'material_id': 'A', 'target_g': target, 'tol_pct': tol}]})
    fsm = ProcessFSM(spec, DosingConfig(scoop_nominal_g=NOMINAL_85, min_fraction=MINFRAC_10),
                     WeightModel(ScaleConfig()), fingerprint=ToolFingerprint())
    run(fsm, Cell(yields=[per_scoop if first is None else first] + [per_scoop] * 40))
    r = fsm.results[0] if fsm.results else fsm.cur
    return r, [d['kind'] for d in fsm.deviations]


def test_고정스쿱_첫_스쿱_미달은_반환만_반복하다_TIMEOUT_으로_끝난다():
    """보충 요청이 **항상 초과**가 되어 스쿱↔반환을 돌다 반환 한도에서 멈춘다.

    고정 스쿱이면 `decide()` 가 몇 g 을 요청하든 85 g 이 온다. 남은 목표량이 그보다
    작으므로 반환 가드가 매번 걸리고, `max_returns` 를 넘겨 TIMEOUT 이 난다.

    ⚠️ **일탈이 둘이다.** TIMEOUT 을 QA 가 승인하면 배치가 이어지고, 투입량이 모자란 채
    VERIFY 에 도달해 `BATCH_OUT_OF_SPEC` 이 또 난다 — 시연자가 QA 를 **두 번** 누른다.
    """
    r, kinds = _fixed_scoop_run(85, 10, NOMINAL_85, first=70.0)
    assert kinds == ['TIMEOUT', 'BATCH_OUT_OF_SPEC'], kinds
    assert r.returns == 3, r.returns          # max_returns 를 소진한다
    assert r.attempts == 2, r.attempts        # 반환은 붓기 시도를 소모하지 않는다
    assert r.actual_g < 85 * 0.9, r.actual_g  # 허용 하한에도 못 미친 채 끝난다


@pytest.mark.parametrize('target,scoops,threshold', [(85, 1, 78.5), (170, 2, 77.5)])
def test_고정스쿱_임계는_스쿱_1회량으로_정해진다(target, scoops, threshold):
    """깨지는 지점이 **스쿱 1회량**으로 정해진다. 실측한 경계를 고정한다.

        투입 = 스쿱수 × 1회량 − 잔량      ← 잔량은 **마지막 사이클 것만** 잃는다
        (중간 사이클의 잔량은 다음 스쿱에 섞여 회수된다)

    그래서 스쿱이 많을수록 임계가 **내려간다** — 잃는 잔량이 한 번뿐이라 목표가 커질수록
    비율로는 작아진다. 85 g 1스쿱 78.5 g · 170 g 2스쿱 77.5 g.

    ⚠️ **운영을 묶는 것은 더 높은 쪽(78.5 g)** 이다. 공칭 85 대비 여유가 6.5 g(7.6 %)뿐이고,
    잔량이 커지면 그대로 줄어든다.
    """
    below, above = round(threshold - 0.1, 1), threshold
    r_bad, kinds_bad = _fixed_scoop_run(target, 10, below)
    assert kinds_bad == ['TIMEOUT', 'BATCH_OUT_OF_SPEC'], (target, below, kinds_bad)

    r_ok, kinds_ok = _fixed_scoop_run(target, 10, above)
    assert kinds_ok == [], (target, above, kinds_ok, r_ok.actual_g)
    assert abs(r_ok.actual_g - target) <= target * 0.10, r_ok.actual_g
    # 잔량을 한 번만 잃는다는 것이 이 경계의 이유다
    assert r_ok.actual_g == pytest.approx(scoops * above - 2.0), (r_ok.actual_g, scoops, above)


def test_고정스쿱_170g_은_첫_스쿱이_미달이어도_보충으로_합격한다():
    """**「첫 미달이면 QA」로 단순화하면 이 경우를 잘못 죽인다** (9/23 B 지적, C 원안 철회).

    170 g ±10 % 는 스쿱 두 번이 목표다. 첫 스쿱이 83 g 이면 투입 81 g 으로 미달이지만,
    한 번 더 퍼면 166 g 으로 **허용 안에 들어온다** — 보충이 상한(187 g)을 넘지 않기 때문이다.

    그래서 보충 중단 조건은 「미달이다」가 아니라 **「보충하면 상한을 넘는다」**여야 한다:

        actual + min_add > target × (1 + tol)      min_add = 고정 스쿱이면 공칭 전량

    85 g 은 1스쿱이 목표라 미달이면 보충이 곧 초과라 사실상 전 구간이 걸리지만,
    170 g 은 `actual ≤ 102 g` 까지 보충이 허용된다. **한 레시피로 일반화하면 틀린다.**
    """
    r, kinds = _fixed_scoop_run(170, 10, NOMINAL_85, first=83.0)
    assert kinds == [], kinds
    assert r.attempts == 2 and r.returns == 0, (r.attempts, r.returns)
    assert abs(r.actual_g - 170) <= 17.0, r.actual_g      # 83 + 85 − 잔량 2 = 166

    # 경계 — 보충이 상한을 넘기 시작하는 지점. `fixed_scoop` 분기는 여기서 갈려야 한다
    over_limit = 170 * 1.10 - NOMINAL_85                  # = 102.0
    assert over_limit == pytest.approx(102.0)
    assert 81.0 + NOMINAL_85 <= 170 * 1.10, '81 g 에서는 보충이 아직 상한 안이다'


def test_고정스쿱_보충요청이_최소채취보다_작아지는_구간은_없다():
    """「보충 요청량 < 최소채취면 QA」 분기는 **발동하지 못한다** (9/23 팀장 제안 검토).

    최소채취 = `min_fraction × scoop_nominal_g` = 8.5 g 인데 목표 85 의 허용오차도
    8.5 g 이라, 보충 요청량이 8.5 g 아래로 내려가기 전에 `decide()` 가 먼저 OK 를 낸다.
    그래서 조건은 「요청량이 작다」가 아니라 **「깊이 제어가 없다」**여야 한다.
    """
    cfg = DosingConfig(scoop_nominal_g=NOMINAL_85, min_fraction=MINFRAC_10)
    floor_g = cfg.min_fraction * cfg.scoop_nominal_g
    assert round(floor_g, 6) == 8.5
    for actual in (70.0, 76.0, 76.4):                     # 아직 보충을 요청한다
        assert decide(85.0, actual, 10.0, 1, True, 0, cfg).action == 'SCOOP', actual
        assert 85.0 - actual > floor_g, actual            # 요청량은 늘 최소채취보다 크다
    for actual in (76.5, 80.0, 85.0):                     # 여기서 이미 끝난다
        assert decide(85.0, actual, 10.0, 1, True, 0, cfg).action == 'DONE', actual
