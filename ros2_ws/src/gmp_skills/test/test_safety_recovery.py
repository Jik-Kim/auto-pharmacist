"""실제 복구·모션 호출 없이 상태 전이와 차단을 검증한다."""
import threading
import json
from types import SimpleNamespace

import pytest
from gmp_skills.core.recovery import recovery_step
from test_skill_shutdown import worker_node


@pytest.mark.parametrize('state,control,target,manual', [
    (1, None, 1, False), (3, 3, 1, False), (5, 2, 1, False),
    (9, 4, 8, True), (10, 5, 8, True), (8, 7, 1, False)])
def test_only_supported_state_transition(state, control, target, manual):
    step = recovery_step(state, True)
    assert (step.control, step.target, step.manual_required) == (control, target, manual)


@pytest.mark.parametrize('state', [-1, 0, 2, 4, 6, 7, 99])
def test_unsupported_state_never_produces_control(state):
    with pytest.raises(ValueError):
        recovery_step(state, True)


def test_operator_confirmation_required():
    with pytest.raises(ValueError):
        recovery_step(5, False)


@pytest.fixture
def recovery(monkeypatch):
    node, module = worker_node(monkeypatch)
    node.mode = 'real'
    node._safety_latched = True
    node._safety_revision = 1
    node._safety_reason = 'stop'
    node._last_robot_state = 5
    node._configured = True
    node._recovery_requests = {}
    node._recovery_inflight = False
    node.recovery_cache_size = 10
    node.recovery_timeout_s = 0.01
    node.state_poll_s = 0.001
    node._last_state_poll = float('-inf')
    node._now_s = __import__('time').monotonic
    node._motion_anchor = object()
    node._held_payload = 'cup'
    node._held_material_id = 'A'
    node.state = 5
    node.controls = []
    node.arm.robot_state = lambda: node.state
    node.arm.self_check = lambda *_: (True, 'OK')
    node.get_parameter = lambda key: SimpleNamespace(value={
        'robot.tool_name': 'tool_weight', 'robot.tcp_name': 'GripperDA_v1',
        'safety.collision_sensitivity': 50.0}[key])

    def control(value, timeout, dispatch):
        def apply():
            node.controls.append(value)
            node.state = {2: 1, 3: 1, 4: 8, 5: 8, 7: 1}[value]
        dispatch(apply)

    node.arm.recover_control = control
    job = module.Job('recover', {'expected_state': 5, 'operator_confirmed': True})
    return node, job, module


def test_success_requires_standby_and_invalidates_old_pose(recovery):
    node, job, _ = recovery
    result = node._do_recover(job)
    assert result[:3] == (True, False, 1)
    assert node.controls == [2]
    assert not node._safety_latched
    assert node._motion_anchor is None and node._held_payload == 'unknown'
    assert node._scoop_extract_uncertain


def test_service_ack_without_state_change_is_failure(recovery):
    node, job, _ = recovery
    node.arm.recover_control = lambda *args: None
    assert node._do_recover(job)[:3] == (False, True, 5)
    assert node._safety_latched


def test_stale_expected_state_sends_no_command(recovery):
    node, job, _ = recovery
    node.state = 6
    assert node._do_recover(job)[:3] == (False, True, 6)
    assert node.controls == []


def test_recovery_mode_requires_second_operator_request(recovery):
    node, job, _ = recovery
    node.state = job.args['expected_state'] = 9
    assert node._do_recover(job)[:3] == (False, True, 8)
    assert node._safety_latched and node.controls == [4]
    job.args['expected_state'] = 8
    assert node._do_recover(job)[:3] == (True, False, 1)
    assert node.controls == [4, 7]


def test_new_alarm_during_release_prevents_unlatching(recovery):
    node, job, _ = recovery
    node.arm.compliance_off = lambda: node._latch_safety('new alarm', alarm=True)
    assert node._do_recover(job)[0] is False
    assert node._safety_latched


def test_latch_blocks_motion_even_after_vendor_auto_reset(recovery):
    node, _, _ = recovery
    node.state = 1
    node._poll_safety(force=True)
    assert node._safety_latched
    assert 'SAFETY_STOP' in node._submit('move').error


def test_alarm_cancels_current_and_queued_jobs_without_device_call(recovery):
    node, _, module = recovery
    node._current = module.Job('scoop', {})
    pending = module.Job('move', {})
    node._q.put(pending)
    node._on_robot_alarm(SimpleNamespace(level=3, group=5, code=42, msg1='stop'))
    assert node._current.cancel and pending.cancel and pending.done.is_set()
    assert not node.controls and not node.calls


def test_duplicate_request_does_not_repeat_command_and_old_success_is_invalid(recovery):
    node, _, module = recovery
    node._worker_thread.start()
    req = SimpleNamespace(request_id='r1', operator_id='operator',
                          expected_state=5, operator_confirmed=True)
    try:
        first = node._srv_recover(req, SimpleNamespace())
        assert first.success
        assert node._srv_recover(req, SimpleNamespace()).success
        assert node.controls == [2]
        node._latch_safety('new stop')
        assert not node._srv_recover(req, SimpleNamespace()).success
        assert node.controls == [2]
    finally:
        node.shutdown()


