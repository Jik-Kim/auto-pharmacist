"""G1 계량 실측 CSV → 보정값·분해능 요약. ROS 비의존.

CSV 열: 실험명, 물체종류, 측정조건, 실제총무게_g, 반복번호, 표본번호, X축힘_N, Y축힘_N, Z축힘_N, …
한 **회차(반복번호)** = 스킬이 계량 한 번에 읽는 표본 묶음(`scale.samples` 개). 운영에서 판정에 쓰는 값은
회차 **평균**이므로 분해능은 회차 평균의 흔들림(σ)으로 본다. 표본 안의 σ 는 `valid` 판정(`max_std_g`)의 근거다.

세트를 합쳐서(pooled) σ 를 구한다 — 세트마다 스쿱을 다시 잡고 자세를 다시 잡으므로 세트 간 평균의 흐름도
운영에서는 매 계량마다 나타나는 오차다. 세트 안 σ 만 평균 내면 그 흐름이 빠져 분해능을 낙관하게 된다.

사용:  python3 -m gmp_dosing.core.calib <csv> [--method tool_force|workpiece]
측정:  calibration/measure_g1.py 가 두 경로를 같은 표본에서 기록한다
"""
import csv
import statistics as st
import sys
from collections import defaultdict
from dataclasses import dataclass

from .scale import ScaleConfig, WeightModel


@dataclass
class Trial:
    set_name: str
    no: int
    actual_g: float
    samples_g: list[float]      # 표본별 환산값 (offset 적용 전)
    raw: list[float]            # 원시값 (Fz [N] 또는 kgf) — 중복 표본 판정용
    t_s: list[float] | None = None   # 표본 시각 (measure_g1.py 출력에만 있다)

    @property
    def mean_g(self) -> float:
        return st.mean(self.samples_g)

    @property
    def std_g(self) -> float:
        return st.pstdev(self.samples_g)


COLUMN = {'tool_force': 'Z축힘_N', 'workpiece': '작업물무게_kgf'}


def load_trials(path: str, method: str = 'tool_force', fz_sign: float = -1.0) -> list[Trial]:
    """노드와 같은 경로로 환산한다 — 단일 출처는 WeightModel.raw_to_g (offset 0, gain 1).
    tool_force 는 `Z축힘_N`, workpiece 는 `작업물무게_kgf` 열을 읽는다. `시각_s` 열이 있으면 갱신 간격 추정에 쓴다."""
    col = COLUMN[method]
    model = WeightModel(ScaleConfig(method=method, gain=1.0, offset_g=0.0, fz_sign=fz_sign))
    by, ts = defaultdict(list), defaultdict(list)
    with open(path, encoding='utf-8') as f:
        rd = csv.DictReader(f)
        if col not in (rd.fieldnames or []):
            raise ValueError(f'{path}: {method} 열 {col!r} 이 없다 — measure_g1.py 로 다시 재거나 --method 를 바꾼다')
        for r in rd:
            if r['반복번호'] == '반복번호' or r[col] in ('', None):   # 헤더 반복 행 · 그 표본에서 값이 안 나온 행
                continue
            k = (r['실험명'], int(r['반복번호']), float(r['실제총무게_g']))
            by[k].append(float(r[col]))
            if r.get('시각_s'):
                ts[k].append(float(r['시각_s']))
    out = [Trial(s, n, a, [model.raw_to_g(z) for z in zs], zs) for (s, n, a), zs in sorted(by.items())]
    for t in out:
        t.t_s = ts.get((t.set_name, t.no, t.actual_g)) or None
    return out


def update_interval_s(trials: list[Trial]):
    """표본값이 바뀌는 시각 간격의 중앙값 — 센서 갱신 주기의 추정. `시각_s` 가 없으면 None."""
    gaps = []
    for t in trials:
        if not t.t_s:
            continue
        last_t = t.t_s[0]
        for v0, v1, t1 in zip(t.raw, t.raw[1:], t.t_s[1:]):
            if v1 != v0:
                gaps.append(t1 - last_t)
                last_t = t1
    return st.median(gaps) if gaps else None


