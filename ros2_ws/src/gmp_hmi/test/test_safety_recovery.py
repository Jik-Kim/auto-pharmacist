"""계약/Flask/ROS 클라이언트 스텁 검사. DDS나 실물 안전 검증은 아니다."""
from concurrent.futures import Future
import json
from types import SimpleNamespace

import pytest

from gmp_hmi.core.safety_recovery import SafetyRecovery
from test_v3_backend import backend, node, app_db, Message, state, login, post, PASSWORD


def stop(node, value=5):
    node._on_event(Message(code='ROBOT_SAFETY_STOP', level=2, batch_id='',
                           text=json.dumps(dict(robot_state=value, reason='시험 정지'))))


def request_body(node):
    return dict(expected_state=5, operator_confirmed=True,
                generation=node.safety_recovery.generation, operator_id='forged')


def test_recovery_auth_csrf_actor_audit_no_resume(node, app_db):
    app, _ = app_db
    client = app.test_client()
    node._on_state(state(mode=4))
    stop(node)
    assert client.post('/recover', json=request_body(node)).status_code in (401, 403)
    token = login(client)
    assert client.post('/recover', json=request_body(node)).status_code == 403
    node.cli_recovery.response = SimpleNamespace(success=True, manual_required=False, robot_state=1, message='STANDBY')
    before = dict(node.snap['state'])
    response = post(client, token, '/recover', request_body(node))
    assert response.status_code == 202
    req = node.cli_recovery.calls[0]
    assert req.operator_id == 'admin' and req.expected_state == 5 and req.operator_confirmed is True
    assert len(req.request_id) == 32
    assert node.snapshot()['safety_recovery']['phase'] == 'recovered'
    assert node.snap['state'] == before and not node.act_batch.calls
    assert not node.cli_lock.calls
    records = [m for m in node.published if m.code.startswith('HMI_SAFETY_RECOVERY')]
    assert len(records) == 2
    assert all(m.batch_id == 'B1' and m.text.startswith('admin ') for m in records)


@pytest.mark.parametrize('role', ['viewer', 'qa'])
def test_recovery_roles_denied(node, app_db, role):
    app, _ = app_db
    node.admin_store.create_user(role, PASSWORD, role)
    client = app.test_client()
    token = login(client, role)
    node._on_state(state(mode=4))
    stop(node)
    assert post(client, token, '/recover', request_body(node)).status_code == 403
    assert not node.cli_recovery.calls


@pytest.mark.parametrize('field,value', [('operator_confirmed', False), ('operator_confirmed', 'true'),
    ('expected_state', True), ('expected_state', 6), ('expected_state', -1),
    ('expected_state', 2), ('expected_state', 4), ('generation', -1)])
def test_invalid_confirmation_state_generation(node, app_db, field, value):
    app, _ = app_db
    node._on_state(state(mode=4))
    stop(node)
    client = app.test_client()
    token = login(client)
    data = request_body(node)
    data[field] = value
    assert post(client, token, '/recover', data).status_code == 400
    assert not node.cli_recovery.calls


def test_new_stop_invalidates_delayed_success(node):
    node._on_state(state(mode=4))
    stop(node)
    future = Future()
    node.cli_recovery.call_async = lambda req: future
    node.recover_safety('admin', 5, True, 1)
    assert node.safety_recovery.phase == 'pending'
    stop(node, 6)
    future.set_result(SimpleNamespace(success=True, manual_required=False, robot_state=1, message='old success'))
    assert node.safety_recovery.active
    assert node.safety_recovery.robot_state == 6 and node.safety_recovery.phase == 'stopped'
    assert '"applied_to_current_stop": false' in node.published[-1].text


@pytest.mark.parametrize('response_first', [False, True])
def test_matching_recovery_start_preserves_request_and_completed_result(node, response_first):
    node._on_state(state(mode=4))
    stop(node)
    future = Future()
    node.cli_recovery.call_async = lambda req: future
    node.recover_safety('admin', 5, True, 1)
    req = dict(node.safety_recovery.request)
    result = SimpleNamespace(success=True, manual_required=False, robot_state=1, message='STANDBY')
    if response_first:
        future.set_result(result)
    node._on_event(Message(code='ROBOT_SAFETY_STOP', level=2, batch_id='', text=json.dumps(
        dict(origin='recovery_request', request_id=req['request_id'], operator_id='admin',
             robot_state=5, reason='HMI 안전 복구 요청'))))
    if not response_first:
        assert node.safety_recovery.active and node.safety_recovery.phase == 'pending'
        future.set_result(result)
    assert node.safety_recovery.phase == 'recovered'
    assert node.snap['state']['mode'] == 'ERROR'
    assert not node.act_batch.calls and not node.cli_lock.calls


@pytest.mark.parametrize('detail', [
    {}, {'origin': 'robot_alarm'},
    {'origin': 'recovery_request', 'request_id': 'other', 'operator_id': 'admin'}])
