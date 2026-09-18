"""측정값 → 그램. 영점·보정·유효성. ROS 비의존.

두 측정 경로 (SOT D-07)
  workpiece : get_workpiece_weight() [kgf] → g
  tool_force: get_tool_force(DR_BASE) Fz [N] → g = -Fz / 9.80665 * 1000 (아래로 당기는 하중이 음의 Fz 라는 가정 — G1 에서 부호 확인)
"""
from dataclasses import dataclass

G0 = 9.80665


@dataclass
class ScaleConfig:
    method: str = 'workpiece'        # workpiece | tool_force
    gain: float = 1.0                # 실제 저울 대비 선형 보정 (9/21 5점 비교)
    offset_g: float = 259.765          # 133 g 단일 조건 기준 1차 임시 보정값
    min_resolvable_g: float = 17.0   # G1 실측 3σ. 이보다 작은 목표량은 이 저울로 못 잰다
    max_std_g: float = 5.0           # 표본 σ 가 이보다 크면 valid=false (정착 실패)
    fz_sign: float = -1.0            # Fz 부호 (G1 확인)


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
        """허용 오차 폭이 분해능보다 좁으면 이 저울로는 그 목표를 판정할 수 없다."""
        # 목표량의 허용 편차[g]가 실측 3σ 분해능 이상인지 직접 비교한다.
        return target_g * tol_pct / 100.0 >= self.cfg.min_resolvable_g
