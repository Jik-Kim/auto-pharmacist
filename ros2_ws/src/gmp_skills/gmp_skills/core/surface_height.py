"""접촉 순간의 TCP 자세에서 스쿱 끝이 놓인 BASE 좌표를 계산한다.

기준 TCP에서 잰 스쿱 끝 오프셋을 TCP 자체의 로컬 방향으로 바꾼 뒤,
접촉 순간의 TCP 회전을 적용해 스쿱 끝의 실제 XYZ를 구한다.
"""
import math

from gmp_skills.core.transfer import _rotation, vector6


def tip_position_base(contact_pose, reference_pose, reference_offset):
    """기준 오프셋과 접촉 TCP 자세로 스쿱 끝의 BASE XYZ(mm)를 반환한다."""
    contact = vector6(contact_pose, '접촉 자세')
    reference = vector6(reference_pose, '오프셋 기준 자세')
    if (len(reference_offset) != 3 or any(isinstance(v, bool)
            or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in reference_offset)):
        raise ValueError('스쿱 끝 오프셋은 유한한 숫자 3개가 필요하다')
    ref_rotation = _rotation(reference)
    local = [sum(ref_rotation[j][i] * reference_offset[j] for j in range(3))
             for i in range(3)]
    rotation = _rotation(contact)
    return [contact[i] + sum(rotation[i][j] * local[j] for j in range(3))
            for i in range(3)]
