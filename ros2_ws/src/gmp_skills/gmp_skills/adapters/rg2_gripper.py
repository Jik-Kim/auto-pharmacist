"""RG2 어댑터 — 백엔드 modbus | dio | virtual (SOT D-04~D-06).

실물 드라이버(OnRobotRGControllerServer)는
  · `/onrobot/sendCommand` (SetCommand.command 문자열): 'c' 닫기 / 'o' 열기 / 'i','d' 파지력 ±2.5 N / "<정수>" 폭 1/10 mm
  · `/onrobot_joint_states` (JointState 50 Hz): finger_joint rad → 폭 mm (아래 기구 상수)
  · Modbus 의 grip·안전스위치 비트는 **토픽으로 내지 않는다** (I-002) → 파지는 폭 추론 (계약 2절)
가상 노드(gripper_virtual_node)는 같은 서비스 이름을 받지만 숫자 문자열을 **rad** 로 읽는다 — 의미가 다르다.
dio 백엔드는 9/16 교육 grip_test.py 방식 (DO1 grip / DO2 release), 폭·힘 설정 없음.

이 클래스는 ROS 클라이언트(서비스·구독)를 **skill_node 가 주입**한다 — 어댑터는 노드를 만들지 않는다.
DIO: 약통 DI1=1, 스쿱 DI1=DI2=1 후 안정 대기, 열림 DI1=0. 폭·힘은 미측정.
"""
import math
import threading
import time

# RG2 기구 상수 (OnRobotRGControllerServer.initParams 와 동일)
_L1, _L3, _TH1, _TH3, _DY = 0.108505, 0.055, 1.41371, 0.76794, -0.0144
RG2_MAX_WIDTH_MM, RG2_MAX_FORCE_N, FORCE_STEP_N = 110.0, 40.0, 2.5


def joint_to_width_mm(theta: float) -> float:
    return (math.cos(theta + _TH3) * _L3 + _DY + _L1 * math.cos(_TH1)) * 2 * 1000.0


def width_mm_to_joint(width_mm: float) -> float:
    w = max(0.0, min(RG2_MAX_WIDTH_MM, width_mm)) / 1000.0
    return math.acos(((w / 2) - _DY - _L1 * math.cos(_TH1)) / _L3) - _TH3


