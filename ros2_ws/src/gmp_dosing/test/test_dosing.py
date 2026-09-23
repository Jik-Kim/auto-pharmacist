from pathlib import Path

import yaml

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
    # max_invalid_retries=2 → 재시도 2회까지는 다시 재고 3회째 무효에서 일탈 (#213 결정 1)
    assert decide(100, 0, 5, 1, False, 1, CFG).action == 'SCOOP'
    assert decide(100, 0, 5, 1, False, 2, CFG).action == 'SCOOP'
    assert decide(100, 0, 5, 1, False, 3, CFG).kind == 'WEIGH_INVALID'


def test_scale_tare_and_reading():
    m = WeightModel(ScaleConfig(method='workpiece', offset_g=0.0, max_std_g=5.0))
    m.set_tare(m.raw_to_g(0.05))            # 50 g 용기
    gross, tare, net, std, valid = m.reading(0.08, 0.001, True)
    assert abs(net - 30.0) < 1e-6 and valid


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


def test_rezero_csv_reproduces_reference_and_defaults():
    """CSV → calib → scale_reference.yaml → ScaleConfig 기본값이 한 줄로 이어지는지.

    숫자를 손으로 옮기면 여기서 깨진다. 9/18·19 근거를 폐기하고 9/21 에 다시 잰 값이다
    (calibration/README.md). 근거가 또 바뀌면 이 테스트부터 고치게 된다.
    """
    import pathlib
    import yaml
    from gmp_dosing.core.calib import fit_gain, load_trials, summarize, summarize_by_weight
    root = pathlib.Path(__file__).resolve().parent.parent
    doc = yaml.safe_load((root / 'config' / 'scale_reference.yaml').read_text())
    ref = doc['tool_force_calibration']
    assert doc['status'] == 'measured' and doc['method'] == 'tool_force'

    b2 = ref['basis_2point']
    by_w = summarize_by_weight(load_trials(str(root / b2['file']), 'tool_force'))   # yaml 에 적힌 경로 그대로
    assert list(by_w) == [32.0, 133.0]
    for w, key in ((32.0, 'w32'), (133.0, 'w133')):
        s = by_w[w]
        assert s['n_sets'] == b2['sets_per_weight'] and s['n_trials'] == b2['sets_per_weight'] * b2['trials_per_set']
        assert abs(s['offset_g'] - b2[key]['offset_single_g']) < 0.01
        assert abs(s['three_sigma_g'] - b2[key]['three_sigma_g']) < 0.01
        assert abs(s['set_drift_g'] - b2[key]['set_drift_g']) < 0.01
    fit = fit_gain(by_w)
    assert abs(fit['gain'] - b2['fit']['gain']) < 5e-4 and abs(fit['offset_g'] - b2['fit']['offset_g']) < 0.01

    b20 = ref['basis_samples20']
    s20 = summarize(load_trials(str(root / b20['file']), 'tool_force'))
    assert s20['samples_per_trial'] == (b20['samples_per_trial'], b20['samples_per_trial'])
    assert s20['distinct_sample_ratio'] == 1.0                       # 0.82 s 간격 — 표본 독립
    assert abs(s20['update_interval_s'] - b20['effective_period_s']) < 0.02
    assert abs(s20['three_sigma_g'] - b20['three_sigma_g']) < 0.01
    assert abs(s20['within_trial_sigma_p95_g'] - b20['within_trial_sigma_p95_g']) < 0.01

    cfg = ScaleConfig()
    assert cfg.method == doc['method'] and cfg.gain == 1.0 and cfg.offset_g == 0.0   # 값은 common.yaml 이 넣는다
    assert cfg.max_std_g == ref['max_std_g'] >= s20['within_trial_sigma_p95_g']      # 정상 계량이 invalid 로 떨어지지 않게
    assert abs(round((fit['gain'] + 1.0279) / 2, 2) - ref['gain']) < 1e-9            # scoop_1 3점과의 교차 검증값


