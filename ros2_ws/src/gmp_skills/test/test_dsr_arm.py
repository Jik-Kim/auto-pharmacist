import sys
import time
import types

import pytest

sys.modules.setdefault('DR_init', types.SimpleNamespace())
sys.modules.setdefault('rclpy', types.SimpleNamespace())

from gmp_skills.adapters.dsr_arm import DsrArm  # noqa: E402


def test_dr_init_names_are_not_class_name_mangled(monkeypatch):
    fake_dr = types.SimpleNamespace()
    fake_node = types.SimpleNamespace(create_client=lambda *_args, **_kwargs: object())
    calls = []
    fake_rclpy = types.SimpleNamespace(
        create_node=lambda *args, **kwargs: calls.append((args, kwargs)) or fake_node)
    monkeypatch.setattr('gmp_skills.adapters.dsr_arm.DR_init', fake_dr)
    monkeypatch.setattr('gmp_skills.adapters.dsr_arm.rclpy', fake_rclpy)
    monkeypatch.setitem(sys.modules, 'DSR_ROBOT2', types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'DR_common2', types.SimpleNamespace(posx=tuple, posj=tuple))
    monkeypatch.setitem(sys.modules, 'dsr_msgs2.srv',
                        types.SimpleNamespace(MoveStop=object(), SetRobotControl=object()))

    monkeypatch.setitem(sys.modules, 'gmp_interfaces.srv',
                        types.SimpleNamespace(GetCollisionSensitivity=object()))

    DsrArm('dsr01', 'm0609', 'virtual', 60.0, 60.0)

    assert fake_dr.__dsr__id == 'dsr01'
    assert fake_dr.__dsr__model == 'm0609'
    assert fake_dr.__dsr__node is fake_node
    assert not hasattr(fake_dr, '_DsrArm__dsr__node')
    assert calls == [(('gmp_dsr_client',), {'namespace': 'dsr01', 'use_global_arguments': False})]


class FakeApi:
    DR_BASE = 0
    DR_TOOL = 1
    DR_MV_MOD_ABS = 0
    DR_FC_MOD_REL = 10
    DR_FC_MOD_ABS = 11
    DR_AXIS_Z = 2
    DR_AVOID = 0
    DR_STATE_IDLE = 0
    DR_STATE_BUSY = 2
    ROBOT_MODE_MANUAL = 0
    ROBOT_MODE_AUTONOMOUS = 1

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == 'get_tool':
                return 'tool_weight'
            if name == 'get_tcp':
                return 'GripperDA_v1'
            if name == 'check_motion':
                return 0
            return 0
        return call


def _arm(mode='real'):
    arm = DsrArm.__new__(DsrArm)
    arm.mode = mode
    arm.vel, arm.acc = 60.0, 60.0
    arm.tool_name, arm.tcp_name = 'tool_weight', 'GripperDA_v1'
    arm.virtual_tcp_name = 'GripperDA_v1'
    arm.tcp_offset_mm_deg = [0.0, 0.0, 208.0, 0.0, 0.0, 0.0]
    arm.R = FakeApi()
    arm.posx = lambda *values: tuple(values)
    arm.posj = lambda *values: tuple(values)
    arm._now = time.monotonic
    arm._sleep = lambda _seconds: None
    arm.startup_timeout_s = 15.0
    arm.pose_xyz_tolerance, arm.pose_rotation_tolerance = 2.0, 2.0
    arm.joint_tolerance = 1.0
    arm._move_stop_cli = type('Client', (), {
        'wait_for_service': lambda self, timeout_sec: True,
    })()
    return arm


@pytest.mark.parametrize('result', [0, -1, None])
def test_compliance_on_logs_response_and_rejects_failure(result):
    arm = _arm()
    messages = []
    arm.log = types.SimpleNamespace(info=messages.append)
    arm.R.task_compliance_ctrl = lambda stx: result
    if result == 0:
        assert arm.compliance_on([3000, 3000, 500, 200, 200, 200]) == 0
    else:
        with pytest.raises(RuntimeError, match='task_compliance_ctrl failed'):
            arm.compliance_on([3000, 3000, 500, 200, 200, 200])
    assert messages == [f'[COMPLIANCE_ON] task_compliance_ctrl return={result!r}']


