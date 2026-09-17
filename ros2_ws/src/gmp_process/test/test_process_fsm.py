from gmp_dosing.core.dosing import DosingConfig
from gmp_dosing.core.scale import ScaleConfig, WeightModel
from gmp_process.core.process_fsm import ProcessFSM
from gmp_process.core.recipe import parse


def _fsm():
    spec = parse({'product': 't', 'items': [{'material_id': 'A', 'target_g': 100, 'tol_pct': 5},
                                              {'material_id': 'B', 'target_g': 50, 'tol_pct': 5}]})
    return ProcessFSM(spec, DosingConfig(scoop_nominal_g=40), WeightModel(ScaleConfig()))


def run(fsm, oracle, max_steps=200):
    """oracle(req) -> 결과 dict. 전이를 끝까지 돌린다."""
    req, trace = fsm.start(), []
    while req and len(trace) < max_steps:
        trace.append((fsm.state, req['kind']))
        req = fsm.on_result(req, oracle(req))
    return trace


def test_happy_path_two_items():
    poured = {'n': 0}
    def oracle(req):
        if req['kind'] in ('grip', 'carry'):
            return {'grip_inferred': True}
        if req['kind'] == 'scoop':
            poured['n'] += 1
            return {'contact_detected': True}
        if req['kind'] == 'weigh':
            return {'gross_g': 50.0, 'net_g': 100.0 if poured['n'] <= 1 else 50.0, 'valid': True}
        return {}
    fsm = _fsm()
    trace = run(fsm, oracle)
    assert fsm.state == 'DONE' and len(fsm.results) == 2 and not fsm.deviations
    # 용기 반송: 매거진 → 칭량 (첫 carry), 칭량 → 완료품 트레이 (마지막 carry)
    assert trace[1] == ('PICK_CONTAINER', 'carry') and trace[-1] == ('FINISH', 'carry')


def test_under_then_correction():
    weights = iter([50.0, 70.0, 100.0])
    def oracle(req):
        if req['kind'] in ('grip', 'carry'):
            return {'grip_inferred': True}
        if req['kind'] == 'scoop':
            return {'contact_detected': True}
        if req['kind'] == 'weigh':
            w = next(weights, 50.0)
            return {'gross_g': w, 'net_g': w - 0.0 if w == 50.0 else w, 'valid': True}
        return {}
    fsm = _fsm()
    trace = run(fsm, oracle, 40)
    assert ('WEIGH', 'weigh') in trace and fsm.results and fsm.results[0].attempts == 2


def test_overfill_goes_to_qa_and_discard():
    def oracle(req):
        if req['kind'] in ('grip', 'carry'):
            return {'grip_inferred': True}
        if req['kind'] == 'scoop':
            return {'contact_detected': True}
        if req['kind'] == 'weigh':
            return {'gross_g': 50.0, 'net_g': 130.0, 'valid': True}
        if req['kind'] == 'wait_qa':
            return {'decision': 'DISCARDED'}
        return {}
    fsm = _fsm()
    trace = run(fsm, oracle, 40)
    assert fsm.deviations[0]['kind'] == 'OVERFILL' and fsm.state == 'DISCARDED'
    assert trace[-1] == ('DISCARDED', 'carry')          # 용기째 폐기함으로


def test_grip_fail_retries_then_forced():
    def oracle(req):
        if req['kind'] in ('grip', 'carry'):
            return {'grip_inferred': False}
        if req['kind'] == 'weigh':
            return {'gross_g': 50.0, 'valid': True}
        return {}
    fsm = _fsm()
    run(fsm, oracle, 40)
    assert [d['kind'] for d in fsm.deviations] == ['GRIP_FAIL'] * 4 and fsm.state == 'ERROR'


def test_container_grip_fail_retries_at_pick_container():
    n = {'carry': 0, 'scoop': 0}
    def oracle(req):
        if req['kind'] == 'carry':
            n['carry'] += 1
            return {'grip_inferred': n['carry'] >= 2}     # 첫 반송만 실패 → 재시도 후 성공
        if req['kind'] == 'grip':
            return {'grip_inferred': True}
        if req['kind'] == 'scoop':
            n['scoop'] += 1
            return {'contact_detected': True}
        if req['kind'] == 'weigh':
            if req['tare_g'] == 0.0:
                return {'gross_g': 30.0, 'valid': True}                       # TARE
            return {'net_g': 100.0 if n['scoop'] <= 1 else 50.0, 'valid': True}
        return {}
    fsm = _fsm()
    trace = run(fsm, oracle)
    assert fsm.deviations == [{'kind': 'GRIP_FAIL', 'step': 'PICK_CONTAINER', 'count': 1, 'action': 'RETRY',
                               'material_id': None}]
    assert trace[:3] == [('SELF_CHECK', 'measure'), ('PICK_CONTAINER', 'carry'), ('PICK_CONTAINER', 'carry')]
    assert fsm.tare_g == 30.0 and fsm.state == 'DONE' and len(fsm.results) == 2
