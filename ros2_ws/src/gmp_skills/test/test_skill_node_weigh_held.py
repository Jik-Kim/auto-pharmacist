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
        class Goal:
            ABOVE = 0
            AT = 1

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
        'dsr_msgs2.msg': _module('dsr_msgs2.msg', RobotError=Message),
        'sensor_msgs': _module('sensor_msgs'),
        'sensor_msgs.msg': _module('sensor_msgs.msg', JointState=Message),
        'onrobot_rg_msgs': _module('onrobot_rg_msgs'),
        'onrobot_rg_msgs.msg': _module('onrobot_rg_msgs.msg', OnRobotRGInput=Message),
        'onrobot_rg_msgs.srv': _module('onrobot_rg_msgs.srv', SetCommand=Service),
        'gmp_interfaces': _module('gmp_interfaces'),
        'gmp_interfaces.action': _module(
            'gmp_interfaces.action',
            MoveToStation=Interface,
            ReturnMaterial=Interface,
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
            RecoverSafety=Service,
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
        _held_material_id='',
        stations=SimpleNamespace(get=lambda _: SimpleNamespace(extra={'material_id': 'A'})),
        _motion_anchor=SimpleNamespace(
            station='scoop_1', approach=1, pose=(1.0,) * 6, joints=(2.0,) * 6),
        arm=SimpleNamespace(current_posx=lambda: [1.0] * 6, current_posj=lambda: [2.0] * 6),
        _pose_matches=lambda actual, target: actual == list(target),
        joint_tolerance=1.0,
    )
    job = skill_node.Job('grip', {
        'close': True, 'width_mm': 15.5, 'force_n': 20.0, 'timeout_s': 3.0,
    })

    result = skill_node.SkillNode._do_grip(node, job)

    assert result == (True, 15.5, True)
    assert node._pending_scoop_extract is True
    assert node._held_material_id == 'A'


