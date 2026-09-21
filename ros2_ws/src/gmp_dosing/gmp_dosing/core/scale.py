"""측정값 → 그램. 영점·보정·유효성. ROS 비의존.

두 측정 경로 (SOT D-07)
  workpiece : get_workpiece_weight() [kgf] → g
  tool_force: get_tool_force(DR_BASE) Fz [N] → g = fz_sign * Fz / 9.80665 * 1000 + offset_g

보정값의 근거는 calibration/*.csv 와 core/calib.py 가 재현한다. config/scale_reference.yaml 이 그 요약이다.
2026-09-21 영점 재작업으로 전부 다시 쟀다 (material_3 자세, 운영 조건 samples 20).
⚠️ 이 값은 **자세에 딸린다** — material_1·2 에서는 회차 평균 σ 가 5~9배 크다 (calibration/README.md).
"""
from dataclasses import dataclass

G0 = 9.80665


@dataclass
class ScaleConfig:
    method: str = 'tool_force'       # tool_force | workpiece — 9/21 재측정은 tool_force 로 했다 (workpiece 는 결론 미정)
    gain: float = 1.0                # 실제 저울 대비 선형 보정 — 보정 전 중립값. 값은 common.yaml 이 넣는다 (9/21 실측 1.03)
    offset_g: float = 0.0            # **method 에 종속** — 기본값 0(미보정). 세션마다 ±5 g 움직이지만 tare 가 소거한다 (scale_reference.yaml)
    min_resolvable_g: float = 5.0    # 회차 평균 3σ — 9/21 운영 조건(samples 20) 실측 4.01 g 에 여유. samples 를 줄이면 나빠진다
    max_std_g: float = 8.0           # 표본 σ 가 이보다 크면 valid=false (정착 실패) — 9/21 회차 내부 σ p95 6.95 g 위
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

    def reading(self, raw_mean: float, raw_std: float, valid_src: bool):
        """(gross_g, tare_g, net_g, std_g, valid)."""
        gross = self.raw_to_g(raw_mean)
        std_g = self.std_to_g(raw_std)
        valid = bool(valid_src) and std_g <= self.cfg.max_std_g
        return gross, self.tare_g, gross - self.tare_g, std_g, valid

    def resolvable(self, target_g: float, tol_pct: float) -> bool:
        """허용 오차 폭이 분해능보다 좁으면 이 저울로는 그 목표를 판정할 수 없다.

        min_resolvable_g 는 회차 평균의 실측 3σ 다 — 계량 한 번의 값이 ±3σ 안에서 흔들리므로 허용 폭(±target×tol)
        이 그보다 좁으면 '맞았다' 도 '틀렸다' 도 말할 수 없다. 레시피는 tol 5 % 라 100 g 이면 ±5 g 다 (Q-11).
        9/21 실측(3σ 4.01 → 5.0)으로 A(200 g)·B(150 g)·C(100 g) 셋 다 판정 가능하다. C 는 ±5 g 로 경계다.
        """
        return target_g * tol_pct / 100.0 >= self.cfg.min_resolvable_g
