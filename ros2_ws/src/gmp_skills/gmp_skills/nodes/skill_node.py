"""로봇 스킬 서버. 로봇을 만지는 유일한 노드 (AGENTS.md 프로젝트 구조).

스레드 구조 (SOT D-02 · 교육 「두산 ROS2 동작 Sequence」 3장)
  Main   : MultiThreadedExecutor.spin(이 노드, ns cell) — Action/Service 콜백, gripper_state 10 Hz
  Worker : 큐에서 Job 을 꺼내 DsrArm/Rg2Gripper 를 블로킹 호출. **로봇 명령은 항상 직렬**
  DR_init 노드(ns dsr01) : DsrArm 이 소유, executor 에 넣지 않는다

입력  Action move_to_station·scoop·pour·return_material·weigh_container·weigh_held / Service set_gripper·measure_force·safe_pose
      /onrobot_joint_states (real) 또는 gripper_joint_states (virtual)
출력  gripper_state (10 Hz, BEST_EFFORT) · event

파라미터는 gmp_bringup/params/common.yaml 이 단일 출처. stations.yaml 경로는 파라미터 `stations_file`.

종료 시 ROS 문맥을 유지한 채 워커에서 정지·힘제어 해제를 시도한다.
"""
import queue
import csv
from pathlib import Path
import json
import signal
import math
import threading
import time
import uuid
from dataclasses import dataclass, field

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy

from sensor_msgs.msg import JointState
from dsr_msgs2.msg import RobotError
from onrobot_rg_msgs.srv import SetCommand
from onrobot_rg_msgs.msg import OnRobotRGInput
from gmp_interfaces.action import MoveToStation, ReturnMaterial, Scoop, Pour, WeighContainer, WeighHeld
from gmp_interfaces.msg import CellEvent, GripperState, WeightReading
from gmp_interfaces.srv import MeasureForce, SafePose, SetGripper, RecoverSafety
from gmp_dosing.core.scale import ScaleConfig, WeightModel, fit_oscillation

from gmp_skills.adapters.dsr_arm import DsrArm
from gmp_skills.adapters.rg2_gripper import Rg2Gripper
from gmp_skills.core.nudge import NudgeDetector
from gmp_skills.core.scooping import finite, plan_scoop, tip_offset_local, tip_z
from gmp_skills.core.transfer import vector6
from gmp_skills.core.recovery import recovery_step, STANDBY
from gmp_skills.core.stations import StationTable
from gmp_skills.core.surface_height import tip_position_base
from gmp_skills.core.transfer import MotionAnchor, joints_match, pose_matches, validate_start


@dataclass
class Job:
    kind: str
    args: dict
    done: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: str = ''
    cancel: bool = False
    feedback: object = None      # callable(phase) — 액션이면 피드백 발행


