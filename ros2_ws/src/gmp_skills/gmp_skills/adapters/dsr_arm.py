"""DSR_ROBOT2 어댑터 — DR_init 노드를 소유하고 블로킹 함수를 감싼다.

**워커 스레드에서만 부른다** (SOT D-02). DSR_ROBOT2 는 호출마다
`rclpy.spin_until_future_complete(g_node, …)` 로 여기서 만든 노드를 직접 spin 하므로
이 노드는 executor 에 넣지 않는다.

교육 방식(rokey/move.py) 그대로: DR_init 채움 → 노드 생성 → DSR_ROBOT2 import.
실물로만 검증되는 것: 힘·작업물무게·툴/TCP 설정(가상은 에뮬레이터에 미등록이라 건너뛴다).

TODO([A]): 9/17 G1 — measure_force / measure_workpiece 분해능 실측.
TODO([A]): I-004 — 이동 취소 수단 (amovel + check_motion).
"""
import statistics
import time

import rclpy
import DR_init


class DsrArm:
    def __init__(self, robot_id: str, robot_model: str, mode: str, vel: float, acc: float,
                 tool_name: str = '', tcp_name: str = '', logger=None, now_fn=None, sleep_fn=None,
                 startup_timeout_s: float = 15.0, virtual_tcp_name: str = '',
                 tcp_offset_mm_deg=None):
        self.mode = mode
        self.vel, self.acc = vel, acc
        self.log = logger
        # 클래스 안의 ``DR_init.__dsr__*`` 표기는 Python 이름 맹글링을 받으므로 setattr을 쓴다.
        setattr(DR_init, '__dsr__id', robot_id)
        setattr(DR_init, '__dsr__model', robot_model)
        # 네임스페이스는 반드시 ROBOT_ID 와 같아야 한다 (교육 자료)
        # skill_node의 __node/__ns 리맵을 상속하면 /cell/skill_node로 중복 생성되고
        # 상대 DSR 서비스가 /cell 아래를 보게 된다. 전용 노드는 dsr01 네임스페이스를 고정한다.
        self.node = rclpy.create_node('gmp_dsr_client', namespace=robot_id,
                                      use_global_arguments=False)
        setattr(DR_init, '__dsr__node', self.node)
        import DSR_ROBOT2 as R   # DR_init 이후에 import (두산 튜토리얼 Caution)
        from DR_common2 import posx, posj
        from dsr_msgs2.srv import MoveStop
        self.R, self.posx, self.posj = R, posx, posj
        self._MoveStop = MoveStop
        self._move_stop_cli = self.node.create_client(
            MoveStop, 'dsr_controller2/motion/move_stop')
        self.tool_name, self.tcp_name = tool_name, tcp_name
        self.virtual_tcp_name = virtual_tcp_name
        self.tcp_offset_mm_deg = list(tcp_offset_mm_deg or [])
        self.startup_timeout_s = float(startup_timeout_s)
        self._now = now_fn or time.monotonic
        self._sleep = sleep_fn or time.sleep

    def initialize(self):
        """DSR 초기 설정. 반드시 skill_node의 DSR 워커에서 호출한다."""
        R = self.R
        # DSR_ROBOT2의 일부 설정 함수는 서비스 대기 없이 call_async부터 실행한다.
        # 컨트롤러 활성화 전에 호출하면 future가 끝나지 않으므로 공통 motion 서비스로 준비를 확인한다.
        if not self._move_stop_cli.wait_for_service(timeout_sec=self.startup_timeout_s):
            raise TimeoutError(
                f'DSR controller not ready after {self.startup_timeout_s:.1f}s')
        if self.mode == 'real':
            # 실물은 티칭 펜던트에 등록된 툴과 TCP를 사용한다 (SOT D-10).
            if self.tool_name:
                self._require_ok('set_tool', R.set_tool(self.tool_name))
            if self.tcp_name:
                self._require_ok('set_tcp', R.set_tcp(self.tcp_name))
        elif self.virtual_tcp_name:
            if len(self.tcp_offset_mm_deg) != 6:
                raise ValueError('virtual TCP offset must contain 6 values')
            # TCP 설정 명령은 수동 모드에서만 허용된다. 가상 컨트롤러에서만 잠시
            # 수동으로 전환해 등록·선택하고, 성공 여부와 관계없이 자동 모드로 복귀한다.
            self._require_ok(
                'set_robot_mode(MANUAL)', R.set_robot_mode(R.ROBOT_MODE_MANUAL))
            try:
                # 가상 컨트롤러에는 실물의 등록 TCP가 없으므로 같은 형상을 직접 만든다.
                # 이미 같은 이름이 남아 있으면 add_tcp는 실패하지만 set_tcp 성공으로 정상 처리한다.
                deadline = self._now() + self.startup_timeout_s
                last_add, last_set = None, None
                while True:
                    last_add = R.add_tcp(self.virtual_tcp_name, self.tcp_offset_mm_deg)
                    last_set = R.set_tcp(self.virtual_tcp_name)
                    if last_set == 0:
                        break
                    if self._now() >= deadline:
                        raise RuntimeError(
                            f'virtual TCP setup failed: name={self.virtual_tcp_name!r} '
                            f'add_tcp={last_add!r} set_tcp={last_set!r}')
                    self._sleep(0.2)
            finally:
                self._require_ok(
                    'set_robot_mode(AUTONOMOUS)',
                    R.set_robot_mode(R.ROBOT_MODE_AUTONOMOUS))
        self._require_ok('set_velj', R.set_velj(self.vel))
        self._require_ok('set_accj', R.set_accj(self.acc))
        self._require_ok('set_velx', R.set_velx(self.vel, self.vel))
        self._require_ok('set_accx', R.set_accx(self.acc, self.acc))
        self._require_ok('set_singular_handling', R.set_singular_handling(R.DR_AVOID))
        # 래퍼 기본값은 이미 DR_BASE다. 에뮬레이터의 set_ref_coord 서비스는 응답이
        # 와도 Python 래퍼 future가 끝나지 않는 버전이 있어 실물에서만 명시한다.
        if self.mode == 'real':
            self._require_ok('set_ref_coord', R.set_ref_coord(R.DR_BASE))

    # ── 이동 ────────────────────────────────────────────────────────────
    @staticmethod
    def _require_ok(command: str, result):
        """DSR_ROBOT2 명령은 성공 시 0, 서비스/컨트롤러 거부 시 -1을 반환한다."""
        if result != 0:
            raise RuntimeError(f'{command} failed: return={result!r}')
        return result

    def movej(self, j6, vel_scale=1.0):
        return self._require_ok(
            'movej', self.R.movej(self.posj(*j6), vel=self.vel * vel_scale,
                                  acc=self.acc * vel_scale))

    def movel(self, x6, vel_scale=1.0):
        return self._require_ok(
            'movel', self.R.movel(self.posx(*x6), vel=self.vel * vel_scale,
                                  acc=self.acc * vel_scale, ref=self.R.DR_BASE,
                                  mod=self.R.DR_MV_MOD_ABS))

    def amovej(self, j6, vel_scale=1.0):
        return self._require_ok(
            'amovej', self.R.amovej(self.posj(*j6), vel=self.vel * vel_scale,
                                    acc=self.acc * vel_scale))

    def amovel(self, x6, vel_scale=1.0):
        return self._require_ok(
            'amovel', self.R.amovel(self.posx(*x6), vel=self.vel * vel_scale,
                                    acc=self.acc * vel_scale, ref=self.R.DR_BASE,
                                    mod=self.R.DR_MV_MOD_ABS))

    def movesx(self, poses, vel_scale=1.0):
        return self._require_ok(
            'movesx', self.R.movesx([self.posx(*p) for p in poses], vel=self.vel * vel_scale,
                                    acc=self.acc * vel_scale))

    def amove_periodic(self, amp, period, atime, repeat, ref_tool=True):
        return self._require_ok(
            'amove_periodic',
            self.R.amove_periodic(amp, period, atime=atime, repeat=repeat,
                                  ref=self.R.DR_TOOL if ref_tool else self.R.DR_BASE))

    def motion_state(self):
        return self.R.check_motion()

    def wait_motion(self):
        return self._require_ok('mwait', self.R.mwait())

    def stop_motion(self, timeout_s: float = 2.0):
        """첨부 예제와 같은 MoveStop(DR_SSTOP) 감속 정지."""
        if not self._move_stop_cli.wait_for_service(timeout_sec=1.0):
            raise RuntimeError('motion/move_stop service is unavailable')
        req = self._MoveStop.Request()
        req.stop_mode = self.R.DR_SSTOP
        future = self._move_stop_cli.call_async(req)
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=timeout_s)
        if not future.done():
            raise TimeoutError('motion stop request timed out')
        response = future.result()
        if response is None or not response.success:
            raise RuntimeError('motion stop request failed')

    def wait_motion_cancellable(self, cancel_requested, timeout_s: float):
        """비동기 모션을 워커에서 감시하고 취소·시간초과 시 감속 정지한다."""
        started_at = self._now()
        deadline = started_at + timeout_s
        motion_started = False
        while True:
            if cancel_requested():
                self.stop_motion()
                raise RuntimeError('cancelled')
            if self._now() >= deadline:
                self.stop_motion()
                raise TimeoutError(f'motion timed out after {timeout_s:.1f}s')
            state = self.motion_state()
            if state != self.R.DR_STATE_IDLE:
                motion_started = True
            elif motion_started or self._now() - started_at >= 0.2:
                return
            self._sleep(0.02)

    def movej_cancellable(self, j6, vel_scale, cancel_requested, timeout_s):
        self.amovej(j6, vel_scale)
        self.wait_motion_cancellable(cancel_requested, timeout_s)
        actual = self.current_posj()
        if len(actual) != 6 or max(abs(float(a) - float(b)) for a, b in zip(actual, j6)) > 1.0:
            raise RuntimeError(f'movej target not reached: target={list(j6)} actual={actual}')

    def movel_cancellable(self, x6, vel_scale, cancel_requested, timeout_s):
        self.amovel(x6, vel_scale)
        self.wait_motion_cancellable(cancel_requested, timeout_s)
        actual = self.current_posx()
        xyz_error = (max(abs(float(a) - float(b)) for a, b in zip(actual[:3], x6[:3]))
                     if len(actual) == 6 else float('inf'))
        if xyz_error > 2.0:
            raise RuntimeError(f'movel target not reached: target={list(x6)} actual={actual}')

    def movel_rel_tool(self, dxyz, vel_scale=1.0):
        """툴 좌표계 상대 이동 (담그기·들어올리기)."""
        R = self.R
        return self._require_ok(
            'movel_rel_tool',
            R.movel(self.posx(dxyz[0], dxyz[1], dxyz[2], 0, 0, 0),
                    vel=self.vel * vel_scale, acc=self.acc * vel_scale,
                    ref=R.DR_TOOL, mod=R.DR_MV_MOD_REL))

    def current_posx(self):
        return list(self.R.get_current_posx()[0])

    def current_posj(self):
        return list(self.R.get_current_posj())

    # ── 관측 ────────────────────────────────────────────────────────────
    def tool_force(self):
        force = self.R.get_tool_force(self.R.DR_BASE)
        return list(force) if isinstance(force, (list, tuple)) and len(force) == 6 else None

    def _settle(self, duration_s: float, period_s: float, observer=None):
        end_s = self._now() + max(0.0, duration_s)
        while self._now() < end_s:
            force = self.tool_force() if observer else None
            if force is not None:
                observer(force)
            self._sleep(min(period_s, max(0.0, end_s - self._now())))

    def measure_force(self, samples: int, settle_s: float, period_s: float = 0.05, observer=None):
        """정지 상태 외력 평균 (계량 폴백). 반환: (mean6, fz_mean, fz_std, valid)."""
        self._settle(settle_s, 0.1, observer)
        rows = []
        for _ in range(samples):
            f = self.tool_force()
            if f is not None:
                rows.append(f)
                if observer:
                    observer(f)
            self._sleep(period_s)
        if len(rows) < max(3, samples // 2):
            return [0.0] * 6, 0.0, 0.0, False
        mean6 = [statistics.fmean(c) for c in zip(*rows)]
        fz = [r[2] for r in rows]
        return mean6, mean6[2], statistics.pstdev(fz), True

    def reset_workpiece(self):
        """빈 그리퍼·계량 자세에서 잔류 오차 제거 (매뉴얼 5.1.2). 세션마다 한 번."""
        return self.R.reset_workpiece_weight()

    def measure_workpiece(self, samples: int, settle_s: float, period_s: float = 0.1, observer=None):
        """get_workpiece_weight 평균 [kgf]. 반환: (mean_kg, std_kg, valid). 음수는 오류."""
        self._settle(settle_s, 0.1, observer)
        vals = []
        for _ in range(samples):
            w = self.R.get_workpiece_weight()
            if isinstance(w, (int, float)) and w >= 0:
                vals.append(float(w))
            if observer:
                force = self.tool_force()
                if force is not None:
                    observer(force)
            self._sleep(period_s)
        if len(vals) < max(3, samples // 2):
            return 0.0, 0.0, False
        return statistics.fmean(vals), statistics.pstdev(vals), True

    # ── 힘/순응 제어 — 반드시 짝으로 ────────────────────────────────────
    def compliance_on(self, stx):
        self.R.task_compliance_ctrl(stx)

    def force_z(self, fz: float, rel: bool = True):
        R = self.R
        R.set_desired_force([0, 0, fz, 0, 0, 0], [0, 0, 1, 0, 0, 0],
                            mod=R.DR_FC_MOD_REL if rel else R.DR_FC_MOD_ABS)

    def force_over(self, axis_z_min_n: float) -> bool:
        """|Fz|가 min 이상이면 True. ROS2 래퍼는 조건 충족 시 0을 반환한다."""
        R = self.R
        return R.check_force_condition(R.DR_AXIS_Z, min=axis_z_min_n, ref=R.DR_BASE) == 0

    def position_at_or_below(self, z_mm: float) -> bool:
        """베이스 Z가 상한 이하인지 확인한다. ROS2 래퍼의 성공값 0을 bool로 바꾸지 않는다."""
        R = self.R
        return R.check_position_condition(R.DR_AXIS_Z, max=z_mm, ref=R.DR_BASE) == 0

    def compliance_off(self):
        R = self.R
        try:
            R.release_force()
        finally:
            R.release_compliance_ctrl()

    # ── IO (dio 그리퍼 백엔드) ───────────────────────────────────────────
    def dout(self, idx: int, on: bool):
        self.R.set_digital_output(idx, 1 if on else 0)

    def din(self, idx: int) -> bool:
        return bool(self.R.get_digital_input(idx))

    # ── 자가진단 ─────────────────────────────────────────────────────────
    def self_check(self, expect_tool: str, expect_tcp: str):
        """툴·TCP가 기대값인지 확인한다. 충돌 감도 getter는 현재 래퍼에 없다."""
        if self.mode != 'real':
            return True, 'virtual: skip'
        R = self.R
        tool, tcp = R.get_tool(), R.get_tcp()
        ok = (not expect_tool or tool == expect_tool) and (not expect_tcp or tcp == expect_tcp)
        return ok, f'tool={tool} tcp={tcp}'
