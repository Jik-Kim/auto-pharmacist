"""높이·질량 보정과 실패 시 후속 이동 차단을 확인한다."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from gmp_skills.core.scooping import plan_scoop, tip_offset_local, tip_z
from test_check_depth import depth_node
from test_dsr_arm import _arm


def profile():
    return dict(min_fraction=0.15, reference_surface_world_z_mm=75.,
                material_bottom_world_z_mm=50., clearance_mm=5.,
                reference_gross_g=100., empty_scoop_g=35.)


def test_rotated_tip_offset():
    offset = tip_offset_local([344,-298,200,90,-180,-90], [0,-120,-20])
    assert offset == pytest.approx([0,120,20])
    assert tip_z([344,-298,200,90,-180,-90], offset) == pytest.approx(180)
    assert tip_z([0,0,100,0,0,0], offset) == pytest.approx(120)


def test_half_mass_half_depth_and_surface_shift():
    points = [[0,0,z,0,0,0] for z in (80,70,60,55,70)]
    full = plan_scoop(profile(), points, [0,0,0], 75, 1)
    half = plan_scoop(profile(), points, [0,0,0], 70, .5)
    assert (full.depth_mm, full.predicted_g, full.shift_mm) == (20,65,0)
    assert (half.depth_mm, half.predicted_g, half.shift_mm) == (10,32.5,5)
    assert half.world_poses[3][2] == 60
    assert points[3][2] == 55


@pytest.mark.parametrize('fraction', [0, .1, 1.1, float('nan'), float('inf'), True])
def test_invalid_fraction(fraction):
    with pytest.raises(ValueError):
        plan_scoop(profile(), [[0,0,55,0,0,0]]*5, [0,0,0], 75, fraction)


def test_insufficient_material_rejected_not_clamped():
    with pytest.raises(ValueError, match='원료 높이 부족'):
        plan_scoop(profile(), [[0,0,55,0,0,0]]*5, [0,0,0], 60, 1)


def test_supplied_approximate_geometry_cannot_be_enabled_blindly(monkeypatch):
    node, job, calls, _ = depth_node(monkeypatch)
    p = node.stations.scooping['A']
    p['execution_mode'] = 'height_compensated'
    off = tip_offset_local(p['reference_pose_base'], p['tip_offset_world_mm'])
    with pytest.raises(ValueError, match='재확인'):
        plan_scoop(p, p['waypoints_base'], off, 72.5, 1)
    with pytest.raises(ValueError, match='보정 미확인'):
        node._do_scoop(job)
    assert not calls


def prepared(monkeypatch):
    node, job, calls, _ = depth_node(monkeypatch)
    p = deepcopy(node.stations.scooping['A'])
    # 테스트용 보정 모델이며 실물 보정값이 아니다.
    p.update(profile(), execution_mode="height_compensated", calibrated=True, tip_offset_world_mm=[0,0,0],
             waypoints_base=[[0,0,z,0,0,0] for z in (80,70,60,55,70)],
             shake_base=[0,0,85,0,0,0])
    node.stations.scooping['A'] = p
    job.args['depth_fraction'] = .5
    node.get_logger = lambda: SimpleNamespace(info=lambda _: None)
    node.arm.transform_pose = lambda p, to_world: list(p)
    node.arm.movesx_cancellable = lambda *a, **k: calls.append(('spline',a[0]))
    node.arm.movel_cancellable = lambda *a, **k: calls.append(('linear',a[0]))
    node.arm.amove_periodic = lambda *a, **k: calls.append(('shake',))
    node.arm.wait_motion_cancellable = lambda *a, **k: None
    node.arm.stop_motion = lambda: calls.append(('stop',))
    node._pose_matches = lambda *a: True
    result = dict(contact_detected=True, contact_pose_base=[0,0,75,0,0,0],
                  max_contact_force_n=2., insertion_depth_mm=3.)
    monkeypatch.setattr(type(node), '_do_check_depth', lambda self, job: result)
    return node, job, calls, result


def test_scoop_flow_and_actual_contact_pose(monkeypatch):
    node, job, calls, result = prepared(monkeypatch)
    assert node._do_scoop(job) is result
    assert [c[0] for c in calls] == ['spline','linear','shake','linear']
    assert calls[0][1][3][2] == 65  # 표면75 - 절반 깊이10
    assert calls[-1][1] == node.stations.for_material('A').posx


def test_no_contact_no_spline(monkeypatch):
    node, job, calls, result = prepared(monkeypatch)
    result.update(contact_detected=False, contact_pose_base=None)
    with pytest.raises(RuntimeError, match='미검출'):
        node._do_scoop(job)
    assert not calls


def test_spline_failure_never_shakes_or_returns(monkeypatch):
    node, job, calls, _ = prepared(monkeypatch)
    def fail(*a, **k):
        raise RuntimeError('spline failed')
    node.arm.movesx_cancellable = fail
    with pytest.raises(RuntimeError, match='spline failed'):
        node._do_scoop(job)
    assert not calls


def test_spline_adapter_waits_for_pose_and_passes_observer():
    arm = _arm()
    observed = []
    arm.current_posx = lambda: [1,2,3,0,0,0]
    def wait(cancel, timeout, observer, target_reached):
        observer()
        assert target_reached()
    arm.wait_motion_cancellable = wait
    arm.movesx_cancellable([[1,2,3,0,0,0]], [20,10], [30,15], lambda: False, 5,
                           observer=lambda: observed.append(True))
    assert observed == [True]
    assert arm.R.calls[-1][0] == 'amovesx'
    assert arm.R.calls[-1][2]['ref'] == 0


def test_spline_pre_cancel_sends_no_motion():
    arm = _arm()
    with pytest.raises(RuntimeError, match='cancelled'):
        arm.movesx_cancellable([[0]*6], [1,1], [1,1], lambda: True, 5)
    assert not arm.R.calls


def test_transform_uses_world_and_base_without_identity_assumption():
    arm = _arm()
    arm.R.DR_WORLD = 2
    calls = []
    arm._bounded_query = lambda op, **kw: calls.append((op,kw)) or SimpleNamespace(conv_posx=[1]*6)
    assert arm.transform_pose([0]*6, to_world=True) == [1]*6
    arm.transform_pose([0]*6, to_world=False)
    assert [(v['ref_in'], v['ref_out']) for _,v in calls] == [(0,2),(2,0)]


def test_height_only_reports_transformed_contact_without_spline(monkeypatch):
    node, job, calls, result = prepared(monkeypatch)
    node.height_measure_only = True
    node.stations.scooping['A']['calibrated'] = False
    def transform(p, to_world):
        p = list(p)
        p[2] += 15 if to_world else -15
        return p
    node.arm.transform_pose = transform
    measured = node._do_scoop(job)
    assert measured['diagnostic_only'] is True
    assert 'surface_world_z_mm=90.000' in measured['measurement_message']
    assert 'contact_base=[0, 0, 75' in measured['measurement_message']
    assert not calls  # 측정 모의 호출 이외 spline·털기·추가 이동 없음


def test_height_only_without_contact_has_no_height(monkeypatch):
    node, job, calls, result = prepared(monkeypatch)
    node.height_measure_only = True
    result['contact_pose_base'] = None
    with pytest.raises(RuntimeError, match='접촉 미검출'):
        node._do_scoop(job)
    assert not calls
