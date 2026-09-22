"""process_fsm 전이 테스트 — 스쿱·용기 안의 양을 추적하는 물리 오라클로 돌린다 (D-22 6단계 흐름).

Cell 오라클: 스쿱 풍량 20 g, 용기 풍량 30 g. scoop 마다 yields 에서 퍼올림량을 꺼내고, pour 는 fraction 만큼 옮기되
residual 만큼 스쿱에 남긴다. weigh_scoop 은 스쿱 총량, weigh 는 용기 총량·순량을 돌려준다.
"""
from gmp_dosing.core.dosing import DosingConfig
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
                width_mm=None, cup_invalid_first=0):
        self.yields, self.residual, self.qa, self.spill, self.cup_bias = list(yields), residual, qa, spill, cup_bias
        self.grip = grip or (lambda req, n: True)
        self.width_mm = width_mm                          # 폭 지문 테스트용 — 정지 폭을 고정값으로 돌려준다
        self.in_scoop = self.in_cup = 0.0
        self.n = {'grip': 0, 'carry': 0, 'scoop': 0, 'weigh_scoop': 0, 'return_material': 0}
        self.invalid_left = invalid_first
        self.cup_invalid_left = cup_invalid_first   # 용기 계량(TARE·VERIFY) 무효 횟수

    def __call__(self, req):
        k = req['kind']
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
    cell = Cell(yields=[0, 0, 0, 0, 100, 50])           # 4번 빈 스쿱 → REFILL → 보충 후 재개
    fsm = _fsm()
    trace = run(fsm, cell)
    kinds = [d['kind'] for d in fsm.deviations]
    assert kinds == ['SCOOP_EMPTY'] * 4 and fsm.deviations[-1]['action'] == 'REFILL'
    assert ('PAUSED', 'wait_interlock') in trace and fsm.state == 'DONE' and len(fsm.results) == 2


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
    assert kinds_for(trace, 'TARE') == ['weigh', 'weigh']
    assert fsm.verify_net_g == 146                     # 순량이 정상 경로와 같다 (happy path 와 동일)


def test_invalid_tare_up_to_limit_raises_weigh_invalid():
    """max_invalid 만큼 무효면 WEIGH_INVALID 일탈로 멈춘다 — 무효 tare 로 배치를 시작하지 않는다."""
    cell = Cell(yields=[100, 50], cup_invalid_first=2)
    fsm = _fsm()
    run(fsm, cell)
    assert [(d['kind'], d['step']) for d in fsm.deviations] == [('WEIGH_INVALID', 'TARE')]
    assert fsm.state == 'ERROR' and fsm.tare_g == 0.0
