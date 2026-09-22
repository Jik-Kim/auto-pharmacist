"""기준 자세에서 주어진 BASE 오프셋을 접촉 자세로 회전한다."""
import math

from gmp_skills.core.transfer import _rotation, vector6


def tip_position_base(contact_pose, reference_pose, reference_offset):
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