class Rg2Gripper:
    def __init__(self, backend: str, send_command, arm=None, grip_margin_mm=2.0, slip_mm=1.5,
                 open_width_mm=100.0, dio_pins=(1, 2), din_pins=(), logger=None, now_fn=None,
                 state_timeout_s=0.5, dio_settle_s=0.3, completion_settle_s=0.2):
        self.backend = backend            # modbus | dio | virtual
        self._send = send_command         # callable(str) -> bool (skill_node 가 서비스 클라이언트로 만든다)
        self.arm = arm                    # dio 백엔드용 DsrArm
        self.grip_margin_mm, self.slip_mm, self.open_width_mm = grip_margin_mm, slip_mm, open_width_mm
        self.dio_pins, self.din_pins = dio_pins, din_pins
        self.log = logger
        self._now = now_fn or time.monotonic
        self.state_timeout_s = float(state_timeout_s)
        self.dio_settle_s = float(dio_settle_s)
        self.completion_settle_s = float(completion_settle_s)
        if not math.isfinite(self.completion_settle_s) or self.completion_settle_s <= 0:
            raise ValueError('그리퍼 완료 안정 시간은 유한한 양수여야 한다')
        self._native_stable_since = 0.0
        self._native_stable_width = None
        self.force_cmd_n = 0.0 if backend == 'dio' else RG2_MAX_FORCE_N   # 드라이버 기동값 400(1/10 N)
        self._width_mm, self._width_at = None, 0.0
        self._moving_until_s = 0.0
        self._grip_ref_mm = None
        self._grip_inferred = False
        self._slip_latched = False
        self._native_at = None
        self._native_busy = True
        self._native_grip = False
        self._native_safety = False
        self._native_busy_seq = 0
        self._last_completed = None
        self._command_width_mm = None
        self._lock = threading.Lock()
        self._dio_at = None
        self._dio_command = None
        self._dio_scoop = False
        self._dio_busy = True
        self._dio_inputs = (False, False)
        self.cancel_requested = lambda: False

    def refresh_dio(self):
        """DSR 워커에서만 입력을 읽고, 타이머에는 캐시만 제공한다."""
        if self.backend != 'dio':
            return
        if len(self.din_pins) != 2 or any(type(p) is not int or p <= 0 for p in self.din_pins):
            raise ValueError('DIO 완료 확인에는 DI 핀 2개가 필요하다')
        with self._lock:
            self._dio_at = None  # 읽기 실패 시 이전 성공을 재사용하지 않는다.
        values = tuple(self.arm.din(p) for p in self.din_pins)
        with self._lock:
            self._dio_inputs, self._dio_at = values, self._now()

    def confirm_open_dio(self):
        """기동 시 열려 있는 입력만 수용한다. 개폐 명령은 보내지 않는다."""
        self.refresh_dio()
        with self._lock:
            if self._dio_inputs[0]:
                return False
            self._dio_command, self._dio_busy = False, False
            return True

    def _dio_state_locked(self, now):
        fresh = self._dio_at is not None and now - self._dio_at <= self.state_timeout_s
        closed = self._dio_inputs[0] and (not self._dio_scoop or self._dio_inputs[1])
        gripped = fresh and self._dio_command is True and closed and not self._dio_busy
        opened = fresh and self._dio_command is False and not self._dio_inputs[0] and not self._dio_busy
        return dict(width_mm=None, fresh=fresh, busy=not (gripped or opened),
                    grip_inferred=bool(gripped), open_confirmed=bool(opened),
                    safety_triggered=False, slip=False)

    def _move_dio(self, close, timeout_s, *, scoop=False):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            return False
        if len(self.din_pins) != 2 or any(type(p) is not int or p <= 0 for p in self.din_pins):
            return False
        if not math.isfinite(self.dio_settle_s) or self.dio_settle_s < 0:
            raise ValueError('DIO 스쿱 대기 시간은 유한한 0 이상이어야 한다')
        deadline = self._now() + timeout_s
        with self._lock:
            self._dio_command, self._dio_scoop = close, scoop
            self._dio_busy, self._dio_at = True, None
        completed = False
        try:
            if self.cancel_requested():
                raise RuntimeError('cancelled')
            # width_mm으로 개폐를 추론하지 않는다. 약통 폭 60도 명시적인 닫기다.
            self.arm.dout(self.dio_pins[0], close)
            if self.cancel_requested():
                raise RuntimeError('cancelled')
            self.arm.dout(self.dio_pins[1], not close)
            matched_at = None
            while self._now() < deadline:
                if self.cancel_requested():
                    raise RuntimeError('cancelled')
                self.refresh_dio()
                di1, di2 = self._dio_inputs
                matched = (di1 and (not scoop or di2)) if close else not di1
                if matched:
                    if matched_at is None:
                        matched_at = self._now()
                    settle = self.dio_settle_s if close and scoop else 0.0
                    if self._now() - matched_at >= settle and self._now() < deadline:
                        if self.cancel_requested():
                            raise RuntimeError('cancelled')
                        completed = True
                        return True
                else:
                    matched_at = None
                time.sleep(0.02)
            return False
        finally:
            with self._lock:
                self._dio_busy = not completed
                if not completed:
                    self._dio_command = None


    def on_native_status(self, status, stamp_s):
        """벤더 상태 비트 사용. 폭은 기존 relative_width 기준을 유지한다."""
        with self._lock:
            # 통신 공백이나 상태 변화 뒤에는 이전 완료 이력을 재사용하지 않는다.
            if self._last_completed is not None:
                _, width, raw_width, grip, force = self._last_completed
                if (self._native_stale_locked(stamp_s) or status.gsta & 0x7d
                        or abs(status.gwdf / 10.0 - width) >= 0.1 - 1e-6
                        or abs(status.ggwd / 10.0 - raw_width) >= 0.1 - 1e-6
                        or bool(status.gsta & 2) != grip or self.force_cmd_n != force):
                    self._last_completed = None
            was_gripped = self._native_grip
            self._native_at = stamp_s
            self._native_busy = bool(status.gsta & 1)
            self._native_grip = bool(status.gsta & 2)
            self._native_safety = bool(status.gsta & 0x7c)
            if self._native_busy:
                self._native_busy_seq += 1
            width = status.ggwd / 10.0
            if (self._native_busy or self._native_stable_width is None
                    or abs(width - self._native_stable_width) >= 0.1 - 1e-6):
                self._native_stable_since = stamp_s
                self._native_stable_width = width
            self._width_mm = width
            self._command_width_mm = status.gwdf / 10.0
            self._width_at = stamp_s
            self._grip_inferred = self._native_grip and not self._native_safety
            if was_gripped and not self._native_grip and self._grip_ref_mm is not None:
                self._slip_latched = True

    def _native_stale_locked(self, now):
        return self._native_at is None or now - self._native_at > self.state_timeout_s

    # skill_node 의 JointState 콜백이 부른다
    def on_joint_state(self, finger_joint_rad: float, stamp_s: float):
        width_mm = joint_to_width_mm(finger_joint_rad)
        with self._lock:
            if self._width_mm is not None and abs(width_mm - self._width_mm) > 0.3:
                self._moving_until_s = stamp_s + 0.15
            self._width_mm, self._width_at = width_mm, stamp_s
            if self._grip_ref_mm is not None and abs(width_mm - self._grip_ref_mm) > self.slip_mm:
                self._slip_latched = True
                self._grip_inferred = False

    def width_mm(self):
        with self._lock:
            return self._width_mm

    def busy(self, now_s: float | None = None) -> bool:
        """최근 0.15초 안에 폭이 변했으면 busy로 본다."""
        now_s = self._now() if now_s is None else now_s
        with self._lock:
            if self.backend == 'dio':
                return self._dio_state_locked(now_s)['busy']
            if self.backend == 'modbus':
                return self._native_stale_locked(now_s) or self._native_busy or self._native_safety
            stale = self._width_mm is None or now_s - self._width_at > self.state_timeout_s
            return stale or now_s < self._moving_until_s

    def state(self, now_s: float | None = None):
        now_s = self._now() if now_s is None else now_s
        with self._lock:
            if self.backend == 'dio':
                return self._dio_state_locked(now_s)
            stale = self._width_mm is None or now_s - self._width_at > self.state_timeout_s
            if self.backend == 'modbus':
                stale = self._native_stale_locked(now_s)
                return {'width_mm': self._width_mm,
                        'fresh': not stale,
                        'busy': stale or self._native_busy or self._native_safety,
                        'grip_inferred': self._native_grip and not stale and not self._native_safety,
                        'safety_triggered': self._native_safety, 'slip': self._slip_latched}
            return {
                'width_mm': self._width_mm,
                'busy': stale or now_s < self._moving_until_s,
                'grip_inferred': self._grip_inferred and not stale,
                'slip': self._slip_latched,
            }

    def consume_slip(self) -> bool:
        with self._lock:
            slip, self._slip_latched = self._slip_latched, False
            return slip

    def set_force(self, force_n: float):
        """modbus 만. 2.5 N 스텝으로 i/d 를 반복한다 (D-06)."""
        if self.backend != 'modbus':
            return True
        target = max(3.0, min(RG2_MAX_FORCE_N, force_n))
        steps = round((target - self.force_cmd_n) / FORCE_STEP_N)
        for _ in range(abs(steps)):
            if self.busy():
                return False
            if not self._send('i' if steps > 0 else 'd'):
                return False
            self.force_cmd_n += FORCE_STEP_N if steps > 0 else -FORCE_STEP_N
        return True

    def move(self, width_mm: float, timeout_s: float = 3.0) -> bool:
        if self.backend == 'dio':
            raise ValueError('DIO는 폭 이동 대신 grip/release를 사용한다')
        command_at_s = self._now()
        target_mm = max(0.0, min(RG2_MAX_WIDTH_MM, width_mm))
        with self._lock:
            if self.backend == 'modbus' and (
                    self._native_stale_locked(command_at_s) or self._native_busy or self._native_safety):
                return False
            previous = self._last_completed
            self._last_completed = None
            busy_seq = self._native_busy_seq
            initial_width = self._width_mm
            initial_command_width = self._command_width_mm
            self._grip_ref_mm = None
            self._grip_inferred = False
            self._slip_latched = False
        if self.backend == 'modbus':
            ok = self._send(str(int(round(max(0.0, min(RG2_MAX_WIDTH_MM, width_mm)) * 10))))
        elif self.backend == 'virtual':
            ok = self._send(f'{width_mm_to_joint(width_mm):.4f}')
        if not ok:
            return False
        moved = False
        while self._now() - command_at_s < timeout_s:
            if self.backend == 'modbus':
                with self._lock:
                    fresh = not self._native_stale_locked(self._now())
                    if self._native_safety or not fresh:
                        return False
                    # 명령 전 idle 표본을 완료로 쓰지 않는다. busy 관측 후 idle만 인정한다.
                    # 이미 같은 폭인 무동작 명령은 명령 전후 모두 목표 근처일 때만 인정한다.
                    at_target = (initial_command_width is not None and self._command_width_mm is not None
                                 and abs(initial_command_width - target_mm) <= self.grip_margin_mm
                                 and abs(self._command_width_mm - target_mm) <= self.grip_margin_mm)
                    repeated = (previous is not None and target_mm == previous[0]
                                and self.force_cmd_n == previous[4]
                                and initial_command_width == previous[1]
                                and initial_width == previous[2]
                                and self._native_stable_since <= command_at_s
                                and self._native_busy_seq == busy_seq
                                and self._command_width_mm == previous[1]
                                and self._width_mm == previous[2]
                                and self._native_grip == previous[3])
                    if (self._native_at > command_at_s and not self._native_busy
                            and self._now() - max(self._native_stable_since, command_at_s) >= self.completion_settle_s
                            and (self._native_busy_seq > busy_seq or at_target or repeated)):
                        self._last_completed = (target_mm, self._command_width_mm,
                                                self._width_mm, self._native_grip,
                                                self.force_cmd_n)
                        return True
                time.sleep(0.02)
                continue
            with self._lock:
                saw_new_state = self._width_at > command_at_s
                width = self._width_mm
                # 새 표본이어도 명령 전 폭이면 아직 동작이 시작되지 않은 것이다.
                if saw_new_state and initial_width is not None and width is not None:
                    moved = moved or abs(width - initial_width) > 0.3
                at_target = width is not None and abs(width - target_mm) <= self.grip_margin_mm
            if saw_new_state and (moved or at_target) and not self.busy():
                return True
            time.sleep(0.02)
        return False

    def grip(self, width_mm: float, force_n: float, timeout_s: float = 3.0, *, scoop=False):
        """닫기. 반환 (success, final_width_mm, grip_inferred)."""
        if self.backend == 'dio':
            ok = self._move_dio(True, timeout_s, scoop=scoop)
            return ok, -1.0, ok
        if self.backend == 'modbus' and self.busy():
            return False, -1.0, False
        if not self.set_force(force_n):
            return False, -1.0, False
        ok = self.move(width_mm, timeout_s)
        w = self.width_mm()
        if w is None or not ok:
            return False, -1.0 if w is None else w, False
        with self._lock:
            grip = (self._native_grip and not self._native_safety
                    and not self._native_stale_locked(self._now())) if self.backend == 'modbus' else (
                        w > width_mm + self.grip_margin_mm)
            self._grip_inferred = grip
            self._grip_ref_mm = w if grip else None
        return ok, w, grip

    def release(self, timeout_s: float = 3.0):
        if self.backend == 'dio':
            return self._move_dio(False, timeout_s)
        return self.move(self.open_width_mm, timeout_s)
