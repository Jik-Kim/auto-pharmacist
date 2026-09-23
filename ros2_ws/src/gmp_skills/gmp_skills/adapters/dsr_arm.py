"""DSR_ROBOT2 어댑터 — DR_init 노드를 소유하고 블로킹 함수를 감싼다.

**워커 스레드에서만 부른다** (SOT D-02). DSR_ROBOT2 는 호출마다
`rclpy.spin_until_future_complete(g_node, …)` 로 여기서 만든 노드를 직접 spin 하므로
이 노드는 executor 에 넣지 않는다.

교육 방식(rokey/move.py) 그대로: DR_init 채움 → 노드 생성 → DSR_ROBOT2 import.
실물로만 검증되는 것: 힘·작업물무게·툴/TCP 설정(가상은 에뮬레이터에 미등록이라 건너뛴다).

TODO([A]): 9/17 G1 — measure_force / measure_workpiece 분해능 실측.
TODO([A]): I-004 — 이동 취소 수단 (amovel + check_motion).
"""
import math
import statistics
import time

import rclpy
import DR_init

from gmp_skills.core.transfer import joints_match, pose_matches, vector6


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
        from dsr_msgs2.srv import MoveStop, SetRobotControl
        from gmp_interfaces.srv import GetCollisionSensitivity
        self.R, self.posx, self.posj = R, posx, posj
        self._SetRobotControl = SetRobotControl
        self._robot_control_cli = self.node.create_client(
            SetRobotControl, 'dsr_controller2/system/set_robot_control')
        self._collision_sensitivity_cli = self.node.create_client(
            GetCollisionSensitivity, 'dsr_controller2/system/get_collision_sensitivity')
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
            tool = self._bounded_call('get_current_tool').info
            tcp = self._bounded_call('get_current_tcp').info
            change_tool = bool(self.tool_name and tool != self.tool_name)
            change_tcp = bool(self.tcp_name and tcp != self.tcp_name)
            if change_tool or change_tcp:
                self._set_mode_checked(R.ROBOT_MODE_MANUAL)
                try:
                    if change_tool:
                        self._bounded_call('set_current_tool', name=self.tool_name)
                    if change_tcp:
                        self._bounded_call('set_current_tcp', name=self.tcp_name)
                finally:
                    self._set_mode_checked(R.ROBOT_MODE_AUTONOMOUS)
            else:
                # 재기동 시 선택값이 맞으면 수동 전환·동일 설정 재전송을 생략한다.
                self._set_mode_checked(R.ROBOT_MODE_AUTONOMOUS)
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
        if self.mode == 'real':
            self._bounded_call('set_singularity_handling', mode=R.DR_AVOID)
        else:
            self._require_ok('set_singular_handling', R.set_singular_handling(R.DR_AVOID))
        # 래퍼 기본값은 이미 DR_BASE다. 에뮬레이터의 set_ref_coord 서비스는 응답이
        # 와도 Python 래퍼 future가 끝나지 않는 버전이 있어 실물에서만 명시한다.
        if self.mode == 'real':
            self._bounded_call('set_ref_coord', coord=R.DR_BASE)

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
        # 스킬 내부 이동도 같은 워커의 취소 플래그로 감시한다.
        cancel = getattr(self, 'cancel_requested', None)
        if cancel is not None:
            return self.movel_cancellable(x6, vel_scale, cancel, self.motion_timeout_s)
        return self._require_ok(
            'movel', self.R.movel(self.posx(*x6), vel=self.vel * vel_scale,
                                  acc=self.acc * vel_scale, ref=self.R.DR_BASE,
                                  mod=self.R.DR_MV_MOD_ABS))

    def amovej(self, j6, vel_scale=1.0, *, joint_vel=None, joint_acc=None):
        return self._require_ok(
            'amovej', self.R.amovej(self.posj(*j6),
                                    vel=(self.vel if joint_vel is None else joint_vel) * vel_scale,
                                    acc=(self.acc if joint_acc is None else joint_acc) * vel_scale))

    def amovel(self, x6, vel_scale=1.0):
        return self._require_ok(
            'amovel', self.R.amovel(self.posx(*x6), vel=self.vel * vel_scale,
                                    acc=self.acc * vel_scale, ref=self.R.DR_BASE,
                                    mod=self.R.DR_MV_MOD_ABS))

    def solution_space(self):
        """단일 워커에서 현재 관절 구성을 제한 시간 내 조회한다."""
        sol = self._bounded_query('get_current_solution_space').sol_space
        if type(sol) is not int or not 0 <= sol <= 7:
            raise RuntimeError(f'잘못된 solution_space: {sol!r}')
        return sol

    def movejx_cancellable(self, x6, sol, vel_scale, cancel_requested, timeout_s):
        """상부 접근점으로 관절 이동하고 TCP와 선택한 관절 구성을 함께 확인한다."""
        if type(sol) is not int or not 0 <= sol <= 7:
            raise ValueError('solution_space는 0~7 정수여야 한다')
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError('motion timeout must be finite and positive')
        if cancel_requested():
            raise RuntimeError('cancelled')
        self._require_ok('amovejx', self.R.amovejx(
            self.posx(*x6), sol=sol, vel=self.vel * vel_scale,
            acc=self.acc * vel_scale, ref=self.R.DR_BASE, mod=self.R.DR_MV_MOD_ABS))
        self.wait_motion_cancellable(
            cancel_requested, timeout_s,
            target_reached=lambda: (
                pose_matches(self.current_posx(), x6, self.pose_xyz_tolerance,
                             self.pose_rotation_tolerance) and self.solution_space() == sol))

    def movesx(self, poses, vel_scale=1.0):
        return self._require_ok(
            'movesx', self.R.movesx([self.posx(*p) for p in poses], vel=self.vel * vel_scale,
                                    acc=self.acc * vel_scale))

    def transform_pose(self, pose, *, to_world):
        """BASE/WORLD 변환 조회도 단일 워커의 제한 시간 안에서 수행한다."""
        response = self._bounded_query(
            'coord_transform', pos_in=list(vector6(pose, '변환 입력')),
            ref_in=self.R.DR_BASE if to_world else self.R.DR_WORLD,
            ref_out=self.R.DR_WORLD if to_world else self.R.DR_BASE)
        return list(vector6(response.conv_posx, '변환 결과'))

    def movesx_cancellable(self, poses, vel, acc, cancel_requested, timeout_s, observer=None):
        points = [vector6(p, 'spline 경유점') for p in poses]
        if not points or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError('spline 경유점과 양수 제한 시간이 필요하다')
        if any(len(v) != 2 or any(not math.isfinite(x) or x <= 0 for x in v)
               for v in (vel, acc)):
            raise ValueError('spline 속도/가속도는 양수 2개여야 한다')
        if cancel_requested():
            raise RuntimeError('cancelled')
        try:
            self._require_ok('amovesx', self.R.amovesx(
                [self.posx(*p) for p in points], vel=list(vel), acc=list(acc),
                ref=self.R.DR_BASE, mod=self.R.DR_MV_MOD_ABS))
        except Exception:
            self.stop_motion()
            raise
        self.wait_motion_cancellable(
            cancel_requested, timeout_s, observer=observer,
            target_reached=lambda: pose_matches(
                self.current_posx(), points[-1], self.pose_xyz_tolerance,
                self.pose_rotation_tolerance))

    def amove_periodic(self, amp, period, atime, repeat, ref_tool=True):
        return self._require_ok(
            'amove_periodic',
            self.R.amove_periodic(amp, period, atime=atime, repeat=repeat,
                                  ref=self.R.DR_TOOL if ref_tool else self.R.DR_BASE))

    def _set_mode_checked(self, target):
        current = self._bounded_call('get_robot_mode').robot_mode
        if current not in (self.R.ROBOT_MODE_MANUAL, self.R.ROBOT_MODE_AUTONOMOUS):
            raise RuntimeError(f'기동 중 지원하지 않는 로봇 모드: {current}')
        if current != target:
            self._bounded_call('set_robot_mode', robot_mode=target)
            actual = self._bounded_call('get_robot_mode').robot_mode
            if actual != target:
                raise RuntimeError(f'로봇 모드 전환 미확인: expected={target}, actual={actual}')

    def _bounded_query(self, operation, **fields):
        return self._bounded_call(operation, **fields)

    def _bounded_call(self, operation, **fields):
        """벤더 클라이언트·승인된 감도 조회 확장으로 조회·초기화 응답을 제한한다."""
        client = (self._collision_sensitivity_cli if operation == 'get_collision_sensitivity'
                  else getattr(self.R, '_ros2_' + operation, None))
        if client is None or not callable(getattr(client, 'wait_for_service', None)):
            raise RuntimeError(f'{operation}: DSR 조회 클라이언트 준비 확인 기능 없음')
        timeout_s = self.startup_timeout_s
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError('robot.startup_timeout_s는 유한한 양수여야 한다')
        logger = getattr(self, 'log', None)
        phase = '서비스 준비'
        future = None
        try:
            if not client.wait_for_service(timeout_sec=timeout_s):
                raise TimeoutError(f'{operation}: {phase} 시간 초과 ({timeout_s:g}s)')
            request = client.srv_type.Request()
            for name, value in fields.items():
                setattr(request, name, value)
            phase = '응답'
            future = client.call_async(request)
            rclpy.spin_until_future_complete(self.node, future, timeout_sec=timeout_s)
            if not future.done():
                raise TimeoutError(f'{operation}: {phase} 시간 초과 ({timeout_s:g}s)')
            if future.cancelled():
                raise RuntimeError(f'{operation}: 조회 취소됨')
            response = future.result()
            if response is None or not response.success:
                raise RuntimeError(f'{operation}: 조회 실패 또는 빈 응답')
            return response
        except Exception as exc:
            if logger:
                logger.error(f'[DSR_QUERY_FAILED] {operation} / {phase}: {exc}')
            raise
        finally:
            if future is not None and not future.done():
                # 응답 대기만 취소한다. 장치에 전달된 설정·동작 취소를 뜻하지 않는다.
                future.cancel()

    def robot_state(self):
        state = self._bounded_query('get_robot_state').robot_state
        if not isinstance(state, int) or state < 0:
            raise RuntimeError(f'get_robot_state: 잘못된 상태값 {state!r}')
        return state

    def recover_control(self, control, timeout_s, dispatch):
        """D-01 승인 예외. 워커에서 기존 ROS 서비스 호출, 상태 확인은 호출자 책임."""
        if control not in (2, 3, 4, 5, 7):
            raise ValueError('허용하지 않은 복구 명령')
        if not self._robot_control_cli.wait_for_service(timeout_sec=timeout_s):
            raise RuntimeError('system/set_robot_control service is unavailable')
        # 서비스 대기 중 새 알람이 왔다면 복구 설정/명령 전송을 중단한다.
        dispatch(lambda: None)
        # 안전 정지 해제 후 DRL 프로그램을 자동 재개하지 않는다.
        if control == 2:
            self._require_ok('set_safe_stop_reset_type', self.R.set_safe_stop_reset_type(0))
        req = self._SetRobotControl.Request()
        req.robot_control = control
        future = dispatch(lambda: self._robot_control_cli.call_async(req))
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=timeout_s)
        if not future.done():
            future.cancel()
            raise TimeoutError('안전 복구 명령 응답 시간 초과. 상태 확인 후 새 요청 필요')
        response = future.result()
        if response is None or not response.success:
            raise RuntimeError('안전 복구 명령 거부')
        # 벤더는 항상 success=true를 반환할 수 있어 이것만으로 성공 처리하지 않는다.

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

    def wait_motion_cancellable(self, cancel_requested, timeout_s: float, observer=None,
                                target_reached=None, stop_requested=None):
        """시작 대기를 완료로 간주하지 않고 정지 상태와 실제 목표 도착을 확인한다."""
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError('motion timeout must be finite and positive')
        deadline = self._now() + timeout_s
        motion_started = False
        last_state = None
        stopping = False
        try:
            while True:
                if cancel_requested():
                    raise RuntimeError('cancelled')
                if self._now() >= deadline:
                    raise TimeoutError(f'motion timed out after {timeout_s:.1f}s; last_state={last_state}')
                if observer is not None:
                    observer()
                if cancel_requested():
                    raise RuntimeError('cancelled')
                if not stopping and stop_requested is not None and stop_requested():
                    # 접촉 정지는 정상 완료 경로다. 응답만으로 완료하지 않고 IDLE을 확인한다.
                    self.stop_motion()
                    stopping = True
                state = self.motion_state()
                if state not in (0, 1, 2):
                    raise RuntimeError(f'invalid motion state: {state!r}')
                if state != last_state:
                    log = getattr(getattr(self, 'log', None), 'info', None)
                    if log:
                        log(f'[MOTION_STATE] {last_state} -> {state}')
                    last_state = state
                if state != self.R.DR_STATE_IDLE:
                    motion_started = True
                # 동일 위치 요청·짧은 이동도 실제 목표 도착을 확인해야 완료한다.
                reached = (state == self.R.DR_STATE_IDLE and
                           (stopping or (target_reached() if target_reached is not None else motion_started)))
                # 조회·관측 중 발생한 취소/시간초과도 완료보다 먼저 처리한다.
                if cancel_requested():
                    raise RuntimeError('cancelled')
                if self._now() >= deadline:
                    raise TimeoutError(f'motion timed out after {timeout_s:.1f}s; last_state={last_state}')
                if reached:
                    return
                self._sleep(0.02)
        except Exception:
            self.stop_motion()
            raise

    def movej_cancellable(self, j6, vel_scale, cancel_requested, timeout_s,
                          *, joint_vel=None, joint_acc=None):
        if cancel_requested():
            raise RuntimeError('cancelled')
        self.amovej(j6, vel_scale, joint_vel=joint_vel, joint_acc=joint_acc)
        self.wait_motion_cancellable(
            cancel_requested, timeout_s,
            target_reached=lambda: joints_match(self.current_posj(), j6, self.joint_tolerance))

    def movel_cancellable(self, x6, vel_scale, cancel_requested, timeout_s, observer=None,
                         stop_requested=None):
        if cancel_requested():
            raise RuntimeError('cancelled')
        self.amovel(x6, vel_scale)
        self.wait_motion_cancellable(
            cancel_requested, timeout_s, observer=observer, stop_requested=stop_requested,
            target_reached=lambda: pose_matches(self.current_posx(), x6,
                                                self.pose_xyz_tolerance, self.pose_rotation_tolerance))

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
        force = list(self._bounded_query('get_tool_force', ref=self.R.DR_BASE).tool_force)
        if len(force) != 6 or not all(math.isfinite(value) for value in force):
            raise RuntimeError('get_tool_force: 유효하지 않은 6축 외력')
        return force

    def _settle(self, duration_s: float, period_s: float, observer=None):
        end_s = self._now() + max(0.0, duration_s)
        while self._now() < end_s:
            force = self.tool_force() if observer else None
            if force is not None:
                observer(force)
            self._sleep(min(period_s, max(0.0, end_s - self._now())))

    @staticmethod
    def _validate_period(period_s):
        if not math.isfinite(period_s) or period_s <= 0:
            raise ValueError('계량 표본 간격은 유한한 양수여야 한다')

    def _wait_sample(self, deadline, observer):
        """표본 시각까지 대기하되 넛지 관측값은 계량 통계에 넣지 않는다."""
        while True:
            remaining = deadline - self._now()
            if remaining <= 0:
                return
            self._sleep(min(0.1, remaining) if observer else remaining)
            if observer and self._now() < deadline:
                force = self.tool_force()
                if force is not None:
                    observer(force)

    def measure_force(self, samples: int, settle_s: float, period_s: float = 0.05, observer=None):
        """정지 외력 평균. period_s는 표본 시작 간격의 하한이다."""
        self._validate_period(period_s)
        self._settle(settle_s, 0.1, observer)
        rows = []
        for index in range(samples):
            deadline = self._now() + period_s
            f = self.tool_force()
            if f is not None:
                rows.append(f)
                if observer:
                    observer(f)
            if index + 1 < samples:
                self._wait_sample(deadline, observer)
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
        self._validate_period(period_s)
        self._settle(settle_s, 0.1, observer)
        vals = []
        for index in range(samples):
            deadline = self._now() + period_s
            w = self.R.get_workpiece_weight()
            if isinstance(w, (int, float)) and w >= 0:
                vals.append(float(w))
            if observer:
                force = self.tool_force()
                if force is not None:
                    observer(force)
            if index + 1 < samples:
                self._wait_sample(deadline, observer)
        if len(vals) < max(3, samples // 2):
            return 0.0, 0.0, False
        return statistics.fmean(vals), statistics.pstdev(vals), True

    # ── 힘/순응 제어 — 반드시 짝으로 ────────────────────────────────────
    def compliance_on(self, stx):
        result = self.R.task_compliance_ctrl(stx)
        logger = getattr(self, 'log', None)
        if logger is not None:
            logger.info(f'[COMPLIANCE_ON] task_compliance_ctrl return={result!r}')
        return self._require_ok('task_compliance_ctrl', result)

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
            self._require_ok('release_force', R.release_force())
        finally:
            self._require_ok('release_compliance_ctrl', R.release_compliance_ctrl())

    # ── IO (dio 그리퍼 백엔드) ───────────────────────────────────────────
    def dout(self, idx: int, on: bool):
        self._require_ok('set_digital_output', self.R.set_digital_output(idx, 1 if on else 0))

    def din(self, idx: int) -> bool:
        value = self.R.get_digital_input(idx)
        if value not in (0, 1):
            raise RuntimeError(f'get_digital_input({idx}) 실패: {value}')
        return bool(value)

    # ── 자가진단 ─────────────────────────────────────────────────────────
    def self_check(self, expect_tool: str, expect_tcp: str, expect_collision: float):
        """툴·TCP·전역 충돌 감도를 확인한다. 감도를 변경하거나 실물 성능을 보장하지 않는다."""
        if (isinstance(expect_collision, bool) or not isinstance(expect_collision, (int, float))
                or not math.isfinite(expect_collision) or not 0 <= expect_collision <= 100):
            raise ValueError('safety.collision_sensitivity는 유한한 0~100 % 값이어야 한다')
        if self.mode != 'real':
            return True, 'virtual: 실물 툴/TCP/충돌 감도 검증 생략'
        tool = self._bounded_call('get_current_tool').info
        tcp = self._bounded_call('get_current_tcp').info
        sensitivity = self._bounded_call('get_collision_sensitivity').sensitivity
        if (isinstance(sensitivity, bool) or not isinstance(sensitivity, (int, float))
                or not math.isfinite(sensitivity) or not 0 <= sensitivity <= 100):
            raise RuntimeError(f'충돌 감도 응답 범위 오류: {sensitivity!r}')
        ok = ((not expect_tool or tool == expect_tool) and (not expect_tcp or tcp == expect_tcp)
              and sensitivity == expect_collision)
        return ok, (f'tool={tool} tcp={tcp} collision_sensitivity={sensitivity:g}% '
                    f'expected={expect_collision:g}%')