def test_transfer_joint_speed_is_separate_from_cartesian_speed():
    arm = _arm()
    arm.amovej([0]*6, 0.2, joint_vel=10, joint_acc=20)
    assert arm.R.calls[-1][2] == {'vel': 2.0, 'acc': 4.0}


def test_movejx_uses_base_absolute_and_checks_solution():
    arm = _arm()
    target = [423, 93, 180, 90, -90, -90]
    arm.current_posx = lambda: target
    arm.solution_space = lambda: 3
    arm.movejx_cancellable(target, 3, .2, lambda: False, 10)
    assert arm.R.calls[0] == ('amovejx', (tuple(target),),
                              dict(sol=3, vel=12., acc=12., ref=0, mod=0))


@pytest.mark.parametrize('fault', ['wrong_solution', 'wrong_pose', 'query_error', 'cancel'])
def test_movejx_failed_completion_stops_motion(fault):
    arm = _arm()
    target = [423, 93, 180, 90, -90, -90]
    arm.current_posx = lambda: ([0]*6 if fault == 'wrong_pose' else target)
    clock = [0.]
    arm._now = lambda: clock[0]
    arm._sleep = lambda t: clock.__setitem__(0, clock[0] + t)
    stops = []
    arm.stop_motion = lambda: stops.append(True)

    def solution():
        if fault == 'query_error':
            raise RuntimeError('query failed')
        return 2 if fault == 'wrong_solution' else 3

    arm.solution_space = solution
    cancel = lambda: fault == 'cancel' and bool(arm.R.calls)
    with pytest.raises((RuntimeError, TimeoutError)):
        arm.movejx_cancellable(target, 3, .2, cancel, .1)
    assert stops == [True]


@pytest.mark.parametrize('sol,timeout,cancel', [(True, 10, False), (8, 10, False),
                                             (3, 0, False), (3, 10, True)])
def test_movejx_bad_request_never_dispatches(sol, timeout, cancel):
    arm = _arm()
    with pytest.raises((ValueError, RuntimeError)):
        arm.movejx_cancellable([0]*6, sol, .2, lambda: cancel, timeout)
    assert not arm.R.calls


@pytest.mark.parametrize('sol', [3, -1, 8, True])
def test_current_solution_result_is_validated(sol):
    arm = _arm()
    calls = []
    arm._bounded_query = lambda operation: calls.append(operation) or types.SimpleNamespace(sol_space=sol)
    if sol == 3:
        assert arm.solution_space() == 3
    else:
        with pytest.raises(RuntimeError):
            arm.solution_space()
    assert calls == ['get_current_solution_space']


@pytest.mark.parametrize('method', ['movej_cancellable', 'movel_cancellable'])
def test_pre_cancelled_motion_never_starts(method):
    arm = _arm()
    with pytest.raises(RuntimeError, match='cancelled'):
        getattr(arm, method)([0]*6, 0.2, lambda: True, 10)
    assert arm.R.calls == []


def real_startup_arm():
    arm = _arm()
    state = dict(tool='tool_weight', tcp='GripperDA_v1', mode=1)
    def call(operation, **fields):
        arm.R.calls.append((operation, (), fields))
        if operation == 'get_current_tool':
            return types.SimpleNamespace(info=state['tool'])
        if operation == 'get_current_tcp':
            return types.SimpleNamespace(info=state['tcp'])
        if operation == 'get_collision_sensitivity':
            return types.SimpleNamespace(sensitivity=50.0)
        if operation == 'get_robot_mode':
            return types.SimpleNamespace(robot_mode=state['mode'])
        if operation == 'set_robot_mode':
            state['mode'] = fields['robot_mode']
        if operation == 'set_current_tool':
            state['tool'] = fields['name']
        if operation == 'set_current_tcp':
            state['tcp'] = fields['name']
        return types.SimpleNamespace(success=True)
    arm._bounded_call = call
    return arm, state


def test_real_restart_keeps_matching_settings_and_autonomous_mode():
    arm, _ = real_startup_arm()
    arm.initialize()
    assert not any(name.startswith('set_current_') or name == 'set_robot_mode'
                   for name, _, _ in arm.R.calls)
    assert arm.self_check('tool_weight', 'GripperDA_v1', 50.0)[0]


