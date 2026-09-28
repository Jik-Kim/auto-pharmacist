"""원료면 높이에 맞춰 티칭한 스쿠핑 경로의 Z를 조정하는 계산 모듈.

TW는 티칭한 5점 경로의 형상을 유지하며 높이만 평행 이동하는 방식이다.
계산된 예상 질량은 깊이에 비례시킨 근사값이며 실제 계량 결과가 아니다.
"""
from dataclasses import dataclass
import math

from gmp_skills.core.transfer import _rotation, vector6


def finite(value, name):
    """설정값이 무한대·NaN·bool이 아닌 숫자인지 확인한다."""
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{name}: 유한한 숫자가 필요하다')
    return float(value)


def tip_offset_local(reference_world, offset_world):
    """WORLD에서 잰 스쿱 끝 방향을 기준 TCP에 붙은 로컬 방향으로 변환한다."""
    r = _rotation(vector6(reference_world, '기준 WORLD 자세'))
    if len(offset_world) != 3:
        raise ValueError('스쿱 끝 오프셋은 3개여야 한다')
    d = [finite(v, '스쿱 끝 오프셋') for v in offset_world]
    return tuple(sum(r[j][i] * d[j] for j in range(3)) for i in range(3))


def tip_z(world_pose, local_offset):
    """TCP 위치·회전을 반영한 스쿱 끝의 WORLD Z 높이를 계산한다."""
    p = vector6(world_pose, 'WORLD 자세')
    return p[2] + sum(a*b for a, b in zip(_rotation(p)[2], local_offset))


@dataclass(frozen=True)
class ScoopPlan:
    """보정 경유점, 원래 경로 대비 Z 이동량, 채취 깊이와 바닥 하한."""
    world_poses: tuple
    shift_mm: float
    depth_mm: float
    predicted_g: float
    floor_z: float


def plan_scoop(profile, world_poses, local_offset, surface_z, fraction):
    """원료면 높이와 요청 깊이에 맞춰 5개 경유점의 WORLD Z를 함께 옮긴다.

    fraction은 저장된 기준 채취 깊이에 대한 비율이다. 원료면에서 그 깊이만큼
    내려갔을 때 스쿱 끝이 바닥 여유 높이를 침범하면 요청을 거부한다.
    예상 질량은 기준 순량에 fraction을 곱한 근사값이다. 경유점 검사만으로
    점과 점 사이 곡선의 최저 높이나 충돌까지 보장하지는 않는다.
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
