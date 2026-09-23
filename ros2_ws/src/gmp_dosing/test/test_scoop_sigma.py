"""#272 스쿱 1회량 판정 스크립트 — 뺄셈·χ²·판정 경계를 고정한다.

실물 기록은 A 가 한 번 찍고 끝이라 **그때 계산이 틀리면 다시 못 찍는다.**
열을 바꿔 적은 기록을 조용히 받아들이는 것이 제일 위험해서 그것부터 고정한다.
"""
import importlib.util
import io
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / 'calibration' / 'scoop_sigma.py'
_spec = importlib.util.spec_from_file_location('scoop_sigma', _SRC)
ss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ss)


def _csv(tmp_path, rows, header='회차,빈스쿱_g,채취후_g,붓기후스쿱_g,원료면,비고'):
    p = tmp_path / 'g.csv'
    p.write_text(header + '\n' + '\n'.join(rows) + '\n', encoding='utf-8')
    return str(p)


def test_three_scale_readings_become_three_quantities(tmp_path):
    """저울 값 3개 → 퍼올림·잔량·투입량. 투입량은 빈 스쿱이 소거되어 두 값의 차다."""
    rows = ss.load(_csv(tmp_path, ['1,35.0,122.0,37.0,가득,']))
    r = rows[0]
    assert r['퍼올림'] == pytest.approx(87.0)     # 122 − 35
    assert r['잔량'] == pytest.approx(2.0)        # 37 − 35
    assert r['투입량'] == pytest.approx(85.0)     # 122 − 37


def test_swapped_columns_are_rejected_not_silently_averaged(tmp_path):
    """열을 바꿔 적으면 **거부**한다 — 조용히 평균에 섞이면 한 번뿐인 실측이 날아간다."""
    with pytest.raises(ss.RowError, match='채취후'):
        ss.load(_csv(tmp_path, ['1,122.0,35.0,37.0,가득,']))     # 빈스쿱↔채취후 뒤바뀜
    with pytest.raises(ss.RowError, match='투입량'):
        ss.load(_csv(tmp_path, ['1,35.0,122.0,140.0,가득,']))    # 붓기후 > 채취후


def test_unfilled_rows_are_skipped_so_the_template_can_be_analysed_midway(tmp_path):
    """기록지를 반만 채우고 돌려도 채운 회차만 읽는다 — 측정 중간에 확인할 수 있어야 한다."""
    rows = ss.load(_csv(tmp_path, ['1,35.0,122.0,37.0,가득,', '2,,,,,', '3,35.0,120.0,37.0,중간,']))
    assert [r['회차'] for r in rows] == ['1', '3']


def test_sigma_upper_bound_matches_published_chi_square_factors():
    """참 σ 의 한쪽 95 % 상한 배수 — #249 설계의 배수표와 같아야 한다.

    이 배수가 틀리면 「σ̂ 는 3 g 인데 상한은 괜찮다」 같은 반대 결론이 나온다.
    ⚠️ practice/B/CURRENT.md 의 N=6 칸은 **2.11 로 적혀 있는데 2.09 가 맞다**(df=5,
    χ²_0.05 = 1.1455). 10·15·30 은 맞다. 문서 정정은 gain PR 묶음에 들어간다.
    """
    for n, expected in ((6, 2.09), (10, 1.65), (15, 1.46), (30, 1.28)):
        assert ss.sigma_bounds(1.0, n)[1] == pytest.approx(expected, abs=0.02), n
    assert ss.sigma_bounds(1.0, 6)[0] == pytest.approx(0.67, abs=0.02)   # 하한 배수


def test_verdict_bands_are_the_leader_approved_ones():
    """σ 3 / 5 g 경계와 평균 ±5 g 경계 (9/23 팀장 권고 ③)."""
    assert '✅' in ss.verdict({'mean': 85.0, 'sd': 2.9, 'sd_hi': 2.95, 'n': 15}, 85.0)[1]
    assert '⚠️' in ss.verdict({'mean': 85.0, 'sd': 3.1, 'sd_hi': 4.5, 'n': 15}, 85.0)[1]
    assert '❌' in ss.verdict({'mean': 85.0, 'sd': 5.1, 'sd_hi': 7.4, 'n': 15}, 85.0)[1]
    assert '❌' in ss.verdict({'mean': 79.9, 'sd': 1.0, 'sd_hi': 1.5, 'n': 15}, 85.0)[0]
    assert '✅' in ss.verdict({'mean': 80.0, 'sd': 1.0, 'sd_hi': 1.5, 'n': 15}, 85.0)[0]


def test_sigma_point_estimate_can_pass_while_upper_bound_does_not(tmp_path):
    """n 이 작으면 σ̂ 이 통과해도 상한이 밴드를 넘는다 — 그 경우 경고가 붙어야 한다."""
    out = ss.verdict({'mean': 85.0, 'sd': 2.5, 'sd_hi': 3.9, 'n': 15}, 85.0)
    assert any('95 % 상한' in line for line in out)


def test_completion_rate_falls_as_sigma_grows():
    """완주율은 σ 에 단조 감소해야 한다 — CURRENT.md 표(3 g 95 % / 8 g 34 %)의 방향."""
    import random
    recipes = {'r1': [(85.0, 10.0)] * 3}
    rates = []
    for sd in (1.0, 3.0, 8.0):
        rng = random.Random(0)
        sample = [rng.gauss(85.0, sd) for _ in range(4000)]
        rates.append(ss.completion_rates(sample, recipes, 85.0, 8, 3000, 272)['r1'])
    assert rates[0] > rates[1] > rates[2]
    assert rates[0] > 99.0 and rates[2] < 60.0


def test_report_runs_end_to_end_and_names_the_judged_quantity(tmp_path):
    rows = ss.load(_csv(tmp_path, [f'{i},35.0,{122.0 + (i % 3):.1f},37.0,가득,' for i in range(1, 13)]))
    buf = io.StringIO()
    res = ss.report(rows, 85.0, 10.0, 8, 500, 272, out=buf)
    text = buf.getvalue()
    assert '투입량 기준' in text and '배치 완주율' in text
    assert res['delivered']['n'] == 12


def test_builtin_rule_agrees_with_decide_once_fixed_scoop_lands():
    """#274 머지되는 순간 완주율 엔진이 내장 규칙 → 진짜 decide() 로 갈아탄다.

    둘이 다르면 **같은 CSV 가 머지 전후로 다른 완주율을 낸다** — 그것도 조용히.
    그래서 같은 스쿱 수열을 두 엔진에 먹여 결과가 한 건도 안 갈리는지 본다.
    #274 전에는 fixed_scoop 이 없어 skip 되고, 머지된 뒤 자동으로 살아난다.
    """
    import random
    cfg = ss._fixed_cfg(85.0, 8)
    if cfg is None:
        pytest.skip('#274 (fixed_scoop) 미머지 — 머지되면 이 시험이 살아난다')
    for target, tol in ((85.0, 10.0), (170.0, 10.0), (85.0, 5.0), (170.0, 5.0), (255.0, 10.0)):
        for sd in (0.5, 2.0, 5.0, 9.0):
            for seed in range(20):
                rng = random.Random(seed)
                draws = [rng.gauss(85.0, sd) for _ in range(20)]
                a = ss._run_material(target, tol, 85.0, iter(draws).__next__, cfg, 8)
                b = ss._run_material(target, tol, 85.0, iter(draws).__next__, None, 8)
                assert a == b, (target, tol, sd, seed, a, b)
