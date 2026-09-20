"""두산 상태별 HMI 복구 정책. 임의 이동·무동력동작·프로그램 재개는 금지한다."""
from dataclasses import dataclass

STANDBY = 1
SAFE_OFF = 3
SAFE_STOP = 5
EMERGENCY_STOP = 6
RECOVERY = 8
SAFE_STOP2 = 9
SAFE_OFF2 = 10


@dataclass(frozen=True)
class RecoveryStep:
    control: int | None
    target: int
    manual_required: bool


def recovery_step(state: int, confirmed: bool) -> RecoveryStep:
    if not confirmed:
        raise ValueError('작업자의 원인 제거·복구 조건 확인이 필요합니다')
    # DRFC.h ROBOT_CONTROL 값. 6(무동력동작)은 원격으로 호출하지 않는다.
    steps = {
        STANDBY: RecoveryStep(None, STANDBY, False),
        SAFE_OFF: RecoveryStep(3, STANDBY, False),
        SAFE_STOP: RecoveryStep(2, STANDBY, False),
        SAFE_STOP2: RecoveryStep(4, RECOVERY, True),
        SAFE_OFF2: RecoveryStep(5, RECOVERY, True),
        RECOVERY: RecoveryStep(7, STANDBY, False),
    }
    if state not in steps:
        raise ValueError(f'이 상태는 HMI 자동 복구 대상이 아닙니다: state={state}. 펜던트 확인 필요')
    return steps[state]
