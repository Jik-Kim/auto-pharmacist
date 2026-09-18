import sys
import types

import pytest

sys.modules.setdefault('DR_init', types.SimpleNamespace())
sys.modules.setdefault('rclpy', types.SimpleNamespace())

from gmp_skills.adapters.dsr_arm import DsrArm  # noqa: E402


def test_dr_init_names_are_not_class_name_mangled(monkeypatch):
    fake_dr = types.SimpleNamespace()
    fake_node = object()
    calls = []
    fake_rclpy = types.SimpleNamespace(
        create_node=lambda *args, **kwargs: calls.append((args, kwargs)) or fake_node)
    monkeypatch.setattr('gmp_skills.adapters.dsr_arm.DR_init', fake_dr)
    monkeypatch.setattr('gmp_skills.adapters.dsr_arm.rclpy', fake_rclpy)
    monkeypatch.setitem(sys.modules, 'DSR_ROBOT2', types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'DR_common2', types.SimpleNamespace(posx=tuple, posj=tuple))

    DsrArm('dsr01', 'm0609', 'virtual', 60.0, 60.0)

    assert fake_dr.__dsr__id == 'dsr01'
    assert fake_dr.__dsr__model == 'm0609'
    assert fake_dr.__dsr__node is fake_node
    assert not hasattr(fake_dr, '_DsrArm__dsr__node')
    assert calls == [(('gmp_dsr_client',), {'namespace': 'dsr01', 'use_global_arguments': False})]


class FakeApi:
    DR_BASE = 0
    DR_TOOL = 1
    DR_FC_MOD_REL = 10
    DR_FC_MOD_ABS = 11
    DR_AXIS_Z = 2

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
    return arm


def test_initialize_and_self_check_use_installed_wrapper_names():
    arm = _arm()
    arm.initialize()
    assert [name for name, _, _ in arm.R.calls[:5]] == [
        'set_tool', 'set_tcp', 'set_velx', 'set_accx', 'set_ref_coord'
    ]
    assert arm.self_check('tool_weight', 'GripperDA_v1')[0]
    assert [name for name, _, _ in arm.R.calls[-2:]] == ['get_tool', 'get_tcp']


def test_virtual_initialize_keeps_wrapper_default_base_reference():
    arm = _arm('virtual')
    arm.initialize()
    assert [name for name, _, _ in arm.R.calls] == ['set_velx', 'set_accx']


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
