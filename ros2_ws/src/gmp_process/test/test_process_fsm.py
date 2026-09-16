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
        if req['kind'] == 'grip':
            return {'grip_inferred': True}
        if req['kind'] == 'scoop':
            poured['n'] += 1
            return {'contact_detected': True}
        if req['kind'] == 'weigh':
            return {'gross_g': 50.0, 'net_g': 100.0 if poured['n'] <= 1 else 50.0, 'valid': True}
        return {}
    fsm = _fsm()
    run(fsm, oracle)
    assert fsm.state == 'DONE' and len(fsm.results) == 2 and not fsm.deviations


def test_under_then_correction():
    weights = iter([50.0, 70.0, 100.0])
    def oracle(req):
        if req['kind'] == 'grip':
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
        if req['kind'] == 'grip':
            return {'grip_inferred': True}
        if req['kind'] == 'scoop':
            return {'contact_detected': True}
        if req['kind'] == 'weigh':
            return {'gross_g': 50.0, 'net_g': 130.0, 'valid': True}
        if req['kind'] == 'wait_qa':
            return {'decision': 'DISCARDED'}
        return {}
    fsm = _fsm()
    run(fsm, oracle, 40)
    assert fsm.deviations[0]['kind'] == 'OVERFILL' and fsm.state == 'DISCARDED'


def test_grip_fail_retries_then_forced():
    def oracle(req):
        if req['kind'] == 'grip':
            return {'grip_inferred': False}
        if req['kind'] == 'weigh':
            return {'gross_g': 50.0, 'valid': True}
        return {}
    fsm = _fsm()
    run(fsm, oracle, 40)
    assert [d['kind'] for d in fsm.deviations] == ['GRIP_FAIL'] * 4 and fsm.state == 'ERROR'
