import math

import pytest

from gmp_skills.adapters.rg2_gripper import Rg2Gripper, joint_to_width_mm, width_mm_to_joint


@pytest.mark.parametrize('width_mm', [0.0, 15.5, 28.0, 38.5, 100.0])
def test_width_joint_round_trip(width_mm):
    assert joint_to_width_mm(width_mm_to_joint(width_mm)) == pytest.approx(width_mm, abs=0.01)


def test_modbus_width_is_tenth_mm_integer():
    sent = []
    g = Rg2Gripper('modbus', lambda command: sent.append(command) or True)
    assert not g.move(28.04, timeout_s=0.0)  # 새 폭 표본이 없으면 성공으로 보지 않는다
    assert sent == ['280']


def test_virtual_width_is_joint_angle_string():
    sent = []
    g = Rg2Gripper('virtual', lambda command: sent.append(command) or True)
    assert not g.move(38.5, timeout_s=0.0)
    assert float(sent[0]) == pytest.approx(width_mm_to_joint(38.5), abs=0.0001)


def test_grip_inference_and_slip_latch():
    now = lambda: 0.0
    g = None

    def send(_command):
        g.on_joint_state(width_mm_to_joint(18.0), 1.0)
        return True

    g = Rg2Gripper('virtual', send, grip_margin_mm=2.0, slip_mm=1.5, now_fn=now)
    ok, final_width, inferred = g.grip(15.5, 20.0, timeout_s=1.0)
    assert ok and inferred and final_width == pytest.approx(18.0, abs=0.01)

    g.on_joint_state(width_mm_to_joint(20.0), 1.2)
    assert g.state(now_s=1.4)['grip_inferred'] is False
    assert g.consume_slip() is True
    assert g.consume_slip() is False


def test_stale_joint_state_fails_closed():
    g = Rg2Gripper('virtual', lambda _command: True, now_fn=lambda: 2.0, state_timeout_s=0.5)
    g.on_joint_state(width_mm_to_joint(18.0), 1.0)
    state = g.state()
    assert state['busy'] is True
    assert state['grip_inferred'] is False


def test_force_command_uses_2_5_n_steps():
    sent = []
    g = Rg2Gripper('modbus', lambda command: sent.append(command) or True)
    g.set_force(35.0)
    assert sent == ['d', 'd']
    assert math.isclose(g.force_cmd_n, 35.0)


def test_force_command_failure_stops_and_preserves_confirmed_level():
    sent = []
    g = Rg2Gripper('modbus', lambda command: bool(sent.append(command)))
    assert not g.set_force(35.0)
    assert sent == ['d']
    assert math.isclose(g.force_cmd_n, 40.0)


def test_dio_without_confirmed_input_fails_closed():
    outputs = []
    arm = type('Arm', (), {'dout': lambda _self, pin, on: outputs.append((pin, on))})()
    g = Rg2Gripper('dio', lambda _command: True, arm=arm, din_pins=(0,), dio_settle_s=0.0)
    ok, width, inferred = g.grip(15.5, 20.0, timeout_s=0.0)
    assert ok and width == -1.0 and inferred is False
    assert outputs == [(1, True), (2, False)]
