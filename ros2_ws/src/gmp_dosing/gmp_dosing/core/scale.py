"""측정값 → 그램. 영점·보정·유효성. ROS 비의존.

두 측정 경로 (SOT D-07)
  workpiece : get_workpiece_weight() [kgf] → g
  tool_force: get_tool_force(DR_BASE) Fz [N] → g = fz_sign * Fz / 9.80665 * 1000 + offset_g

보정값의 근거는 calibration/*.csv 와 core/calib.py 가 재현한다. config/scale_reference.yaml 이 그 요약이다.
2026-09-21 영점 재작업으로 전부 다시 쟀다 (material_3 자세, 운영 조건 samples 20).
⚠️ **아래 기본값은 아직 운영에 적용되지 않은 제안값이다.** 런타임 단일 출처인 common.yaml 의 scale.* 과
   process_node 의 declare_parameter 기본값은 폐기된 9/19 값(gain 0.8859·offset 247.091·19.0·10.0) 그대로다.
   process_node 가 항상 파라미터를 넘기므로 ScaleConfig() 기본값은 런타임에 쓰이지 않는다 — 동작 변화 없음.
⚠️ 이 값은 **자세에 딸린다** — material_1·2 에서는 회차 평균 σ 가 5~9배 크고, 거기서는 max_std_g 가
   8 이든 10 이든 계량이 거의 전부 valid=false 다 (실측 valid: material_1 0/15, material_2 1~2/15,
   material_3 15/15). **계량 자세를 조장·A 가 정한 뒤 common.yaml 을 갱신해야 하는 이유다.**
"""
import math
from dataclasses import dataclass

G0 = 9.80665


def _solve3(a, b):
    """3x3 정규방정식 — 가우스 소거. core 는 ROS·numpy 비의존이라 직접 푼다."""
    m = [list(a[i]) + [b[i]] for i in range(3)]
    for i in range(3):
        piv = max(range(i, 3), key=lambda r: abs(m[r][i]))
        if abs(m[piv][i]) < 1e-12:
            return None
        m[i], m[piv] = m[piv], m[i]
        for r in range(3):
            if r == i:
                continue
            f = m[r][i] / m[i][i]
            for c in range(i, 4):
                m[r][c] -= f * m[i][c]
    return [m[i][3] / m[i][i] for i in range(3)]


def fit_oscillation(samples, period_s, *, apply_ratio=0.20,
                    min_period_s=8.0, max_period_s=30.0, step_s=0.5):
    """표본열에서 저주파 진동을 빼고 **상수항**을 얻는다 — 그것이 하중 추정값이다.

    (offset, residual_std, hf_std, fitted_period_s) 를 돌려준다.

    왜 필요한가 (9/22 실측):
      · 자세에 따라 진폭 ±35 g · 주기 13~20 s 의 느린 진동이 실린다 (material_1/2). material_3 에는 없다.
      · 고주파(센서) 잡음은 어디나 4~7 g 로 같다 — 차이는 전부 이 진동이다.
      · 창을 정수배 주기에 맞추면 상쇄되지만 **주기가 회차마다 13.5~20 s 로 흔들려** 고정 창으로는 못 맞춘다.
        (실제로 창 길이 최적값이 실행마다 18 ↔ 20 표본으로 바뀌어 재현되지 않았다.)
      · 회차마다 적합하면 material_1 회차간 σ 가 **5.15 → 3.09 (-40 %)** 로 내려갔다.
    ⚠️ **가드가 핵심이다 (apply_ratio).** 진동이 없는 표본에 사인 3개 파라미터를 맞추면 잡음을 진동으로
      오인해 상수항이 오염된다. 9/22 전체 회귀(측정 17건·회차 171개)에서 무조건 적용하면 **25 % 악화**하고
      17건 중 10건이 나빠졌다 (용기 한 파지 0.85 → 3.85). 그래서 **적합이 표본 분산의 대부분을 설명할 때만**
      쓴다 — residual_std ≤ apply_ratio × 표본 σ. 0.20 에서 적용률 11 %, 전체 **-15.1 %**, 악화 1건이고
      그 1건은 회차 3개짜리(같은 자리를 8회로 다시 재니 1.90 이 아니라 6.21 이었다)라 기준 자체가 약하다.
      apply_ratio=0 이면 가드를 끄고 항상 단순 평균을 쓴다.
    반환값 쓰임:
      · offset       → raw_mean 대신 쓴다 (진동이 빠진 하중)
      · residual_std → **정확도** 게이트 (max_std_g). 적합 뒤 남은 흔들림이라 진동에 부풀지 않는다
      · hf_std       → **무결성** 게이트 (max_hf_std_g). 재는 중 하중이 바뀌면 여기서 튄다
    표본이 모자라거나 적합이 실패하면 단순 평균으로 물러난다 (offset=mean, residual_std=std).
    """
    n = len(samples)
    mean = sum(samples) / n if n else 0.0
    std = math.sqrt(sum((x - mean) ** 2 for x in samples) / n) if n else 0.0
    d = [samples[i + 1] - samples[i] for i in range(n - 1)]
    hf = (math.sqrt(sum(x * x for x in d) / len(d) - (sum(d) / len(d)) ** 2) / math.sqrt(2.0)) if len(d) > 1 else 0.0
    if n < 8 or period_s <= 0:
        return mean, std, hf, 0.0
    best = None
    steps = int((max_period_s - min_period_s) / step_s) + 1
    for k in range(steps):
        P = min_period_s + k * step_s
        w = 2.0 * math.pi * period_s / P
        if w * (n - 1) < math.pi:          # 창이 반 주기도 안 되면 상수와 구분이 안 된다
            continue
        c = [math.cos(w * i) for i in range(n)]
        t = [math.sin(w * i) for i in range(n)]
        A = [[float(n), sum(c), sum(t)],
             [sum(c), sum(x * x for x in c), sum(c[i] * t[i] for i in range(n))],
             [sum(t), sum(c[i] * t[i] for i in range(n)), sum(x * x for x in t)]]
        rhs = [sum(samples), sum(samples[i] * c[i] for i in range(n)), sum(samples[i] * t[i] for i in range(n))]
        sol = _solve3(A, rhs)
        if sol is None:
            continue
        res = [samples[i] - (sol[0] + sol[1] * c[i] + sol[2] * t[i]) for i in range(n)]
        ss = sum(x * x for x in res)
        if best is None or ss < best[0]:
            best = (ss, sol[0], P, math.sqrt(ss / n))
    if best is None:
        return mean, std, hf, 0.0
    if apply_ratio <= 0 or std <= 0 or best[3] > apply_ratio * std:
        return mean, std, hf, 0.0          # 진동이 뚜렷하지 않다 — 단순 평균으로 간다
    return best[1], best[3], hf, best[2]


