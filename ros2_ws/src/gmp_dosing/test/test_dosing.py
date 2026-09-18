from gmp_dosing.core.dosing import DosingConfig, decide, verdict_of
from gmp_dosing.core.scale import ScaleConfig, WeightModel

CFG = DosingConfig(max_attempts=3, scoop_nominal_g=40.0)


def test_boundary_is_ok():
    assert verdict_of(100, 105, 5.0)[0] == 'OK'
    assert verdict_of(100, 95, 5.0)[0] == 'OK'
    assert verdict_of(100, 105.1, 5.0)[0] == 'OVER'


def test_under_scoops_with_fraction():
    d = decide(100, 70, 5.0, attempts=1, valid=True, invalid_count=0, cfg=CFG)
    assert d.action == 'SCOOP' and abs(d.fraction - 0.75) < 1e-9


def test_over_is_deviation_immediately():
    d = decide(100, 120, 5.0, attempts=1, valid=True, invalid_count=0, cfg=CFG)
    assert d.action == 'DEVIATION' and d.kind == 'OVERFILL'


def test_timeout_after_max_attempts():
    d = decide(100, 70, 5.0, attempts=3, valid=True, invalid_count=0, cfg=CFG)
    assert d.action == 'DEVIATION' and d.kind == 'TIMEOUT'


def test_invalid_then_deviation():
    assert decide(100, 0, 5, 1, False, 0, CFG).action == 'SCOOP'
    assert decide(100, 0, 5, 1, False, 1, CFG).kind == 'WEIGH_INVALID'


# 기존 30 g 가정 기준 테스트. G1 실측 1차 제안값으로 교체해 이력만 남긴다.
# def test_scale_tare_and_resolution():
#     m = WeightModel(ScaleConfig(method='workpiece', min_resolvable_g=30.0))
#     m.set_tare(m.raw_to_g(0.05))            # 50 g 용기
#     gross, tare, net, std, valid = m.reading(0.08, 0.001, True)
#     assert abs(net - 30.0) < 1e-6 and valid
#     assert m.resolvable(100, 5.0) is False   # ±5 g 폭은 30 g 분해능으로 못 가른다
#     assert m.resolvable(100, 30.0)


def test_scale_tare_and_resolution_with_g1_proposal():
    m = WeightModel(ScaleConfig(
        method='workpiece',
        offset_g=0.0,
        min_resolvable_g=17.0,
        max_std_g=5.0,
    ))
    m.set_tare(m.raw_to_g(0.05))            # 50 g 용기
    gross, tare, net, std, valid = m.reading(0.08, 0.001, True)
    assert abs(net - 30.0) < 1e-6 and valid
    assert m.resolvable(100, 16.9) is False  # 허용 폭이 실측 3σ보다 작아 판정 불가
    assert m.resolvable(100, 17.0)           # 허용 폭이 실측 3σ 이상이면 판정 가능


def test_scale_g1_proposal_defaults():
    cfg = ScaleConfig()
    assert cfg.fz_sign == -1.0
    assert cfg.gain == 1.0
    assert cfg.offset_g == 259.765
    assert cfg.min_resolvable_g == 17.0
    assert cfg.max_std_g == 5.0
