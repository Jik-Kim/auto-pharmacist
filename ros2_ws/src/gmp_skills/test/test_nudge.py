from gmp_skills.core.nudge import NudgeDetector
import math


def test_nudge_requires_continuous_window():
    d = NudgeDetector(8.0, 0.2, 1.5)
    assert not d.update([8.1, 0, 0, 0, 0, 0], 0.0)
    assert not d.update([7.9, 0, 0, 0, 0, 0], 0.15)
    assert not d.update([8.1, 0, 0, 0, 0, 0], 0.2)
    assert not d.update([8.1, 0, 0, 0, 0, 0], 0.39)
    assert d.update([8.1, 0, 0, 0, 0, 0], 0.4)


def test_sustained_force_is_one_nudge_and_release_rearms():
    d = NudgeDetector(8.0, 0.2, 1.5)
    assert not d.update([0, 0, 9, 0, 0, 0], 0.0)
    assert d.update([0, 0, 9, 0, 0, 0], 0.2)
    assert not d.update([0, 0, 9, 0, 0, 0], 2.0)
    assert not d.update([0, 0, 0, 0, 0, 0], 2.1)
    assert not d.update([0, 0, 9, 0, 0, 0], 2.2)
    assert d.update([0, 0, 9, 0, 0, 0], 2.41)


def test_force_vector_uses_xyz_magnitude():
    d = NudgeDetector(8.0, 0.1, 0.0)
    assert not d.update([6, 6, 0, 99, 99, 99], 1.0)
    assert d.update([6, 6, 0, 99, 99, 99], 1.1)


def test_non_finite_force_never_triggers():
    d = NudgeDetector(8.0, 0.1, 0.0)
    assert not d.update([math.nan, 9, 0, 0, 0, 0], 1.0)
    assert not d.update([math.inf, 9, 0, 0, 0, 0], 1.2)
