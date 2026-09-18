"""시도 1회 기록(ScoopCycle 원본)의 계산 규칙."""
from gmp_process.core.attempt import Attempt, Reading


def _a(**kw):
    base = dict(material_id='A', attempt=1, target_g=100.0, actual_before_g=0.0, t0=10.0)
    base.update(kw)
    return Attempt(**base)


def r(net, valid=True):
    return Reading(gross_g=45.0 + net, tare_g=45.0, net_g=net, std_g=0.3, samples=20, valid=valid)


def test_delivered_is_the_drop_between_pre_and_post():
    a = _a(pre_pour=r(40.0), post_pour=r(2.0), outcome='COMPLETE', scoop_tare=r(0.0))
    assert a.delivered_g() == 38.0
    assert a.is_valid()


def test_negative_raw_difference_folds_to_zero_but_readings_survive():
    """붓기 후가 더 무겁게 나오는 일(노이즈)이 있다 — 음수 투입량은 남기지 않되 원본은 둘 다 보존한다."""
    a = _a(pre_pour=r(10.0), post_pour=r(10.4), scoop_tare=r(0.0), outcome='COMPLETE')
    assert a.delivered_g() == 0.0
    assert a.post_pour.net_g > a.pre_pour.net_g


def test_missing_reading_is_not_learning_material():
    a = _a(pre_pour=r(40.0), outcome='COMPLETE', scoop_tare=r(0.0))     # post_pour 없음
    assert a.delivered_g() == 0.0
    assert not a.is_valid()


def test_invalid_reading_or_failed_outcome_is_not_valid():
    assert not _a(scoop_tare=r(0.0), pre_pour=r(40.0), post_pour=r(2.0, valid=False),
                  outcome='COMPLETE').is_valid()
    assert not _a(scoop_tare=r(0.0), pre_pour=r(40.0), post_pour=r(2.0),
                  outcome='SCOOP_EMPTY').is_valid()


def test_duration_never_goes_backwards():
    assert _a().duration_s(9.0) == 0.0
    assert _a().duration_s(12.5) == 2.5