def test_real_restart_restores_manual_mode_to_auto_without_tool_selection():
    arm, state = real_startup_arm()
    state['mode'] = 0
    arm.initialize()
    assert state['mode'] == 1
    assert [kw for name, _, kw in arm.R.calls if name == 'set_robot_mode'] == [{'robot_mode': 1}]
    assert not any(name.startswith('set_current_') for name, _, _ in arm.R.calls)


def test_real_mismatched_settings_select_in_manual_then_restore_auto():
    arm, state = real_startup_arm()
    state.update(tool='other', tcp='other')
    arm.initialize()
    assert state == dict(tool='tool_weight', tcp='GripperDA_v1', mode=1)
    assert [kw for name, _, kw in arm.R.calls if name == 'set_robot_mode'] == [
        {'robot_mode': 0}, {'robot_mode': 1}]


def test_real_tool_failure_still_restores_autonomous_mode():
    arm, state = real_startup_arm()
    state['tool'] = 'other'
    original = arm._bounded_call
    def call(operation, **fields):
        if operation == 'set_current_tool':
            raise RuntimeError('set_tool failed')
        return original(operation, **fields)
    arm._bounded_call = call
    with pytest.raises(RuntimeError, match='set_tool failed'):
        arm.initialize()
    assert state['mode'] == 1


@pytest.mark.parametrize('failure', ['timeout', 'not_applied'])
def test_mode_failure_blocks_later_initialization(failure):
    arm, state = real_startup_arm()
    state['mode'] = 0
    original = arm._bounded_call
    def call(operation, **fields):
        if operation == 'set_robot_mode':
            if failure == 'timeout':
                raise TimeoutError('set_robot_mode timeout')
            return types.SimpleNamespace(success=True)
        return original(operation, **fields)
    arm._bounded_call = call
    with pytest.raises((RuntimeError, TimeoutError)):
        arm.initialize()
    assert not any(name in ('set_velj', 'set_singularity_handling', 'set_ref_coord')
                   for name, _, _ in arm.R.calls)


def test_virtual_initialize_keeps_wrapper_default_base_reference():
    arm = _arm('virtual')
    arm.initialize()
    assert [name for name, _, _ in arm.R.calls] == [
        'set_robot_mode', 'add_tcp', 'set_tcp', 'set_robot_mode', 'set_velj',
        'set_accj', 'set_velx', 'set_accx', 'set_singular_handling'
    ]
    assert arm.R.calls[0][1] == (arm.R.ROBOT_MODE_MANUAL,)
    assert arm.R.calls[1][1] == ('GripperDA_v1', [0.0, 0.0, 208.0, 0.0, 0.0, 0.0])
    assert arm.R.calls[3][1] == (arm.R.ROBOT_MODE_AUTONOMOUS,)


def test_virtual_initialize_selects_existing_tcp_without_deleting_it():
    arm = _arm('virtual')
    arm.R.add_tcp = lambda *args: arm.R.calls.append(('add_tcp', args, {})) or -1
    arm.initialize()
    assert [name for name, _, _ in arm.R.calls[:4]] == [
        'set_robot_mode', 'add_tcp', 'set_tcp', 'set_robot_mode'
    ]
    assert all(name != 'del_tcp' for name, _, _ in arm.R.calls)


def test_virtual_tcp_failure_still_restores_autonomous_mode():
    arm = _arm('virtual')
    arm.R.set_tcp = lambda *_args: -1
    times = iter([0.0, 20.0])
    arm._now = lambda: next(times)
    with pytest.raises(RuntimeError, match='virtual TCP setup failed'):
        arm.initialize()
    mode_calls = [args[0] for name, args, _ in arm.R.calls if name == 'set_robot_mode']
    assert mode_calls == [arm.R.ROBOT_MODE_MANUAL, arm.R.ROBOT_MODE_AUTONOMOUS]


def test_motion_wrappers_preserve_async_wait_contract():
    arm = _arm('virtual')
    arm.amovel([1, 2, 3, 4, 5, 6], 0.5)
    arm.movesx([[1] * 6, [2] * 6], 0.5)
    arm.amove_periodic([0, 0, 0, 4, 0, 0], [0.5] * 6, 0.2, 2)
    assert arm.motion_state() == 0
    arm.wait_motion()
    assert [name for name, _, _ in arm.R.calls] == [
        'amovel', 'movesx', 'amove_periodic', 'check_motion', 'mwait'
    ]


