"""벤더가 반환한 RG2 Modbus 상태를 메시지에 넣을 정수 필드로 바꾼다.

상태를 발행하는 노드는 nodes/rg2_status_driver.py이며, 이 파일은
단위 변환과 원시 상태 워드 검증만 한다.
"""

from collections.abc import Mapping


def _tenth_mm(value: object, *, signed: bool = False) -> int:
    """mm 값을 0.1 mm 정수로 바꿔 16비트 메시지 필드에 맞춘다."""
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
    # 벤더 dict의 busy 키에는 단순 참/거짓이 아니라 response[10]의 gSTA
    # 상태 비트 전체가 들어 있다. 인접 response[11] 이후는 예약 영역이므로
    # 파지·안전 비트는 gSTA 한 값에서만 해석한다.
    gsta = int(status["busy"])
    if not 0 <= gsta <= 0x7F:
        raise ValueError("원시 gSTA word가 유효한 0..127 범위를 벗어남")
    return {
        "gfof": _tenth_mm(status["offset"], signed=True),
        "ggwd": _tenth_mm(status["relative_width"]),
        "gsta": gsta,
        "gwdf": _tenth_mm(status["width"]),
    }
