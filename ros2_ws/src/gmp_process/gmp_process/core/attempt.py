"""스쿠핑 시도 1회의 사실을 모은다 — `ScoopCycle` 발행 직전의 순수 형태. ROS 비의존.

`ScoopCycle` 은 **시도**(attempt) 단위다. 원료 1종이 3번 재시도하면 3건이 나간다.
정상은 `WEIGH_RESIDUAL` 직후, 실패는 실패가 확정된 단계에서 발행한다 (docs/interfaces.md 1.1).

한 시도에서 계량은 셋이다 — 빈 스쿱(`scoop_tare`) · 붓기 전(`pre_pour`) · 붓기 후(`post_pour`).
빈 스쿱은 원료마다 1회만 재므로 같은 원료의 시도끼리는 같은 값을 공유한다 (D-22).
"""
from dataclasses import dataclass, field

OUTCOMES = ('COMPLETE', 'SCOOP_EMPTY', 'WEIGH_INVALID', 'POUR_FAILED', 'ABORTED')


@dataclass
class Reading:
    """WeightReading 의 숫자만. 메시지 변환은 nodes 가 한다."""
    gross_g: float = 0.0
    tare_g: float = 0.0
    net_g: float = 0.0
    std_g: float = 0.0
    samples: int = 0
    valid: bool = False


@dataclass
class Attempt:
    material_id: str
    attempt: int
    target_g: float
    actual_before_g: float           # 이 시도 전까지 누적 투입량
    t0: float                        # Scoop 시작 시각 [s]
    scoop_tare: Reading | None = None
    pre_pour: Reading | None = None
    post_pour: Reading | None = None
    commanded_pour_fraction: float = 0.0
    contact_detected: bool = False
    max_contact_force_n: float = 0.0
    insertion_depth_mm: float = 0.0
    grip_width_mm: float = 0.0
    outcome: str = 'ABORTED'
    _extra: dict = field(default_factory=dict)

    def delivered_g(self) -> float:
        """용기에 실제로 들어간 양. 음수 원시차는 0 으로 접고 두 reading 에 원본을 남긴다."""
        if not (self.pre_pour and self.post_pour):
            return 0.0
        return max(0.0, self.pre_pour.net_g - self.post_pour.net_g)

    def is_valid(self) -> bool:
        """필수 계량 3건이 모두 있고 유효할 때만 학습에 쓸 수 있다."""
        rs = (self.scoop_tare, self.pre_pour, self.post_pour)
        return self.outcome == 'COMPLETE' and all(r is not None and r.valid for r in rs)

    def duration_s(self, now: float) -> float:
        return max(0.0, now - self.t0)