def test_failed_motion_return_raises_instead_of_reporting_success():
    arm = _arm('virtual')
    arm.R.movel = lambda *_args, **_kwargs: -1
    with pytest.raises(RuntimeError, match='movel failed'):
        arm.movel([300, 0, 450, 0, 180, 0])


def test_safe_joint_move_uses_async_motion_and_checks_actual_pose():
    arm = _arm('virtual')
    target = [0, 0, 90, 0, 90, 0]
    arm.R.get_current_posj = lambda: target
    states = iter([arm.R.DR_STATE_BUSY, arm.R.DR_STATE_IDLE])
    arm.R.check_motion = lambda: arm.R.calls.append(('check_motion', (), {})) or next(states)
    arm.movej_cancellable(target, 0.3, lambda: False, 5.0)
    assert [name for name, _, _ in arm.R.calls] == ['amovej', 'check_motion', 'check_motion']


def test_async_motion_does_not_finish_on_initial_idle_sample():
    arm = _arm('virtual')
    states = iter([arm.R.DR_STATE_IDLE, arm.R.DR_STATE_BUSY, arm.R.DR_STATE_IDLE])
    arm.R.check_motion = lambda: next(states)
    arm.wait_motion_cancellable(lambda: False, 5.0)


def motion_clock(arm):
    clock = [0.0]
    arm._now = lambda: clock[0]
    arm._sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    stopped = []
    arm.stop_motion = lambda: stopped.append(True)
    return clock, stopped


def test_delayed_start_beyond_old_grace_period_is_not_completion():
    arm = _arm()
    clock, stopped = motion_clock(arm)
    arm.motion_state = lambda: 0 if clock[0] < 0.5 or clock[0] >= 0.8 else 2
    arm.wait_motion_cancellable(lambda: False, 2.0, target_reached=lambda: clock[0] >= 0.8)
    assert clock[0] >= 0.8
    assert not stopped


def test_never_started_motion_times_out_and_stops():
    arm = _arm()
    clock, stopped = motion_clock(arm)
    arm.motion_state = lambda: 0
    with pytest.raises(TimeoutError):
        arm.wait_motion_cancellable(lambda: False, 1.0, target_reached=lambda: False)
    assert clock[0] >= 1.0 and stopped == [True]


def test_busy_then_idle_short_of_target_is_not_success():
    arm = _arm()
    clock, stopped = motion_clock(arm)
    arm.motion_state = lambda: 2 if clock[0] < 0.2 else 0
    with pytest.raises(TimeoutError):
        arm.wait_motion_cancellable(lambda: False, 0.5, target_reached=lambda: False)
    assert stopped == [True]


def test_same_pose_request_can_complete_without_busy_transition():
    arm = _arm()
    _, stopped = motion_clock(arm)
    target = [344, -298, 200, 90, -180, -90]
    arm.motion_state = lambda: 0
    arm.current_posx = lambda: target
    arm.movel_cancellable(target, 1.0, lambda: False, 1.0)
    assert not stopped


def test_cartesian_completion_requires_rotation_as_well_as_xyz():
    arm = _arm()
    _, stopped = motion_clock(arm)
    target = [344, -334, 120, 90, 160, -90]
    arm.motion_state = lambda: 0
    arm.current_posx = lambda: [344, -334, 120, 90, 180, -90]
    with pytest.raises(TimeoutError):
        arm.movel_cancellable(target, 1.0, lambda: False, 0.5)
    assert stopped == [True]


@pytest.mark.parametrize('status', [-1, 99])
def test_invalid_motion_status_stops_instead_of_counting_as_start(status):
    arm = _arm()
    _, stopped = motion_clock(arm)
    arm.motion_state = lambda: status
    with pytest.raises(RuntimeError, match='invalid motion state'):
        arm.wait_motion_cancellable(lambda: False, 1.0)
    assert stopped == [True]


def test_cancel_during_target_read_takes_priority_over_arrival():
    arm = _arm()
    _, stopped = motion_clock(arm)
    cancelled = [False]
    arm.motion_state = lambda: 0
    def reached():
        cancelled[0] = True
        return True
    with pytest.raises(RuntimeError, match='cancelled'):
        arm.wait_motion_cancellable(lambda: cancelled[0], 1.0, target_reached=reached)
    assert stopped == [True]


