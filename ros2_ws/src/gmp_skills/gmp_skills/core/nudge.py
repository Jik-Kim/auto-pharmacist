"""사람이 로봇을 밀어 준 외력을 한 번의 넛지 입력으로 판정한다.

짧은 힘 노이즈나 한 번 밀고 있는 동안의 반복 이벤트를 걸러낸다.
"""
from dataclasses import dataclass
import math


@dataclass
class NudgeDetector:
    """힘 크기가 threshold_n 이상으로 window_s 동안 유지되면 참을 반환한다.

    같은 접촉을 두 번 세지 않도록 힘이 임계값 아래로 내려가고 cooldown_s가
    지나야 다시 판정한다. now_s는 호출자가 전달하는 동일한 시간 기준이다.
    """

    threshold_n: float
    window_s: float
    cooldown_s: float
    _over_since_s: float | None = None
    _last_trigger_s: float = float('-inf')
    _armed: bool = True

    def update(self, force6, now_s: float) -> bool:
        if len(force6) < 3:
            self._over_since_s = None
            return False
        xyz = [float(v) for v in force6[:3]]
        if not all(math.isfinite(v) for v in xyz) or not math.isfinite(float(now_s)):
            self._over_since_s = None
            return False
        magnitude_n = math.sqrt(sum(v ** 2 for v in xyz))
        if magnitude_n < self.threshold_n:
            self._over_since_s = None
            if now_s - self._last_trigger_s >= self.cooldown_s:
                self._armed = True
            return False
        if not self._armed or now_s - self._last_trigger_s < self.cooldown_s:
            return False
        if self._over_since_s is None:
            self._over_since_s = now_s
            return False
        if now_s - self._over_since_s < self.window_s:
            return False
        self._last_trigger_s = now_s
        self._over_since_s = None
        self._armed = False
        return True