def summarize(trials: list[Trial]) -> dict:
    means = [t.mean_g for t in trials]
    actual = {t.actual_g for t in trials}
    assert len(actual) == 1, f'실제 무게가 한 종류여야 한다: {actual}'
    actual_g = actual.pop()
    offset_g = actual_g - st.mean(means)
    sigma = st.stdev(means)
    sets = defaultdict(list)
    for t in trials:
        sets[t.set_name].append(t.mean_g)
    set_means = {k: st.mean(v) + offset_g for k, v in sets.items()}
    within = sorted(t.std_g for t in trials)
    p95 = within[max(0, int(round(0.95 * len(within))) - 1)]
    distinct = st.mean(len(set(t.raw)) / len(t.raw) for t in trials)
    ns = [len(t.samples_g) for t in trials]
    return {
        'actual_g': actual_g, 'n_sets': len(sets), 'n_trials': len(trials),
        'samples_per_trial': (min(ns), max(ns)),
        'offset_g': offset_g,                                   # gain=1 가정, 단일 무게
        'repeat_sigma_g': sigma, 'three_sigma_g': 3.0 * sigma,  # 회차 평균의 흔들림 (세트 합산)
        'within_set_sigma_mean_g': st.mean(st.stdev(v) for v in sets.values()),
        'set_means_g': set_means, 'set_drift_g': max(set_means.values()) - min(set_means.values()),
        'within_trial_sigma_mean_g': st.mean(within), 'within_trial_sigma_p95_g': p95,
        'distinct_sample_ratio': distinct,                      # 1.0 이면 표본 중복 없음
        'update_interval_s': update_interval_s(trials),         # 시각 열이 있을 때만
    }


def summarize_by_weight(trials: list[Trial]) -> dict[float, dict]:
    """실제 무게별 요약. 한 CSV 에 32 g(빈 스쿱)·133 g(원료 담음) 이 같이 있어도 된다."""
    by = defaultdict(list)
    for t in trials:
        by[t.actual_g].append(t)
    return {w: summarize(ts) for w, ts in sorted(by.items())}


def fit_gain(by_weight: dict[float, dict]) -> dict | None:
    """무게가 2점 이상이면 actual = gain × raw + offset 최소제곱. raw 는 offset 0·gain 1 로 환산한 회차 평균의 평균.
    반환: gain, offset_g, residual_g(무게별 잔차), max_residual_g. 1점이면 None (gain 은 1 로 두고 offset 만)."""
    if len(by_weight) < 2:
        return None
    xs = [w - s['offset_g'] for w, s in by_weight.items()]     # raw 평균 = actual − offset(1점 기준)
    ys = list(by_weight)
    n, mx, my = len(xs), st.mean(xs), st.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    gain = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    offset = my - gain * mx
    res = {y: y - (gain * x + offset) for x, y in zip(xs, ys)}
    return {'gain': gain, 'offset_g': offset, 'residual_g': res, 'max_residual_g': max(abs(v) for v in res.values()),
            'n_weights': n}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('csv')
    ap.add_argument('--method', choices=sorted(COLUMN), default='tool_force')
    ap.add_argument('--fz-sign', type=float, default=-1.0)
    a = ap.parse_args(argv)
    by_w = summarize_by_weight(load_trials(a.csv, a.method, a.fz_sign))
    for s in by_w.values():
        _print_one(a.method, s)
    fit = fit_gain(by_w)
    if fit:
        print(f"\n[{a.method}] 다중 무게 {fit['n_weights']}점 직선: gain = {fit['gain']:.4f}, offset_g = {fit['offset_g']:.3f}, "
              f"잔차 최대 {fit['max_residual_g']:.2f} g" + ('  ← 3점 이상이어야 잔차가 의미 있다' if fit['n_weights'] < 3 else ''))
        print("   → common.yaml scale.gain / scale.offset_g 후보. 무게별 offset 이 3σ 안에서 같으면 gain 1 로 두어도 된다")
    return 0


def _print_one(method, s):
    print(f"[{method}] 실제 {s['actual_g']:.0f} g · {s['n_sets']}세트 × 회차 {s['n_trials'] // s['n_sets']} × 표본 {s['samples_per_trial'][0]}~{s['samples_per_trial'][1]}")
    print(f"offset_g            = {s['offset_g']:.3f}   (gain 1.0, 단일 무게 → 임시값)")
    print(f"회차 평균 σ (합산)   = {s['repeat_sigma_g']:.4f}   3σ = {s['three_sigma_g']:.4f}")
    print(f"세트 안 σ 평균       = {s['within_set_sigma_mean_g']:.4f}   (세트 간 흐름 {s['set_drift_g']:.1f} g 는 빠진 값)")
    print(f"회차 내부 σ 평균/p95 = {s['within_trial_sigma_mean_g']:.4f} / {s['within_trial_sigma_p95_g']:.4f}  → max_std_g 는 p95 이상")
    print(f"표본 중 서로 다른 값 = {s['distinct_sample_ratio'] * 100:.0f} %   (낮으면 표본 간격이 센서 갱신보다 짧다)")
    if s['update_interval_s'] is not None:
        print(f"값이 바뀌는 간격 중앙값 = {s['update_interval_s'] * 1000:.0f} ms   → --period 는 이보다 길게")
    for k, v in s['set_means_g'].items():
        print(f"  {k}: {v:.1f} g")


if __name__ == '__main__':
    sys.exit(main())
