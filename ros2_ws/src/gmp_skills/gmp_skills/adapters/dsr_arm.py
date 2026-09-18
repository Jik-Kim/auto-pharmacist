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
                 tool_name: str = '', tcp_name: str = '', logger=None, now_fn=None, sleep_fn=None):
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
        self.R, self.posx, self.posj = R, posx, posj
        self.tool_name, self.tcp_name = tool_name, tcp_name
        self._now = now_fn or time.monotonic
        self._sleep = sleep_fn or time.sleep

    def initialize(self):
        """DSR 초기 설정. 반드시 skill_node의 DSR 워커에서 호출한다."""
        R = self.R
        if self.mode == 'real':
            # 컨트롤러 등록명. 가상은 에뮬레이터에 미등록이라 건너뛴다 (SOT D-10)
            if self.tool_name:
                R.set_tool(self.tool_name)
            if self.tcp_name:
                R.set_tcp(self.tcp_name)
        R.set_velx(self.vel, self.vel)   # 병진 mm/s, 회전 deg/s
        R.set_accx(self.acc, self.acc)
        # 래퍼 기본값은 이미 DR_BASE다. 에뮬레이터의 set_ref_coord 서비스는 응답이
        # 와도 Python 래퍼 future가 끝나지 않는 버전이 있어 실물에서만 명시한다.
        if self.mode == 'real':
            R.set_ref_coord(R.DR_BASE)

    # ── 이동 ────────────────────────────────────────────────────────────
    def movej(self, j6, vel_scale=1.0):
        return self.R.movej(self.posj(*j6), vel=self.vel * vel_scale, acc=self.acc * vel_scale)

    def movel(self, x6, vel_scale=1.0):
        return self.R.movel(self.posx(*x6), vel=self.vel * vel_scale, acc=self.acc * vel_scale)

    def amovel(self, x6, vel_scale=1.0):
        return self.R.amovel(self.posx(*x6), vel=self.vel * vel_scale, acc=self.acc * vel_scale)

    def movesx(self, poses, vel_scale=1.0):
        return self.R.movesx([self.posx(*p) for p in poses], vel=self.vel * vel_scale,
                             acc=self.acc * vel_scale)

    def amove_periodic(self, amp, period, atime, repeat, ref_tool=True):
        return self.R.amove_periodic(amp, period, atime=atime, repeat=repeat,
                                    ref=self.R.DR_TOOL if ref_tool else self.R.DR_BASE)

    def motion_state(self):
        return self.R.check_motion()

    def wait_motion(self):
        return self.R.mwait()

    def movel_rel_tool(self, dxyz, vel_scale=1.0):
        """툴 좌표계 상대 이동 (담그기·들어올리기)."""
        R = self.R
        return R.movel(self.posx(dxyz[0], dxyz[1], dxyz[2], 0, 0, 0), vel=self.vel * vel_scale,
                       acc=self.acc * vel_scale, ref=R.DR_TOOL, mod=R.DR_MV_MOD_REL)

    def current_posx(self):
        return list(self.R.get_current_posx()[0])

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
