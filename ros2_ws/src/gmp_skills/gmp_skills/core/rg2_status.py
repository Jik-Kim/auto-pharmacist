"""ROS에 의존하지 않는 OnRobot RG 상태 변환."""

from collections.abc import Mapping


def _tenth_mm(value: object, *, signed: bool = False) -> int:
    """밀리미터 값을 메시지의 0.1 mm uint16 필드로 변환한다."""
    encoded = int(round(float(value) * 10.0))
    if signed:
        # gfof는 uint16 필드지만 문서상 signed two's-complement 값이다.
        # 벤더 dict가 raw register를 0.1로 나눈 값(예: 6553.5)을
        # 그대로 전달하는 경우에도 음수로 복원한다.
        if 32767 < encoded <= 0xFFFF:
            encoded -= 0x10000
        if not -32768 <= encoded <= 32767:
            raise ValueError("핑거팁 오프셋이 signed uint16 범위를 벗어남")
        return encoded & 0xFFFF
    return max(0, min(0xFFFF, encoded))


def status_fields(status: Mapping[str, object]) -> dict[str, int]:
    """벤더 Modbus 상태 dict를 ``OnRobotRGInput`` 필드로 변환한다.

    ``relative_width``는 핑거팁 오프셋을 제외한 ``ggwd``이고 ``width``는
    오프셋을 포함한 ``gwdf``이다. 벤더 ``getStatus``의 두 값은 mm 단위다.
    """
    required = {"busy", "offset", "relative_width", "width"}
    missing = sorted(name for name in required if name not in status)
    if missing:
        raise KeyError(f"필수 그리퍼 상태 필드 누락: {', '.join(missing)}")
    # comModbusTcp가 response[10] 원시 gSTA word를 잘못 ``busy``라고
    # 이름 붙여 반환한다. response[11] 이후를 grip/safety 비트로 나누면
    # reserved 레지스터를 해석하게 되므로 인접 키는 사용하지 않는다.
    gsta = int(status["busy"])
    if not 0 <= gsta <= 0x7F:
        raise ValueError("원시 gSTA word가 유효한 0..127 범위를 벗어남")
    return {
        "gfof": _tenth_mm(status["offset"], signed=True),
        "ggwd": _tenth_mm(status["relative_width"]),
        "gsta": gsta,
        "gwdf": _tenth_mm(status["width"]),
    }
