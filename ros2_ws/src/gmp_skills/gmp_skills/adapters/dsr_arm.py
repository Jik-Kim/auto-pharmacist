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
                 tool_name: str = '', tcp_name: str = '', logger=None):
        self.mode = mode
        self.vel, self.acc = vel, acc
        self.log = logger
        DR_init.__dsr__id = robot_id
        DR_init.__dsr__model = robot_model
        # 네임스페이스는 반드시 ROBOT_ID 와 같아야 한다 (교육 자료)
        self.node = rclpy.create_node('gmp_dsr_client', namespace=robot_id)
        DR_init.__dsr__node = self.node
        import DSR_ROBOT2 as R   # DR_init 이후에 import (두산 튜토리얼 Caution)
        from DR_common2 import posx, posj
        self.R, self.posx, self.posj = R, posx, posj
        if mode == 'real':
            # 컨트롤러 등록명. 가상은 에뮬레이터에 미등록이라 건너뛴다 (SOT D-10)
            if tool_name:
                R.set_tool(tool_name)
            if tcp_name:
                R.set_tcp(tcp_name)
        R.set_velx(vel, vel)   # 병진 mm/s, 회전 deg/s
        R.set_accx(acc, acc)
        R.set_ref_coord(R.DR_BASE)

    # ── 이동 ────────────────────────────────────────────────────────────
    def movej(self, j6, vel_scale=1.0):
        return self.R.movej(self.posj(*j6), vel=self.vel * vel_scale, acc=self.acc * vel_scale)

    def movel(self, x6, vel_scale=1.0):
        return self.R.movel(self.posx(*x6), vel=self.vel * vel_scale, acc=self.acc * vel_scale)

    def movel_rel_tool(self, dxyz, vel_scale=1.0):
        """툴 좌표계 상대 이동 (담그기·들어올리기)."""
        R = self.R
        return R.movel(self.posx(dxyz[0], dxyz[1], dxyz[2], 0, 0, 0), vel=self.vel * vel_scale,
                       acc=self.acc * vel_scale, ref=R.DR_TOOL, mod=R.DR_MV_MOD_REL)

    def current_posx(self):
        return list(self.R.get_current_posx()[0])

    # ── 관측 ────────────────────────────────────────────────────────────
    def measure_force(self, samples: int, settle_s: float, period_s: float = 0.05):
        """정지 상태 외력 평균 (계량 폴백). 반환: (mean6, fz_mean, fz_std, valid)."""
        time.sleep(settle_s)
        rows = []
        for _ in range(samples):
            f = self.R.get_tool_force(self.R.DR_BASE)
            if isinstance(f, list) and len(f) == 6:
                rows.append(f)
            time.sleep(period_s)
        if len(rows) < max(3, samples // 2):
            return [0.0] * 6, 0.0, 0.0, False
        mean6 = [statistics.fmean(c) for c in zip(*rows)]
        fz = [r[2] for r in rows]
        return mean6, mean6[2], statistics.pstdev(fz), True

    def reset_workpiece(self):
        """빈 그리퍼·계량 자세에서 잔류 오차 제거 (매뉴얼 5.1.2). 세션마다 한 번."""
        return self.R.reset_workpiece_weight()

    def measure_workpiece(self, samples: int, settle_s: float, period_s: float = 0.1):
        """get_workpiece_weight 평균 [kgf]. 반환: (mean_kg, std_kg, valid). 음수는 오류."""
        time.sleep(settle_s)
        vals = []
        for _ in range(samples):
            w = self.R.get_workpiece_weight()
            if isinstance(w, (int, float)) and w >= 0:
                vals.append(float(w))
            time.sleep(period_s)
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

    def force_over(self, axis_z_max_n: float) -> bool:
        """|Fz| 가 max 를 넘으면 True (check_force_condition 은 크기만 본다, 매뉴얼 6.2.13)."""
        R = self.R
        return bool(R.check_force_condition(R.DR_AXIS_Z, max=axis_z_max_n, ref=R.DR_BASE))

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
        """툴·TCP 가 기대값인지. 가상은 항상 통과. TODO([A]): get_collision_sensitivity 도 비교."""
        if self.mode != 'real':
            return True, 'virtual: skip'
        R = self.R
        tool, tcp = R.get_current_tool(), R.get_current_tcp()
        ok = (not expect_tool or tool == expect_tool) and (not expect_tcp or tcp == expect_tcp)
        return ok, f'tool={tool} tcp={tcp}'
