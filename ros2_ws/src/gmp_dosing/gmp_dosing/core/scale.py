"""측정값 → 그램. 영점·보정·유효성. ROS 비의존.

두 측정 경로 (SOT D-07)
  workpiece : get_workpiece_weight() [kgf] → g
  tool_force: get_tool_force(DR_BASE) Fz [N] → g = fz_sign * Fz / 9.80665 * 1000 + offset_g (G1 9/18: fz_sign=-1, offset≈260 g)

보정값의 근거는 calibration/*.csv 와 core/calib.py 가 재현한다. config/scale_reference.yaml 이 그 요약이다.
"""
from dataclasses import dataclass

G0 = 9.80665


@dataclass
class ScaleConfig:
    method: str = 'workpiece'        # workpiece | tool_force
    gain: float = 1.0                # 실제 저울 대비 선형 보정 (9/21 5점 비교)
    offset_g: float = 0.0            # **method 에 종속** — tool_force 는 260.2 (G1, 133 g 단일 조건 임시값), workpiece 는 미측정.
                                     # 값은 common.yaml 이 넣는다. 기본값 0 — 다른 경로의 편향을 섞어 쓰면 안 된다
    min_resolvable_g: float = 19.0   # G1 실측(9/18) 회차 평균 3σ = 18.0 g 에 여유. 이보다 좁은 허용 폭은 이 저울로 못 가른다
    max_std_g: float = 5.0           # 표본 σ 가 이보다 크면 valid=false (정착 실패) — G1 회차 내부 σ p95 = 4.1 g
    fz_sign: float = -1.0            # Fz 부호 (G1 확인: 아래 하중이 +Fz 로 읽혀 -1 로 뒤집는다)


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
        이 그보다 좁으면 '맞았다' 도 '틀렸다' 도 말할 수 없다. 100 g ±5 % 는 ±5 g 라 3σ 18 g 로는 판정 불가 (Q-11).
        """
        return target_g * tol_pct / 100.0 >= self.cfg.min_resolvable_g
