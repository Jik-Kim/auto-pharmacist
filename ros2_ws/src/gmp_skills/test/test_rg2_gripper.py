import math
from types import SimpleNamespace

import pytest

from gmp_skills.adapters.rg2_gripper import Rg2Gripper, joint_to_width_mm, width_mm_to_joint


@pytest.mark.parametrize('width_mm', [0.0, 15.5, 18.0, 28.0, 100.0])
def test_width_joint_round_trip(width_mm):
    assert joint_to_width_mm(width_mm_to_joint(width_mm)) == pytest.approx(width_mm, abs=0.01)


def test_modbus_width_is_tenth_mm_integer():
    sent = []
    g = Rg2Gripper('modbus', lambda command: sent.append(command) or True)
    g.on_native_status(SimpleNamespace(gsta=0, ggwd=1000, gwdf=1000), g._now())
    assert not g.move(28.04, timeout_s=0.0)  # 새 폭 표본이 없으면 성공으로 보지 않는다
    assert sent == ['280']


def test_virtual_width_is_joint_angle_string():
    sent = []
    g = Rg2Gripper('virtual', lambda command: sent.append(command) or True)
    assert not g.move(28.0, timeout_s=0.0)
    assert float(sent[0]) == pytest.approx(width_mm_to_joint(28.0), abs=0.0001)


def test_grip_inference_and_slip_latch(monkeypatch):
    clock = [1.0]
    g = Rg2Gripper('virtual', lambda _: True, now_fn=lambda: clock[0])
    g.on_joint_state(width_mm_to_joint(100.0), clock[0])
    def tick(dt):
        clock[0] += dt
        g.on_joint_state(width_mm_to_joint(18.0), clock[0])
    monkeypatch.setattr('gmp_skills.adapters.rg2_gripper.time.sleep', tick)
    ok, final_width, inferred = g.grip(15.5, 20.0, timeout_s=1.0)
    assert ok and inferred and final_width == pytest.approx(18.0, abs=0.01)
    g.on_joint_state(width_mm_to_joint(20.0), clock[0] + 0.1)
    assert g.state(now_s=clock[0] + 0.2)['grip_inferred'] is False
    assert g.consume_slip() is True
    assert g.consume_slip() is False


@pytest.mark.parametrize('starts_moving', [True, False])
def test_fresh_old_width_does_not_complete_command(monkeypatch, starts_moving):
    clock = [1.0]
    g = Rg2Gripper('virtual', lambda _: True, now_fn=lambda: clock[0])
    g.on_joint_state(width_mm_to_joint(100.0), clock[0])
    def tick(dt):
        clock[0] += dt
        width = 60.0 if starts_moving and clock[0] >= 1.3 else 100.0
        g.on_joint_state(width_mm_to_joint(width), clock[0])
    monkeypatch.setattr('gmp_skills.adapters.rg2_gripper.time.sleep', tick)
    ok, _, inferred = g.grip(60.0, 40.0, timeout_s=0.8)
    assert ok == starts_moving
    assert not inferred
    assert clock[0] >= (1.45 if starts_moving else 1.8)


def test_stale_joint_state_fails_closed():
    g = Rg2Gripper('virtual', lambda _command: True, now_fn=lambda: 2.0, state_timeout_s=0.5)
    g.on_joint_state(width_mm_to_joint(18.0), 1.0)
    state = g.state()
    assert state['busy'] is True
    assert state['grip_inferred'] is False


def test_force_command_uses_2_5_n_steps():
    sent = []
    g = Rg2Gripper('modbus', lambda command: sent.append(command) or True)
    g.on_native_status(SimpleNamespace(gsta=0, ggwd=1000, gwdf=1000), g._now())
    g.set_force(35.0)
    assert sent == ['d', 'd']
    assert math.isclose(g.force_cmd_n, 35.0)


def test_force_command_failure_stops_and_preserves_confirmed_level():
    sent = []
    g = Rg2Gripper('modbus', lambda command: bool(sent.append(command)))
    g.on_native_status(SimpleNamespace(gsta=0, ggwd=1000, gwdf=1000), g._now())
    assert not g.set_force(35.0)
    assert sent == ['d']
    assert math.isclose(g.force_cmd_n, 40.0)


def test_dio_without_confirmed_input_fails_closed():
    outputs = []
    arm = type('Arm', (), {'dout': lambda _self, pin, on: outputs.append((pin, on))})()
    g = Rg2Gripper('dio', lambda _command: True, arm=arm, din_pins=(0,), dio_settle_s=0.0)
    ok, width, inferred = g.grip(15.5, 20.0, timeout_s=0.0)
    assert not ok and width == -1.0 and inferred is False
    assert outputs == []