def test_motion_observes_while_busy_and_at_completion():
    arm = _arm()
    states = iter([arm.R.DR_STATE_BUSY, arm.R.DR_STATE_IDLE])
    arm.motion_state = lambda: next(states)
    observations = []
    arm.wait_motion_cancellable(lambda: False, 5.0, observer=lambda: observations.append(True))
    assert observations == [True, True]


@pytest.mark.parametrize('failure', ['observation', 'cancel', 'timeout'])
def test_observation_failure_or_late_cancel_stops_motion(failure):
    arm = _arm()
    stopped = []
    arm.stop_motion = lambda: stopped.append(True)
    state = {'cancel': False, 'now': 0.0}
    arm._now = lambda: state['now']
    def observe():
        if failure == 'observation':
            raise RuntimeError('sensor failed')
        if failure == 'cancel':
            state['cancel'] = True
        else:
            state['now'] = 6.0
    with pytest.raises((RuntimeError, TimeoutError)):
        arm.wait_motion_cancellable(lambda: state['cancel'], 5.0, observer=observe)
    assert stopped == [True]


def test_stop_motion_uses_soft_stop_and_checks_response(monkeypatch):
    arm = _arm('virtual')
    arm.R.DR_SSTOP = 2
    request_type = type('Request', (), {'__init__': lambda self: setattr(self, 'stop_mode', 0)})
    arm._MoveStop = type('MoveStop', (), {'Request': request_type})
    future = type('Future', (), {
        'done': lambda self: True,
        'result': lambda self: type('Response', (), {'success': True})(),
    })()
    requests = []
    arm._move_stop_cli = type('Client', (), {
        'wait_for_service': lambda self, timeout_sec: True,
        'call_async': lambda self, req: requests.append(req) or future,
    })()
    arm.node = object()
    monkeypatch.setattr('gmp_skills.adapters.dsr_arm.rclpy.spin_until_future_complete',
                        lambda *_args, **_kwargs: None, raising=False)
    arm.stop_motion()
    assert requests[0].stop_mode == 2


def test_compliance_off_releases_compliance_even_if_release_force_fails():
    arm = _arm()

    def release_force():
        arm.R.calls.append(('release_force', (), {}))
        raise RuntimeError('force release failed')

    arm.R.release_force = release_force
    with pytest.raises(RuntimeError):
        arm.compliance_off()
    assert arm.R.calls[-1][0] == 'release_compliance_ctrl'


def test_condition_wrappers_treat_zero_as_condition_met():
    arm = _arm()
    assert arm.force_over(15.0)
    assert arm.position_at_or_below(120.0)
    assert arm.R.calls[-2][0] == 'check_force_condition'
    assert arm.R.calls[-2][2]['min'] == 15.0
    assert arm.R.calls[-1][0] == 'check_position_condition'
    assert arm.R.calls[-1][2]['max'] == 120.0


@pytest.mark.parametrize('method', ['measure_force', 'measure_workpiece'])
@pytest.mark.parametrize('period', [0.0, -1.0, float('nan'), float('inf')])
def test_invalid_sampling_period_rejected_before_device_call(method, period):
    arm = _arm()
    with pytest.raises(ValueError, match='표본 간격'):
        getattr(arm, method)(3, 0, period_s=period)
    assert arm.R.calls == []


@pytest.mark.parametrize('method', ['measure_force', 'measure_workpiece'])
@pytest.mark.parametrize('with_observer', [False, True])
def test_sampling_cadence_and_nudge_reads_are_separate(method, with_observer):
    arm = _arm()
    clock = [0.0]
    reads, observed, workpiece_reads = [], [], []
    arm._now = lambda: clock[0]
    arm._sleep = lambda duration: clock.__setitem__(0, clock[0] + duration)

    def force():
        reads.append(clock[0])
        # 표본 시각 사이의 큰 값이 계량 평균에 들어가면 실패한다.
        sample = any(abs(clock[0] - t) < 1e-8 for t in (0, 0.82, 1.64))
        return [0, 0, 2.0 if sample else 100.0, 0, 0, 0]

    def weight():
        workpiece_reads.append(clock[0])
        return 0.1

    arm.tool_force = force
    arm.R.get_workpiece_weight = weight
    result = getattr(arm, method)(3, 0, period_s=0.82,
                                  observer=(lambda _: observed.append(clock[0])) if with_observer else None)
    assert result[-1] is True
    assert clock[0] == pytest.approx(1.64)
    if method == 'measure_force':
        assert result[1:3] == pytest.approx((2.0, 0.0))
        if not with_observer:
            assert reads == pytest.approx([0, 0.82, 1.64])
    else:
        assert workpiece_reads == pytest.approx([0, 0.82, 1.64])
    if with_observer:
        assert max(b-a for a, b in zip(observed, observed[1:])) <= 0.100001


