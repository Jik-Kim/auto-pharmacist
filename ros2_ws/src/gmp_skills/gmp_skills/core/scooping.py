"""TW 경로의 WORLD 높이 보정. 질량 비례는 실측으로 보정할 근사 모델이다."""
from dataclasses import dataclass
import math

from gmp_skills.core.transfer import _rotation, vector6


def finite(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{name}: 유한한 숫자가 필요하다')
    return float(value)


def tip_offset_local(reference_world, offset_world):
    """기준 TCP 자세에서 WORLD로 측정한 벡터를 TCP 로컬 벡터로 변환한다."""
    r = _rotation(vector6(reference_world, '기준 WORLD 자세'))
    if len(offset_world) != 3:
        raise ValueError('스쿱 끝 오프셋은 3개여야 한다')
    d = [finite(v, '스쿱 끝 오프셋') for v in offset_world]
    return tuple(sum(r[j][i] * d[j] for j in range(3)) for i in range(3))


def tip_z(world_pose, local_offset):
    p = vector6(world_pose, 'WORLD 자세')
    return p[2] + sum(a*b for a, b in zip(_rotation(p)[2], local_offset))


@dataclass(frozen=True)
class ScoopPlan:
    world_poses: tuple
    shift_mm: float
    depth_mm: float
    predicted_g: float
    floor_z: float


def plan_scoop(profile, world_poses, local_offset, surface_z, fraction):
    """검증 경로의 상대 형상을 유지하고 WORLD Z만 평행 이동한다.

    fraction=1은 기준 순량(현재 65 g)을 얻은 채취 깊이다. 원료가 부족하면
    몰래 더 얕게 실행하거나 바닥을 침범하지 않고 요청을 거부한다.
    경유점 검사만으로 spline 사이의 최소 높이/충돌을 보장하지는 않는다.
    """
    fraction = finite(fraction, 'depth_fraction')
    minimum = finite(profile['min_fraction'], 'min_fraction')
    if not 0 < minimum <= fraction <= 1:
        raise ValueError('depth_fraction 범위 밖')
    poses = tuple(vector6(p, '스쿠핑 경유점') for p in world_poses)
    if len(poses) != 5:
        raise ValueError('TW 스쿠핑 경유점 5개가 필요하다')
    surface = finite(surface_z, '원료 표면')
    reference = finite(profile['reference_surface_world_z_mm'], '기준 표면')
    floor = (finite(profile['material_bottom_world_z_mm'], '원료 바닥')
             + finite(profile['clearance_mm'], '바닥 여유'))
    if profile['clearance_mm'] <= 0:
        raise ValueError('바닥 여유는 양수여야 한다')
    lowest = min(tip_z(p, local_offset) for p in poses)
    if lowest < floor:
        raise ValueError(f'티칭/스쿱 오프셋 재확인 필요: 끝 높이 {lowest:.2f} < 하한 {floor:.2f}')
    reference_depth = reference - lowest
    net = finite(profile['reference_gross_g'], '기준 총량') - finite(profile['empty_scoop_g'], '빈 스쿱')
    if reference_depth <= 0 or net <= 0:
        raise ValueError('기준 채취 깊이와 순량은 양수여야 한다')
    depth = reference_depth * fraction
    if surface - depth < floor:
        raise ValueError('요청 깊이에 필요한 원료 높이 부족: 보충 또는 목표량 축소 필요')
    shift = surface - depth - lowest
    adjusted = tuple(tuple([*p[:2], p[2] + shift, *p[3:]]) for p in poses)
    return ScoopPlan(adjusted, shift, depth, net * fraction, floor)