@pytest.mark.parametrize('busy_seen,grip', [(False, False), (True, False), (True, True)])
def test_native_status_requires_busy_cycle_and_uses_grip_bit(monkeypatch, busy_seen, grip):
    clock = [1.0]
    g = Rg2Gripper('modbus', lambda _: True, now_fn=lambda: clock[0])
    g.on_native_status(SimpleNamespace(gsta=0, ggwd=1043, gwdf=1043), clock[0])
    def tick(dt):
        clock[0] += dt
        busy = busy_seen and 1.3 <= clock[0] < 1.6
        width = 629 if clock[0] >= 1.6 else 1040
        g.on_native_status(SimpleNamespace(gsta=int(busy) | (2 if grip and clock[0]>=1.6 else 0), ggwd=width, gwdf=width), clock[0])
    monkeypatch.setattr('gmp_skills.adapters.rg2_gripper.time.sleep', tick)
    ok, width, inferred = g.grip(60., 40., timeout_s=1.)
    assert ok == busy_seen
    assert inferred == (busy_seen and grip)
    assert clock[0] >= 1.6


@pytest.mark.parametrize('flags', [1, 4, 8, 16, 32, 64])
def test_native_busy_or_safety_prevents_motion(flags):
    sent = []
    g = Rg2Gripper('modbus', lambda cmd: sent.append(cmd) or True, now_fn=lambda: 1.)
    g.on_native_status(SimpleNamespace(gsta=flags, ggwd=1000, gwdf=1000), 1.)
    assert not g.move(60.)
    assert not sent


def test_native_missing_status_prevents_motion():
    sent = []
    g = Rg2Gripper('modbus', lambda cmd: sent.append(cmd) or True)
    assert not g.move(60.)
    assert not sent


def test_force_steps_stop_when_safety_arrives():
    sent = []
    g = None
    def send(cmd):
        sent.append(cmd)
        g.on_native_status(SimpleNamespace(gsta=8, ggwd=1000, gwdf=1000), g._now())
        return True
    g = Rg2Gripper('modbus', send)
    g.on_native_status(SimpleNamespace(gsta=0, ggwd=1000, gwdf=1000), g._now())
    assert not g.set_force(20.)
    assert sent == ['d']
    assert g.force_cmd_n == 37.5


def test_native_idle_still_waits_for_width_settling(monkeypatch):
    clock = [1.0]
    g = Rg2Gripper('modbus', lambda _: True, now_fn=lambda: clock[0])
    g.on_native_status(SimpleNamespace(gsta=0, ggwd=1000, gwdf=960), 1.)
    def tick(dt):
        clock[0] += dt
        busy = 1.1 <= clock[0] < 1.3
        width = 700 - int((clock[0] - 1.0) * 100) if clock[0] < 1.7 else 630
        g.on_native_status(SimpleNamespace(gsta=int(busy), ggwd=width, gwdf=width-40), clock[0])
    monkeypatch.setattr('gmp_skills.adapters.rg2_gripper.time.sleep', tick)
    assert g.move(60., 2.)
    assert clock[0] >= 1.9
    assert g.width_mm() == 63.


def test_native_noop_waits_for_post_command_stability(monkeypatch):
    clock = [1.0]
    g = Rg2Gripper('modbus', lambda _: True, now_fn=lambda: clock[0])
    status = SimpleNamespace(gsta=0, ggwd=640, gwdf=600)
    g.on_native_status(status, .9)
    def tick(dt):
        clock[0] += dt
        g.on_native_status(status, clock[0])
    monkeypatch.setattr('gmp_skills.adapters.rg2_gripper.time.sleep', tick)
    assert g.move(60., 1.)
    assert clock[0] >= 1.2


@pytest.mark.parametrize('change', ['none', 'target', 'width', 'gap', 'safety', 'force'])
def test_repeat_offset_command_requires_unchanged_success(monkeypatch, change):
    clock = [1.0]
    phase = [0]
    g = Rg2Gripper('modbus', lambda _: True, now_fn=lambda: clock[0])
    idle = SimpleNamespace(gsta=0, ggwd=618, gwdf=578)
    g.on_native_status(SimpleNamespace(gsta=0, ggwd=840, gwdf=800), clock[0])
    def tick(dt):
        clock[0] += dt
        if phase[0] == 0 and clock[0] < 1.1:
            g.on_native_status(SimpleNamespace(gsta=1, ggwd=700, gwdf=660), clock[0])
        else:
            g.on_native_status(idle, clock[0])
    monkeypatch.setattr('gmp_skills.adapters.rg2_gripper.time.sleep', tick)
    assert g.move(60., 1.)
    phase[0] = 1
    if change == 'width':
        g.on_native_status(SimpleNamespace(gsta=0, ggwd=620, gwdf=580), clock[0])
    elif change == 'gap':
        clock[0] += 1.
        g.on_native_status(idle, clock[0])
    elif change == 'safety':
        g.on_native_status(SimpleNamespace(gsta=8, ggwd=618, gwdf=578), clock[0])
        g.on_native_status(idle, clock[0])
    elif change == 'force':
        g.force_cmd_n = 30.
    assert g.move(65. if change == 'target' else 60., .6) == (change == 'none')
