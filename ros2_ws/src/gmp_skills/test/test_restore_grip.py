"""센서 조회만으로 파지 상태를 복구하며 불명확한 중단은 차단한다."""
from types import SimpleNamespace as NS

import pytest
from test_safety_recovery import recovery


@pytest.fixture
def setup(recovery):
    node, _, module = recovery
    node._station_id = 'safe'
    node._safety_latched = False
    node._resume_grip_ready = True
    node._resume_grip = dict(payload='empty', material_id='', pending=False,
                             uncertain=False, resumable=True)
    node.stations = NS(get=lambda _: NS(extra={'posj': [0]*6}))
    node.arm.current_posj = lambda: [0]*6
    node.joint_tolerance = 1.0
    sensor = dict(busy=False, grip_inferred=False, safety_triggered=False, slip=False)
    node.gripper = NS(backend='dio', confirm_open_dio=lambda: not sensor['grip_inferred'],
                      state=lambda _: sensor)
    return node, module, sensor


@pytest.mark.parametrize('payload', ['empty', 'unknown', 'cup', 'scoop'])
def test_restores_only_sensor_confirmed_payload_without_actuation(setup, payload):
    node, module, sensor = setup
    node._resume_grip.update(payload=payload, material_id='A' if payload == 'scoop' else '')
    sensor['grip_inferred'] = payload in ('cup', 'scoop')
    result = node._do_restore_grip(module.Job('restore_grip', {}))
    expected = 'empty' if payload == 'unknown' else payload
    assert result == (expected, 'A' if payload == 'scoop' else '')
    assert node._held_payload == expected
    # 대역에 개폐/이동 메서드가 없으므로 호출했다면 위 실행이 실패한다.


@pytest.mark.parametrize('fault', ['not_safe', 'no_history', 'not_ready', 'lost_cup',
                                  'unknown_object', 'busy', 'slip', 'alarm',
                                  'pending', 'uncertain', 'mid_pour', 'missing_material', 'cancel'])
def test_uncertain_state_refuses_resume(setup, fault):
    node, module, sensor = setup
    job = module.Job('restore_grip', {})
    if fault == 'not_safe': node.arm.current_posj = lambda: [20]*6
    elif fault == 'no_history': node._resume_grip = None
    elif fault == 'not_ready': node._resume_grip_ready = False
    elif fault == 'lost_cup': node._resume_grip['payload'] = 'cup'
    elif fault == 'unknown_object': sensor['grip_inferred'] = True
    elif fault in ('busy', 'slip'): sensor[fault] = True
    elif fault == 'alarm': node._safety_latched = True
    elif fault in ('pending', 'uncertain'): node._resume_grip[fault] = True
    elif fault == 'mid_pour': node._resume_grip['resumable'] = False
    elif fault == 'missing_material':
        node._resume_grip['payload'] = 'scoop'
        sensor['grip_inferred'] = True
    else: job.cancel = True
    with pytest.raises(RuntimeError): node._do_restore_grip(job)


def test_safety_revision_change_during_sensor_read_refuses_commit(setup):
    node, module, sensor = setup
    def changed(_):
        node._safety_revision += 1
        return sensor
    node.gripper.state = changed
    with pytest.raises(RuntimeError, match='안전 상태 변경'):
        node._do_restore_grip(module.Job('restore_grip', {}))


def test_safe_request_saves_candidate_before_cancel(setup):
    node, module, _ = setup
    node._held_payload, node._held_material_id = 'cup', ''
    node._pending_scoop_extract = node._scoop_extract_uncertain = False
    node._current = module.Job('move', {})
    node._submit = lambda *a, **k: NS(error='')
    node.event = lambda *a: None
    result = module.SkillNode._srv_safe(node, NS(reason='REFILL'), NS())
    assert result.success and node._current.cancel
    assert node._resume_grip['payload'] == 'cup'
    assert not node._resume_grip_ready


@pytest.mark.parametrize('payload,material,valid', [
    ('cup', '', True), ('empty', '', False), ('unknown', '', False),
    ('scoop', '', False), ('cup', 'A', False), ('', 'A', False),
])
def test_expected_payload_is_checked_before_commit(setup, payload, material, valid):
    node, module, sensor = setup
    node._resume_grip['payload'] = 'cup'
    node._held_payload = 'unknown'
    sensor['grip_inferred'] = True
    job = module.Job('restore_grip', dict(expected_payload=payload, expected_material_id=material))
    if valid:
        assert node._do_restore_grip(job) == ('cup', '')
    else:
        with pytest.raises(RuntimeError):
            node._do_restore_grip(job)
        assert node._held_payload == 'unknown'


@pytest.mark.parametrize('material,valid', [('A', True), ('B', False)])
def test_expected_scoop_material_must_match_history(setup, material, valid):
    node, module, sensor = setup
    node._resume_grip.update(payload='scoop', material_id='A')
    sensor['grip_inferred'] = True
    job = module.Job('restore_grip', dict(expected_payload='scoop', expected_material_id=material))
    if valid:
        assert node._do_restore_grip(job) == ('scoop', 'A')
    else:
        with pytest.raises(RuntimeError, match='기대 파지/원료'):
            node._do_restore_grip(job)


@pytest.mark.parametrize('payload,error,canceled,extracted', [
    ('scoop', '', False, True), ('cup', '', False, False),
    ('scoop', '센서 불일치', False, False), ('scoop', '', True, False),
])
def test_service_forwards_expectation_and_reports_extraction(setup, payload, error, canceled, extracted):
    node, module, _ = setup
    def submit(kind, **args):
        assert kind == 'restore_grip'
        assert args == dict(expected_payload='scoop', expected_material_id='A')
        return NS(error=error, cancel=canceled, result=(payload, 'A' if payload == 'scoop' else ''))
    node._submit = submit
    res = module.SkillNode._srv_restore_grip(
        node, NS(expected_payload='scoop', expected_material_id='A'), NS())
    assert res.scoop_extracted == extracted
    assert res.success == (not error and not canceled)
    if not res.success:
        assert res.payload == 'unknown' and res.material_id == ''