@pytest.mark.parametrize('latency', [0.2, 1.0])
def test_force_sampling_accounts_for_device_call_latency(latency):
    arm = _arm()
    clock, starts = [0.0], []
    arm._now = lambda: clock[0]
    arm._sleep = lambda duration: clock.__setitem__(0, clock[0] + duration)

    def force():
        starts.append(clock[0])
        clock[0] += latency
        return [0, 0, 2, 0, 0, 0]

    arm.tool_force = force
    assert arm.measure_force(3, 0, period_s=0.82)[-1]
    step = max(0.82, latency)
    assert starts == pytest.approx([0, step, 2*step])
    assert clock[0] == pytest.approx(2*step + latency)


def test_internal_linear_move_obeys_worker_cancel():
    arm = _arm()
    arm.cancel_requested = lambda: True
    arm.motion_timeout_s = 30
    with pytest.raises(RuntimeError, match='cancelled'):
        arm.movel([1]*6)
    assert not arm.R.calls


def test_rejected_force_release_is_not_reported_success():
    arm = _arm()
    arm.R.release_force = lambda: -1
    with pytest.raises(RuntimeError, match='release_force failed'):
        arm.compliance_off()
    assert arm.R.calls[-1][0] == 'release_compliance_ctrl'


@pytest.mark.parametrize('control', [2, 3, 4, 5, 7])
def test_recovery_service_dispatch_and_stop_reset(monkeypatch, control):
    arm = _arm()
    sent = []
    future = types.SimpleNamespace(done=lambda: True,
                                   result=lambda: types.SimpleNamespace(success=True))
    arm.node = object()
    arm._SetRobotControl = types.SimpleNamespace(Request=types.SimpleNamespace)
    arm._robot_control_cli = types.SimpleNamespace(
        wait_for_service=lambda **_: True,
        call_async=lambda req: sent.append(req.robot_control) or future)
    monkeypatch.setattr('gmp_skills.adapters.dsr_arm.rclpy', types.SimpleNamespace(
        spin_until_future_complete=lambda *args, **kwargs: None))
    arm.recover_control(control, 1.0, lambda operation: operation())
    assert sent == [control]
    assert arm.R.calls == ([('set_safe_stop_reset_type', (0,), {})] if control == 2 else [])


def test_recovery_service_timeout_cancels_future(monkeypatch):
    arm = _arm()
    cancelled = []
    arm.node = object()
    arm._SetRobotControl = types.SimpleNamespace(Request=types.SimpleNamespace)
    arm._robot_control_cli = types.SimpleNamespace(
        wait_for_service=lambda **_: True,
        call_async=lambda req: types.SimpleNamespace(
            done=lambda: False, cancel=lambda: cancelled.append(True)))
    monkeypatch.setattr('gmp_skills.adapters.dsr_arm.rclpy', types.SimpleNamespace(
        spin_until_future_complete=lambda *args, **kwargs: None))
    with pytest.raises(TimeoutError):
        arm.recover_control(3, 1.0, lambda operation: operation())
    assert cancelled == [True]


def test_backdrive_command_is_never_dispatched():
    with pytest.raises(ValueError):
        _arm().recover_control(6, 1.0, lambda operation: operation())