def test_offset_cancels_out_in_net_weight():
    """offset 은 세션마다 ±5 g 움직이지만 tare 를 빼는 순간 사라진다 — 판정에 쓰이는 것은 gain 뿐이다.

    scale_reference.yaml 의 offset_is_cancelled 가 말하는 성질을, 운영에서 실제로 쓰이는 **두 경로**로 고정한다.
      (a) reading() 의 net_g — skill_node 가 set_tare 뒤 reading 을 불러 WeightReading.net_g 로 내보낸다
      (b) gross_g 끼리 빼기 — process_fsm 이 scooped_g·residual_g 를 구하는 방식 (gross − scoop_tare_g)
    (b) 를 따로 두는 이유: FSM 은 net_g 를 쓰지 않고 두 gross 를 직접 뺀다. 소거가 성립하는 근거가
    경로마다 다르므로 둘 다 고정해야 한다.
    """
    raw_tare, raw_gross = 1.0, 2.5
    sessions = (190.8, 195.0, 197.4)          # 9/21 에 관측된 세션별 offset 범위
    nets_a, nets_b = [], []
    for offset in sessions:
        m = WeightModel(ScaleConfig(gain=1.03, offset_g=offset))
        tare_g = m.raw_to_g(raw_tare)
        m.set_tare(tare_g)
        nets_a.append(m.reading(raw_gross, 0.0, True)[2])
        gross_g = m.reading(raw_gross, 0.0, True)[0]
        nets_b.append(gross_g - tare_g)       # FSM 방식
    assert max(nets_a) - min(nets_a) < 1e-9   # offset 이 6.6 g 달라져도 순량은 같다
    assert max(nets_b) - min(nets_b) < 1e-9
    assert abs(nets_a[0] - nets_b[0]) < 1e-9  # 두 경로가 같은 값을 준다
    assert abs(nets_a[0] - (raw_gross - raw_tare) * -1.0 / 9.80665 * 1000 * 1.03) < 1e-9   # 남는 것은 gain 뿐


def test_fit_oscillation_removes_slow_swing():
    """느린 진동이 실린 표본에서 상수항을 뽑는다 — 단순 평균보다 참값에 가깝다 (9/22)."""
    import math
    from gmp_dosing.core.scale import fit_oscillation
    s = [100.0 + 30.0 * math.sin(2 * math.pi * 0.82 * i / 15.0) for i in range(32)]
    value, resid, hf, period = fit_oscillation(s, 0.82)
    assert abs(value - 100.0) < 0.5          # 참값 복원
    assert abs(sum(s) / len(s) - 100.0) > 2  # 단순 평균은 창이 정수배가 아니라 치우친다
    assert 14.0 <= period <= 16.0
    assert resid < 0.5


def test_fit_oscillation_guard_falls_back_on_plain_noise():
    """⚠️ 진동이 없으면 적합하지 않는다 — 안 그러면 잡음을 진동으로 오인해 값이 나빠진다.

    9/22 전체 회귀에서 무조건 적용하면 측정 17건 중 10건이 악화하고 전체 25 % 나빠졌다.
    가드(residual ≤ apply_ratio × 표본 σ) 로 적용률을 11 % 로 낮추니 전체 -15.1 % 가 됐다.
    """
    import random
    from gmp_dosing.core.scale import fit_oscillation
    random.seed(1)
    s = [100.0 + random.gauss(0, 6) for _ in range(32)]
    value, resid, hf, period = fit_oscillation(s, 0.82)
    assert period == 0.0                     # 적합을 안 썼다는 표시
    assert value == sum(s) / len(s)          # 단순 평균 그대로
    assert fit_oscillation(s, 0.82, apply_ratio=0)[3] == 0.0   # 가드를 꺼도 단순 평균


