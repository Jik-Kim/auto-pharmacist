"""9/23 실물 DRL의 고정 경로·DI 완료 및 실패 후 중단 회귀 검사."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from gmp_skills.adapters.rg2_gripper import Rg2Gripper
from gmp_skills.core.stations import StationTable
from test_check_depth import depth_node
from test_skill_node_weigh_held import _load_skill_node


@pytest.fixture
def dio(monkeypatch):
    clock, inputs, outputs = [0.], [False, False], []
    arm = SimpleNamespace(din=lambda pin: inputs[pin-1],
                          dout=lambda pin, on: outputs.append((pin, on)))
    gripper = Rg2Gripper('dio', lambda _: pytest.fail('DIO에서 Modbus 명령 금지'),
                         arm=arm, din_pins=(1, 2), now_fn=lambda: clock[0], dio_settle_s=.8)
    monkeypatch.setattr('gmp_skills.adapters.rg2_gripper.time.sleep',
                        lambda dt: clock.__setitem__(0, clock[0]+dt))
    return gripper, clock, inputs, outputs


def test_cup_60mm_is_close_without_force_command(dio):
    g, _, pins, outputs = dio
    pins[:] = [True, False]
    assert g.grip(60, 20, 3) == (True, -1., True)
    assert outputs == [(1, True), (2, False)]
    assert g.force_cmd_n == 0 and g.state()['grip_inferred']
    assert not g.busy()


def test_scoop_needs_both_inputs_and_settle(dio):
    g, clock, pins, _ = dio
    pins[:] = [True, False]
    assert g.grip(0, 20, 1, scoop=True) == (False, -1., False)
    pins[1] = True
    start = clock[0]
    assert g.grip(0, 20, 2, scoop=True) == (True, -1., True)
    assert clock[0]-start >= .8
    clock[0] += 1
    assert g.state()['busy'] and not g.state()['grip_inferred']
    g.refresh_dio()
    assert g.state()['grip_inferred']
    pins[1] = False
    g.refresh_dio()
    assert not g.state()['grip_inferred']


def test_release_waits_for_di1_off(dio):
    g, _, pins, outputs = dio
    pins[0] = True
    assert not g.release(.1)
    assert not g.state()['open_confirmed']
    pins[0] = False
    assert g.release(.1)
    assert g.state()['open_confirmed']
    assert outputs[-2:] == [(1, False), (2, True)]


def test_cancel_does_not_send_outputs_or_reuse_grip(dio):
    g, _, pins, outputs = dio
    pins[:] = [True, True]
    g.cancel_requested = lambda: True
    with pytest.raises(RuntimeError, match='cancelled'):
        g.grip(0, 20, 3, scoop=True)
    assert outputs == []
    assert not g.state()['grip_inferred']


def fixed_node(monkeypatch, material='A'):
    node, job, calls, _ = depth_node(monkeypatch, material)
    job.args['depth_fraction'] = 1.
    pose = list(node.stations.for_material(material).posx)
    node.arm.current_posx = lambda: list(pose)
    def move(target, *args, **kwargs):
        calls.append(('L', list(target)))
        pose[:] = target
        kwargs.get('observer', lambda: None)()
    def spline(points, *args, **kwargs):
        calls.append(('S', points))
        pose[:] = points[-1]
        kwargs.get('observer', lambda: None)()
    node.arm.movel_cancellable = move
    node.arm.movesx_cancellable = spline
    node.arm.amove_periodic = lambda *a, **kw: calls.append(('P', a))
    node.arm.wait_motion_cancellable = lambda *a, **kw: kw['observer']()
    node.arm.stop_motion = lambda: calls.append(('STOP',))
    return node, job, calls


@pytest.mark.parametrize('material,x,amp', [('A',298,14), ('B',395,12), ('C',490,14)])
def test_fixed_path_ignores_uncalibrated_height_without_faking_contact(monkeypatch, material, x, amp):
    node, job, calls = fixed_node(monkeypatch, material)
    result = node._do_scoop(job)
    assert not node.stations.scooping[material]['calibrated']
    assert [c[0] for c in calls] == ['S', 'L', 'P', 'L']
    assert calls[0][1] == [[x,-255,200,90,141.62,-90], [x,-280,165,90,145.79,-90],
                          [x,-306,136,90,163.59,-90], [x,-341,124,90,171.59,-90],
                          [x,-347,141,90,-176.37,-90]]
    assert calls[2][1][0] == [amp,15,0,0,0,0]
    assert calls[-1][1] == [x,-298,200,90,-180,-90]
    assert result['contact_detected'] is False
    assert result['max_contact_force_n'] == result['insertion_depth_mm'] == 0
    assert result['message'].startswith('TAUGHT_FIXED:')


@pytest.mark.parametrize('fault', ['fraction', 'verified', 'cancel', 'pose', 'speed', 'points'])
def test_fixed_bad_request_never_moves(monkeypatch, fault):
    node, job, calls = fixed_node(monkeypatch)
    profile = node.stations.scooping['A']['fixed_path']
    if fault == 'fraction': job.args['depth_fraction'] = .5
    elif fault == 'verified': profile['verified'] = False
    elif fault == 'cancel': job.cancel = True
    elif fault == 'pose': node.arm.current_posx = lambda: [0]*6
    elif fault == 'speed': profile['velocity'][0] = -1
    else: profile['waypoints_base'].pop()
    with pytest.raises((ValueError, RuntimeError)):
        node._do_scoop(job)
    assert not calls


def test_fixed_spline_failure_never_shakes(monkeypatch):
    node, job, calls = fixed_node(monkeypatch)
    def fail(*a, **kw): raise TimeoutError('motion timeout')
    node.arm.movesx_cancellable = fail
    with pytest.raises(TimeoutError): node._do_scoop(job)
    assert not calls


@pytest.fixture
def motion(monkeypatch):
    module = _load_skill_node(monkeypatch)
    node = module.SkillNode.__new__(module.SkillNode)
    node.stations = StationTable.from_yaml(Path(__file__).parents[2]/'gmp_bringup/params/stations.yaml')
    node._station_id, node._held_payload, node._held_material_id = 'safe', 'empty', ''
    node._motion_anchor = None
    node._pending_scoop_extract = node._scoop_extract_uncertain = False
    node.vel_scale, node.motion_timeout_s = .2, 30
    node.transfer_joint_vel, node.transfer_joint_acc = 60, 100
    node.joint_tolerance = node.pose_xyz_tolerance = node.pose_rotation_tolerance = 2
    node._cancel_requested = lambda: False
    node._now_s = lambda: 0
    node._cartesian_ready, node.mode = True, 'real'
    state = dict(busy=False, width_mm=100, grip_inferred=False)
    node.gripper = SimpleNamespace(state=lambda _: state, open_width_mm=100, grip_margin_mm=2)
    node.arm = SimpleNamespace(pose=[0]*6, joints=[0]*6)
    node.arm.current_posx = lambda: list(node.arm.pose)
    node.arm.current_posj = lambda: list(node.arm.joints)
    calls = []
    node.after_move = lambda: None
    def linear(target, scale, cancel, timeout):
        if cancel(): raise RuntimeError('cancelled')
        calls.append(('L', list(target)))
        node.arm.pose = list(target)
        node.after_move()
    def joint(target, scale, cancel, timeout, **kw):
        if cancel(): raise RuntimeError('cancelled')
        calls.append(('J', list(target)))
        node.arm.joints = list(target)
        for st in node.stations.stations.values():
            if st.extra.get('approach_posj') == list(target): node.arm.pose = st.above(60)
            if st.extra.get('empty_approach_posj') == list(target):
                node.arm.pose = [423,93,330,90,-90,-90]
        node.after_move()
    node.arm.movel_cancellable, node.arm.movej_cancellable = linear, joint
    job = module.Job('move', dict(station_id='passbox_empty', approach=1))
    return node, job, calls, state, module


def test_cup_pick_lifts_200_before_next_joint_move(motion):
    node, job, calls, state, _ = motion
    node._do_move(job)
    assert [c[0] for c in calls] == ['J','L']
    node._held_payload = 'cup'
    state.update(grip_inferred=True, width_mm=60)
    job.args.update(station_id='workbench', approach=1)
    node._do_move(job)
    assert calls[2] == ('L', [705,-73,330,180,-90,-90])
    assert calls[-1] == ('L', [420,93,130,90,-90,-90])


def test_empty_workbench_uses_middle_and_exit_joint(motion):
    node, job, calls, _, _ = motion
    job.args.update(station_id='workbench')
    node._do_move(job)
    assert [c[0] for c in calls] == ['L','J','L']
    assert calls[0][1] == [395,-90,230,90,-180,-90]
    assert calls[-1][1] == [423,93,130,90,-90,-90]


def test_joint_cancel_never_descends(motion):
    node, job, calls, _, _ = motion
    node.after_move = lambda: setattr(job, 'cancel', True)
    with pytest.raises(RuntimeError): node._do_move(job)
    assert [c[0] for c in calls] == ['J']
    assert node._motion_anchor is None


def test_scoop_return_uses_side_insertion_then_release_lift(motion):
    node, job, calls, state, module = motion
    node._held_payload, node._held_material_id = 'scoop', 'A'
    state.update(grip_inferred=True, width_mm=15.5)
    job.args.update(station_id='scoop_1')
    node._do_move(job)
    assert [c[1][:3] for c in calls] == [[298,-142,150], [298,-142,50], [298,-292,50]]
    node.gripper.release = lambda _: True
    node.gripper.width_mm = lambda: None
    node._do_grip(module.Job('grip', dict(close=False, timeout_s=3)))
    assert calls[-1][1][:3] == [298,-292,150]
    assert node._held_payload == 'empty'


def test_pour_follows_drl_exit_not_reverse_tilt(motion):
    node, _, calls, state, module = motion
    node._held_payload, node._held_material_id = 'scoop', 'A'
    state.update(grip_inferred=True)
    assert node._do_pour(module.Job('pour', dict(fraction=1.)))
    assert [c[1][:3] for c in calls] == [[395,-90,230], [420,195,290], [420,195,243],
        [420,150,320], [420,195,290], [420,195,340], [395,-90,230]]


def test_startup_only_adopts_open_inputs_without_outputs(dio):
    g, _, pins, outputs = dio
    assert g.confirm_open_dio()
    assert g.state()['open_confirmed'] and not outputs
    pins[0] = True
    assert not g.confirm_open_dio()
    assert not g.state()['open_confirmed'] and not outputs


def test_sensor_exception_invalidates_cached_success(dio):
    g, _, pins, _ = dio
    pins[:] = [True, True]
    assert g.grip(0, 20, 3, scoop=True)[0]
    def fail(_): raise RuntimeError('IO error')
    g.arm.din = fail
    with pytest.raises(RuntimeError): g.refresh_dio()
    assert not g.state()['grip_inferred']


def test_taught_lost_grip_after_joint_never_descends(motion):
    node, job, calls, state, _ = motion
    node._held_payload = 'cup'
    state.update(grip_inferred=True, width_mm=60)
    job.args['station_id'] = 'passbox_done'
    node.after_move = lambda: state.update(grip_inferred=False)
    with pytest.raises(RuntimeError): node._do_move(job)
    assert [c[0] for c in calls] == ['J']


def test_taught_manual_shift_refuses_departure(motion):
    node, job, calls, _, _ = motion
    node._do_move(job)
    calls.clear()
    node.arm.joints[0] += 10
    job.args['station_id'] = 'workbench'
    with pytest.raises(RuntimeError, match='출발 이력'): node._do_move(job)
    assert not calls


def test_taught_weigh_keeps_entry_then_records_empty_departure(motion):
    node, job, calls, state, module = motion
    job.args['station_id'] = 'workbench'
    node._do_move(job)
    calls.clear()
    params = {'scale.simulated': True, 'gripper.cup_width_mm':60., 'gripper.force_n':20.}
    node.get_parameter = lambda key: SimpleNamespace(value=params[key])
    node.arm.movel = lambda target, scale: node.arm.movel_cancellable(target, scale, lambda:False, 30)
    node.gripper.grip = lambda *a: state.update(grip_inferred=True) or (True,60,True)
    node.gripper.release = lambda *a: state.update(grip_inferred=False) or True
    node._measure_weight_reading = lambda *a: 'reading'
    assert node._do_weigh(module.Job('weigh', dict(tare_g=0))) == 'reading'
    assert all(c[0] == 'L' for c in calls)  # 기존 AT에서 관절을 다시 뒤집지 않는다.
    assert node._held_payload == 'empty'
    assert node._motion_anchor.pose[:3] == (423,93,180)


@pytest.mark.parametrize('key,value', [('approach_posj',[0]*5), ('empty_descent_mm',-1),
                                      ('exit_mm',None), ('pour_exit_mm',float('nan'))])
def test_bad_taught_configuration_rejected_before_startup(key,value):
    import yaml
    path = Path(__file__).parents[2]/'gmp_bringup/params/stations.yaml'
    data = yaml.safe_load(path.read_text())
    data['stations']['workbench'][key] = value
    with pytest.raises(ValueError): StationTable(data)


@pytest.mark.parametrize('backend,expected', [('dio',0), ('modbus',1)])
def test_robot_launch_removes_vendor_modbus_server_for_dio(tmp_path, backend, expected):
    import ast
    import os
    import yaml
    source = Path(__file__).parents[2]/'gmp_bringup/launch/robot.launch.py'
    tree = ast.parse(source.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'generate_launch_description')
    class Node:
        def __init__(self, package, executable='', **kwargs):
            self.node_package, self.node_executable = package, executable
    vendor = SimpleNamespace(generate_launch_description=lambda: SimpleNamespace(entities=[
        Node('onrobot_rg_control'), Node('controller_manager','ros2_control_node')]))
    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
    (tmp_path/'params').mkdir()
    (tmp_path/'params/common.yaml').write_text(yaml.safe_dump({
        '/**': {'ros__parameters': {'gripper': {'backend':backend}}}}))
    ns = dict(os=os, Path=Path, yaml=yaml, Node=Node, LaunchDescription=lambda actions: actions,
              importlib=SimpleNamespace(util=SimpleNamespace(spec_from_file_location=lambda *a:spec,
                                                              module_from_spec=lambda _:vendor)),
              get_package_share_directory=lambda _:str(tmp_path),
              _control_node=lambda: Node('controller_manager','ros2_control_node'),
              _gripper_pythonpath=lambda: '', LaunchConfiguration=lambda key:key,
              IfCondition=lambda x:x, PythonExpression=lambda x:x)
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),ns)
    nodes = ns['generate_launch_description']()
    assert not any(n.node_package == 'onrobot_rg_control' for n in nodes)
    assert sum(n.node_executable == 'rg2_status_driver' for n in nodes) == expected


def test_first_scoop_pick_enters_joint_home_before_cartesian_motion(motion):
    node, job, calls, _, _ = motion
    node._cartesian_ready = False
    job.args['station_id'] = 'scoop_1'
    node._do_move(job)
    assert [c[0] for c in calls] == ['J','L','L']
    assert calls[0][1] == [0,0,90,0,90,0]


def test_bad_joint_speed_never_moves_even_departure(motion):
    node, job, calls, _, _ = motion
    node.transfer_joint_vel = 0
    with pytest.raises(ValueError): node._do_move(job)
    assert not calls
