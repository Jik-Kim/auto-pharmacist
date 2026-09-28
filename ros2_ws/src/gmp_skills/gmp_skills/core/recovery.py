"""두산 로봇의 현재 상태 번호를 허용된 복구 단계에 대응시킨다.

작업자가 원인을 확인한 뒤에만 호출한다. 이 표는 컨트롤러 상태 전이만
정하며 로봇 자세 이동, 무동력 동작, 중단된 배치 재개를 지시하지 않는다.
"""
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
    """보낼 ROBOT_CONTROL 번호, 기대 상태 번호, 추가 현장 조치 필요 여부."""
    control: int | None
    target: int
    manual_required: bool


def recovery_step(state: int, confirmed: bool) -> RecoveryStep:
    """현재 로봇 상태에 맞는 허용 복구 명령과 기대 상태를 반환한다."""
    if not confirmed:
        raise ValueError('작업자의 원인 제거·복구 조건 확인이 필요합니다')
    # control은 두산 DRFC.h의 ROBOT_CONTROL 명령 번호다. 번호 6은
    # 무동력 동작이므로 원격 복구 표에 넣지 않는다.
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