def query_arm(monkeypatch, operation='get_robot_state', *, ready=True, done=True,
              success=True, payload=None):
    arm = _arm()
    arm.node = object()
    calls = []
    cancelled = []
    response = types.SimpleNamespace(success=success, **(
        payload if payload is not None else {'robot_state': 1}))
    future = types.SimpleNamespace(done=lambda: done, cancelled=lambda: False,
                                   result=lambda: response,
                                   cancel=lambda: cancelled.append(True))
    client = types.SimpleNamespace(
        srv_type=types.SimpleNamespace(Request=types.SimpleNamespace),
        wait_for_service=lambda **kw: calls.append(('wait', kw)) or ready,
        call_async=lambda req: calls.append(('query', vars(req))) or future)
    arm.R = types.SimpleNamespace(DR_BASE=0, **{'_ros2_' + operation: client})
    logs = []
    arm.log = types.SimpleNamespace(error=logs.append)
    monkeypatch.setattr('gmp_skills.adapters.dsr_arm.rclpy', types.SimpleNamespace(
        spin_until_future_complete=lambda node, fut, **kw: calls.append(('spin', kw))))
    return arm, calls, cancelled, logs


@pytest.mark.parametrize('ready', [True, False])
def test_robot_state_waits_for_wrapper_client_before_query(monkeypatch, ready):
    arm, calls, _, _ = query_arm(monkeypatch, ready=ready)
    if ready:
        assert arm.robot_state() == 1
        assert calls[1] == ('query', {})
        assert calls[2] == ('spin', {'timeout_sec': arm.startup_timeout_s})
    else:
        with pytest.raises(TimeoutError, match='서비스 준비'):
            arm.robot_state()
        assert len(calls) == 1
    assert calls[0] == ('wait', {'timeout_sec': arm.startup_timeout_s})


@pytest.mark.parametrize('operation', ['get_robot_state', 'get_tool_force'])
def test_query_timeout_cancels_without_retry_and_names_failed_operation(monkeypatch, operation):
    arm, calls, cancelled, logs = query_arm(monkeypatch, operation, done=False)
    with pytest.raises(TimeoutError, match=operation + ': 응답 시간 초과'):
        getattr(arm, 'robot_state' if operation == 'get_robot_state' else 'tool_force')()
    assert cancelled == [True]
    assert sum(name == 'query' for name, _ in calls) == 1
    assert operation in logs[-1] and '응답' in logs[-1]


@pytest.mark.parametrize('operation', ['get_robot_state', 'get_tool_force'])
def test_failed_response_is_never_treated_as_valid_reading(monkeypatch, operation):
    arm, _, _, _ = query_arm(monkeypatch, operation, success=False)
    with pytest.raises(RuntimeError, match='조회 실패'):
        getattr(arm, 'robot_state' if operation == 'get_robot_state' else 'tool_force')()


def test_tool_force_keeps_base_reference_and_six_axes(monkeypatch):
    force = [1., 2., 3., 4., 5., 6.]
    arm, calls, _, _ = query_arm(monkeypatch, 'get_tool_force', payload={'tool_force': force})
    assert arm.tool_force() == force
    assert calls[1] == ('query', {'ref': 0})


@pytest.mark.parametrize('force', [[1.] * 5, [float('nan')] * 6, [float('inf')] * 6])
def test_invalid_force_is_rejected(monkeypatch, force):
    arm, _, _, _ = query_arm(monkeypatch, 'get_tool_force', payload={'tool_force': force})
    with pytest.raises(RuntimeError, match='6축 외력'):
        arm.tool_force()


def test_robot_state_missing_private_client_fails_without_query():
    arm = DsrArm.__new__(DsrArm)
    arm.R = types.SimpleNamespace(get_robot_state=lambda: pytest.fail('준비 확인 없이 조회'))
    with pytest.raises(RuntimeError, match='준비 확인 기능'):
        arm.robot_state()


@pytest.mark.parametrize('operation,fields', [
    ('set_robot_mode', {'robot_mode': 1}),
    ('set_current_tool', {'name': 'tool_weight'}),
    ('set_current_tcp', {'name': 'GripperDA_v1'}),
    ('set_ref_coord', {'coord': 0}),
    ('set_singularity_handling', {'mode': 0}),
])
def test_startup_setting_timeout_is_bounded_and_never_retried(monkeypatch, operation, fields):
    arm, calls, cancelled, _ = query_arm(monkeypatch, operation, done=False)
    with pytest.raises(TimeoutError, match=operation):
        arm._bounded_call(operation, **fields)
    assert [entry for entry in calls if entry[0] == 'query'] == [('query', fields)]
    assert cancelled == [True]