@dataclass
class ScaleConfig:
    method: str = 'tool_force'       # tool_force | workpiece — 9/21 재측정은 tool_force 로 했다 (workpiece 는 결론 미정)
    gain: float = 1.0                # 실제 저울 대비 선형 보정 — 보정 전 중립값. 값은 common.yaml 이 넣는다 (9/21 실측 1.03)
    offset_g: float = 0.0            # **method 에 종속** — 기본값 0(미보정). 세션마다 ±5 g 움직이지만 tare 가 소거한다 (scale_reference.yaml)
    max_std_g: float = 8.0           # **정확도** 게이트. fit_oscillation 의 residual_std 와 비교한다 (적합 전 표본 σ 가 아니다)
    max_hf_std_g: float = 9.5        # **무결성** 게이트 — 재는 중 하중이 바뀌면 고주파가 튄다. 0 이면 끔.
                                     # 9/22 실측 분리: 오염 10.57·12.26·26.45 vs 양성 최대 8.54 (양성 15건·오염 3건)
    fz_sign: float = -1.0            # Fz 부호 — 아래 하중이 +Fz 로 읽혀 뒤집는다 (9/21 material_3 에서 재확인)


class WeightModel:
    def __init__(self, cfg: ScaleConfig):
        self.cfg = cfg
        self.tare_g = 0.0

    def raw_to_g(self, value: float) -> float:
        if self.cfg.method == 'workpiece':
            g = value * 1000.0
        else:
            g = self.cfg.fz_sign * value / G0 * 1000.0
        return g * self.cfg.gain + self.cfg.offset_g

    def std_to_g(self, std: float) -> float:
        return abs(self.raw_to_g(std) - self.raw_to_g(0.0))

    def set_tare(self, gross_g: float):
        self.tare_g = gross_g

    def reading(self, raw_mean: float, raw_std: float, valid_src: bool, raw_hf_std: float = None):
        """(gross_g, tare_g, net_g, std_g, valid).

        raw_std 는 fit_oscillation 의 residual_std 를 넣는다 (적합을 안 하면 표본 σ 그대로도 된다).
        raw_hf_std 를 주면 **무결성** 게이트를 함께 본다 — 재는 중 하중이 바뀐 계량을 잡는다 (9/22).
        둘은 다른 것을 잡는다: std 는 "이 평균을 믿을 수 있나", hf 는 "재는 중에 건드렸나".
        """
        gross = self.raw_to_g(raw_mean)
        std_g = self.std_to_g(raw_std)
        valid = bool(valid_src) and std_g <= self.cfg.max_std_g
        if valid and raw_hf_std is not None and self.cfg.max_hf_std_g > 0:
            valid = self.std_to_g(raw_hf_std) <= self.cfg.max_hf_std_g
        return gross, self.tare_g, gross - self.tare_g, std_g, valid