def test_reading_hf_gate_catches_load_change_but_passes_oscillation():
    """무결성 게이트는 '재는 중 조작' 만 잡고 느린 진동은 통과시킨다 (9/22 실측 분리).

    오염 고주파 σ 10.57·12.26·26.45  vs  양성 최대 8.54 — 임계 9.5 가 그 사이다.
    정확도 게이트(max_std_g)와 다른 것을 잡으므로 둘 다 필요하다.
    """
    from gmp_dosing.core.scale import ScaleConfig, WeightModel
    m = WeightModel(ScaleConfig(method='workpiece', gain=1.0, offset_g=0.0,
                                max_std_g=8.0, max_hf_std_g=9.5))
    # 진동은 크지만 고주파는 작다 → 통과
    assert m.reading(0.100, 0.005, True, raw_hf_std=0.006)[4] is True
    # 재는 중 하중이 바뀌어 고주파가 튀었다 → 거부
    assert m.reading(0.100, 0.005, True, raw_hf_std=0.012)[4] is False
    # hf 를 안 주면 기존 동작 그대로 (하위호환)
    assert m.reading(0.100, 0.005, True)[4] is True


# ── 교착 조건 — 파라미터 파일을 직접 읽는다 (9/23 조장 결정) ──────────────
# 값이 바뀌면 이 시험이 먼저 깨진다. 주석이 아니라 시험으로 고정하는 이유는,
# 9/23 까지 min_fraction 0.15 × scoop_nominal_g 40 = 6.0 g 이 40 g·±5 % 의 한계
# 4.0 g 을 넘겨 **recipe-01 세 원료 전부가 교착이었는데도 아무도 못 봤기** 때문이다.
_PARAMS = Path(__file__).resolve().parents[2] / 'gmp_bringup' / 'params'   # …/ros2_ws/src/
assert _PARAMS.is_dir(), f'파라미터 경로를 못 찾는다: {_PARAMS} — 패키지 배치가 바뀌었는지 볼 것'


def _dosing_params():
    common = yaml.safe_load((_PARAMS / 'common.yaml').read_text(encoding='utf-8'))
    node = next(iter(common.values()))
    return node['ros__parameters']['dosing']


def _recipes():
    for path in sorted((_PARAMS / 'recipes').glob('recipe-*.yaml')):
        yield path.name, yaml.safe_load(path.read_text(encoding='utf-8'))['items']


def test_min_scoop_cannot_overshoot_tolerance():
    """UNDER 뒤 **최소 채취**가 허용 상한을 넘으면 스쿱↔반환이 끝없이 반복된다.

    조건: min_fraction × scoop_nominal_g ≤ 2 × target × tol/100
    (허용 구간 폭이 2×target×tol 이므로, 최소 채취가 그보다 크면 UNDER 에서 한 번에
     건너뛸 수밖에 없는 구간이 생긴다.)
    """
    d = _dosing_params()
    min_scoop = d['min_fraction'] * d['scoop_nominal_g']
    for name, items in _recipes():
        for it in items:
            limit = 2.0 * it['target_g'] * it['tol_pct'] / 100.0
            assert min_scoop <= limit, (
                f"{name} {it['material_id']}: 최소 채취 {min_scoop:.1f} g 이 한계 {limit:.1f} g 을 넘어 "
                f"교착한다 (target {it['target_g']:g} g, tol {it['tol_pct']:g} %)")


def test_min_fraction_matches_across_param_files():
    """stations.yaml 의 스쿠핑 min_fraction 은 dosing 과 같아야 한다 — 파일이 둘이라 갈라지기 쉽다."""
    stations = yaml.safe_load((_PARAMS / 'stations.yaml').read_text(encoding='utf-8'))
    assert stations['scooping']['A']['min_fraction'] == _dosing_params()['min_fraction']


def test_dosing_defaults_match_operational_params():
    """DosingConfig 기본값이 운영값과 갈라지면, 기본값으로 돈 시험이 운영을 대변하지 못한다."""
    d = _dosing_params()
    cfg = DosingConfig()
    assert cfg.scoop_nominal_g == d['scoop_nominal_g']
    assert cfg.min_fraction == d['min_fraction']
    # max_invalid_retries 는 common.yaml dosing 절에 없다 — process_node 가 따로 선언한다.
    # 여기서 단언하면 KeyError 라, 그 값의 정합은 gmp_process 쪽 시험이 본다.