@pytest.mark.parametrize('actual,expected_ok', [(50.0, True), (49.0, False), (51.0, False), (0.0, False), (100.0, False)])
def test_self_check_requires_exact_configured_sensitivity(actual, expected_ok):
    arm, _ = real_startup_arm()
    original = arm._bounded_call
    seen = []
    def call(operation, **fields):
        seen.append(operation)
        if operation == 'get_collision_sensitivity':
            return types.SimpleNamespace(sensitivity=actual)
        return original(operation, **fields)
    arm._bounded_call = call
    ok, detail = arm.self_check('tool_weight', 'GripperDA_v1', 50.0)
    assert ok is expected_ok
    assert 'expected=50%' in detail
    assert seen == ['get_current_tool', 'get_current_tcp', 'get_collision_sensitivity']
    assert not any(name.startswith(('set_', 'change_')) for name, *_ in arm.R.calls)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1.0, 101.0, True, '50'])
def test_self_check_rejects_invalid_sensitivity(value):
    arm, _ = real_startup_arm()
    with pytest.raises(ValueError):
        arm.self_check('tool_weight', 'GripperDA_v1', value)
    arm._bounded_call = lambda operation: types.SimpleNamespace(info='tool', sensitivity=value)
    with pytest.raises(RuntimeError, match='감도 응답'):
        arm.self_check('tool', 'tool', 50.0)


@pytest.mark.parametrize('fault', ['unavailable', 'timeout', 'failure', 'success'])
def test_collision_query_uses_extension_with_bounded_wait(monkeypatch, fault):
    arm, calls, cancelled, _ = query_arm(
        monkeypatch, ready=fault != 'unavailable', done=fault != 'timeout',
        success=fault != 'failure', payload={'sensitivity': 50.0})
    arm._collision_sensitivity_cli = arm.R._ros2_get_robot_state
    if fault == 'success':
        assert arm._bounded_call('get_collision_sensitivity').sensitivity == 50.0
    else:
        with pytest.raises((TimeoutError, RuntimeError)):
            arm._bounded_call('get_collision_sensitivity')
    assert sum(name == 'query' for name, _ in calls) == (0 if fault == 'unavailable' else 1)
    assert cancelled == ([True] if fault == 'timeout' else [])


def test_virtual_self_check_does_not_call_controller():
    arm = _arm('virtual')
    arm._bounded_call = lambda *_: pytest.fail('virtual에서는 실물 감도를 조회하지 않는다')
    ok, detail = arm.self_check('tool', 'tcp', 50.0)
    assert ok and '생략' in detail


def test_contact_stop_waits_for_idle_without_requiring_original_target():
    arm = _arm()
    _, stopped = motion_clock(arm)
    states = iter([2, 2, 0])
    reads = []
    arm.motion_state = lambda: reads.append(True) or next(states)
    arm.current_posx = lambda: [0]*6
    arm.movel_cancellable([100]*6, .2, lambda: False, 1.0,
                          stop_requested=lambda: True)
    assert stopped == [True]
    assert len(reads) == 3


def test_contact_stop_ack_without_idle_times_out():
    arm = _arm()
    _, stopped = motion_clock(arm)
    arm.motion_state = lambda: 2
    with pytest.raises(TimeoutError):
        arm.wait_motion_cancellable(lambda: False, .1, stop_requested=lambda: True)
    assert stopped == [True, True]  # 접촉 요청 후 시간 초과 정지


def test_failed_contact_stop_is_not_completion():
    arm = _arm()
    motion_clock(arm)
    def fail():
        raise RuntimeError('stop failed')
    arm.stop_motion = fail
    with pytest.raises(RuntimeError, match='stop failed'):
        arm.wait_motion_cancellable(lambda: False, 1.0, stop_requested=lambda: True)


def test_cancel_wins_over_contact_stop():
    arm = _arm()
    _, stopped = motion_clock(arm)
    cancelled = [False]
    def observe():
        cancelled[0] = True
    with pytest.raises(RuntimeError, match='cancelled'):
        arm.wait_motion_cancellable(lambda: cancelled[0], 1.0, observer=observe,
                                    stop_requested=lambda: True)
    assert stopped == [True]
