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


def test_scale_tare_and_resolution():
    m = WeightModel(ScaleConfig(method='workpiece', offset_g=0.0, min_resolvable_g=19.0, max_std_g=5.0))
    m.set_tare(m.raw_to_g(0.05))            # 50 g 용기
    gross, tare, net, std, valid = m.reading(0.08, 0.001, True)
    assert abs(net - 30.0) < 1e-6 and valid
    assert m.resolvable(100, 5.0) is False   # ±5 g 폭은 3σ 19 g 로 못 가른다 (Q-11)
    assert m.resolvable(100, 18.9) is False
    assert m.resolvable(100, 19.0)


def test_g1_csv_reproduces_reference_and_defaults():
    """CSV → calib → scale_reference.yaml → ScaleConfig 기본값이 한 줄로 이어지는지. 숫자를 손으로 옮기면 여기서 깨진다."""
    import pathlib
    import yaml
    from gmp_dosing.core.calib import load_trials, summarize
    root = pathlib.Path(__file__).resolve().parent.parent
    s = summarize(load_trials(str(root / 'calibration' / 'g1_scoop133g_tool_force.csv')))
    ref = yaml.safe_load((root / 'config' / 'scale_reference.yaml').read_text())['tool_force_calibration']
    assert s['n_sets'] == 6 and s['n_trials'] == 180 and s['samples_per_trial'] == (10, 10)
    assert abs(s['offset_g'] - ref['offset_g']) < 0.01
    assert abs(s['three_sigma_g'] - ref['basis']['three_sigma_g']) < 0.01
    assert abs(s['within_set_sigma_mean_g'] - ref['basis']['within_set_sigma_mean_g']) < 0.01
    cfg = ScaleConfig()
    assert cfg.offset_g == 0.0                                  # method 별 값 — 기본값에 섞지 않는다
    assert cfg.min_resolvable_g == ref['min_resolvable_g'] >= s['three_sigma_g']
    assert cfg.max_std_g == ref['max_std_g'] >= s['within_trial_sigma_p95_g']
    assert s['distinct_sample_ratio'] < 0.7                     # 표본 중복 — 간격을 고치기 전까지는 사실로 남긴다


def test_calib_reads_both_methods_and_estimates_update_interval(tmp_path):
    """measure_g1.py 형식(두 경로 + 시각) 을 읽고, 값이 바뀌는 간격으로 센서 갱신 주기를 추정한다."""
    from gmp_dosing.core.calib import load_trials, summarize
    rows = ['실험명,물체종류,측정조건,실제총무게_g,반복번호,표본번호,시각_s,X축힘_N,Y축힘_N,Z축힘_N,X축모멘트_Nm,Y축모멘트_Nm,Z축모멘트_Nm,작업물무게_kgf']
    t = 0.0
    for s_ in ('a', 'b'):
        for n in range(1, 4):
            for k in range(1, 5):
                fz = 1.2 + 0.01 * ((k + 1) // 2)         # 두 표본마다 값이 바뀐다 (갱신 0.2 s)
                kg = 0.133 + 0.001 * n
                rows.append(f'set_{s_},scoop,c,133,{n},{k},{t:.3f},0,0,{fz},0,0,0,{kg}')
                t += 0.1
    p = tmp_path / 'g1.csv'; p.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    wp = summarize(load_trials(str(p), 'workpiece'))
    assert wp['n_sets'] == 2 and wp['n_trials'] == 6 and abs(wp['offset_g'] - (-2.0)) < 1e-6   # 133 − mean(134,135,136)
    tf = summarize(load_trials(str(p), 'tool_force'))
    assert tf['offset_g'] > 250 and abs(tf['update_interval_s'] - 0.2) < 1e-6 and tf['distinct_sample_ratio'] == 0.5
    old = tmp_path / 'old_format.csv'                     # 9/18 형식 — workpiece 열이 없다
    old.write_text(rows[0].replace(',작업물무게_kgf', '') + '\n' + rows[1].rsplit(',', 1)[0] + '\n', encoding='utf-8')
    import pytest
    with pytest.raises(ValueError, match='workpiece'):
        load_trials(str(old), 'workpiece')


def test_calib_two_weights_give_gain_line(tmp_path):
    """한 CSV 에 32 g(빈 스쿱)·133 g 이 있으면 무게별 요약과 gain·offset 직선이 나온다. 센서가 실제의 0.9 배로 읽는다고 가정."""
    from gmp_dosing.core.calib import load_trials, summarize_by_weight, fit_gain
    rows = ['실험명,물체종류,측정조건,실제총무게_g,반복번호,표본번호,시각_s,X축힘_N,Y축힘_N,Z축힘_N,X축모멘트_Nm,Y축모멘트_Nm,Z축모멘트_Nm,작업물무게_kgf']
    for w in (32.0, 133.0):
        for n in range(1, 4):
            for k in range(1, 4):
                rows.append(f'set_{w:g},scoop,c,{w:g},{n},{k},0,0,0,0,0,0,0,{0.9 * w / 1000 + 0.010:.6f}')   # 0.9×실제 + 10 g 편향
    p = tmp_path / 'g1.csv'; p.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    by_w = summarize_by_weight(load_trials(str(p), 'workpiece'))
    assert list(by_w) == [32.0, 133.0] and by_w[32.0]['n_trials'] == 3
    fit = fit_gain(by_w)
    assert abs(fit['gain'] - 1 / 0.9) < 1e-6 and abs(fit['offset_g'] - (-10 / 0.9)) < 1e-6 and fit['max_residual_g'] < 1e-9
    assert fit_gain({32.0: by_w[32.0]}) is None
