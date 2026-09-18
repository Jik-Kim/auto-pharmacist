"""외력 표본으로 서로 분리된 nudge 입력을 판정한다."""
from dataclasses import dataclass
import math


@dataclass
class NudgeDetector:
    """힘 임계값이 일정 시간 이어진 뒤 한 번만 nudge를 발생시킨다.

    같은 접촉을 두 번 세지 않도록 힘이 임계값 아래로 내려가야 다시 무장한다.
    ``now_s``는 ROS clock 등 한 종류의 단조 증가 시각을 호출자가 주입한다.
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
