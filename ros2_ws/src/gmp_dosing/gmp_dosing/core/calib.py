"""G1 계량 실측 CSV → 보정값·분해능 요약. ROS 비의존.

CSV 열: 실험명, 물체종류, 측정조건, 실제총무게_g, 반복번호, 표본번호, X축힘_N, Y축힘_N, Z축힘_N, …
한 **회차(반복번호)** = 스킬이 계량 한 번에 읽는 표본 묶음(`scale.samples` 개). 운영에서 판정에 쓰는 값은
회차 **평균**이므로 분해능은 회차 평균의 흔들림(σ)으로 본다. 표본 안의 σ 는 `valid` 판정(`max_std_g`)의 근거다.

세트를 합쳐서(pooled) σ 를 구한다 — 세트마다 스쿱을 다시 잡고 자세를 다시 잡으므로 세트 간 평균의 흐름도
운영에서는 매 계량마다 나타나는 오차다. 세트 안 σ 만 평균 내면 그 흐름이 빠져 분해능을 낙관하게 된다.

사용:  python3 -m gmp_dosing.core.calib ros2_ws/src/gmp_dosing/calibration/g1_scoop133g_tool_force.csv
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
    raw: list[float]            # 원시 Fz [N] — 중복 표본 판정용

    @property
    def mean_g(self) -> float:
        return st.mean(self.samples_g)

    @property
    def std_g(self) -> float:
        return st.pstdev(self.samples_g)


def load_trials(path: str, fz_sign: float = -1.0) -> list[Trial]:
    """tool_force 경로 그대로 환산한다 — 단일 출처는 WeightModel.raw_to_g."""
    model = WeightModel(ScaleConfig(method='tool_force', gain=1.0, offset_g=0.0, fz_sign=fz_sign))
    by = defaultdict(list)
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if r['반복번호'] == '반복번호':          # 파일을 이어 붙이며 헤더가 반복된 행
                continue
            by[(r['실험명'], int(r['반복번호']), float(r['실제총무게_g']))].append(float(r['Z축힘_N']))
    return [Trial(s, n, a, [model.raw_to_g(z) for z in zs], zs) for (s, n, a), zs in sorted(by.items())]


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
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    s = summarize(load_trials(argv[0]))
    print(f"실제 {s['actual_g']:.0f} g · {s['n_sets']}세트 × 회차 {s['n_trials'] // s['n_sets']} × 표본 {s['samples_per_trial'][0]}~{s['samples_per_trial'][1]}")
    print(f"offset_g            = {s['offset_g']:.3f}   (gain 1.0, 단일 무게 → 임시값)")
    print(f"회차 평균 σ (합산)   = {s['repeat_sigma_g']:.4f}   3σ = {s['three_sigma_g']:.4f}  → min_resolvable_g 는 이 이상")
    print(f"세트 안 σ 평균       = {s['within_set_sigma_mean_g']:.4f}   (세트 간 흐름 {s['set_drift_g']:.1f} g 는 빠진 값)")
    print(f"회차 내부 σ 평균/p95 = {s['within_trial_sigma_mean_g']:.4f} / {s['within_trial_sigma_p95_g']:.4f}  → max_std_g 는 p95 이상")
    print(f"표본 중 서로 다른 값 = {s['distinct_sample_ratio'] * 100:.0f} %   (낮으면 표본 간격이 센서 갱신보다 짧다)")
    for k, v in s['set_means_g'].items():
        print(f"  {k}: {v:.1f} g")
    return 0


if __name__ == '__main__':
    sys.exit(main())