def test_unrelated_stop_during_recovery_remains_blocked(node, detail):
    node._on_state(state(mode=4))
    stop(node)
    future = Future()
    node.cli_recovery.call_async = lambda req: future
    node.recover_safety('admin', 5, True, 1)
    req = dict(node.safety_recovery.request)
    detail = dict(detail)
    if detail.get('request_id') == 'same':
        detail['request_id'] = req['request_id']
    node._on_event(Message(code='ROBOT_SAFETY_STOP', level=2, batch_id='', text=json.dumps(detail)))
    # 뒤늦은 원래 요청의 시작 이벤트도 지워진 요청을 되살리지 않는다.
    node._on_event(Message(code='ROBOT_SAFETY_STOP', level=2, batch_id='', text=json.dumps(
        dict(origin='recovery_request', request_id=req['request_id'], operator_id='admin'))))
    future.set_result(SimpleNamespace(success=True, manual_required=False, robot_state=1, message='late'))
    assert node.safety_recovery.active and node.safety_recovery.request is None


def test_matching_recovery_start_allows_operator_handoff(node):
    node._on_state(state(mode=4))
    stop(node)
    future = Future()
    node.cli_recovery.call_async = lambda req: future
    node.recover_safety('admin', 5, True, 1)
    req = dict(node.safety_recovery.request)
    node._on_event(Message(code='ROBOT_SAFETY_STOP', level=2, batch_id='', text=json.dumps(
        dict(origin='recovery_request', request_id=req['request_id'], operator_id='other'))))
    future.set_result(SimpleNamespace(success=True, manual_required=False, robot_state=1, message='STANDBY'))
    assert node.safety_recovery.phase == 'recovered'


def test_stop_blocks_order_and_exit_but_not_other_error(node, backend):
    node._on_state(state(mode=4))
    stop(node)
    with pytest.raises(backend.CommandUnavailable):
        node.submit('demo_batch', 'admin')
    with pytest.raises(backend.CommandUnavailable):
        node.interlock(2, 'EXIT', 'admin')
    node._on_state(state(mode=0))
    assert node.safety_recovery.active  # IDLE/STANDBY 추정으로 해제하지 않음
    assert not node.act_batch.calls and not node.cli_lock.calls


@pytest.mark.parametrize('result,phase', [
    (dict(success=False, manual_required=True, robot_state=8, message='현장 교정'), 'manual_required'),
    (dict(success=False, manual_required=False, robot_state=-1, message='virtual'), 'failed'),
    (dict(success=True, manual_required=False, robot_state=-1, message='bad response'), 'failed'),
    (dict(success=True, manual_required=True, robot_state=1, message='manual'), 'manual_required'),
    (dict(success='true', manual_required=False, robot_state=1), 'uncertain'),
    (None, 'uncertain')])
def test_non_success_never_unlocks(result, phase):
    model = SafetyRecovery()
    model.stop(dict(robot_state=5))
    req = model.begin('admin', 5, True, 'B1', 1)
    model.finish(req, result)
    assert model.active and model.phase == phase


def test_uncertain_retry_preserves_entire_request():
    model = SafetyRecovery()
    req = model.begin('operator', 5, True, 'B1', 0)
    model.finish(req, None)
    with pytest.raises(ValueError):
        model.begin('operator', 5, True, 'B1', 0)
    assert model.retry('other', req['request_id']) == req
    model.stop({})
    with pytest.raises(ValueError):
        model.retry('operator', req['request_id'])


def test_missing_service_and_stale_state_fail_closed(node, backend):
    with pytest.raises(backend.CommandUnavailable):
        node.recover_safety('admin', 5, True, 0)
    node._on_state(state(mode=4))
    node.cli_recovery.ready = False
    with pytest.raises(backend.CommandUnavailable):
        node.recover_safety('admin', 5, True, 0)
    node.cli_recovery = None
    with pytest.raises(backend.CommandUnavailable):
        node.recover_safety('admin', 5, True, 0)


def test_malformed_stop_is_not_ignored(node):
    node._on_event(Message(code='ROBOT_SAFETY_STOP', level=2, batch_id='', text='broken'))
    assert node.safety_recovery.active and node.safety_recovery.robot_state == -1


def test_unrelated_recovery_event_cannot_unlock(node):
    stop(node)
    node._on_event(Message(code='ROBOT_SAFETY_RECOVERY', level=0, batch_id='', text=json.dumps(
        dict(request_id='old', operator_id='admin', success=True, manual_required=False, robot_state=1))))
    assert node.safety_recovery.active


def test_event_can_resolve_matching_uncertain_request(node):
    stop(node)
    req = node.safety_recovery.begin('admin', 5, True, 'B1', 1)
    node.safety_recovery.finish(req, None)
    node._on_event(Message(code='ROBOT_SAFETY_RECOVERY', level=0, batch_id='', text=json.dumps(
        dict(request_id=req['request_id'], operator_id='admin', success=True,
             manual_required=False, robot_state=1, message='STANDBY'))))
    assert node.safety_recovery.phase == 'recovered'
    assert not node.act_batch.calls
