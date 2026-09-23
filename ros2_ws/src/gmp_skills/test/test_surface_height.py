import pytest

from gmp_skills.core.surface_height import tip_position_base


def test_reference_pose_preserves_supplied_base_offset():
    reference = [344, -298, 200, 90, -180, -90]
    assert tip_position_base(reference, reference, [0, -120, -20]) == pytest.approx([344, -418, 180])


def test_rotated_tool_uses_rotated_offset():
    assert tip_position_base([1, 2, 3, 90, 0, 0], [0]*6, [10, 0, 0]) == pytest.approx([1, 12, 3])


@pytest.mark.parametrize('offset', [[0, 1], [0, 1, float('nan')], [True, 0, 1]])
def test_invalid_offset_rejected(offset):
    with pytest.raises(ValueError):
        tip_position_base([0]*6, [0]*6, offset)