def test_same_id_with_different_payload_rejected(recovery):
    node, _, _ = recovery
    node._recovery_requests['r1'] = {'fingerprint': ('another', 5, True)}
    req = SimpleNamespace(request_id='r1', operator_id='operator',
                          expected_state=5, operator_confirmed=True)
    assert not node._srv_recover(req, SimpleNamespace()).success
    assert node.controls == []


def test_start_event_carries_request_but_actual_alarm_does_not(recovery):
    node, _, _ = recovery
    events = []
    node.event = lambda level, code, text: events.append((code, json.loads(text)))
    node._worker_thread.start()
    req = SimpleNamespace(request_id='r1', operator_id='operator', expected_state=5, operator_confirmed=True)
    try:
        assert node._srv_recover(req, SimpleNamespace()).success
        start, done = events[:2]
        assert start[0] == 'ROBOT_SAFETY_STOP'
        assert start[1]['origin'] == 'recovery_request'
        assert start[1]['request_id'] == done[1]['request_id'] == 'r1'
        assert start[1]['safety_revision'] == done[1]['safety_revision']
        assert start[1]['safety_session'] == done[1]['safety_session']
        node._latch_safety('alarm', alarm=True)
        node._latch_safety('alarm', alarm=True)
        assert events[-1][1]['origin'] == 'robot_alarm'
        assert 'request_id' not in events[-1][1]
        assert events[-1][1]['safety_revision'] > events[-2][1]['safety_revision']
    finally:
        node.shutdown()


def test_new_alarm_before_worker_starts_rejects_recovery(recovery):
    node, job, _ = recovery
    job.args['safety_revision'] = node._safety_revision
    node._latch_safety('new alarm', alarm=True)
    assert node._do_recover(job)[0] is False
    assert not node.controls


def test_new_alarm_before_control_dispatch_sends_nothing(recovery):
    node, job, _ = recovery

    def race(control, timeout, dispatch):
        node._latch_safety('new alarm', alarm=True)
        dispatch(lambda: node.controls.append(control))

    node.arm.recover_control = race
    with pytest.raises(RuntimeError, match='새 알람'):
        node._do_recover(job)
    assert node.controls == [] and node._safety_latched


@pytest.mark.parametrize('request_id', ['pending', 'different'])
def test_pending_recovery_requests_do_not_block_alarm_executor(recovery, request_id):
    node, _, _ = recovery
    node._recovery_inflight = True
    node._recovery_requests['pending'] = {
        'fingerprint': ('operator', 5, True), 'done': threading.Event()}
    req = SimpleNamespace(request_id=request_id, operator_id='operator',
                          expected_state=5, operator_confirmed=True)
    result = node._srv_recover(req, SimpleNamespace())
    assert not result.success and not result.manual_required
    assert '처리 중' in result.message
    assert not node.controls


@pytest.mark.parametrize('configured,latched', [(False, False), (True, True)])
def test_nudge_does_not_query_force_before_configuration_or_while_latched(recovery, configured, latched):
    node, _, module = recovery
    node._nudge_enabled = True
    node._configured = configured
    node._safety_latched = latched
    node.arm.tool_force = lambda: pytest.fail('차단 상태에서 외력 조회')
    module.SkillNode._poll_nudge(node)


@pytest.mark.parametrize('active_job', [False, True])
def test_nudge_query_timeout_latches_and_aborts_active_job(recovery, active_job):
    node, _, module = recovery
    node._nudge_enabled = True
    node._safety_latched = False
    node._nudge_fault_logged = False
    node._current = module.Job('pour', {}) if active_job else None
    def fail():
        raise TimeoutError('get_tool_force: 응답 시간 초과')
    node.arm.tool_force = fail
    if active_job:
        with pytest.raises(TimeoutError):
            module.SkillNode._poll_nudge(node)
    else:
        module.SkillNode._poll_nudge(node)
    assert node._safety_latched
    assert 'get_tool_force' in node._safety_reason


def test_worker_handles_recovery_after_observation_timeout(recovery):
    node, _, module = recovery
    node._safety_latched = False
    node.state = 1
    def fail(job):
        raise TimeoutError('get_tool_force: 응답 시간 초과')
    node._do_measure = fail
    active = module.Job('measure', {})
    node._q.put(active)
    node._worker_thread.start()
    try:
        assert active.done.wait(1)
        assert node._safety_latched
        assert 'SAFETY_STOP' in node._submit('measure').error
        recovery_job = module.Job('recover', {'expected_state': 1, 'operator_confirmed': True,
                                               'safety_revision': node._safety_revision})
        node._q.put(recovery_job)
        assert recovery_job.done.wait(1)
        assert recovery_job.result[:3] == (True, False, 1)
        assert not node._safety_latched
    finally:
        assert node.shutdown()


@pytest.mark.parametrize('failure', ['mismatch', 'timeout'])
def test_recovery_rechecks_sensitivity_before_unlocking(recovery, failure):
    node, job, _ = recovery
    calls = []
    def check(*args):
        calls.append(args)
        if failure == 'timeout':
            raise TimeoutError('collision query timeout')
        return False, 'collision_sensitivity=49% expected=50%'
    node.arm.self_check = check
    with pytest.raises((RuntimeError, TimeoutError)):
        node._do_recover(job)
    assert calls == [('tool_weight', 'GripperDA_v1', 50.0)]
    assert node._safety_latched
