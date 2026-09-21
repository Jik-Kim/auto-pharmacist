"""측정값 → 그램. 영점·보정·유효성. ROS 비의존.

두 측정 경로 (SOT D-07)
  workpiece : get_workpiece_weight() [kgf] → g
  tool_force: get_tool_force(DR_BASE) Fz [N] → g = fz_sign * Fz / 9.80665 * 1000 + offset_g

보정값의 근거는 calibration/*.csv 와 core/calib.py 가 재현한다. config/scale_reference.yaml 이 그 요약이다.
⚠️ 2026-09-21 영점 재작업 — 9/18·9/19 G1 근거를 폐기했다(records/deprecated/scale_20260921/).
   아래 기본값 중 min_resolvable_g·max_std_g·fz_sign 은 그 폐기된 측정에서 온 숫자이고, 대신 넣을 새 값이
   아직 없어 그대로 둔 것이다 — 재측정 전까지 이 숫자로 계량 정확도를 판단하지 않는다 (README 참조).
"""
from dataclasses import dataclass

G0 = 9.80665


@dataclass
class ScaleConfig:
    method: str = 'tool_force'       # tool_force | workpiece — 9/19 의 tool_force 확정도 폐기 근거 위였다. 재측정에서 다시 고른다
    gain: float = 1.0                # 실제 저울 대비 선형 보정 — 보정 전 중립값. 값은 common.yaml 이 넣는다 (9/19 두 점 0.8859 는 폐기)
    offset_g: float = 0.0            # **method 에 종속** — 영점 그 자체. 기본값 0(미보정), 다른 경로의 편향을 섞어 쓰면 안 된다 (9/19 247.1 은 폐기)
    min_resolvable_g: float = 19.0   # [폐기 근거] 회차 평균 3σ. 9/18 중복 표본 조건 18.0 g 에 여유를 둔 값 — 재측정 전까지 자리만 지킨다
    max_std_g: float = 10.0          # [폐기 근거] 표본 σ 가 이보다 크면 valid=false (정착 실패). 9/19 회차 내부 σ p95 7.8 g 위로 잡았던 값
    fz_sign: float = -1.0            # Fz 부호 — 아래 하중이 +Fz 로 읽혀 뒤집는다. 부호는 자세·설치에 딸린 것이라 재측정에서 다시 확인


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
        ⚠️ 지금 min_resolvable_g 는 폐기된 측정에서 온 숫자다 — 재측정 3σ 가 나와야 이 판정이 의미를 가진다.
        """
        return target_g * tol_pct / 100.0 >= self.cfg.min_resolvable_g