def test_scoop_grip_with_stale_anchor_does_not_assign_material(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    node = SimpleNamespace(
        gripper=SimpleNamespace(grip=lambda *_: (True, 15.5, True)),
        _station_id='scoop_1',
        _pending_scoop_extract=False,
        _scoop_extract_uncertain=False,
        _held_material_id='',
        stations=SimpleNamespace(get=lambda _: SimpleNamespace(extra={'material_id': 'A'})),
        _motion_anchor=SimpleNamespace(
            station='scoop_1', approach=0, pose=(1.0,) * 6, joints=(2.0,) * 6),
        arm=SimpleNamespace(current_posx=lambda: [1.0] * 6, current_posj=lambda: [2.0] * 6),
        _pose_matches=lambda actual, target: actual == list(target),
        joint_tolerance=1.0,
    )

    skill_node.SkillNode._do_grip(node, skill_node.Job('grip', {
        'close': True, 'width_mm': 15.5, 'force_n': 20.0, 'timeout_s': 3.0,
    }))

    assert node._pending_scoop_extract is False
    assert node._held_payload == 'unknown'
    assert node._held_material_id == ''


@pytest.mark.parametrize('lift_mm', [100.0, 125.0])
def test_weigh_held_extracts_then_lifts_before_matching_material(monkeypatch, lift_mm):
    skill_node = _load_skill_node(monkeypatch)
    moves = []
    phases = []
    reading = object()
    material = SimpleNamespace(
        station_id='material_1', posx=[400.0, -298.0, 200.0, 90.0, -180.0, -90.0])
    node = SimpleNamespace(
        gripper=SimpleNamespace(state=lambda _: {'grip_inferred': True}),
        arm=SimpleNamespace(
            current_posx=lambda: moves[-1][0] if moves else [400.0, -298.0, 50.0, 90.0, -180.0, -90.0],
            movel=lambda target, scale: moves.append((list(target), scale)),
        ),
        stations=SimpleNamespace(for_material=lambda material_id: material),
        vel_scale=0.3,
        scoop_extract_y_mm=150.0,
        scoop_extract_lift_z_mm=lift_mm,
        _pending_scoop_extract=True,
        _scoop_extract_uncertain=False,
        _station_id='scoop_1',
        _held_payload='scoop',
        _held_material_id='A',
        _now_s=lambda: 0.0,
        _measure_weight_reading=lambda tare_g, subject, station_id: (
            reading if (tare_g, subject, station_id) == (12.0, 'scoop', 'material_1') else None),
    )
    job = skill_node.Job('weigh_held', {'tare_g': 12.0}, feedback=phases.append)

    result = skill_node.SkillNode._do_weigh_held(node, job)

    assert result is reading
    assert moves == [
        ([400.0, -148.0, 50.0, 90.0, -180.0, -90.0], 0.3),
        ([400.0, -148.0, 50.0 + lift_mm, 90.0, -180.0, -90.0], 0.3),
        (material.posx, 0.3),
    ]
    assert phases == ['LIFT', 'SETTLE', 'MEASURE']
    assert node._pending_scoop_extract is False
    assert node._scoop_extract_uncertain is False
    assert node._station_id == 'material_1'

    # 같은 파지의 후속 계량은 인출·상승을 반복하지 않는다.
    moves.clear()
    assert skill_node.SkillNode._do_weigh_held(node, job) is reading
    assert moves == [(material.posx, 0.3)]


def test_weigh_held_rejects_empty_gripper(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    node = SimpleNamespace(
        gripper=SimpleNamespace(state=lambda _: {'grip_inferred': False}),
        _scoop_extract_uncertain=False,
        _held_payload='scoop',
        _held_material_id='A',
        _now_s=lambda: 0.0,
    )

    with pytest.raises(RuntimeError, match='스쿱 파지'):
        skill_node.SkillNode._do_weigh_held(
            node, skill_node.Job('weigh_held', {'tare_g': 0.0}))


def test_weigh_held_rejects_unknown_material_history_before_motion(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    moves = []
    node = SimpleNamespace(
        gripper=SimpleNamespace(state=lambda _: {'busy': False, 'grip_inferred': True}),
        _scoop_extract_uncertain=False,
        _held_payload='scoop',
        _held_material_id='',
        _now_s=lambda: 0.0,
        arm=SimpleNamespace(movel=lambda *args: moves.append(args)),
    )

    with pytest.raises(RuntimeError, match='원료 ID'):
        skill_node.SkillNode._do_weigh_held(
            node, skill_node.Job('weigh_held', {'tare_g': 0.0}))
    assert moves == []


def test_weigh_held_cancel_after_material_move_does_not_measure(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    moves = []
    measured = []
    material = SimpleNamespace(
        station_id='material_1', posx=[400.0, -298.0, 200.0, 90.0, -180.0, -90.0])
    job = skill_node.Job('weigh_held', {'tare_g': 0.0})

    def move(target, _scale):
        moves.append(list(target))
        job.cancel = True

    node = SimpleNamespace(
        gripper=SimpleNamespace(state=lambda _: {'busy': False, 'grip_inferred': True}),
        _scoop_extract_uncertain=False,
        _pending_scoop_extract=False,
        _held_payload='scoop',
        _held_material_id='A',
        _now_s=lambda: 0.0,
        stations=SimpleNamespace(for_material=lambda _: material),
        arm=SimpleNamespace(movel=move),
        vel_scale=0.3,
        _measure_weight_reading=lambda *_: measured.append(True),
    )

    with pytest.raises(RuntimeError, match='cancelled'):
        skill_node.SkillNode._do_weigh_held(node, job)
    assert moves == [material.posx]
    assert measured == []


def test_scoop_rejects_mismatched_material_before_motion(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    moves = []
    node = SimpleNamespace(
        _require_scoop_extracted=lambda: None,
        _held_payload='scoop',
        _held_material_id='A',
        _now_s=lambda: 0.0,
        gripper=SimpleNamespace(state=lambda _: {'busy': False, 'grip_inferred': True}),
        arm=SimpleNamespace(movel=lambda *args: moves.append(args)),
    )

    with pytest.raises(RuntimeError, match='원료 ID가 다르다'):
        skill_node.SkillNode._do_scoop(
            node, skill_node.Job('scoop', {'material_id': 'B'}))
    assert moves == []


def test_other_motion_is_rejected_before_pending_extraction(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    node = SimpleNamespace(
        _pending_scoop_extract=True,
        _scoop_extract_uncertain=False,
        _held_payload='scoop',
        _held_material_id='A',
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
        scoop_extract_lift_z_mm=100.0,
        _pending_scoop_extract=True,
        _scoop_extract_uncertain=False,
        _held_payload='scoop',
        _held_material_id='A',
        stations=SimpleNamespace(for_material=lambda _: SimpleNamespace(
            station_id='material_1', posx=[400.0, -298.0, 200.0, 90.0, -180.0, -90.0])),
        _now_s=lambda: 0.0,
    )
    job = skill_node.Job('weigh_held', {'tare_g': 0.0})

    with pytest.raises(RuntimeError, match='controller response lost'):
        skill_node.SkillNode._do_weigh_held(node, job)
    assert node._pending_scoop_extract is False
    assert node._scoop_extract_uncertain is True

    with pytest.raises(RuntimeError, match='불확실'):
        skill_node.SkillNode._do_weigh_held(node, job)


def test_container_weigh_grips_at_and_measures_at_above(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    moves = []
    commands = []
    reading = object()
    at = [423.0, 93.0, 100.0, 90.0, -90.0, -90.0]
    above = [423.0, 93.0, 200.0, 90.0, -90.0, -90.0]
    workbench = SimpleNamespace(posx=at, above=lambda _: above, extra={})
    node = SimpleNamespace(
        _require_scoop_extracted=lambda: None,
        get_parameter=lambda name: SimpleNamespace(value={
            'scale.simulated': False,
            'gripper.cup_width_mm': 30.0,
            'gripper.force_n': 20.0,
        }[name]),
        stations=SimpleNamespace(approach_mm=60.0, get=lambda _: workbench),
        arm=SimpleNamespace(
            movel=lambda target, scale: moves.append((list(target), scale)),
            reset_workpiece=lambda: commands.append('reset'),
        ),
        gripper=SimpleNamespace(
            grip=lambda *_: (True, 20.0, True),
            release=lambda _: commands.append('release'),
        ),
        vel_scale=0.3,
        _measure_weight_reading=lambda tare_g, subject: (
            reading if (tare_g, subject) == (8.0, 'container') else None),
    )

    assert skill_node.SkillNode._do_weigh(node, skill_node.Job('weigh', {'tare_g': 8.0})) is reading
    assert [pose for pose, _ in moves] == [above, at, above, at, above]
    assert commands == ['reset', 'release']


def _held_scoop_node(skill_node, station):
    moves = []
    holds = []
    node = SimpleNamespace(
        _require_scoop_extracted=lambda: None,
        _held_payload='scoop',
        _held_material_id='A',
        _now_s=lambda: 0.0,
        gripper=SimpleNamespace(state=lambda _: {'busy': False, 'grip_inferred': True}),
        stations=SimpleNamespace(get=lambda _: station, for_material=lambda _: station),
        arm=SimpleNamespace(movel=lambda target, scale: moves.append((list(target), scale))),
        motion_timeout_s=30.0,
        vel_scale=0.3,
        get_parameter=lambda _: SimpleNamespace(value=0.5),
        _wait_with_nudge=lambda duration, job: holds.append((duration, job.kind)),
    )
    node.arm.movej_cancellable = lambda target, scale, cancel, timeout: moves.append((list(target), scale))
    return node, moves, holds


def test_pour_uses_taught_start_end_and_restores_start(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    start = [420.02, 220.0, 233.15, 90.0, 180.0, -90.0]
    end = [420.0, 160.0, 315.0, 90.0, 130.0, -90.0]
    station = SimpleNamespace(station_id='workbench', extra={
        'pour_start_posx': start, 'pour_end_posx': end,
    })
    node, moves, holds = _held_scoop_node(skill_node, station)
    phases = []

    assert skill_node.SkillNode._do_pour(
        node, skill_node.Job('pour', {'fraction': 1.0}, feedback=phases.append)) is True
    assert [pose for pose, _ in moves] == [start, end, start]
    assert holds == [(0.5, 'pour')]
    assert phases == ['APPROACH', 'TILT', 'HOLD', 'RETURN']


def test_partial_pour_is_rejected_without_motion(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    station = SimpleNamespace(station_id='workbench', extra={
        'pour_start_posx': [1.0] * 6, 'pour_end_posx': [2.0] * 6,
    })
    node, moves, _ = _held_scoop_node(skill_node, station)

    with pytest.raises(ValueError, match='fraction=1.0'):
        skill_node.SkillNode._do_pour(node, skill_node.Job('pour', {'fraction': 0.5}))
    assert moves == []


@pytest.mark.parametrize('kind', ['pour', 'return_material'])
def test_cancelled_pour_or_return_never_starts_motion(monkeypatch, kind):
    skill_node = _load_skill_node(monkeypatch)
    station = SimpleNamespace(station_id='material_1', extra={
        'pour_start_posx': [1.0] * 6,
        'pour_end_posx': [2.0] * 6,
        'return_start_posx': [3.0] * 6,
        'return_end_posj': [4.0] * 6,
    })
    node, moves, _ = _held_scoop_node(skill_node, station)
    job = skill_node.Job(kind, {'fraction': 1.0} if kind == 'pour' else {'material_id': 'A'}, cancel=True)

    with pytest.raises(RuntimeError, match='cancelled'):
        getattr(skill_node.SkillNode, f'_do_{kind}')(node, job)
    assert moves == []


@pytest.mark.parametrize('kind', ['pour', 'return_material'])
def test_end_move_failure_does_not_issue_restore_motion(monkeypatch, kind):
    skill_node = _load_skill_node(monkeypatch)
    start = [1.0] * 6 if kind == 'pour' else [3.0] * 6
    end = [2.0] * 6 if kind == 'pour' else [4.0] * 6
    station = SimpleNamespace(station_id='material_1', extra={
        'pour_start_posx': [1.0] * 6,
        'pour_end_posx': [2.0] * 6,
        'return_start_posx': [3.0] * 6,
        'return_end_posj': [4.0] * 6,
    })
    node, moves, _ = _held_scoop_node(skill_node, station)

    def fail_at_end(target, scale):
        moves.append((list(target), scale))
        if list(target) == end:
            raise RuntimeError('end move failed')

    node.arm.movel = fail_at_end
    node.arm.movej_cancellable = lambda target, scale, cancel, timeout: fail_at_end(target, scale)
    job = skill_node.Job(kind, {'fraction': 1.0} if kind == 'pour' else {'material_id': 'A'})

    with pytest.raises(RuntimeError, match='end move failed'):
        getattr(skill_node.SkillNode, f'_do_{kind}')(node, job)
    assert [pose for pose, _ in moves] == [start, end]


def test_return_without_taught_poses_is_rejected_before_motion(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    station = SimpleNamespace(station_id='material_1', extra={})
    node, moves, _ = _held_scoop_node(skill_node, station)

    with pytest.raises(ValueError, match='return_start_posx'):
        skill_node.SkillNode._do_return_material(
            node, skill_node.Job('return_material', {'material_id': 'A'}))
    assert moves == []


def test_return_uses_joint_end_without_restoring_start(monkeypatch):
    skill_node = _load_skill_node(monkeypatch)
    start = [400.0, -298.0, 240.0, 90.0, -180.0, -90.0]
    end = [400.0, -298.0, 180.0, 90.0, -140.0, -90.0]
    station = SimpleNamespace(station_id='material_1', extra={
        'return_start_posx': start, 'return_end_posj': end,
    })
    node, moves, holds = _held_scoop_node(skill_node, station)

    joint_moves = []
    node.arm.movej_cancellable = lambda target, scale, cancel, timeout: (joint_moves.append(list(target)), moves.append((list(target), scale)))
    assert skill_node.SkillNode._do_return_material(
        node, skill_node.Job('return_material', {'material_id': 'A'})) is True
    assert [pose for pose, _ in moves] == [start, end]
    assert holds == [(0.5, 'return_material')]

    assert joint_moves == [end]


@pytest.mark.parametrize('period', [0, -1, float('nan'), float('inf')])
def test_scale_period_rejects_invalid_parameter(monkeypatch, period):
    module = _load_skill_node(monkeypatch)
    node = SimpleNamespace(get_parameter=lambda _: SimpleNamespace(value=period))
    with pytest.raises(ValueError, match='scale.period_s'):
        module.SkillNode._scale_period_s(node)


@pytest.mark.parametrize('entry', ['service', 'tool_force', 'workpiece', 'simulated'])
def test_sampling_parameter_reaches_all_measurement_paths(monkeypatch, entry):
    module = _load_skill_node(monkeypatch)
    calls = []
    params = {'scale.period_s': 0.82, 'scale.samples': 3, 'scale.settle_s': 0.2,
              'scale.method': 'workpiece' if entry == 'workpiece' else 'tool_force',
              'scale.simulated': entry == 'simulated', 'scale.gain': 1,
              'scale.offset_g': 0,
              'scale.max_std_g': 10, 'scale.fz_sign': -1}
    node = SimpleNamespace(
        get_parameter=lambda name: SimpleNamespace(value=params[name]),
        _observe_force=lambda _: None,
        get_clock=lambda: SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: None)),
        arm=SimpleNamespace(
            measure_force=lambda *a, **kw: (calls.append((a, kw)) or ([0]*6, 2, 0, True)),
            measure_workpiece=lambda *a, **kw: (calls.append((a, kw)) or (2, 0, True))))
    node._scale_period_s = lambda: module.SkillNode._scale_period_s(node)
    monkeypatch.setattr(module, 'ScaleConfig', lambda **kw: kw)
    monkeypatch.setattr(module, 'WeightModel', lambda _: SimpleNamespace(
        set_tare=lambda _: None, reading=lambda mean, std, valid: (mean, 0, mean, std, valid)))
    if entry == 'service':
        module.SkillNode._do_measure(node, module.Job('measure', {'samples': 3, 'settle_s': 0.2}))
    else:
        module.SkillNode._measure_weight_reading(node, 0, 'scoop')
    if entry == 'simulated':
        assert calls == []
    else:
        assert calls == [((3, 0.2), {'period_s': 0.82, 'observer': node._observe_force})]


@pytest.mark.parametrize('end', [None, [1.0] * 5, [float('nan')] * 6])
def test_return_invalid_joint_end_rejected_before_approach(monkeypatch, end):
    skill_node = _load_skill_node(monkeypatch)
    station = SimpleNamespace(station_id='material_1', extra={
        'return_start_posx': [1.0] * 6, 'return_end_posj': end})
    node, moves, _ = _held_scoop_node(skill_node, station)
    with pytest.raises(ValueError, match='return_end_posj'):
        skill_node.SkillNode._do_return_material(
            node, skill_node.Job('return_material', {'material_id': 'A'}))
    assert moves == []


def test_return_cancel_during_joint_move_skips_hold_and_restore(monkeypatch):
    module = _load_skill_node(monkeypatch)
    station = SimpleNamespace(station_id='material_1', extra={
        'return_start_posx': [1.0] * 6, 'return_end_posj': [2.0] * 6})
    node, moves, holds = _held_scoop_node(module, station)
    job = module.Job('return_material', {'material_id': 'A'})
    def cancel_move(target, scale, cancelled, timeout):
        job.cancel = True
        assert cancelled()
        raise RuntimeError('cancelled')
    node.arm.movej_cancellable = cancel_move
    with pytest.raises(RuntimeError, match='cancelled'):
        module.SkillNode._do_return_material(node, job)
    assert len(moves) == 1
    assert holds == []


@pytest.mark.parametrize('outcome', ['success', 'cancel', 'failure', 'hold_failure'])
def test_return_joint_attempt_blocks_followup_scoop_before_any_motion(monkeypatch, outcome):
    module = _load_skill_node(monkeypatch)
    station = SimpleNamespace(station_id='material_1', extra={
        'return_start_posx': [1.0] * 6, 'return_end_posj': [2.0] * 6})
    node, moves, _ = _held_scoop_node(module, station)
    job = module.Job('return_material', {'material_id': 'A'})
    def joint_move(target, scale, cancelled, timeout):
        assert node._return_rescoop_blocked
        moves.append((list(target), scale))
        if outcome == 'cancel':
            job.cancel = True
            raise RuntimeError('cancelled')
        if outcome == 'failure':
            raise RuntimeError('joint failure')
    node.arm.movej_cancellable = joint_move
    if outcome == 'hold_failure':
        def fail_hold(*args):
            raise RuntimeError('hold failure')
        node._wait_with_nudge = fail_hold
    if outcome == 'success':
        assert module.SkillNode._do_return_material(node, job)
    else:
        with pytest.raises(RuntimeError):
            module.SkillNode._do_return_material(node, job)
    before = list(moves)
    for material_id in ('A', 'B'):
        with pytest.raises(RuntimeError, match='재스쿱 연결 경로 미구현'):
            module.SkillNode._do_scoop(node, module.Job('scoop', {'material_id': material_id}))
    assert moves == before


def test_return_rejected_before_motion_does_not_set_rescoop_guard(monkeypatch):
    module = _load_skill_node(monkeypatch)
    station = SimpleNamespace(station_id='material_1', extra={
        'return_start_posx': [1.0] * 6, 'return_end_posj': None})
    node, moves, _ = _held_scoop_node(module, station)
    node._return_rescoop_blocked = False
    with pytest.raises(ValueError):
        module.SkillNode._do_return_material(node, module.Job('return_material', {'material_id': 'A'}))
    assert not node._return_rescoop_blocked
    assert moves == []


@pytest.mark.parametrize('failure_step', [1, 2])
@pytest.mark.parametrize('cancel', [False, True])
def test_extract_or_lift_failure_never_continues_to_material(monkeypatch, failure_step, cancel):
    module = _load_skill_node(monkeypatch)
    moves = []
    pose = [344.0, -298.0, 50.0, 90.0, -180.0, -90.0]
    job = module.Job('weigh_held', {'tare_g': 0.0})
    def move(target, scale):
        moves.append(list(target))
        pose[:] = target
        if len(moves) == failure_step:
            if cancel:
                job.cancel = True
            else:
                raise RuntimeError('motion failed')
    node = SimpleNamespace(
        gripper=SimpleNamespace(state=lambda _: {'grip_inferred': True}),
        arm=SimpleNamespace(current_posx=lambda: list(pose), movel=move),
        stations=SimpleNamespace(for_material=lambda _: SimpleNamespace(
            station_id='material_1', posx=[344.0, -298.0, 200.0, 90.0, -180.0, -90.0])),
        vel_scale=0.2, scoop_extract_y_mm=150.0, scoop_extract_lift_z_mm=100.0,
        _pending_scoop_extract=True, _scoop_extract_uncertain=False,
        _held_payload='scoop', _held_material_id='A', _now_s=lambda: 0.0,
        _measure_weight_reading=lambda *args: pytest.fail('실패 후 계량하면 안 됨'))
    with pytest.raises(RuntimeError, match='cancelled|motion failed'):
        module.SkillNode._do_weigh_held(node, job)
    assert len(moves) == failure_step
    assert node._scoop_extract_uncertain
    job.cancel = False
    with pytest.raises(RuntimeError, match='불확실'):
        module.SkillNode._do_weigh_held(node, job)
    assert len(moves) == failure_step


@pytest.mark.parametrize('height', [0.0, -100.0, float('nan'), float('inf')])
def test_invalid_extract_lift_height_rejected_before_first_motion(monkeypatch, height):
    module = _load_skill_node(monkeypatch)
    node = SimpleNamespace(
        gripper=SimpleNamespace(state=lambda _: {'grip_inferred': True}),
        stations=SimpleNamespace(for_material=lambda _: object()),
        arm=SimpleNamespace(current_posx=lambda: pytest.fail('설정 검증 전 장치 조회')),
        scoop_extract_lift_z_mm=height, _pending_scoop_extract=True,
        _scoop_extract_uncertain=False, _held_payload='scoop', _held_material_id='A',
        _now_s=lambda: 0.0)
    with pytest.raises(ValueError, match='유한한 양수'):
        module.SkillNode._do_weigh_held(node, module.Job('weigh_held', {'tare_g': 0.0}))
    assert node._pending_scoop_extract
