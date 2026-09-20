import pytest

from gmp_dosing.core.inventory import InventoryState, MaterialInventoryConfig, estimate, remaining_by_height

CFG_LEDGER_ONLY = MaterialInventoryConfig(
    material_id='A', initial_g=1000.0, refill_threshold_g=150.0, empty_contact_z_mm=120.0)
CFG_WITH_HEIGHT = MaterialInventoryConfig(
    material_id='A', initial_g=1000.0, refill_threshold_g=150.0,
    empty_contact_z_mm=120.0, full_contact_z_mm=160.0)   # 40 mm 가 초기량 1000 g 전체 폭


def test_ledger_only_when_no_height_calibration():
    e = estimate(CFG_LEDGER_ONLY, InventoryState(delivered_g=700.0))
    assert e.source == 'LEDGER' and e.remaining_g == 300.0 and e.drift_g is None
    assert e.needs_refill is False


def test_ledger_refill_threshold_is_inclusive():
    assert estimate(CFG_LEDGER_ONLY, InventoryState(delivered_g=850.0)).needs_refill is True    # 150 g 남음 == 임계
    assert estimate(CFG_LEDGER_ONLY, InventoryState(delivered_g=849.0)).needs_refill is False   # 151 g 남음


def test_height_overrides_ledger_and_reports_drift():
    state = InventoryState(delivered_g=550.0)   # 장부상 450 g 남음
    e = estimate(CFG_WITH_HEIGHT, state, contact_z_mm=140.0)   # (140-120)/40 = 0.5 → 500 g
    assert e.source == 'HEIGHT' and e.remaining_g == 500.0
    assert e.drift_g == pytest.approx(450.0 - 500.0)


def test_height_clamps_outside_calibrated_range():
    assert remaining_by_height(CFG_WITH_HEIGHT, contact_z_mm=120.0) == 0.0       # 바닥 접촉 = 완전히 빔
    assert remaining_by_height(CFG_WITH_HEIGHT, contact_z_mm=200.0) == 1000.0    # 기준보다 높게 걸려도 초기량을 넘지 않는다
    assert remaining_by_height(CFG_WITH_HEIGHT, contact_z_mm=110.0) == 0.0       # 잡음으로 바닥보다 낮게 나와도 0 아래로는 안 간다


def test_height_unavailable_without_full_calibration():
    assert remaining_by_height(CFG_LEDGER_ONLY, contact_z_mm=140.0) is None


def test_full_must_be_above_empty():
    bad = MaterialInventoryConfig(material_id='A', initial_g=1000.0, refill_threshold_g=150.0,
                                   empty_contact_z_mm=120.0, full_contact_z_mm=120.0)
    with pytest.raises(ValueError, match='empty_contact_z_mm'):
        remaining_by_height(bad, contact_z_mm=130.0)
