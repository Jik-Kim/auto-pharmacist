import importlib
import sys
import types
from types import SimpleNamespace

import pytest


def _module(name, **members):
    module = types.ModuleType(name)
    for key, value in members.items():
        setattr(module, key, value)
    return module


def _load_skill_node(monkeypatch):
    class Interface:
        class Feedback:
            pass

        class Result:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

    class Message:
        INFO = 1
        WARN = 2
        ERROR = 3

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.header = SimpleNamespace(stamp=None)

    class Service:
        pass

    fake_modules = {
        'rclpy': _module('rclpy', ok=lambda: False),
        'rclpy.action': _module(
            'rclpy.action',
            ActionServer=object,
            CancelResponse=SimpleNamespace(ACCEPT=1),
            GoalResponse=SimpleNamespace(ACCEPT=1),
        ),
        'rclpy.callback_groups': _module(
            'rclpy.callback_groups', ReentrantCallbackGroup=object),
        'rclpy.executors': _module(
            'rclpy.executors', MultiThreadedExecutor=object),
        'rclpy.node': _module('rclpy.node', Node=object),
        'rclpy.qos': _module(
            'rclpy.qos',
            QoSProfile=lambda **_: None,
            ReliabilityPolicy=SimpleNamespace(BEST_EFFORT=1),
        ),
        'sensor_msgs': _module('sensor_msgs'),
        'sensor_msgs.msg': _module('sensor_msgs.msg', JointState=Message),
        'onrobot_rg_msgs': _module('onrobot_rg_msgs'),
        'onrobot_rg_msgs.srv': _module('onrobot_rg_msgs.srv', SetCommand=Service),
        'gmp_interfaces': _module('gmp_interfaces'),
        'gmp_interfaces.action': _module(
            'gmp_interfaces.action',
            MoveToStation=Interface,
            Scoop=Interface,
            Pour=Interface,
            WeighContainer=Interface,
            WeighHeld=Interface,
        ),
        'gmp_interfaces.msg': _module(
            'gmp_interfaces.msg',
            CellEvent=Message,
            GripperState=Message,
            WeightReading=Message,
        ),
        'gmp_interfaces.srv': _module(
            'gmp_interfaces.srv',
            MeasureForce=Service,
            SafePose=Service,
            SetGripper=Service,
        ),
        'gmp_dosing': _module('gmp_dosing'),
        'gmp_dosing.core': _module('gmp_dosing.core'),
        'gmp_dosing.core.scale': _module(
            'gmp_dosing.core.scale', ScaleConfig=object, WeightModel=object),
        'gmp_skills.adapters.dsr_arm': _module(
            'gmp_skills.adapters.dsr_arm', DsrArm=object),
        'gmp_skills.adapters.rg2_gripper': _module(
            'gmp_skills.adapters.rg2_gripper', Rg2Gripper=object),
    }
    for name, module in fake_modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules.pop('gmp_skills.nodes.skill_node', None)
    return importlib.import_module('gmp_skills.nodes.skill_node')


def test_scoop_grip_marks_extraction_pending(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    node = SimpleNamespace(
        gripper=SimpleNamespace(grip=lambda *_: (True, 15.5, True)),
        _station_id='scoop_1',
        _pending_scoop_extract=False,
        _scoop_extract_uncertain=False,
    )
    job = skill_node.Job('grip', {
        'close': True, 'width_mm': 15.5, 'force_n': 20.0, 'timeout_s': 3.0,
    })

    result = skill_node.SkillNode._do_grip(node, job)

    assert result == (True, 15.5, True)
    assert node._pending_scoop_extract is True


def test_weigh_held_extracts_scoop_plus_y_before_workbench(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    moves = []
    phases = []
    reading = object()
    workbench = SimpleNamespace(posx=[420.0, 220.0, 233.15, 90.0, 180.0, -90.0])
    node = SimpleNamespace(
        gripper=SimpleNamespace(state=lambda _: {'grip_inferred': True}),
        arm=SimpleNamespace(
            current_posx=lambda: [400.0, -298.0, 50.0, 90.0, -180.0, -90.0],
            movel=lambda target, scale: moves.append((list(target), scale)),
        ),
        stations=SimpleNamespace(get=lambda station_id: workbench),
        vel_scale=0.3,
        scoop_extract_y_mm=150.0,
        _pending_scoop_extract=True,
        _scoop_extract_uncertain=False,
        _station_id='scoop_1',
        _now_s=lambda: 0.0,
        _measure_weight_reading=lambda tare_g, subject: (
            reading if (tare_g, subject) == (12.0, 'scoop') else None),
    )
    job = skill_node.Job('weigh_held', {'tare_g': 12.0}, feedback=phases.append)

    result = skill_node.SkillNode._do_weigh_held(node, job)

    assert result is reading
    assert moves == [
        ([400.0, -148.0, 50.0, 90.0, -180.0, -90.0], 0.3),
        (workbench.posx, 0.3),
    ]
    assert phases == ['LIFT', 'SETTLE', 'MEASURE']
    assert node._pending_scoop_extract is False
    assert node._scoop_extract_uncertain is False
    assert node._station_id == 'workbench'


def test_weigh_held_rejects_empty_gripper(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    node = SimpleNamespace(
        gripper=SimpleNamespace(state=lambda _: {'grip_inferred': False}),
        _scoop_extract_uncertain=False,
        _now_s=lambda: 0.0,
    )

    with pytest.raises(RuntimeError, match='스쿱 파지'):
        skill_node.SkillNode._do_weigh_held(
            node, skill_node.Job('weigh_held', {'tare_g': 0.0}))


def test_other_motion_is_rejected_before_pending_extraction(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    node = SimpleNamespace(
        _pending_scoop_extract=True,
        _scoop_extract_uncertain=False,
    )
    node._require_scoop_extracted = lambda: (
        skill_node.SkillNode._require_scoop_extracted(node))

    with pytest.raises(RuntimeError, match='WeighHeld'):
        skill_node.SkillNode._do_move(
            node, skill_node.Job('move', {'station_id': 'workbench'}))


def test_failed_extraction_is_not_automatically_retried(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)

    def fail_move(_target, _scale):
        raise RuntimeError('controller response lost')

    node = SimpleNamespace(
        gripper=SimpleNamespace(state=lambda _: {'grip_inferred': True}),
        arm=SimpleNamespace(
            current_posx=lambda: [400.0, -298.0, 50.0, 90.0, -180.0, -90.0],
            movel=fail_move,
        ),
        vel_scale=0.3,
        scoop_extract_y_mm=150.0,
        _pending_scoop_extract=True,
        _scoop_extract_uncertain=False,
        _now_s=lambda: 0.0,
    )
    job = skill_node.Job('weigh_held', {'tare_g': 0.0})

    with pytest.raises(RuntimeError, match='controller response lost'):
        skill_node.SkillNode._do_weigh_held(node, job)
    assert node._pending_scoop_extract is False
    assert node._scoop_extract_uncertain is True

    with pytest.raises(RuntimeError, match='불확실'):
        skill_node.SkillNode._do_weigh_held(node, job)