class SkillNode(Node):
    def __init__(self):
        # 기본값과 설명은 gmp_bringup/params/common.yaml 한 곳에서 관리한다.
        # launch 또는 --params-file로 전달된 값만 자동 선언해 코드와 YAML의 중복을 없앤다.
        super().__init__('skill_node', automatically_declare_parameters_from_overrides=True)
        g = lambda k: self.get_parameter(k).value  # noqa: E731
        self._scale_period_s()  # 장치 생성 전에 잘못된 계량 설정을 거부한다.
        self.mode = g('mode')
        self.height_measure_only = g('scoop.height_measure_only')
        if type(self.height_measure_only) is not bool:
            raise ValueError('scoop.height_measure_only는 bool이어야 한다')
        collision = g('safety.collision_sensitivity')
        if (isinstance(collision, bool) or not isinstance(collision, (int, float))
                or not math.isfinite(collision) or not 0 <= collision <= 100):
            raise ValueError('safety.collision_sensitivity는 유한한 0~100 % 값이어야 한다')
        self.vel_scale = float(g('robot.vel_scale'))
        self.motion_timeout_s = float(g('robot.motion_timeout_s'))
        self.scoop_extract_y_mm = float(g('gripper.scoop_extract_y_mm'))
        self.scoop_extract_lift_z_mm = float(g('gripper.scoop_extract_lift_z_mm'))
        self.stations = StationTable.from_yaml(g('stations_file'))
        self._cartesian_ready = False
        self._station_id = ''
        self._motion_anchor = None
        self._held_payload = 'unknown'
        self._held_material_id = ''
        self._empty_scoop_force_baseline = None
        self._empty_scoop_baseline_pending = False
        self.transfer_joint_vel = float(g('robot.transfer_joint_vel_deg_s'))
        self.transfer_joint_acc = float(g('robot.transfer_joint_acc_deg_s2'))
        self.pose_xyz_tolerance = float(g('robot.pose_xyz_tolerance_mm'))
        self.pose_rotation_tolerance = float(g('robot.pose_rotation_tolerance_deg'))
        self.joint_tolerance = float(g('robot.joint_tolerance_deg'))
        for value in (self.pose_xyz_tolerance, self.pose_rotation_tolerance, self.joint_tolerance):
            if not math.isfinite(value) or value <= 0:
                raise ValueError('도착·출발 검증 허용오차는 유한한 양수여야 한다')
        self._return_rescoop_blocked = False  # 연결 경로 구현 전에는 반환 후 재스쿱 금지
        self._pending_scoop_extract = False
        self._scoop_extract_uncertain = False
        self.arm = DsrArm(g('robot.id'), g('robot.model'), self.mode, float(g('robot.vel')), float(g('robot.acc')),
                          g('robot.tool_name'), g('robot.tcp_name'), self.get_logger(), self._now_s,
                          startup_timeout_s=float(g('robot.startup_timeout_s')),
                          virtual_tcp_name=g('robot.virtual_tcp_name'),
                          tcp_offset_mm_deg=g('robot.tcp_offset_mm_deg'),
                          task_vel=g('robot.task_vel'), task_acc=g('robot.task_acc'))

        backend = 'virtual' if self.mode == 'virtual' else g('gripper.backend')
        self._grip_cli = (self.create_client(SetCommand, '/onrobot/sendCommand')
                          if backend != 'dio' else None)
        self.gripper = Rg2Gripper(backend, self._send_gripper_command, self.arm,
                                  float(g('gripper.grip_margin_mm')), float(g('gripper.slip_mm')),
                                  float(g('gripper.open_width_mm')), tuple(g('gripper.dio_pins')),
                                  tuple(g('gripper.din_pins')), self.get_logger(), self._now_s,
                                  float(g('gripper.state_timeout_s')),
                                  float(g('gripper.dio_settle_s')), float(g('gripper.completion_settle_s')))
        if backend == 'modbus':
            self.create_subscription(OnRobotRGInput, '/onrobot/status',
                                     lambda msg: self.gripper.on_native_status(msg, self._now_s()),
                                     QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        elif backend == 'virtual':
            self.create_subscription(JointState, f"/{g('robot.id')}/gripper_joint_states", self._on_js,
                                     QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))

        self.cb = ReentrantCallbackGroup()
        self.pub_state = self.create_publisher(GripperState, 'gripper_state',
                                               QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.pub_event = self.create_publisher(CellEvent, 'event', 100)
        self.create_timer(0.1, self._pub_gripper_state, callback_group=self.cb)

        self._nudge_enabled = bool(g('safety.nudge_enabled')) and not bool(g('scale.simulated'))
        self._nudge = NudgeDetector(float(g('safety.nudge_force_n')), float(g('safety.nudge_window_s')),
                                     float(g('safety.nudge_cooldown_s')))
        self._nudge_fault_logged = False

        self._q: 'queue.Queue[Job]' = queue.Queue()
        self._job_lock = threading.Lock()
        self._current: Job | None = None
        self._stopping = threading.Event()
        self._worker_stopped = threading.Event()
        self._cleanup_error = ''
        self.shutdown_timeout_s = float(g('robot.shutdown_timeout_s'))
        if not math.isfinite(self.shutdown_timeout_s) or self.shutdown_timeout_s <= 0:
            raise ValueError('robot.shutdown_timeout_s는 유한한 양수여야 한다')
        self.arm.cancel_requested = self._cancel_requested
        self.gripper.cancel_requested = self._cancel_requested
        self.arm.motion_timeout_s = self.motion_timeout_s
        self.arm.pose_xyz_tolerance = self.pose_xyz_tolerance
        self.arm.pose_rotation_tolerance = self.pose_rotation_tolerance
        self.arm.joint_tolerance = self.joint_tolerance
        self._safety_latched = False
        self._safety_reason = ''
        self._safety_revision = 0
        self._safety_session = uuid.uuid4().hex
        self._last_robot_state = -1
        self._last_state_poll = float('-inf')
        self._configured = False
        self._recovery_requests = {}
        self._recovery_inflight = False
        self.state_poll_s = float(g('safety.state_poll_s'))
        self.recovery_timeout_s = float(g('safety.recovery_timeout_s'))
        self.recovery_cache_size = int(g('safety.recovery_cache_size'))
        if (any(not math.isfinite(v) or v <= 0 for v in
                (self.state_poll_s, self.recovery_timeout_s)) or self.recovery_cache_size <= 0):
            raise ValueError('안전 조회·복구 설정은 유한한 양수여야 한다')
        self._worker_thread = threading.Thread(target=self._worker, daemon=True, name='dsr-worker')
        self._worker_thread.start()
        self._ready = False
        self._startup_job = Job('startup', {'expect_tool': g('robot.tool_name'),
                                             'expect_tcp': g('robot.tcp_name'),
                                             'restore_material_id': g('restore.material_id'),
                                             'restore_operator_id': g('restore.operator_id'),
                                             'restore_confirmed': g('restore.confirmed'),
                                             'restore_empty_scoop_confirmed': g('restore.empty_scoop_confirmed')})
        self._q.put(self._startup_job)

        # 서버 객체를 멤버로 유지해야 가비지 컬렉션 뒤에도 ROS 그래프에 계속 남는다.
        self._action_servers = [
            ActionServer(self, MoveToStation, 'move_to_station', self._exec_move,
                         callback_group=self.cb, goal_callback=lambda _: GoalResponse.ACCEPT,
                         cancel_callback=self._on_cancel),
            ActionServer(self, Scoop, 'scoop', self._exec_scoop, callback_group=self.cb,
                         cancel_callback=self._on_cancel),
            ActionServer(self, Pour, 'pour', self._exec_pour, callback_group=self.cb,
                         cancel_callback=self._on_cancel),
            ActionServer(self, ReturnMaterial, 'return_material', self._exec_return_material,
                         callback_group=self.cb, cancel_callback=self._on_cancel),
            ActionServer(self, WeighContainer, 'weigh_container', self._exec_weigh,
                         callback_group=self.cb, cancel_callback=self._on_cancel),
            ActionServer(self, WeighHeld, 'weigh_held', self._exec_weigh_held,
                         callback_group=self.cb, cancel_callback=self._on_cancel),
        ]
        self.get_logger().info(
            '[ACTION_SERVERS_READY] move_to_station, scoop, pour, return_material, weigh_container, weigh_held')
        self.create_service(SetGripper, 'set_gripper', self._srv_set_gripper, callback_group=self.cb)
        self.create_service(MeasureForce, 'measure_force', self._srv_measure, callback_group=self.cb)
        self.create_service(RecoverSafety, 'recover_safety', self._srv_recover, callback_group=self.cb)
        self.create_subscription(RobotError, f"/{g('robot.id')}/dsr_controller2/error",
                                 self._on_robot_alarm, 100, callback_group=self.cb)
        self.create_service(SafePose, 'safe_pose', self._srv_safe, callback_group=self.cb)

    # ── 공용 ────────────────────────────────────────────────────────────
    def _now_s(self):
        return self.get_clock().now().nanoseconds / 1e9

    def event(self, level: str, code: str, text: str, batch_id: str = ''):
        m = CellEvent(level=getattr(CellEvent, level), code=code, text=text, batch_id=batch_id)
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_event.publish(m)
        if level == 'ERROR':
            self.get_logger().error(f'[{code}] {text}')
        else:
            self.get_logger().info(f'[{code}] {text}')

    def _send_gripper_command(self, cmd: str) -> bool:
        if not self._grip_cli.wait_for_service(timeout_sec=2.0):
            self.event('ERROR', 'GRIPPER_SVC', '/onrobot/sendCommand 없음')
            return False
        fut = self._grip_cli.call_async(SetCommand.Request(command=cmd))
        t0 = self._now_s()
        while not fut.done() and self._now_s() - t0 < 3.0:
            time.sleep(0.01)          # 워커 스레드에서 호출되므로 spin 하지 않는다 — executor 가 돌린다
        return bool(fut.done() and fut.result().success)

    def _on_js(self, msg: JointState):
        source_s = msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9
        stamp_s = source_s if source_s > 0.0 else self._now_s()
        for n, pos in zip(msg.name, msg.position):
            if n.endswith('finger_joint'):
                self.gripper.on_joint_state(pos, stamp_s)
                break

    def _pub_gripper_state(self):
        state = self.gripper.state(self._now_s())
        w = state['width_mm']
        m = GripperState(width_mm=-1.0 if w is None else w, busy=state['busy'],
                         grip_inferred=state['grip_inferred'],
                         safety_triggered=state.get('safety_triggered', False), force_cmd_n=self.gripper.force_cmd_n, backend=self.gripper.backend)
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_state.publish(m)
        if self.gripper.consume_slip():
            self.event('WARN', 'GRIP_SLIP', f'폭 변화가 slip_mm를 초과함: width={m.width_mm:.2f} mm')

    def _observe_force(self, force6):
        if self._cancel_requested():
            raise RuntimeError('cancelled')
        if self._nudge_enabled and self._nudge.update(force6, self._now_s()):
            magnitude_n = math.sqrt(sum(float(v) ** 2 for v in force6[:3]))
            self.event('INFO', 'NUDGE', f'외력 nudge 입력 감지: |F|={magnitude_n:.2f} N')

    def _poll_nudge(self):
        if not self._nudge_enabled or self._safety_latched or not self._configured:
            return
        try:
            force = self.arm.tool_force()
            if force is None:
                raise RuntimeError('외력 조회 결과 없음')
            self._observe_force(force)
            self._nudge_fault_logged = False
        except Exception as exc:  # noqa: BLE001 — 유휴 감시 실패가 워커를 죽이면 안 된다
            if not self._nudge_fault_logged:
                self.event('WARN', 'NUDGE_UNAVAILABLE', f'외력 감시 실패: {exc}')
                self._nudge_fault_logged = True
            self._latch_safety(f'외력 조회 실패: {exc}')
            # 스킬 수행 중에는 이 실패를 삼키고 다음 이동을 이어가지 않는다.
            if self._current is not None:
                raise

    def _wait_with_nudge(self, duration_s: float, job: Job):
        end_s = self._now_s() + max(0.0, duration_s)
        while self._now_s() < end_s:
            if job.cancel:
                raise RuntimeError('cancelled')
            self._poll_nudge()
            time.sleep(min(0.1, max(0.0, end_s - self._now_s())))

    # ── 워커: 로봇 명령은 여기서만 ──────────────────────────────────────
    def _latch_safety(self, reason, *, alarm=False, recovery_request=None):
        # 콜백은 상태만 저장한다. 정지·복구 명령은 워커에서만 실행한다.
        with self._job_lock:
            changed = not self._safety_latched or reason != self._safety_reason
            if changed or alarm or recovery_request is not None:
                self._safety_revision += 1
            self._safety_latched = True
            self._safety_reason = reason
            self._motion_anchor = None
            self._held_payload = 'unknown'
            self._held_material_id = ''
            self._empty_scoop_force_baseline = None
            self._empty_scoop_baseline_pending = False
            self._scoop_extract_uncertain = True
            if self._current and self._current.kind != 'startup':
                self._current.cancel = True
            self._drain_jobs_locked(f'SAFETY_STOP: {reason}')
            # 상태 변경과 발행을 직렬화한다. 실제 알람에는 복구 요청 상관관계를 붙이지 않는다.
            if changed or alarm or recovery_request is not None:
                detail = dict(robot_state=self._last_robot_state, reason=reason,
                              origin='robot_alarm' if alarm else 'state_monitor',
                              safety_session=self._safety_session,
                              safety_revision=self._safety_revision)
                if recovery_request is not None:
                    detail.update(origin='recovery_request',
                                  request_id=recovery_request.request_id,
                                  operator_id=recovery_request.operator_id)
                self.event('ERROR', 'ROBOT_SAFETY_STOP', json.dumps(detail, ensure_ascii=False))
            return self._safety_revision

    def _poll_safety(self, force=False):
        if self.mode == 'virtual':
            return
        now = self._now_s()
        if not force and now - self._last_state_poll < self.state_poll_s:
            return
        self._last_state_poll = now
        try:
            self._last_robot_state = self.arm.robot_state()
        except Exception as exc:  # noqa: BLE001 — 조회 불가도 동작 허용 근거가 아니다.
            self._last_robot_state = -1
            self._latch_safety(f'로봇 상태 조회 실패: {exc}')
            return
        if self._last_robot_state not in (1, 2):
            self._latch_safety(f'로봇 상태 {self._last_robot_state}: 작업자 복구 필요')

    def _on_robot_alarm(self, msg):
        if msg.level >= 3 or (msg.group == 5 and msg.level >= 2):
            self._latch_safety(f'vendor alarm {msg.group}/{msg.code}: {msg.msg1}', alarm=True)

    def _do_recover(self, job):
        args = job.args
        if self.mode == 'virtual':
            return False, True, -1, '가상 모드의 안전 복구는 실물 복구 성공으로 처리하지 않습니다'
        revision = args.get('safety_revision', self._safety_revision)
        if revision != self._safety_revision:
            return False, True, self._last_robot_state, '복구 대기 중 새 정지 발생. 차단 유지'
        state = self.arm.robot_state()
        self._last_robot_state = state
        if state != args['expected_state']:
            return False, True, state, '로봇 상태가 요청 이후 변경됐습니다. 상태를 확인하고 새 요청을 보내세요'
        step = recovery_step(state, args['operator_confirmed'])
        if step.control is not None:
            if self._stopping.is_set() or job.cancel:
                raise RuntimeError('복구 요청 취소됨')
            def dispatch(operation):
                # 비동기 전송 순간만 잠근다. 응답 대기 중에는 알람 콜백이 실행돼야 한다.
                with self._job_lock:
                    if (self._safety_revision != revision or self._stopping.is_set()
                            or job.cancel):
                        raise RuntimeError('복구 명령 전 새 알람·종료·취소 발생')
                    return operation()
            self.arm.recover_control(step.control, self.recovery_timeout_s, dispatch)
        deadline = self._now_s() + self.recovery_timeout_s
        while True:
            if self._stopping.is_set() or job.cancel:
                raise RuntimeError('복구 요청 취소됨')
            state = self.arm.robot_state()
            self._last_robot_state = state
            if state == step.target:
                break
            if self._now_s() >= deadline:
                return False, True, state, '복구 후 기대 상태 미도달. 작업자 확인 필요'
            time.sleep(self.state_poll_s)
        if step.manual_required:
            return False, True, state, '복구 모드 진입. 펜던트에서 원인 제거·자세 교정 후 새 복구 요청 필요'
        # STANDBY에서도 남아 있는 힘제어 해제 실패를 숨기지 않는다.
        self.arm.compliance_off()
        if not self._configured:
            self.arm.initialize()
        # 복구 때도 감도 변경·조회 실패를 확인한 뒤에만 차단을 해제한다.
        ok, detail = self.arm.self_check(self.get_parameter('robot.tool_name').value,
                                         self.get_parameter('robot.tcp_name').value,
                                         self.get_parameter('safety.collision_sensitivity').value)
        if not ok:
            self._configured = False
            raise RuntimeError(f'복구 후 자가진단 실패: {detail}')
        self._configured = True
        state = self.arm.robot_state()
        with self._job_lock:
            if (self._safety_revision != revision or self._stopping.is_set()
                    or job.cancel or state != STANDBY):
                return False, True, state, '복구 중 새 알람·정지·상태 변경 발생. 차단 유지'
            self._safety_latched = False
            self._safety_reason = ''
            self._station_id = ''
            self._motion_anchor = None
            self._cartesian_ready = False
            self._held_payload = 'unknown'
            self._held_material_id = ''
            self._empty_scoop_force_baseline = None
            self._empty_scoop_baseline_pending = False
            self._pending_scoop_extract = False
            # 자동 재개는 금지하고 이후 명시적 SafePose/현장 재설정을 요구한다.
            self._scoop_extract_uncertain = True
        return True, False, state, '로봇 복구 확인. 배치 재개·자세 이동은 수행하지 않았습니다'

    def _srv_recover(self, req, res):
        fingerprint = (req.operator_id, req.expected_state, req.operator_confirmed)
        if not req.request_id.strip() or not req.operator_id.strip() or not req.operator_confirmed:
            res.success, res.manual_required, res.robot_state = False, True, -1
            res.message = '작업자·고유 요청 ID·원인 제거 확인이 필요합니다'
            return res
        with self._job_lock:
            entry = self._recovery_requests.get(req.request_id)
            if entry is None:
                if self._recovery_inflight:
                    res.success, res.manual_required, res.robot_state = False, False, self._last_robot_state
                    res.message = '다른 복구 요청 처리 중입니다'
                    return res
                if len(self._recovery_requests) >= self.recovery_cache_size:
                    res.success, res.manual_required, res.robot_state = False, True, -1
                    res.message = '기동 세션 복구 요청 기록 상한 도달. 운영자 확인 필요'
                    return res
                entry = {'fingerprint': fingerprint, 'done': threading.Event()}
                self._recovery_requests[req.request_id] = entry
                self._recovery_inflight = True
                owner = True
            else:
                owner = False
        if entry['fingerprint'] != fingerprint:
            res.success, res.manual_required, res.robot_state = False, True, -1
            res.message = '같은 요청 ID의 내용이 다릅니다'
            return res
        if owner:
            try:
                # 복구 요청 자체도 동작 차단을 먼저 설정한다. 자동 리셋된 상태도 명시 확인한다.
                revision = self._latch_safety('HMI 안전 복구 요청', recovery_request=req)
                job = self._submit('recover', operator_id=req.operator_id,
                                   safety_revision=revision,
                                   expected_state=req.expected_state,
                                   operator_confirmed=req.operator_confirmed)
                result = job.result if not job.error and job.result else (
                    False, True, self._last_robot_state, job.error or '복구 결과 없음')
                with self._job_lock:
                    if result[0] and self._safety_latched:
                        result = False, True, self._last_robot_state, '복구 직후 새 정지 발생. 차단 유지'
                    entry['result'] = result
                    entry['revision'] = self._safety_revision
                    self.event('INFO' if result[0] else 'WARN', 'ROBOT_SAFETY_RECOVERY', json.dumps(
                        {'request_id': req.request_id, 'operator_id': req.operator_id,
                         'safety_session': self._safety_session,
                         'safety_revision': self._safety_revision,
                         'success': result[0], 'manual_required': result[1],
                         'robot_state': result[2], 'message': result[3]}, ensure_ascii=False))
            finally:
                entry.setdefault('result', (False, True, -1, '복구 처리 실패'))
                entry.setdefault('revision', self._safety_revision)
                with self._job_lock:
                    self._recovery_inflight = False
                    entry['done'].set()
        elif not entry['done'].is_set():
            res.success, res.manual_required, res.robot_state = False, False, self._last_robot_state
            res.message = '동일 복구 요청 처리 중입니다. 같은 ID로 결과를 재조회하세요'
            return res
        result = entry['result']
        with self._job_lock:
            if result[0] and (entry['revision'] != self._safety_revision or self._safety_latched):
                result = False, True, self._last_robot_state, '이전 복구 결과 이후 새 정지 발생. 새 요청 필요'
        res.success, res.manual_required, res.robot_state, res.message = result
        return res

    def check_startup(self):
        if self._ready or not self._startup_job.done.is_set():
            return
        job = self._startup_job
        if job.cancel or job.error or not job.result or not job.result[0]:
            raise RuntimeError(f'자가진단 실패 — 기동 거부: {job.error or job.result}')
        self._ready = True
        self.event('INFO', 'SELF_CHECK', f'OK {job.result[1]}')

    def _cancel_requested(self):
        if self._current and self._current.kind not in ('startup', 'recover'):
            self._poll_safety()
        return (self._stopping.is_set() or bool(self._current and self._current.cancel)
                or (self._safety_latched and bool(self._current)
                    and self._current.kind not in ('startup', 'recover')))

    def _drain_jobs_locked(self, reason):
        while True:
            try:
                job = self._q.get_nowait()
            except queue.Empty:
                return
            job.cancel, job.error = True, reason
            job.done.set()

    def shutdown(self):
        """DSR 호출은 워커에 맡기고 정해진 시간까지만 기다린다."""
        with self._job_lock:
            self._stopping.set()
            if self._current:
                self._current.cancel = True
            self._drain_jobs_locked('skill_node shutdown')
        self._worker_thread.join(self.shutdown_timeout_s)
        stopped = self._worker_stopped.is_set()
        if not stopped:
            self._cleanup_error = '워커 종료 시간 초과: 정지·힘제어 해제 확인 불가'
        if self._cleanup_error:
            self.get_logger().error(self._cleanup_error)
        return stopped and not self._cleanup_error

    def _submit(self, kind: str, feedback=None, **args) -> Job:
        job = Job(kind, args, feedback=feedback)
        with self._job_lock:
            if self._stopping.is_set() or self._worker_stopped.is_set():
                job.cancel, job.error = True, 'skill_node shutdown'
                job.done.set()
                return job
            if self._safety_latched and kind != 'recover':
                job.error = f'SAFETY_STOP: {self._safety_reason}'
                job.done.set()
                return job
            if not self._ready:
                job.error = '기동 자가진단 대기 중'
                job.done.set()
                return job
            self._q.put(job)
        job.done.wait()
        return job

    def _worker(self):
        try:
            while rclpy.ok() and not self._stopping.is_set():
                try:
                    job = self._q.get(timeout=0.1)
                except queue.Empty:
                    self._poll_safety()
                    if getattr(self, '_configured', False) and getattr(getattr(self, 'gripper', None), 'backend', '') == 'dio':
                        try:
                            self.gripper.refresh_dio()
                        except Exception as exc:
                            self._latch_safety(f'그리퍼 DI 조회 실패: {exc}')
                    self._poll_nudge()
                    continue
                with self._job_lock:
                    self._current = job
                    if self._stopping.is_set():
                        job.cancel = True
                try:
                    if job.kind not in ('startup', 'recover'):
                        self._poll_safety(force=True)
                    if job.cancel or (self._safety_latched and job.kind not in ('startup', 'recover')):
                        raise RuntimeError(f'SAFETY_STOP: {self._safety_reason}' if self._safety_latched else 'cancelled')
                    if job.kind in ('scoop', 'pour', 'return_material', 'weigh_held', 'safe'):
                        self._motion_anchor = None
                    if job.kind == 'weigh':
                        self._held_payload = 'unknown'
                        self._held_material_id = ''
                        self._empty_scoop_force_baseline = None
                        self._empty_scoop_baseline_pending = False
                    job.result = getattr(self, f'_do_{job.kind}')(job)
                except Exception as e:  # noqa: BLE001
                    self._motion_anchor = None
                    self._held_payload = 'unknown'
                    self._held_material_id = ''
                    self._empty_scoop_force_baseline = None
                    self._empty_scoop_baseline_pending = False
                    job.error = f'{type(e).__name__}: {e}'
                    if not self._stopping.is_set():
                        if isinstance(e, TimeoutError):
                            self._latch_safety(f'{job.kind} 응답 시간 초과: {e}')
                        self.event('ERROR', f'{job.kind.upper()}_FAIL', job.error)
                finally:
                    with self._job_lock:
                        if job.cancel:
                            self._motion_anchor = None
                            self._held_payload = 'unknown'
                            self._held_material_id = ''
                            self._empty_scoop_force_baseline = None
                            self._empty_scoop_baseline_pending = False
                        self._current = None
                        job.done.set()
        finally:
            # 정지 요청 실패와 무관하게 두 힘제어 해제를 시도한다.
            errors = []
            for name in ('stop_motion', 'compliance_off'):
                try:
                    getattr(self.arm, name)()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f'{name}: {exc}')
            with self._job_lock:
                self._stopping.set()
                self._drain_jobs_locked('skill worker stopped')
                self._cleanup_error = '; '.join(errors)
                self._worker_stopped.set()

    def _do_startup(self, job: Job):
        restore_requested = any(job.args.get(k) for k in
                                ('restore_material_id', 'restore_operator_id', 'restore_confirmed'))
        self._poll_safety(force=True)
        if self._safety_latched:
            if restore_requested:
                raise RuntimeError('안전 차단 중에는 파지 이력을 복원할 수 없습니다')
            return True, '안전 복구 필요 — 일반 동작 차단'
        self.get_logger().info('[STARTUP] initialize 시작')
        self.arm.initialize()
        self.get_logger().info('[STARTUP] tool/TCP/충돌 감도 self_check 시작')
        result = self.arm.self_check(job.args['expect_tool'], job.args['expect_tcp'],
                                     self.get_parameter('safety.collision_sensitivity').value)
        if result[0] and restore_requested:
            SkillNode._restore_extracted_scoop(self, job)
        if result[0] and getattr(self.gripper, 'backend', '') == 'dio':
            if self.gripper.confirm_open_dio():
                self._held_payload = 'empty'
        self._configured = bool(result[0])
        return result

    def _restore_extracted_scoop(self, job: Job):
        """작업자가 확인한 인출 완료 스쿱만 계량 자세에서 복원한다. 이동·개폐는 없다."""
        self._empty_scoop_force_baseline = None
        self._empty_scoop_baseline_pending = False
        material_id = job.args.get('restore_material_id', '')
        operator_id = job.args.get('restore_operator_id', '')
        if (not isinstance(material_id, str) or not material_id.strip()
                or not isinstance(operator_id, str) or not operator_id.strip()
                or job.args.get('restore_confirmed') is not True):
            raise ValueError('스쿱 복원에는 원료 ID·작업자 ID·인출 완료 확인이 필요합니다')
        if self.mode != 'real' or self.gripper.backend != 'modbus':
            raise RuntimeError('스쿱 복원은 실물 Modbus 파지 센서가 필요합니다')
        station = self.stations.for_material(material_id)
        revision = self._safety_revision
        if getattr(self, '_return_rescoop_blocked', False):
            raise RuntimeError('반환 후 재스쿱 차단은 파지 복원으로 해제할 수 없습니다')
        self._poll_safety(force=True)
        if self._last_robot_state != STANDBY or self._safety_latched:
            raise RuntimeError('스쿱 복원은 안전 차단 없는 대기 상태에서만 가능합니다')
        if not self._pose_matches(self.arm.current_posx(), station.posx):
            raise RuntimeError('현재 자세가 해당 원료 계량 자세와 다릅니다. 파지 복원 거부')
        # 기동 직후 첫 표본의 도착만 제한 시간 동안 기다린다. 실제 미파지/안전 이상은 즉시 거부한다.
        timeout_s = float(self.get_parameter('robot.startup_timeout_s').value)
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError('스쿱 복원 센서 대기 시간은 유한한 양수여야 합니다')
        deadline = self._now_s() + timeout_s
        while True:
            if (job.cancel or self._stopping.is_set() or self._safety_latched
                    or self._safety_revision != revision):
                raise RuntimeError('센서 대기 중 취소 또는 안전 상태 변경 발생')
            state = self.gripper.state(self._now_s())
            if state.get('fresh', False):
                break
            remaining = deadline - self._now_s()
            if remaining <= 0:
                raise TimeoutError(f'스쿱 복원 센서 대기 {timeout_s:g}s 초과: {state}')
            time.sleep(min(self.state_poll_s, remaining))
        # 센서를 기다리는 동안 위치·로봇 상태가 바뀌었을 수 있어 다시 확인한다.
        self._poll_safety(force=True)
        if self._last_robot_state != STANDBY or not self._pose_matches(self.arm.current_posx(), station.posx):
            raise RuntimeError('센서 대기 후 로봇 상태 또는 계량 자세 불일치')
        state = self.gripper.state(self._now_s())
        width = state.get('width_mm')
        if (not state.get('fresh', False) or state.get('busy', True) or not state.get('grip_inferred', False)
                or state.get('safety_triggered', True) or state.get('slip', False)
                or width is None or not math.isfinite(float(width)) or float(width) <= 0):
            raise RuntimeError(f'최신 파지·폭·안전 상태를 확인할 수 없어 복원을 거부합니다: {state}')
        with self._job_lock:
            if (job.cancel or self._stopping.is_set() or self._safety_latched
                    or self._safety_revision != revision):
                raise RuntimeError('복원 확인 중 취소 또는 안전 상태 변경 발생')
            self._held_payload = 'scoop'
            self._empty_scoop_baseline_pending = job.args.get('restore_empty_scoop_confirmed') is True
            self._held_material_id = material_id
            self._station_id = station.station_id
            self._cartesian_ready = True
            self._motion_anchor = None  # 관절 이송 출발 이력으로 사용하지 않는다.
            self._pending_scoop_extract = False
            self._scoop_extract_uncertain = False
        self.get_logger().info(
            f'[SCOOP_STATE_RESTORED] operator={operator_id} material={material_id} '
            f'station={station.station_id} width_mm={width}; 공정 자동 재개 없음')

    def _require_scoop_extracted(self):
        if self._scoop_extract_uncertain:
            raise RuntimeError('스쿱 인출 상태가 불확실하다. SafePose 후 수동 확인이 필요하다')
        if self._pending_scoop_extract:
            raise RuntimeError('스쿱 파지 후 WeighHeld로 +Y 인출을 먼저 수행해야 한다')

    def _do_move(self, job: Job, *, station_id=None, approach=None):
        self._require_scoop_extracted()
        try:
            return self._move_checked(job, station_id=station_id, approach=approach)
        except Exception:
            self._motion_anchor = None
            self._held_payload = 'unknown'
            self._held_material_id = ''
            self._empty_scoop_force_baseline = None
            self._empty_scoop_baseline_pending = False
            raise

    def _pose_matches(self, actual, target):
        return pose_matches(actual, target, self.pose_xyz_tolerance, self.pose_rotation_tolerance)

    def _record_arrival(self, station_id, approach, target):
        actual = self.arm.current_posx()
        if not self._pose_matches(actual, target):
            raise RuntimeError(f'이동 위치/자세 미도달: target={target}, actual={actual}')
        self._motion_anchor = MotionAnchor(station_id, approach, tuple(actual),
                                           tuple(self.arm.current_posj()))
        self._station_id = station_id

    def _move_checked(self, job: Job, *, station_id=None, approach=None):
        if job.cancel:
            raise RuntimeError('cancelled')
        approach = job.args['approach'] if approach is None else approach
        if approach not in (MoveToStation.Goal.ABOVE, MoveToStation.Goal.AT):
            raise ValueError('approach는 ABOVE(0) 또는 AT(1)이어야 한다')
        st = self.stations.get(station_id or job.args['station_id'])
        # 티칭 관절 경로만 가상에서 직선 폴백한다. solution_space 접근은 양 모드에 적용한다.
        if 'approach_posj' in st.extra or 'return_entry_posx' in st.extra:
            return SkillNode._move_taught_station(self, job, st, approach)
        transfers = self.stations.transfers if self.mode != 'virtual' else {}
        incoming = [r for r in transfers.values() if r.destination == st.station_id]
        if incoming and all(r.arrival == 'at' for r in incoming) and approach != MoveToStation.Goal.AT:
            raise ValueError('관절 직접 도착 목적지는 AT 요청만 허용한다')
        target = st.above(self.stations.approach_mm) if approach == MoveToStation.Goal.ABOVE else st.posx
        vel_scale = job.args.get('vel_scale') or self.vel_scale
        if not math.isfinite(vel_scale) or not 0 < vel_scale <= 1:
            raise ValueError('vel_scale은 0 초과 1 이하여야 한다')
        route = transfers.get((self._station_id, st.station_id))
        if route is not None:
            self._run_transfer(route, job, target, vel_scale)
            return st.station_id
        protected = any(r.destination == st.station_id for r in transfers.values())
        if protected:
            # 같은 스테이션의 AT↔ABOVE만 기존 직선 접근으로 허용한다.
            anchor = self._motion_anchor
            if (anchor is None or anchor.station != st.station_id
                    or not self._pose_matches(self.arm.current_posx(), anchor.pose)
                    or not joints_match(self.arm.current_posj(), anchor.joints, self.joint_tolerance)):
                # 재기동·수동 티칭 후 이미 출발점에 있다면 움직이지 않고 확인만 한다.
                # 다른 위치에서 그 점으로 자동 복구하는 경로는 추측하지 않는다.
                for outgoing in self.stations.transfers.values():
                    if outgoing.source != st.station_id or not outgoing.enabled:
                        continue
                    if outgoing.start_from == 'above' and approach != MoveToStation.Goal.ABOVE:
                        continue
                    taught = (outgoing.start_above_posj if approach == MoveToStation.Goal.ABOVE
                              else outgoing.start_at_posj)
                    if (self._pose_matches(self.arm.current_posx(), target)
                            and joints_match(self.arm.current_posj(), taught, self.joint_tolerance)):
                        self._held_payload = 'unknown'
                        self._record_arrival(st.station_id, approach, target)
                        self._cartesian_ready = True
                        return st.station_id
                raise ValueError('등록된 출발 이력이 없는 보호 대상 이송이다')
        SkillNode._leave_taught_station(self, job, st.station_id)
        safe_posj = st.extra.get('posj') if approach != MoveToStation.Goal.ABOVE else None
        if safe_posj is not None:
            job.feedback and job.feedback('HOMING')
            self.arm.movej_cancellable(safe_posj, vel_scale, lambda: job.cancel,
                                       self.motion_timeout_s)
            self._cartesian_ready = True
            # safe.posx는 자리표시자일 수 있다. 실제 관절 목표가 도착 기준이다.
            target = self.arm.current_posx()
        else:
            if not self._cartesian_ready:
                entry = self.stations.get('safe')
                entry_posj = entry.extra.get('posj')
                if not isinstance(entry_posj, list) or len(entry_posj) != 6:
                    raise ValueError('safe station에 시작 posj 6개가 필요하다')
                job.feedback and job.feedback('HOMING')
                self.arm.movej_cancellable(entry_posj, vel_scale, lambda: job.cancel,
                                           self.motion_timeout_s)
                self._cartesian_ready = True
            job.feedback and job.feedback('MOVING')
            self._leave_solution_station(job, st.station_id, vel_scale)
            if 'solution_space' in st.extra:
                self._move_solution_station(job, st, target, vel_scale)
            else:
                self.arm.movel_cancellable(target, vel_scale, lambda: job.cancel,
                                           self.motion_timeout_s)
        if job.cancel:
            raise RuntimeError('cancelled')
        self._record_arrival(st.station_id, approach, target)
        return st.station_id

    def _taught_linear(self, job, target):
        scale = job.args.get('vel_scale') or self.vel_scale
        if not math.isfinite(scale) or not 0 < scale <= 1:
            raise ValueError('vel_scale은 0 초과 1 이하여야 한다')
        if not self._cartesian_ready:
            joints = list(vector6(self.stations.get('safe').extra['posj'], 'safe.posj'))
            self.arm.movej_cancellable(joints, scale,
                lambda: job.cancel or self._cancel_requested(), self.motion_timeout_s,
                joint_vel=self.transfer_joint_vel, joint_acc=self.transfer_joint_acc)
            self._cartesian_ready = True
        self.arm.movel_cancellable(list(target), scale,
                                   lambda: job.cancel or self._cancel_requested(), self.motion_timeout_s)

    def _leave_taught_station(self, job, destination):
        source = self.stations.stations.get(self._station_id)
        if source is None or source.station_id == destination:
            return
        if 'approach_posj' not in source.extra and 'return_entry_posx' not in source.extra:
            return
        anchor = self._motion_anchor
        if (anchor is None or anchor.station != source.station_id
                or not self._pose_matches(self.arm.current_posx(), anchor.pose)
                or not joints_match(self.arm.current_posj(), anchor.joints, self.joint_tolerance)):
            raise RuntimeError('티칭 경로 출발 이력이 불확실하다')
        if self._held_payload not in ('cup', 'empty'):
            raise RuntimeError('티칭 경로 출발 전 인출·파지 확인이 필요하다')
        self._require_transfer_payload(self._held_payload)
        target = list(anchor.pose)
        target[2] = source.posx[2] + source.extra['exit_mm']
        if not self._pose_matches(self.arm.current_posx(), target):
            SkillNode._taught_linear(self, job, target)
        self._motion_anchor = None

    def _move_taught_station(self, job, station, approach):
        """DRL 관절 진입·직선 하강과 거치대 측면 반납을 외부 AT/ABOVE에 연결한다."""
        scale = job.args.get('vel_scale') or self.vel_scale
        if not math.isfinite(scale) or not 0 < scale <= 1:
            raise ValueError('vel_scale은 0 초과 1 이하여야 한다')
        if any(not math.isfinite(v) or v <= 0
               for v in (self.transfer_joint_vel, self.transfer_joint_acc)):
            raise ValueError('티칭 관절 속도·가속도는 유한한 양수여야 한다')
        anchor = self._motion_anchor
        local = (anchor is not None and anchor.station == station.station_id)
        if local and (not self._pose_matches(self.arm.current_posx(), anchor.pose)
                      or not joints_match(self.arm.current_posj(), anchor.joints, self.joint_tolerance)):
            raise RuntimeError('티칭 스테이션 도착 후 위치/관절이 변경되었다')
        if 'return_entry_posx' in station.extra:
            if self._held_payload == 'scoop':
                SkillNode._require_held_scoop(self, station.extra['material_id'])
                if approach != MoveToStation.Goal.AT:
                    raise ValueError('스쿱 반납은 AT 요청으로 실행해야 한다')
                entry = SkillNode._pose_from_extra(station, 'return_entry_posx')
                lower = list(entry)
                lower[2] -= finite(station.extra['return_lower_mm'], '반납 하강량')
                for target in (entry, lower, station.posx):
                    SkillNode._taught_linear(self, job, target)
            else:
                self._require_transfer_payload('empty')
                SkillNode._leave_taught_station(self, job, station.station_id)
                target = (station.offset_z(station.extra['exit_mm']) if local
                          and approach == MoveToStation.Goal.ABOVE else station.above(self.stations.approach_mm))
                if not local or approach == MoveToStation.Goal.ABOVE:
                    SkillNode._taught_linear(self, job, target)
                if approach == MoveToStation.Goal.AT:
                    SkillNode._taught_linear(self, job, station.posx)
        else:
            if self._held_payload not in ('cup', 'empty'):
                raise RuntimeError('용기 스테이션 진입 전 파지/열림 이력이 필요하다')
            self._require_transfer_payload(self._held_payload)
            if local:
                target = list(anchor.pose)
                if approach == MoveToStation.Goal.AT:
                    # 빈 그리퍼의 workbench 진입 관절각은 놓기와 다르다.
                    target[2] = station.posx[2]
                    if self._held_payload == 'cup':
                        target = list(station.posx)
                else:
                    target[2] = station.posx[2] + station.extra['exit_mm']
                SkillNode._taught_linear(self, job, target)
            else:
                SkillNode._leave_taught_station(self, job, station.station_id)
                empty_entry = self._held_payload == 'empty' and 'empty_approach_posj' in station.extra
                if empty_entry:
                    SkillNode._taught_linear(self, job, SkillNode._pose_from_extra(station, 'middle_posx'))
                key = 'empty_approach_posj' if empty_entry else 'approach_posj'
                joints = list(vector6(station.extra[key], key))
                self.arm.movej_cancellable(joints, scale,
                    lambda: job.cancel or self._cancel_requested(), self.motion_timeout_s,
                    joint_vel=self.transfer_joint_vel, joint_acc=self.transfer_joint_acc)
                self._cartesian_ready = True
                self._require_transfer_payload(self._held_payload)
                if approach == MoveToStation.Goal.AT:
                    if empty_entry:
                        target = list(self.arm.current_posx())
                        target[2] -= station.extra['empty_descent_mm']
                    else:
                        target = station.posx
                    SkillNode._taught_linear(self, job, target)
            self._require_transfer_payload(self._held_payload)
        if job.cancel or self._cancel_requested():
            raise RuntimeError('cancelled')
        # 각 어댑터는 실제 관절/직선 목표 도달을 확인한다. 관절각 TCP를 임의 합성하지 않는다.
        self._record_arrival(station.station_id, approach, self.arm.current_posx())
        return station.station_id

    def _require_solution(self, station):
        sol = self.arm.solution_space()
        if sol != station.extra['solution_space']:
            raise RuntimeError(f'{station.station_id}: 관절 구성 불일치 sol={sol}')

    def _leave_solution_station(self, job, destination, vel_scale):
        """용기 위치를 떠날 때는 파지·출발 이력을 확인하고 직선으로 이탈한다."""
        if self._station_id == destination or self._station_id not in self.stations.stations:
            return
        source = self.stations.get(self._station_id)
        if 'solution_space' not in source.extra:
            return
        anchor = self._motion_anchor
        if (anchor is None or anchor.station != source.station_id
                or not self._pose_matches(self.arm.current_posx(), anchor.pose)
                or not joints_match(self.arm.current_posj(), anchor.joints, self.joint_tolerance)):
            raise RuntimeError('용기 스테이션 출발 이력이 불확실하다')
        if self._held_payload not in ('cup', 'empty'):
            raise RuntimeError('용기 이송 전 파지 상태 확인이 필요하다')
        self._require_transfer_payload(self._held_payload)
        self._require_solution(source)
        self.arm.movel_cancellable(source.exit(), vel_scale, lambda: job.cancel,
                                   self.motion_timeout_s)
        self._require_solution(source)
        self._require_transfer_payload(self._held_payload)
        self._motion_anchor = None

    def _move_solution_station(self, job, station, target, vel_scale):
        """ABOVE에서 관절 구성을 선택하고 AT 접근은 직선으로 유지한다."""
        if self._held_payload in ('cup', 'empty'):
            self._require_transfer_payload(self._held_payload)
        above = station.above(self.stations.approach_mm)
        actual = self.arm.current_posx()
        at_station = (self._pose_matches(actual, station.posx)
                      or self._pose_matches(actual, above))
        if at_station:
            # 작업점에서 손목을 뒤집지 않는다. 수동 이동 뒤에도 구성 확인이 먼저다.
            self._require_solution(station)
        else:
            self.arm.movejx_cancellable(above, station.extra['solution_space'], vel_scale,
                                        lambda: job.cancel, self.motion_timeout_s)
            self._require_solution(station)
        if self._held_payload in ('cup', 'empty'):
            self._require_transfer_payload(self._held_payload)
        if not self._pose_matches(self.arm.current_posx(), target):
            self.arm.movel_cancellable(target, vel_scale, lambda: job.cancel,
                                       self.motion_timeout_s)
        self._require_solution(station)

    def _require_transfer_payload(self, expected):
        if getattr(self.gripper, 'backend', '') == 'dio':
            self.gripper.refresh_dio()
            state = self.gripper.state(self._now_s())
            if (state['busy'] or self._held_payload != expected
                    or (expected == 'cup' and not state['grip_inferred'])
                    or (expected == 'empty' and not state['open_confirmed'])):
                raise RuntimeError('이송 파지 이력 또는 DI 완료 상태가 불확실하다')
            return
        state = self.gripper.state(self._now_s())
        if state['busy'] or state['width_mm'] is None or self._held_payload != expected:
            raise RuntimeError('이송 파지 이력 또는 그리퍼 피드백이 불확실하다')
        if bool(state['grip_inferred']) != (expected == 'cup'):
            raise RuntimeError('이송 중 파지 상태가 변경되었다')
        if (not math.isfinite(state['width_mm']) or
                (expected == 'empty' and abs(state['width_mm'] - self.gripper.open_width_mm)
                 > self.gripper.grip_margin_mm)):
            raise RuntimeError('빈 그리퍼의 열림 폭을 확인할 수 없다')

    def _run_transfer(self, route, job, target, vel_scale):
        if route.arrival == 'at' and job.args['approach'] != MoveToStation.Goal.AT:
            raise ValueError('관절 직접 도착 경로는 AT 요청만 허용한다')
        validate_start(route, self._motion_anchor, self.arm.current_posx(),
                       self.arm.current_posj(), self._held_payload,
                       self.pose_xyz_tolerance, self.pose_rotation_tolerance, self.joint_tolerance)
        if any(not math.isfinite(v) or v <= 0
               for v in (self.transfer_joint_vel, self.transfer_joint_acc)):
            raise ValueError('이송 관절 속도·가속도를 먼저 설정해야 한다')
        self._require_transfer_payload(route.payload)
        self._motion_anchor = None
        job.feedback and job.feedback('MOVING')

        def checkpoint():
            if job.cancel:
                raise RuntimeError('cancelled')
            self._require_transfer_payload(route.payload)

        # 이미 이탈점이면 다시 움직이지 않는다. 마지막 관절점은 경로의 도착 방식에 따른다.
        checkpoint()
        if not self._pose_matches(self.arm.current_posx(), route.exit_posx):
            self.arm.movel_cancellable(route.exit_posx, vel_scale, lambda: job.cancel,
                                       self.motion_timeout_s)
        if (not self._pose_matches(self.arm.current_posx(), route.exit_posx)
                or not joints_match(self.arm.current_posj(), route.exit_posj, self.joint_tolerance)):
            raise RuntimeError('직선 이탈 후 관절 구성/자세가 티칭값과 다르다')
        for point in route.waypoints_posj:
            checkpoint()
            self.arm.movej_cancellable(point, vel_scale, lambda: job.cancel, self.motion_timeout_s,
                                       joint_vel=self.transfer_joint_vel, joint_acc=self.transfer_joint_acc)
        checkpoint()
        destination = self.stations.get(route.destination)
        entry = (destination.posx if route.arrival == 'at'
                 else destination.above(self.stations.approach_mm))
        if not self._pose_matches(self.arm.current_posx(), entry):
            raise RuntimeError(f'마지막 관절점이 목적지 {route.arrival.upper()} 위치/자세와 일치하지 않는다')
        if route.arrival == 'above' and job.args['approach'] == MoveToStation.Goal.AT:
            self.arm.movel_cancellable(target, vel_scale, lambda: job.cancel, self.motion_timeout_s)
        checkpoint()
        self._record_arrival(route.destination, job.args['approach'], target)

    def _do_grip(self, job: Job):
        a = job.args
        self._held_payload = 'unknown'
        self._held_material_id = ''
        self._empty_scoop_force_baseline = None
        self._empty_scoop_baseline_pending = False
        if a['close']:
            if self._scoop_extract_uncertain:
                raise RuntimeError('스쿱 인출 상태가 불확실하여 재파지할 수 없다')
            anchor = getattr(self, '_motion_anchor', None)
            scoop_at = (
                anchor is not None
                and self._station_id.startswith('scoop_')
                and anchor.station == self._station_id
                and anchor.approach == MoveToStation.Goal.AT
                and self._pose_matches(self.arm.current_posx(), anchor.pose)
                and joints_match(self.arm.current_posj(), anchor.joints, self.joint_tolerance)
            )
            options = {'scoop': scoop_at} if getattr(self.gripper, 'backend', '') == 'dio' else {}
            result = self.gripper.grip(a['width_mm'], a['force_n'], a['timeout_s'] or 3.0, **options)
            self._pending_scoop_extract = bool(result[0] and result[2] and scoop_at)
            self._scoop_extract_uncertain = False
            if result[0] and result[2]:
                if scoop_at:
                    scoop = self.stations.get(self._station_id)
                    material_id = scoop.extra.get('material_id')
                    if not isinstance(material_id, str) or not material_id:
                        raise ValueError(f'{self._station_id}.material_id가 필요하다')
                    self._held_payload = 'scoop'
                    self._held_material_id = material_id
                    self._empty_scoop_baseline_pending = True
                elif (anchor is not None and anchor.station == self._station_id and anchor.approach == MoveToStation.Goal.AT
                      and self._station_id in ('workbench', 'passbox_empty', 'passbox_done', 'reject_bin')
                      and self._pose_matches(self.arm.current_posx(), anchor.pose)
                      and joints_match(self.arm.current_posj(), anchor.joints, self.joint_tolerance)):
                    self._held_payload = 'cup'
            return result
        self._pending_scoop_extract = False
        self._scoop_extract_uncertain = False
        released = self.gripper.release(a['timeout_s'] or 3.0)
        if released:
            self._held_payload = 'empty'
            self._held_material_id = ''
            self._empty_scoop_force_baseline = None
            self._empty_scoop_baseline_pending = False
        anchor = getattr(self, '_motion_anchor', None)
        if released and anchor is not None and anchor.station.startswith('scoop_'):
            station = self.stations.get(anchor.station)
            if 'return_entry_posx' in station.extra:
                target = station.offset_z(station.extra['exit_mm'])
                SkillNode._taught_linear(self, job, target)
                self._record_arrival(station.station_id, MoveToStation.Goal.ABOVE, target)
        return released, self.gripper.width_mm() or -1.0, False

    def _scale_period_s(self):
        period = float(self.get_parameter('scale.period_s').value)
        if not math.isfinite(period) or period <= 0:
            raise ValueError('scale.period_s는 유한한 양수여야 한다')
        return period

    def _do_measure(self, job: Job):
        period_s = self._scale_period_s()
        if self.get_parameter('scale.simulated').value:
            return [0.0] * 6, 0.0, 0.0, False, 'simulated'
        mean6, fz, std, valid = self.arm.measure_force(job.args['samples'], job.args['settle_s'],
                                                       period_s=period_s, observer=self._observe_force)
        return mean6, fz, std, valid, ''

    def _do_safe(self, job: Job):
        try:
            self.arm.compliance_off()
        except Exception:  # noqa: BLE001 — 힘제어 중이 아니었으면 무시
            pass
        safe = self.stations.get('safe')
        posj = safe.extra.get('posj')
        if posj is None:
            raise ValueError('safe station에 posj 6개가 필요하다')
        self.arm.movej_cancellable(posj, 0.3, lambda: job.cancel, self.motion_timeout_s)
        self._cartesian_ready = True
        self._station_id = 'safe'
        self._pending_scoop_extract = False
        self._scoop_extract_uncertain = False
        self._held_material_id = ''
        self._empty_scoop_force_baseline = None
        self._empty_scoop_baseline_pending = False
        return True

    def _require_held_scoop(self, material_id=None):
        if self._held_payload != 'scoop' or not self._held_material_id:
            raise RuntimeError('원료 ID가 확인된 스쿱 파지 이력이 필요하다')
        if material_id is not None and self._held_material_id != material_id:
            raise RuntimeError('반환 요청 원료와 파지한 스쿱의 원료 ID가 다르다')
        if getattr(self.gripper, 'backend', '') == 'dio':
            self.gripper.refresh_dio()
        state = self.gripper.state(self._now_s())
        if state.get('busy', False) or not state.get('grip_inferred', False):
            raise RuntimeError('스쿱 파지 상태가 불확실하다')

    @staticmethod
    def _pose_from_extra(station, key):
        pose = station.extra.get(key)
        if (not isinstance(pose, list) or len(pose) != 6
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(float(v)) for v in pose)):
            raise ValueError(f'{station.station_id}.{key} 6개 유한 좌표가 필요하다')
        return list(pose)

    def _do_scoop(self, job: Job):
        """고정 BASE 티칭 또는 보정된 WORLD 경로로 스쿠핑 후 계량 자세에 복귀한다."""
        if getattr(self, '_return_rescoop_blocked', False):
            raise RuntimeError('반환 후 재스쿱 연결 경로 미구현: 자동 Scoop을 차단합니다')
        self._require_scoop_extracted()
        material = job.args['material_id']
        SkillNode._require_held_scoop(self, material)
        if getattr(self, 'height_measure_only', False):
            return SkillNode._measure_surface_world(self, job)
        profile = self.stations.scooping.get(material)
        if profile and profile.get('execution_mode', 'height_compensated') == 'taught_fixed':
            return SkillNode._do_fixed_scoop(self, job, profile)
        if profile and profile.get('execution_mode', 'height_compensated') != 'height_compensated':
            raise ValueError('알 수 없는 스쿠핑 실행 모드')
        if not profile or profile.get('calibrated') is not True:
            raise ValueError('스쿠핑 경로/스쿱 끝 높이 보정 미확인: 원료별 보정 후 실행 필요')
        fraction = finite(job.args['depth_fraction'], 'depth_fraction')
        station = self.stations.for_material(material)
        cancel = lambda: job.cancel or self._cancel_requested()
        if cancel():
            raise RuntimeError('cancelled')
        # 설정과 좌표 변환은 첫 이동 전에 확인한다. WORLD=BASE를 가정하지 않는다.
        reference = self.arm.transform_pose(profile['reference_pose_base'], to_world=True)
        offset = tip_offset_local(reference, profile['tip_offset_world_mm'])
        points = [self.arm.transform_pose(p, to_world=True) for p in profile['waypoints_base']]
        shake = self.arm.transform_pose(profile['shake_base'], to_world=True)
        plan_scoop(profile, points, offset, profile['reference_surface_world_z_mm'], fraction)
        vel = [finite(v, 'spline 속도') * self.vel_scale for v in profile['velocity']]
        acc = [finite(v, 'spline 가속도') * self.vel_scale for v in profile['acceleration']]
        if len(vel) != 2 or len(acc) != 2 or min(vel + acc) <= 0:
            raise ValueError('spline 속도/가속도는 양수 2개여야 한다')
        amp = vector6(profile['shake_amp'], '털기 진폭')
        period = vector6(profile['shake_period'], '털기 주기')
        atime = finite(profile['shake_atime'], '털기 가속시간')
        repeat = profile['shake_repeat']
        if (atime <= 0 or type(repeat) is not int or repeat <= 0
                or any(t < 0 or (a != 0 and t <= 0) for a, t in zip(amp, period))
                or not any(amp)):
            raise ValueError('털기 주기/반복 설정 오류')
        result = SkillNode._do_check_depth(self, job)
        contact = result.get('contact_pose_base')
        if contact is None:
            raise RuntimeError('원료면 접촉 미검출: 스쿠핑을 실행하지 않습니다')
        surface = tip_z(self.arm.transform_pose(contact, to_world=True), offset)
        plan = plan_scoop(profile, points, offset, surface, fraction)
        targets = [self.arm.transform_pose(p, to_world=False) for p in plan.world_poses]
        shake[2] += plan.shift_mm
        if tip_z(shake, offset) < plan.floor_z:
            raise ValueError('털기 위치가 스쿱 끝 높이 하한을 침범한다')
        shake_target = self.arm.transform_pose(shake, to_world=False)
        self.get_logger().info(
            f'[SCOOP_PLAN] material={material} surface_world_z={surface:.2f} '
            f'depth_mm={plan.depth_mm:.2f} fraction={fraction:.3f} '
            f'predicted_g={plan.predicted_g:.2f} shift_mm={plan.shift_mm:.2f}')

        def observe():
            SkillNode._require_held_scoop(self, material)
            world = self.arm.transform_pose(self.arm.current_posx(), to_world=True)
            if tip_z(world, offset) < plan.floor_z:
                raise RuntimeError('스쿠핑 중 스쿱 끝 높이 하한 침범')

        # 취소·미도달·관측 실패 시 어댑터가 정지하고 다음 이동은 수행하지 않는다.
        job.feedback and job.feedback('DIP', True, result['max_contact_force_n'],
                                      result['insertion_depth_mm'])
        self.arm.movesx_cancellable(targets, vel, acc, cancel, self.motion_timeout_s,
                                    observer=observe)
        self.arm.movel_cancellable(shake_target, self.vel_scale, cancel,
                                   self.motion_timeout_s, observer=observe)
        if cancel():
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('LEVEL', True, result['max_contact_force_n'],
                                      result['insertion_depth_mm'])
        try:
            self.arm.amove_periodic(list(amp), list(period), atime, repeat, ref_tool=False)
            self.arm.wait_motion_cancellable(cancel, self.motion_timeout_s, observer=observe)
            if not self._pose_matches(self.arm.current_posx(), shake_target):
                raise RuntimeError('털기 종료 자세 미확인')
        except Exception:
            self.arm.stop_motion()
            raise
        self.arm.movel_cancellable(station.posx, self.vel_scale, cancel,
                                   self.motion_timeout_s, observer=observe)
        job.feedback and job.feedback('LIFT', True, result['max_contact_force_n'],
                                      result['insertion_depth_mm'])
        return result

    def _do_fixed_scoop(self, job, profile):
        """검증된 BASE 경로만 실행한다. 원료면/끝 높이/담금량을 계산하지 않는다."""
        fixed = profile.get('fixed_path', {})
        if fixed.get('verified') is not True or self.stations.frame != 'base':
            raise ValueError('BASE 고정 경로의 실물 검증 확인이 필요하다')
        fraction = finite(job.args['depth_fraction'], 'depth_fraction')
        if fraction != 1.0:
            raise ValueError('고정 티칭 경로는 depth_fraction=1.0만 지원한다')
        points = [list(vector6(p, '고정 경유점')) for p in fixed['waypoints_base']]
        if len(points) != 5:
            raise ValueError('고정 스쿠핑은 검증된 경유점 5개가 필요하다')
        shake = list(vector6(fixed['shake_base'], '털기 위치'))
        vel = [finite(v, '속도') * self.vel_scale for v in fixed['velocity']]
        acc = [finite(v, '가속도') * self.vel_scale for v in fixed['acceleration']]
        amp = list(vector6(fixed['shake_amp'], '털기 진폭'))
        period = list(vector6(fixed['shake_period'], '털기 주기'))
        atime, repeat = finite(fixed['shake_atime'], '털기 가속시간'), fixed['shake_repeat']
        if (len(vel) != 2 or len(acc) != 2 or min(vel + acc) <= 0
                or atime <= 0 or type(repeat) is not int or repeat <= 0
                or not any(amp) or any(t < 0 or (a != 0 and t <= 0) for a, t in zip(amp, period))):
            raise ValueError('고정 경로 속도/주기 운동 설정 오류')
        material = job.args['material_id']
        station = self.stations.for_material(material)
        cancel = lambda: job.cancel or self._cancel_requested()

        def observe():
            SkillNode._require_held_scoop(self, material)

        if cancel():
            raise RuntimeError('cancelled')
        if not self._pose_matches(self.arm.current_posx(), station.posx):
            raise RuntimeError('고정 스쿠핑은 해당 원료 계량 자세에서 시작해야 한다')
        observe()
        job.feedback and job.feedback('DIP')
        self.arm.movesx_cancellable(points, vel, acc, cancel, self.motion_timeout_s, observer=observe)
        self.arm.movel_cancellable(shake, self.vel_scale, cancel, self.motion_timeout_s, observer=observe)
        if cancel():
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('LEVEL')
        try:
            self.arm.amove_periodic(amp, period, atime, repeat, ref_tool=False)
            self.arm.wait_motion_cancellable(cancel, self.motion_timeout_s, observer=observe)
            if not self._pose_matches(self.arm.current_posx(), shake):
                raise RuntimeError('털기 종료 자세 미확인')
        except Exception:
            self.arm.stop_motion()
            raise
        job.feedback and job.feedback('LIFT')
        self.arm.movel_cancellable(station.posx, self.vel_scale, cancel,
                                   self.motion_timeout_s, observer=observe)
        return dict(contact_detected=False, max_contact_force_n=0.0, insertion_depth_mm=0.0,
                    message='TAUGHT_FIXED: 검증된 full 경로 완료; 접촉력·삽입 깊이 미측정')

    def _measure_surface_world(self, job: Job):
        """측정 전용: 기존 접촉 경로와 복귀만 실행하고 스쿠핑은 하지 않는다."""
        material = job.args['material_id']
        profile = self.stations.scooping.get(material)
        if not profile:
            raise ValueError('원료별 스쿱 끝 오프셋 설정이 필요하다')
        reference = self.arm.transform_pose(profile['reference_pose_base'], to_world=True)
        offset = tip_offset_local(reference, profile['tip_offset_world_mm'])
        result = SkillNode._do_check_depth(self, job)
        contact = result.get('contact_pose_base')
        if contact is None:
            raise RuntimeError('원료면 접촉 미검출: WORLD 원료 높이를 계산할 수 없습니다')
        world = self.arm.transform_pose(contact, to_world=True)
        z = tip_z(world, offset)
        message = (f'HEIGHT_MEASUREMENT_ONLY material={material} '
                   f'contact_base={contact} contact_world={world} '
                   f'tip_offset_local={list(offset)} surface_world_z_mm={z:.3f} '
                   f'max_contact_force_n={result["max_contact_force_n"]:.3f}; '
                   '스쿠핑 미실행, 근사 오프셋으로 계산한 접촉 지점 높이')
        self.get_logger().info(message)
        result.update(diagnostic_only=True, measurement_message=message)
        return result

    def _wait_compliance_settle(self, job: Job, duration_s: float):
        """순응 진입 응답 후 컨트롤러 전환 시간을 확보하며 취소를 확인한다."""
        deadline = self._now_s() + duration_s
        while True:
            if job.cancel or self._cancel_requested():
                raise RuntimeError('cancelled')
            remaining = deadline - self._now_s()
            if remaining <= 0:
                return
            time.sleep(min(0.02, remaining))

    def _do_check_depth(self, job: Job):
        """티칭 목표로 접근하다 최초 접촉에서 감속 정지하고 계량 자세로 복귀한다."""
        if getattr(self, '_return_rescoop_blocked', False):
            raise RuntimeError('반환 후 재스쿱 연결 경로 미구현: 자동 Scoop을 차단합니다')
        self._require_scoop_extracted()
        SkillNode._require_held_scoop(self, job.args['material_id'])
        p = self.get_parameter
        settle_s = float(p('safety.compliance_settle_s').value)
        if not math.isfinite(settle_s) or not 0 < settle_s <= self.motion_timeout_s:
            raise ValueError('순응 전환 대기는 양수이며 이동 제한 시간 이하여야 합니다')
        station = self.stations.for_material(job.args['material_id'])
        baseline = getattr(self, '_empty_scoop_force_baseline', None)
        if (baseline is None or baseline['material_id'] != job.args['material_id']
                or baseline['station_id'] != station.station_id
                or baseline['safety_revision'] != getattr(self, '_safety_revision', 0)
                or not self._pose_matches(baseline['pose'], station.posx)):
            raise RuntimeError('현재 파지·자세의 유효한 빈 스쿱 Fz 계량 기준이 필요합니다')
        baseline_fz = baseline['fz_mean_n']
        if not math.isfinite(baseline_fz):
            raise ValueError('빈 스쿱 기준 Fz가 유효하지 않습니다')
        # 한번 접근한 뒤의 계량을 빈 스쿱 영점으로 다시 저장하지 않는다.
        self._empty_scoop_baseline_pending = False
        target = SkillNode._pose_from_extra(station, 'measure_posx')
        start = list(station.posx)
        reference_station = self.stations.get(p('height_measurement.reference_station').value)
        reference = list(reference_station.posx)
        tip_offset = list(reference_station.extra['scoop_tip_offset_base_mm'])
        trace_only = bool(p('height_measurement.force_trace_only').value)
        trace_hz = float(p('height_measurement.trace_hz').value)
        if not math.isfinite(trace_hz) or trace_hz <= 0:
            raise ValueError('힘 기록 주파수는 유한한 양수여야 합니다')
        contact_threshold = float(p('safety.fz_max_n').value)
        if not math.isfinite(contact_threshold) or contact_threshold <= 0:
            raise ValueError('접촉 판정 힘은 유한한 양수여야 합니다')
        # 기하 설정 오류는 이동 전에 거부한다.
        tip_position_base(start, reference, tip_offset)
        if job.cancel:
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('APPROACH')
        self.arm.movel(start, self.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        contact_z = None
        contact_pose = None
        max_force_n = 0.0
        insertion_mm = 0.0
        trace_file = None
        trace_writer = None
        trace_path = None
        trace_start = self._now_s()
        next_trace_at = trace_start
        phase = 'BEFORE_COMPLIANCE'
        if trace_only:
            directory = Path(p('height_measurement.trace_directory').value).expanduser().resolve()
            directory.mkdir(parents=True, exist_ok=True)
            trace_path = directory / f'force_{station.station_id}_{uuid.uuid4().hex}.csv'
            trace_file = trace_path.open('x', newline='', buffering=1)
            trace_writer = csv.writer(trace_file)
            trace_writer.writerow(['ros_time_s', 'elapsed_s', 'force_read_end_s', 'pose_read_end_s',
                'frame', 'phase', 'Fx_N', 'Fy_N', 'Fz_N', 'Mx_Nm', 'My_Nm', 'Mz_Nm',
                'tcp_x_mm', 'tcp_y_mm', 'tcp_z_mm', 'tcp_a_deg', 'tcp_b_deg', 'tcp_c_deg',
                'baseline_fz_N', 'delta_fz_N', 'threshold_N', 'contact_detected'])
            self.get_logger().info(f'[FORCE_TRACE_CSV] {trace_path}')

        def observe_depth():
            nonlocal contact_z, contact_pose, max_force_n, insertion_mm, next_trace_at
            sample_at = self._now_s()
            if trace_only:
                if sample_at < next_trace_at:
                    return
                next_trace_at += (math.floor((sample_at - next_trace_at) * trace_hz) + 1) / trace_hz
            force = self.arm.tool_force()
            if force is None or len(force) != 6 or not all(math.isfinite(float(v)) for v in force):
                raise RuntimeError('깊이 측정 외력 조회 실패')
            force_read_end = self._now_s()
            current = self.arm.current_posx()
            pose_read_end = self._now_s()
            if len(current) != 6 or not all(math.isfinite(float(v)) for v in current):
                raise RuntimeError('깊이 측정 자세 조회 실패')
            max_force_n = max(max_force_n, abs(float(force[2])))
            delta_fz = float(force[2]) - baseline_fz
            if phase != 'RETURN' and contact_z is None and abs(delta_fz) >= contact_threshold:
                contact_z = float(current[2])
                contact_pose = list(current)
                # 이후 목표 미도달·취소로 실패해도 최초 표본은 남긴다.
                self.get_logger().info('[SURFACE_CONTACT_BASE] ' + json.dumps({
                    'baseline_fz_n': baseline_fz,
                    'contact_fz_n': float(force[2]), 'delta_fz_n': delta_fz,
                    'contact_tcp_posx': contact_pose,
                    'tip_position_mm': tip_position_base(contact_pose, reference, tip_offset),
                    'approximate_offset': True,
                }, ensure_ascii=False))
            insertion_mm = 0.0 if contact_z is None else abs(float(current[2]) - contact_z)
            if trace_writer is not None:
                trace_writer.writerow([sample_at, sample_at - trace_start, force_read_end, pose_read_end,
                    'BASE', phase, *force, *current, baseline_fz, delta_fz, contact_threshold,
                    contact_z is not None])
                trace_file.flush()
            job.feedback and job.feedback('DIP', contact_z is not None, abs(float(force[2])), insertion_mm)

        try:
            try:
                if trace_only:
                    observe_depth()
                self.arm.compliance_on(list(p('safety.compliance_stx').value))
                self._wait_compliance_settle(job, settle_s)
                phase = 'APPROACH'
                # 목표의 XYZ와 회전을 모두 사용한다. 고정 Z 힘·상대 40 mm 담그기는 사용하지 않는다.
                self.arm.movel_cancellable(
                    target, self.vel_scale, lambda: job.cancel or self._cancel_requested(),
                    self.motion_timeout_s, observer=observe_depth,
                    stop_requested=lambda: not trace_only and contact_pose is not None)
                if (trace_only or contact_pose is None) and not self._pose_matches(self.arm.current_posx(), target):
                    raise RuntimeError('깊이 측정 목표 자세 미도달')
            finally:
                self.arm.compliance_off()
            if job.cancel or self._cancel_requested():
                raise RuntimeError('cancelled')
            # 성공한 경로만 계량 자세로 되짚는다. 실패·취소 시 자동 복귀하지 않는다.
            if trace_only:
                phase = 'RETURN'
                self.arm.movel_cancellable(start, self.vel_scale,
                    lambda: job.cancel or self._cancel_requested(), self.motion_timeout_s,
                    observer=observe_depth)
            else:
                self.arm.movel(start, self.vel_scale)
            job.feedback and job.feedback('LIFT', contact_z is not None, max_force_n, insertion_mm)
            measurement = {
                'frame': 'BASE', 'baseline_fz_n': baseline_fz, 'csv_path': str(trace_path) if trace_path else None,
                'contact_tcp_posx': contact_pose,
                'tip_position_mm': (tip_position_base(contact_pose, reference, tip_offset)
                                    if contact_pose is not None else None),
                'approximate_offset': True,
            }
            message = json.dumps(measurement, ensure_ascii=False)
            self.get_logger().info(f'[SURFACE_HEIGHT_BASE] {message}')
            return {'contact_detected': contact_z is not None, 'max_contact_force_n': max_force_n,
                    'insertion_depth_mm': insertion_mm, 'contact_pose_base': contact_pose,
                    'message': message}
        finally:
            if trace_file is not None:
                trace_file.close()

    def _do_pour(self, job: Job):
        self._empty_scoop_baseline_pending = False
        self._require_scoop_extracted()
        SkillNode._require_held_scoop(self)
        fraction = float(job.args['fraction'])
        if not math.isfinite(fraction) or fraction != 1.0:
            raise ValueError('Pour는 전체 스쿱 투입(fraction=1.0)만 허용한다')
        workbench = self.stations.get('workbench')
        if 'pour_above_posx' in workbench.extra:
            return SkillNode._do_taught_pour(self, job, workbench)
        p = self.get_parameter
        start = SkillNode._pose_from_extra(workbench, 'pour_start_posx')
        end = SkillNode._pose_from_extra(workbench, 'pour_end_posx')
        height = workbench.extra.get('approach_mm', self.stations.approach_mm)
        if (type(height) not in (int, float) or not math.isfinite(height) or height <= 0):
            raise ValueError('Pour 접근 높이는 유한한 양수여야 합니다')
        above = list(start)
        above[2] += height  # 붓기 시작점 기준 BASE Z 상승. 용기 파지 ABOVE와 구분한다.
        if job.cancel:
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('APPROACH')
        self.arm.movel(above, self.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        self.arm.movel(start, self.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        completed = False
        try:
            job.feedback and job.feedback('TILT')
            self.arm.movel(end, self.vel_scale)
            if job.cancel:
                raise RuntimeError('cancelled')
            job.feedback and job.feedback('HOLD')
            self._wait_with_nudge(float(p('pour.hold_s').value), job)
            if job.cancel:
                raise RuntimeError('cancelled')
            completed = True
        finally:
            if completed and not job.cancel:
                job.feedback and job.feedback('RETURN')
                self.arm.movel(start, self.vel_scale)
        return True

    def _do_taught_pour(self, job, station):
        middle = SkillNode._pose_from_extra(station, 'middle_posx')
        above = SkillNode._pose_from_extra(station, 'pour_above_posx')
        start = SkillNode._pose_from_extra(station, 'pour_start_posx')
        end = SkillNode._pose_from_extra(station, 'pour_end_posx')
        exit_mm = finite(station.extra['pour_exit_mm'], '붓기 후 상승량')
        if exit_mm <= 0:
            raise ValueError('붓기 후 상승량은 양수여야 한다')
        high = list(above)
        high[2] += exit_mm
        cancel = lambda: job.cancel or self._cancel_requested()
        for phase, target in [('APPROACH', middle), ('APPROACH', above), ('APPROACH', start),
                              ('TILT', end), ('RETURN', above), ('RETURN', high), ('RETURN', middle)]:
            if cancel():
                raise RuntimeError('cancelled')
            SkillNode._require_held_scoop(self)
            job.feedback and job.feedback(phase)
            self.arm.movel_cancellable(target, self.vel_scale, cancel, self.motion_timeout_s)
        return True

    def _do_return_material(self, job: Job):
        self._empty_scoop_baseline_pending = False
        self._require_scoop_extracted()
        material_id = job.args['material_id']
        SkillNode._require_held_scoop(self, material_id)
        station = self.stations.for_material(material_id)
        # 두 자세를 모두 검증한 뒤에만 첫 이동을 시작한다. 미티칭이면 현재 자세를 유지한다.
        start = SkillNode._pose_from_extra(station, 'return_start_posx')
        end = SkillNode._pose_from_extra(station, 'return_end_posj')
        if job.cancel:
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('APPROACH')
        self.arm.movel(start, self.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('TILT')
        # 실패·취소도 기울어진 자세일 수 있어 성공 여부와 무관하게 유지한다.
        # SafePose·파지 변경으로 해제하지 않는다. 연결 경로 구현 시 해제 조건을 정한다.
        self._return_rescoop_blocked = True
        # 손목 특이점을 지나는 직선 보간 대신 티칭한 관절각으로 이동한다.
        self.arm.movej_cancellable(end, self.vel_scale, lambda: job.cancel, self.motion_timeout_s)
        if job.cancel:
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('HOLD')
        self._wait_with_nudge(float(self.get_parameter('pour.hold_s').value), job)
        if job.cancel:
            raise RuntimeError('cancelled')
        # TODO([A]): 반환 끝 → 재스쿱 연결은 스쿱 모션 구현 시 함께 티칭·검증한다.
        # 시작 자세로 돌아가지 않고 반환 끝 자세에서 종료한다.
        return True

    def _measure_weight_reading(self, tare_g: float, subject: str,
                                station_id: str = 'workbench') -> WeightReading:
        p = self.get_parameter
        capture_baseline = (subject == 'scoop'
                            and getattr(self, '_empty_scoop_baseline_pending', False))
        if capture_baseline:
            self._empty_scoop_force_baseline = None
        period_s = self._scale_period_s()
        samples = int(p('scale.samples').value)
        settle_s = float(p('scale.settle_s').value)
        method = p('scale.method').value
        if method not in ('workpiece', 'tool_force'):
            raise ValueError(f'scale.method는 workpiece 또는 tool_force여야 한다: {method}')
        if bool(p('scale.simulated').value):
            raw_mean, raw_std, raw_hf_std, valid_src = 0.0, 0.0, 0.0, False
            baseline_mean, baseline_std = raw_mean, raw_std
        elif method == 'workpiece':
            raw_mean, raw_std, valid_src, raw_samples = self.arm.measure_workpiece(
                samples, settle_s, period_s=period_s, observer=self._observe_force,
                include_samples=True)
            baseline_mean, baseline_std = raw_mean, raw_std
            if valid_src:
                raw_mean, raw_std, raw_hf_std, _ = fit_oscillation(raw_samples, period_s)
            else:
                raw_hf_std = 0.0
        else:
            _, raw_mean, raw_std, valid_src, raw_samples = self.arm.measure_force(
                samples, settle_s, period_s=period_s, observer=self._observe_force,
                include_samples=True)
            baseline_mean, baseline_std = raw_mean, raw_std
            if valid_src:
                raw_mean, raw_std, raw_hf_std, _ = fit_oscillation(raw_samples, period_s)
            else:
                raw_hf_std = 0.0
        model = WeightModel(ScaleConfig(
            method=method,
            gain=float(p('scale.gain').value),
            offset_g=float(p('scale.offset_g').value),
            max_std_g=float(p('scale.max_std_g').value),
            max_hf_std_g=float(p('scale.max_hf_std_g').value),
            fz_sign=float(p('scale.fz_sign').value),
        ))
        model.set_tare(tare_g)
        gross_g, _, net_g, std_g, valid = model.reading(
            raw_mean, raw_std, valid_src, raw_hf_std=raw_hf_std)
        reading = WeightReading(
            gross_g=gross_g,
            tare_g=tare_g,
            net_g=net_g,
            std_g=std_g,
            samples=samples,
            valid=valid,
            station=station_id,
            subject=subject,
        )
        reading.header.stamp = self.get_clock().now().to_msg()
        if (capture_baseline and method == 'tool_force' and valid_src
                and not bool(p('scale.simulated').value)
                and math.isfinite(baseline_mean) and math.isfinite(baseline_std) and baseline_std >= 0
                and self._held_payload == 'scoop' and self._held_material_id
                and not self._cancel_requested()):
            station = self.stations.for_material(self._held_material_id)
            if station.station_id == station_id and self._pose_matches(self.arm.current_posx(), station.posx):
                self._empty_scoop_force_baseline = {
                    'fz_mean_n': float(baseline_mean), 'fz_std_n': float(baseline_std),
                    'weight_valid': bool(valid),
                    'material_id': self._held_material_id, 'station_id': station_id,
                    'pose': list(station.posx),
                    'safety_revision': getattr(self, '_safety_revision', 0),
                }
                self._empty_scoop_baseline_pending = False
                self.get_logger().info('[EMPTY_SCOOP_FZ_BASELINE] ' + json.dumps(
                    self._empty_scoop_force_baseline, ensure_ascii=False))
        return reading

    def _do_weigh(self, job: Job):
        self._require_scoop_extracted()
        p = self.get_parameter
        station = self.stations.get('workbench')
        pick_posx = station.posx
        measure_posx = station.above(self.stations.approach_mm)
        if 'approach_posj' in station.extra:
            if getattr(self.gripper, 'backend', '') == 'dio':
                self.gripper.refresh_dio()
            state = self.gripper.state(self._now_s())
            if state.get('busy', True) or state.get('grip_inferred', False):
                raise RuntimeError('용기 계량 전 열린 그리퍼 확인이 필요하다')
            self._held_payload = 'empty'
            self._do_move(job, station_id='workbench', approach=MoveToStation.Goal.AT)
            pick_posx = list(self.arm.current_posx())
            measure_posx = list(pick_posx)
            measure_posx[2] += station.extra['approach_mm']
        elif 'solution_space' in station.extra:
            # Weigh의 내부 이동도 MoveToStation과 같은 상부 접근 정책을 따른다.
            self._do_move(job, station_id='workbench', approach=MoveToStation.Goal.ABOVE)
        else:
            self.arm.movel(measure_posx, self.vel_scale)
        if not bool(p('scale.simulated').value):
            self.arm.reset_workpiece()
        self.arm.movel(pick_posx, self.vel_scale)
        job.feedback and job.feedback('GRIP')
        grip_commanded = True
        reading = WeightReading()
        completed = False
        try:
            ok, _, inferred = self.gripper.grip(float(p('gripper.cup_width_mm').value),
                                                float(p('gripper.force_n').value), 3.0)
            if not ok or not inferred:
                raise RuntimeError('용기 파지 실패')
            job.feedback and job.feedback('LIFT')
            self.arm.movel(measure_posx, self.vel_scale)
            job.feedback and job.feedback('SETTLE')
            reading = self._measure_weight_reading(float(job.args['tare_g']), 'container')
            job.feedback and job.feedback('MEASURE')
            completed = True
        finally:
            if completed and grip_commanded and not job.cancel:
                job.feedback and job.feedback('PLACE')
                self.arm.movel(pick_posx, self.vel_scale)
                if not self.gripper.release(3.0):
                    raise RuntimeError('용기 계량 후 열림 미확인')
                self.arm.movel(measure_posx, self.vel_scale)
                if 'approach_posj' in station.extra:
                    self._held_payload = 'empty'
                    self._record_arrival(station.station_id, MoveToStation.Goal.ABOVE, measure_posx)
        return reading

    def _do_weigh_held(self, job: Job):
        if self._scoop_extract_uncertain:
            raise RuntimeError('스쿱 인출 상태가 불확실하다. SafePose 후 수동 확인이 필요하다')
        SkillNode._require_held_scoop(self)
        material = self.stations.for_material(self._held_material_id)
        if job.cancel:
            raise RuntimeError('cancelled')

        job.feedback and job.feedback('LIFT')
        if self._pending_scoop_extract:
            lift_z_mm = self.scoop_extract_lift_z_mm
            if not math.isfinite(lift_z_mm) or lift_z_mm <= 0:
                raise ValueError('gripper.scoop_extract_lift_z_mm는 유한한 양수여야 한다')
            target = list(self.arm.current_posx())
            if len(target) != 6:
                raise ValueError('스쿱 인출 기준 posx는 6개여야 한다')
            target[1] += self.scoop_extract_y_mm
            self._pending_scoop_extract = False
            self._scoop_extract_uncertain = True
            self.arm.movel(target, self.vel_scale)
            if job.cancel:
                raise RuntimeError('cancelled')
            # 원료통으로 대각선 진입하기 전에 인출 완료 위치에서 수직 상승한다.
            # 실제 BASE 자세의 X/Y·회전은 유지하고 Z에만 설정 높이를 더한다.
            lift_target = list(self.arm.current_posx())
            if len(lift_target) != 6 or not all(math.isfinite(v) for v in lift_target):
                raise ValueError('스쿱 상승 기준 posx는 유한한 6개 값이어야 한다')
            lift_target[2] += lift_z_mm
            if job.cancel:
                raise RuntimeError('cancelled')
            self.arm.movel(lift_target, self.vel_scale)
            if job.cancel:
                raise RuntimeError('cancelled')
            self._scoop_extract_uncertain = False

        self.arm.movel(material.posx, self.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        self._station_id = material.station_id
        job.feedback and job.feedback('SETTLE')
        reading = self._measure_weight_reading(float(job.args['tare_g']), 'scoop', material.station_id)
        job.feedback and job.feedback('MEASURE')
        return reading

    # ── 콜백: 큐에 넣고 기다린다 ────────────────────────────────────────
    def _on_cancel(self, _goal):
        with self._job_lock:
            if self._current:
                self._current.cancel = True
        return CancelResponse.ACCEPT

    def _exec_move(self, gh):
        g = gh.request
        fb = MoveToStation.Feedback()
        def feedback(phase):
            fb.phase = phase
            gh.publish_feedback(fb)
        job = self._submit('move', feedback, station_id=g.station_id, approach=g.approach, vel_scale=g.vel_scale)
        res = MoveToStation.Result(success=not job.error and not job.cancel,
                                   message=job.error or ('cancelled' if job.cancel else ''),
                                   reached=job.result or '')
        gh.succeed() if res.success else (gh.canceled() if job.cancel else gh.abort())
        return res

    def _exec_scoop(self, gh):
        fb = Scoop.Feedback()

        def feedback(phase, contact_detected=False, contact_force_n=0.0, insertion_depth_mm=0.0):
            fb.phase = phase
            fb.contact_detected = bool(contact_detected)
            fb.contact_force_n = float(contact_force_n)
            fb.insertion_depth_mm = float(insertion_depth_mm)
            gh.publish_feedback(fb)

        job = self._submit('scoop', feedback, material_id=gh.request.material_id,
                           attempt=gh.request.attempt, depth_fraction=gh.request.depth_fraction)
        data = job.result if isinstance(job.result, dict) else {}
        res = Scoop.Result(
            success=not job.error and not job.cancel and not data.get('diagnostic_only', False),
            contact_detected=bool(data.get('contact_detected', job.result if not data else False)),
            max_contact_force_n=float(data.get('max_contact_force_n', 0.0)),
            insertion_depth_mm=float(data.get('insertion_depth_mm', 0.0)),
            message=job.error or ('cancelled' if job.cancel else
                                  data.get('measurement_message', data.get('message', ''))),
        )
        # 내부 중단/시간 초과는 ROS 클라이언트의 취소 요청과 다르다.
        gh.succeed() if res.success else (gh.canceled() if gh.is_cancel_requested else gh.abort())
        return res

    def _exec_pour(self, gh):
        fb = Pour.Feedback()
        def feedback(phase):
            fb.phase = phase
            gh.publish_feedback(fb)
        job = self._submit('pour', feedback, fraction=gh.request.fraction)
        res = Pour.Result(success=not job.error and not job.cancel,
                          message=job.error or ('cancelled' if job.cancel else ''))
        gh.succeed() if res.success else (gh.canceled() if job.cancel else gh.abort())
        return res

    def _exec_return_material(self, gh):
        fb = ReturnMaterial.Feedback()

        def feedback(phase):
            fb.phase = phase
            gh.publish_feedback(fb)

        job = self._submit('return_material', feedback, material_id=gh.request.material_id)
        res = ReturnMaterial.Result(success=not job.error and not job.cancel,
                                    message=job.error or ('cancelled' if job.cancel else ''))
        gh.succeed() if res.success else (gh.canceled() if job.cancel else gh.abort())
        return res

    def _exec_weigh(self, gh):
        fb = WeighContainer.Feedback()
        def feedback(phase):
            fb.phase = phase
            gh.publish_feedback(fb)
        job = self._submit('weigh', feedback, tare_g=gh.request.tare_g)
        res = WeighContainer.Result(success=not job.error and not job.cancel,
                                    message=job.error or ('cancelled' if job.cancel else ''),
                                    reading=job.result or WeightReading())
        gh.succeed() if res.success else (gh.canceled() if job.cancel else gh.abort())
        return res

    def _exec_weigh_held(self, gh):
        fb = WeighHeld.Feedback()

        def feedback(phase):
            fb.phase = phase
            gh.publish_feedback(fb)

        job = self._submit('weigh_held', feedback, tare_g=gh.request.tare_g)
        res = WeighHeld.Result(success=not job.error and not job.cancel,
                               message=job.error or ('cancelled' if job.cancel else ''),
                               reading=job.result or WeightReading())
        gh.succeed() if res.success else (gh.canceled() if job.cancel else gh.abort())
        return res

    def _srv_set_gripper(self, req, res):
        job = self._submit('grip', close=req.close, width_mm=req.width_mm, force_n=req.force_n, timeout_s=req.timeout_s)
        if job.error:
            res.success, res.message = False, job.error
        else:
            res.success, res.final_width_mm, res.grip_inferred = job.result
        return res

    def _srv_measure(self, req, res):
        p = self.get_parameter
        job = self._submit('measure', samples=req.samples or int(p('scale.samples').value),
                           settle_s=req.settle_s or float(p('scale.settle_s').value))
        if job.error:
            res.valid, res.message = False, job.error
        else:
            res.force, res.fz_mean_n, res.fz_std_n, res.valid, res.message = job.result
        return res

    def _srv_safe(self, req, res):
        with self._job_lock:
            self._drain_jobs_locked('cancelled by safe_pose')
            if self._current:
                self._current.cancel = True
        job = self._submit('safe', reason=req.reason)
        res.success, res.message = not job.error, job.error
        self.event('WARN', 'SAFE_POSE', req.reason)
        return res


def main(args=None):
    # SIGINT/SIGTERM에서 먼저 ROS 문맥이 종료되면 DSR 해제 응답을 받을 수 없다.
    from rclpy.signals import SignalHandlerOptions
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    exit_requested = threading.Event()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous:
        signal.signal(sig, lambda *_: exit_requested.set())
    node = None
    cleanup_failed = False
    ex = MultiThreadedExecutor(num_threads=4)
    try:
        node = SkillNode()
        ex.add_node(node)  # DR_init 노드는 워커만 spin한다.
        while rclpy.ok() and not exit_requested.is_set():
            node.check_startup()
            ex.spin_once(timeout_sec=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            cleanup_failed = not node.shutdown()
            # 살아 있는 워커가 사용하는 노드를 먼저 파괴하지 않는다.
            if node._worker_stopped.is_set():
                callbacks_stopped = ex.shutdown(timeout_sec=node.shutdown_timeout_s)
                if callbacks_stopped:
                    node.destroy_node()
                    node.arm.node.destroy_node()
                    rclpy.try_shutdown()
                else:
                    cleanup_failed = True
                    node.get_logger().error('콜백 종료 미확인: 사용 중인 노드를 파괴하지 않음')
        else:
            rclpy.try_shutdown()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if cleanup_failed:
            # 응답 없는 장치 호출은 Python에서 강제 취소할 수 없다. 정상 종료로 숨기지 않는다.
            raise SystemExit(1)


if __name__ == '__main__':
    main()
