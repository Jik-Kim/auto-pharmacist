"""RG2 어댑터 — 백엔드 modbus | dio | virtual (SOT D-04~D-06).

실물 드라이버(OnRobotRGControllerServer)는
  · `/onrobot/sendCommand` (SetCommand.command 문자열): 'c' 닫기 / 'o' 열기 / 'i','d' 파지력 ±2.5 N / "<정수>" 폭 1/10 mm
  · `/onrobot_joint_states` (JointState 50 Hz): finger_joint rad → 폭 mm (아래 기구 상수)
  · Modbus 의 grip·안전스위치 비트는 **토픽으로 내지 않는다** (I-002) → 파지는 폭 추론 (계약 2절)
가상 노드(gripper_virtual_node)는 같은 서비스 이름을 받지만 숫자 문자열을 **rad** 로 읽는다 — 의미가 다르다.
dio 백엔드는 9/16 교육 grip_test.py 방식 (DO1 grip / DO2 release), 폭·힘 설정 없음.

이 클래스는 ROS 클라이언트(서비스·구독)를 **skill_node 가 주입**한다 — 어댑터는 노드를 만들지 않는다.
TODO([A]): Q-02 실물 G2 — modbus 폭·힘이 되는지. Q-03 dio DI 핀.
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
                 open_width_mm=100.0, dio_pins=(1, 2), din_pins=(), logger=None):
        self.backend = backend            # modbus | dio | virtual
        self._send = send_command         # callable(str) -> bool (skill_node 가 서비스 클라이언트로 만든다)
        self.arm = arm                    # dio 백엔드용 DsrArm
        self.grip_margin_mm, self.slip_mm, self.open_width_mm = grip_margin_mm, slip_mm, open_width_mm
        self.dio_pins, self.din_pins = dio_pins, din_pins
        self.log = logger
        self.force_cmd_n = RG2_MAX_FORCE_N   # 드라이버 기동값 400(1/10 N)
        self._width_mm, self._width_at = None, 0.0
        self._lock = threading.Lock()

    # skill_node 의 JointState 콜백이 부른다
    def on_joint_state(self, finger_joint_rad: float, stamp_s: float):
        with self._lock:
            self._width_mm, self._width_at = joint_to_width_mm(finger_joint_rad), stamp_s

    def width_mm(self):
        with self._lock:
            return self._width_mm

    def busy(self, window_s=0.15) -> bool:
        """폭이 아직 변하는 중이면 busy 로 본다 (드라이버가 busy 를 토픽으로 안 내므로 폭 변화로 추론)."""
        w0 = self.width_mm()
        time.sleep(window_s)
        w1 = self.width_mm()
        return w0 is None or w1 is None or abs(w1 - w0) > 0.3

    def set_force(self, force_n: float):
        """modbus 만. 2.5 N 스텝으로 i/d 를 반복한다 (D-06)."""
        if self.backend != 'modbus':
            return
        target = max(3.0, min(RG2_MAX_FORCE_N, force_n))
        steps = round((target - self.force_cmd_n) / FORCE_STEP_N)
        for _ in range(abs(steps)):
            self._send('i' if steps > 0 else 'd')
        self.force_cmd_n += steps * FORCE_STEP_N

    def move(self, width_mm: float, timeout_s: float = 3.0) -> bool:
        if self.backend == 'modbus':
            ok = self._send(str(int(round(max(0.0, min(RG2_MAX_WIDTH_MM, width_mm)) * 10))))
        elif self.backend == 'virtual':
            ok = self._send(f'{width_mm_to_joint(width_mm):.4f}')
        else:  # dio — 폭 없음, 닫힘/열림만
            close = width_mm < self.open_width_mm / 2
            self.arm.dout(self.dio_pins[0], close)
            self.arm.dout(self.dio_pins[1], not close)
            ok = True
        t0 = time.time()
        while time.time() - t0 < timeout_s and self.busy():
            pass
        return ok

    def grip(self, width_mm: float, force_n: float, timeout_s: float = 3.0):
        """닫기. 반환 (success, final_width_mm, grip_inferred)."""
        self.set_force(force_n)
        ok = self.move(width_mm, timeout_s)
        w = self.width_mm()
        if self.backend == 'dio' or w is None:
            # 폭 피드백이 없으면 추론 불가 — DI 핀이 있으면 그것으로 (Q-03)
            grip = self.arm.din(self.din_pins[0]) if (self.backend == 'dio' and self.din_pins) else True
            return ok, -1.0, grip
        return ok, w, (w > width_mm + self.grip_margin_mm)

    def release(self, timeout_s: float = 3.0):
        return self.move(self.open_width_mm, timeout_s)
