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
                        types.SimpleNamespace(MoveStop=object()))

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
    arm.R = FakeApi()
    arm.posx = lambda *values: tuple(values)
    arm.posj = lambda *values: tuple(values)
    arm._now = time.monotonic
    arm._sleep = lambda _seconds: None
    return arm


def test_initialize_and_self_check_use_installed_wrapper_names():
    arm = _arm()
    arm.initialize()
    assert [name for name, _, _ in arm.R.calls[:8]] == [
        'set_tool', 'set_tcp', 'set_velj', 'set_accj', 'set_velx', 'set_accx',
        'set_singular_handling', 'set_ref_coord'
    ]
    assert arm.self_check('tool_weight', 'GripperDA_v1')[0]
    assert [name for name, _, _ in arm.R.calls[-2:]] == ['get_tool', 'get_tcp']


def test_virtual_initialize_keeps_wrapper_default_base_reference():
    arm = _arm('virtual')
    arm.initialize()
    assert [name for name, _, _ in arm.R.calls] == [
        'set_velj', 'set_accj', 'set_velx', 'set_accx', 'set_singular_handling'
    ]


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
    arm.movej_cancellable(target, 0.3, lambda: False, 5.0)
    assert [name for name, _, _ in arm.R.calls] == ['amovej', 'check_motion']


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
