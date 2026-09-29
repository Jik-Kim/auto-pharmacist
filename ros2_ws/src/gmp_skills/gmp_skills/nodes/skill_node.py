"""공정 노드의 ROS 요청을 받아 스킬 실행 결과를 돌려주는 창구.

호출 순서: process_node의 Action/Service 요청 → 이 파일의 _exec_*/_srv_*
→ runtime._submit()의 Job 큐 → 단일 로봇 워커 → execution의 이동/스쿠핑/
계량/안전 객체 → adapters의 실제 장치 호출. 결과는 Job을 거쳐 ROS 응답으로
돌아간다. 이 파일의 ROS 콜백에서는 로봇을 직접 움직이지 않는다.
위치 설정은 stations.yaml, 속도·계량·안전 설정은 common.yaml에서 읽는다.
"""

import queue
import json
import signal
import math
import threading
import time
import uuid

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
from gmp_interfaces.srv import MeasureForce, SafePose, SetGripper, RecoverSafety, RestoreGrip
from gmp_skills.adapters.dsr_arm import DsrArm
from gmp_skills.adapters.rg2_gripper import Rg2Gripper
from gmp_skills.core.nudge import NudgeDetector
from gmp_skills.core.stations import StationTable
# 기존 Job import 경로는 외부 호출자와의 호환을 위해 유지한다.
from gmp_skills.execution import ExecutionContext, Job, SkillExecution


class SkillNode(Node):
    def __init__(self):
        # launch가 넘긴 ROS 파라미터를 읽어 장치, 공유 상태, Action/Service를 만든다.
        # 설정 기본값은 common.yaml에 있으며 장치 초기화는 별도 워커가 수행한다.
        super().__init__('skill_node', automatically_declare_parameters_from_overrides=True)
        self.ctx = ExecutionContext(parameter=self.get_parameter, clock=self.get_clock,
                                    now=self._now_s, logger=self.get_logger,
                                    event=self.event, ok=rclpy.ok)
        self.execution = SkillExecution(self.ctx)
        g = lambda k: self.get_parameter(k).value  # noqa: E731
        self.execution.weighing._scale_period_s()  # 장치 생성 전에 잘못된 계량 설정을 거부한다.
        self.ctx.config.mode = g('mode')
        self.ctx.config.height_measure_only = g('scoop.height_measure_only')
        if type(self.ctx.config.height_measure_only) is not bool:
            raise ValueError('scoop.height_measure_only는 bool이어야 한다')
        collision = g('safety.collision_sensitivity')
        if (isinstance(collision, bool) or not isinstance(collision, (int, float))
                or not math.isfinite(collision) or not 0 <= collision <= 100):
            raise ValueError('safety.collision_sensitivity는 유한한 0~100 % 값이어야 한다')
        self.ctx.config.vel_scale = float(g('robot.vel_scale'))
        self.ctx.config.motion_timeout_s = float(g('robot.motion_timeout_s'))
        self.ctx.config.scoop_extract_y_mm = float(g('gripper.scoop_extract_y_mm'))
        self.ctx.config.scoop_extract_lift_z_mm = float(g('gripper.scoop_extract_lift_z_mm'))
        self.ctx.stations = StationTable.from_yaml(g('stations_file'))
        self.ctx.state.station_id = ''
        self.ctx.state.motion_anchor = None
        self.ctx.state.held_payload = 'unknown'
        self.ctx.state.held_material_id = ''
        self.ctx.state.empty_scoop_force_baseline = None
        self.ctx.state.empty_scoop_baseline_pending = False
        self.ctx.config.transfer_joint_vel = float(g('robot.transfer_joint_vel_deg_s'))
        self.ctx.config.transfer_joint_acc = float(g('robot.transfer_joint_acc_deg_s2'))
        self.ctx.config.pose_xyz_tolerance = float(g('robot.pose_xyz_tolerance_mm'))
        self.ctx.config.pose_rotation_tolerance = float(g('robot.pose_rotation_tolerance_deg'))
        self.ctx.config.joint_tolerance = float(g('robot.joint_tolerance_deg'))
        for value in (self.ctx.config.pose_xyz_tolerance, self.ctx.config.pose_rotation_tolerance, self.ctx.config.joint_tolerance):
            if not math.isfinite(value) or value <= 0:
                raise ValueError('도착·출발 검증 허용오차는 유한한 양수여야 한다')
        self.ctx.state.return_rescoop_blocked = False  # 연결 경로 구현 전에는 반환 후 재스쿱 금지
        self.ctx.state.pending_scoop_extract = False
        self.ctx.state.scoop_extract_uncertain = False
        self.ctx.arm = DsrArm(g('robot.id'), g('robot.model'), self.ctx.config.mode, float(g('robot.vel')), float(g('robot.acc')),
                          g('robot.tool_name'), g('robot.tcp_name'), self.get_logger(), self._now_s,
                          startup_timeout_s=float(g('robot.startup_timeout_s')),
                          virtual_tcp_name=g('robot.virtual_tcp_name'),
                          tcp_offset_mm_deg=g('robot.tcp_offset_mm_deg'),
                          task_vel=g('robot.task_vel'), task_acc=g('robot.task_acc'))

        backend = 'virtual' if self.ctx.config.mode == 'virtual' else g('gripper.backend')
        self._grip_cli = (self.create_client(SetCommand, '/onrobot/sendCommand')
                          if backend != 'dio' else None)
        self.ctx.gripper = Rg2Gripper(backend, self._send_gripper_command, self.ctx.arm,
                                  float(g('gripper.grip_margin_mm')), float(g('gripper.slip_mm')),
                                  float(g('gripper.open_width_mm')), tuple(g('gripper.dio_pins')),
                                  tuple(g('gripper.din_pins')), self.get_logger(), self._now_s,
                                  float(g('gripper.state_timeout_s')),
                                  float(g('gripper.dio_settle_s')), float(g('gripper.completion_settle_s')))
        if backend == 'modbus':
            self.create_subscription(OnRobotRGInput, '/onrobot/status',
                                     lambda msg: self.ctx.gripper.on_native_status(msg, self._now_s()),
                                     QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        elif backend == 'virtual':
            self.create_subscription(JointState, f"/{g('robot.id')}/gripper_joint_states", self._on_js,
                                     QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))

        self.cb = ReentrantCallbackGroup()
        self.pub_state = self.create_publisher(GripperState, 'gripper_state',
                                               QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.pub_event = self.create_publisher(CellEvent, 'event', 100)
        self.create_timer(0.1, self._pub_gripper_state, callback_group=self.cb)

        self.ctx.state.nudge_enabled = bool(g('safety.nudge_enabled')) and not bool(g('scale.simulated'))
        self.ctx.state.nudge = NudgeDetector(float(g('safety.nudge_force_n')), float(g('safety.nudge_window_s')),
                                     float(g('safety.nudge_cooldown_s')))
        self.ctx.state.nudge_fault_logged = False

        self.ctx.state.q: 'queue.Queue[Job]' = queue.Queue()
        self.ctx.state.job_lock = threading.Lock()
        self.ctx.state.current: Job | None = None
        self.ctx.state.stopping = threading.Event()
        self.ctx.state.worker_stopped = threading.Event()
        self.ctx.state.cleanup_error = ''
        self.ctx.config.shutdown_timeout_s = float(g('robot.shutdown_timeout_s'))
        if not math.isfinite(self.ctx.config.shutdown_timeout_s) or self.ctx.config.shutdown_timeout_s <= 0:
            raise ValueError('robot.shutdown_timeout_s는 유한한 양수여야 한다')
        self.ctx.arm.cancel_requested = self.execution.runtime._cancel_requested
        self.ctx.gripper.cancel_requested = self.execution.runtime._cancel_requested
        self.ctx.arm.motion_timeout_s = self.ctx.config.motion_timeout_s
        self.ctx.arm.pose_xyz_tolerance = self.ctx.config.pose_xyz_tolerance
        self.ctx.arm.pose_rotation_tolerance = self.ctx.config.pose_rotation_tolerance
        self.ctx.arm.joint_tolerance = self.ctx.config.joint_tolerance
        self.ctx.state.safety_latched = False
        self.ctx.state.safety_reason = ''
        self.ctx.state.safety_revision = 0
        self.ctx.state.safety_session = uuid.uuid4().hex
        self.ctx.state.last_robot_state = -1
        self.ctx.state.last_state_poll = float('-inf')
        self.ctx.state.configured = False
        self.ctx.state.recovery_requests = {}
        self.ctx.state.recovery_inflight = False
        self.ctx.config.state_poll_s = float(g('safety.state_poll_s'))
        self.ctx.config.recovery_timeout_s = float(g('safety.recovery_timeout_s'))
        self.ctx.config.recovery_cache_size = int(g('safety.recovery_cache_size'))
        if (any(not math.isfinite(v) or v <= 0 for v in
                (self.ctx.config.state_poll_s, self.ctx.config.recovery_timeout_s)) or self.ctx.config.recovery_cache_size <= 0):
            raise ValueError('안전 조회·복구 설정은 유한한 양수여야 한다')
        self.ctx.state.worker_thread = threading.Thread(target=self.execution.runtime._worker, daemon=True, name='dsr-worker')
        self.ctx.state.worker_thread.start()
        self.ctx.state.ready = False
        self.ctx.state.startup_job = Job('startup', {'expect_tool': g('robot.tool_name'),
                                             'expect_tcp': g('robot.tcp_name'),
                                             'restore_material_id': g('restore.material_id'),
                                             'restore_operator_id': g('restore.operator_id'),
                                             'restore_confirmed': g('restore.confirmed'),
                                             'restore_empty_scoop_confirmed': g('restore.empty_scoop_confirmed')})
        self.ctx.state.q.put(self.ctx.state.startup_job)

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
        self.create_service(RestoreGrip, 'restore_grip', self._srv_restore_grip, callback_group=self.cb)


    def _now_s(self):
        # 계량·이벤트에 같은 ROS 시계를 쓰도록 현재 시각을 초 단위로 반환한다.
        return self.get_clock().now().nanoseconds / 1e9


    def event(self, level: str, code: str, text: str, batch_id: str = ''):
        # 배치·안전 이벤트를 /cell/event로 발행하고 운영 로그에도 같은 내용을 남긴다.
        m = CellEvent(level=getattr(CellEvent, level), code=code, text=text, batch_id=batch_id)
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_event.publish(m)
        if level == 'ERROR':
            self.get_logger().error(f'[{code}] {text}')
        else:
            self.get_logger().info(f'[{code}] {text}')


    def _send_gripper_command(self, cmd: str) -> bool:
        # 그리퍼 어댑터가 요청한 문자열을 /onrobot/sendCommand 서비스로 보낸다.
        # 서비스 미준비 또는 3초 내 응답 없음은 실패로 돌려준다.
        if not self._grip_cli.wait_for_service(timeout_sec=2.0):
            self.event('ERROR', 'GRIPPER_SVC', '/onrobot/sendCommand 없음')
            return False
        fut = self._grip_cli.call_async(SetCommand.Request(command=cmd))
        t0 = self._now_s()
        while not fut.done() and self._now_s() - t0 < 3.0:
            time.sleep(0.01)          # ROS executor가 서비스 응답을 처리하므로 워커에서는 추가 spin을 하지 않는다.
        return bool(fut.done() and fut.result().success)


    def _on_js(self, msg: JointState):
        # 가상 그리퍼의 JointState에서 손가락 관절각과 표본 시각을 꺼내
        # 어댑터에 전달한다. Modbus 실물 상태는 /onrobot/status로 따로 받는다.
        source_s = msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9
        stamp_s = source_s if source_s > 0.0 else self._now_s()
        for n, pos in zip(msg.name, msg.position):
            if n.endswith('finger_joint'):
                self.ctx.gripper.on_joint_state(pos, stamp_s)
                break


    def _pub_gripper_state(self):
        # 어댑터가 마지막 센서 입력으로 판단한 폭·동작 중·파지 상태를 발행한다.
        # 이전 파지 폭에서 slip_mm 이상 달라졌다면 미끄러짐 이벤트도 낸다.
        state = self.ctx.gripper.state(self._now_s())
        w = state['width_mm']
        m = GripperState(width_mm=-1.0 if w is None else w, busy=state['busy'],
                         grip_inferred=state['grip_inferred'],
                         safety_triggered=state.get('safety_triggered', False), force_cmd_n=self.ctx.gripper.force_cmd_n, backend=self.ctx.gripper.backend)
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_state.publish(m)
        if self.ctx.gripper.consume_slip():
            self.event('WARN', 'GRIP_SLIP', f'폭 변화가 slip_mm를 초과함: width={m.width_mm:.2f} mm')


    def _on_robot_alarm(self, msg):
        # 두산 알람의 level/group이 차단 기준에 해당하면 안전 차단을 건다.
        # 이 콜백은 상태만 변경하며 로봇 정지 명령은 단일 워커가 처리한다.
        if msg.level >= 3 or (msg.group == 5 and msg.level >= 2):
            self.execution.safety._latch_safety(f'vendor alarm {msg.group}/{msg.code}: {msg.msg1}', alarm=True)


    def _srv_recover(self, req, res):
        # 같은 request_id의 복구 명령이 중복 전송되지 않게 기록한다. 작업자 ID,
        # 기대 로봇 상태, 현장 확인 여부가 이전 요청과 같아야 결과를 재사용한다.
        fingerprint = (req.operator_id, req.expected_state, req.operator_confirmed)
        if not req.request_id.strip() or not req.operator_id.strip() or not req.operator_confirmed:
            res.success, res.manual_required, res.robot_state = False, True, -1
            res.message = '작업자·고유 요청 ID·원인 제거 확인이 필요합니다'
            return res
        with self.ctx.state.job_lock:
            entry = self.ctx.state.recovery_requests.get(req.request_id)
            if entry is None:
                if self.ctx.state.recovery_inflight:
                    res.success, res.manual_required, res.robot_state = False, False, self.ctx.state.last_robot_state
                    res.message = '다른 복구 요청 처리 중입니다'
                    return res
                if len(self.ctx.state.recovery_requests) >= self.ctx.config.recovery_cache_size:
                    res.success, res.manual_required, res.robot_state = False, True, -1
                    res.message = '기동 세션 복구 요청 기록 상한 도달. 운영자 확인 필요'
                    return res
                entry = {'fingerprint': fingerprint, 'done': threading.Event()}
                self.ctx.state.recovery_requests[req.request_id] = entry
                self.ctx.state.recovery_inflight = True
                owner = True
            else:
                owner = False
        if entry['fingerprint'] != fingerprint:
            res.success, res.manual_required, res.robot_state = False, True, -1
            res.message = '같은 요청 ID의 내용이 다릅니다'
            return res
        if owner:
            try:
                # 복구 요청을 받으면 먼저 새 일반 동작을 막는다. 컨트롤러가 이미
                # 대기 상태로 돌아왔더라도 워커가 상태와 자가진단을 확인해야 푼다.
                revision = self.execution.safety._latch_safety('HMI 안전 복구 요청', recovery_request=req)
                job = self.execution.runtime._submit('recover', operator_id=req.operator_id,
                                   safety_revision=revision,
                                   expected_state=req.expected_state,
                                   operator_confirmed=req.operator_confirmed)
                result = job.result if not job.error and job.result else (
                    False, True, self.ctx.state.last_robot_state, job.error or '복구 결과 없음')
                with self.ctx.state.job_lock:
                    if result[0] and self.ctx.state.safety_latched:
                        result = False, True, self.ctx.state.last_robot_state, '복구 직후 새 정지 발생. 차단 유지'
                    entry['result'] = result
                    entry['revision'] = self.ctx.state.safety_revision
                    self.event('INFO' if result[0] else 'WARN', 'ROBOT_SAFETY_RECOVERY', json.dumps(
                        {'request_id': req.request_id, 'operator_id': req.operator_id,
                         'safety_session': self.ctx.state.safety_session,
                         'safety_revision': self.ctx.state.safety_revision,
                         'success': result[0], 'manual_required': result[1],
                         'robot_state': result[2], 'message': result[3]}, ensure_ascii=False))
            finally:
                entry.setdefault('result', (False, True, -1, '복구 처리 실패'))
                entry.setdefault('revision', self.ctx.state.safety_revision)
                with self.ctx.state.job_lock:
                    self.ctx.state.recovery_inflight = False
                    entry['done'].set()
        elif not entry['done'].is_set():
            res.success, res.manual_required, res.robot_state = False, False, self.ctx.state.last_robot_state
            res.message = '동일 복구 요청 처리 중입니다. 같은 ID로 결과를 재조회하세요'
            return res
        result = entry['result']
        with self.ctx.state.job_lock:
            if result[0] and (entry['revision'] != self.ctx.state.safety_revision or self.ctx.state.safety_latched):
                result = False, True, self.ctx.state.last_robot_state, '이전 복구 결과 이후 새 정지 발생. 새 요청 필요'
        res.success, res.manual_required, res.robot_state, res.message = result
        return res


    def _on_cancel(self, _goal):
        # Action 취소 요청을 현재 Job에 기록한다. 실제 이동 정지는 워커와
        # DsrArm의 취소 감시 경로에서 실행한다.
        with self.ctx.state.job_lock:
            if self.ctx.state.current:
                self.ctx.state.current.cancel = True
        return CancelResponse.ACCEPT


    def _exec_move(self, gh):
        # MoveToStation 요청의 스테이션·AT/ABOVE·속도를 move Job에 전달한다.
        g = gh.request
        fb = MoveToStation.Feedback()
        def feedback(phase):
            # 워커가 알린 이동 단계(phase)를 이 Action의 진행 피드백으로 전달한다.
            fb.phase = phase
            gh.publish_feedback(fb)
        job = self.execution.runtime._submit('move', feedback, station_id=g.station_id, approach=g.approach, vel_scale=g.vel_scale)
        res = MoveToStation.Result(success=not job.error and not job.cancel,
                                   message=job.error or ('cancelled' if job.cancel else ''),
                                   reached=job.result or '')
        gh.succeed() if res.success else (gh.canceled() if job.cancel else gh.abort())
        return res


    def _exec_scoop(self, gh):
        # Scoop 요청을 Job으로 전달한다. 실제 로봇 이동은 execution/scooping.py가
        # 수행하고, 여기서는 접촉·깊이 또는 미측정 결과를 ROS 메시지에 담는다.
        fb = Scoop.Feedback()

        def feedback(phase, contact_detected=False, contact_force_n=0.0, insertion_depth_mm=0.0):
            # 워커가 알린 이동 단계(phase)를 이 Action의 진행 피드백으로 전달한다.
            fb.phase = phase
            fb.contact_detected = bool(contact_detected)
            fb.contact_force_n = float(contact_force_n)
            fb.insertion_depth_mm = float(insertion_depth_mm)
            gh.publish_feedback(fb)

        job = self.execution.runtime._submit('scoop', feedback, material_id=gh.request.material_id,
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
        # 로봇 내부 오류·시간 초과는 Action ABORTED다. CANCELED는 클라이언트가
        # 명시적으로 취소를 요청했을 때만 사용한다.
        gh.succeed() if res.success else (gh.canceled() if gh.is_cancel_requested else gh.abort())
        return res


    def _exec_pour(self, gh):
        # Pour의 투입 비율을 Job에 전달하고 워커의 단계·결과를 ROS로 응답한다.
        fb = Pour.Feedback()
        def feedback(phase):
            # 워커가 알린 이동 단계(phase)를 이 Action의 진행 피드백으로 전달한다.
            fb.phase = phase
            gh.publish_feedback(fb)
        job = self.execution.runtime._submit('pour', feedback, fraction=gh.request.fraction)
        res = Pour.Result(success=not job.error and not job.cancel,
                          message=job.error or ('cancelled' if job.cancel else ''))
        gh.succeed() if res.success else (gh.canceled() if job.cancel else gh.abort())
        return res


    def _exec_return_material(self, gh):
        # ReturnMaterial 요청의 원료 ID를 전달해 해당 원료통의 반환 경로를 고른다.
        fb = ReturnMaterial.Feedback()

        def feedback(phase):
            # 워커가 알린 이동 단계(phase)를 이 Action의 진행 피드백으로 전달한다.
            fb.phase = phase
            gh.publish_feedback(fb)

        job = self.execution.runtime._submit('return_material', feedback, material_id=gh.request.material_id)
        res = ReturnMaterial.Result(success=not job.error and not job.cancel,
                                    message=job.error or ('cancelled' if job.cancel else ''))
        gh.succeed() if res.success else (gh.canceled() if job.cancel else gh.abort())
        return res


    def _exec_weigh(self, gh):
        # WeighContainer의 빈 용기 무게(tare_g)를 전달하고 측정 결과를 응답한다.
        fb = WeighContainer.Feedback()
        def feedback(phase):
            # 워커가 알린 이동 단계(phase)를 이 Action의 진행 피드백으로 전달한다.
            fb.phase = phase
            gh.publish_feedback(fb)
        job = self.execution.runtime._submit('weigh', feedback, tare_g=gh.request.tare_g)
        res = WeighContainer.Result(success=not job.error and not job.cancel,
                                    message=job.error or ('cancelled' if job.cancel else ''),
                                    reading=job.result or WeightReading())
        gh.succeed() if res.success else (gh.canceled() if job.cancel else gh.abort())
        return res


    def _exec_weigh_held(self, gh):
        # WeighHeld의 빈 스쿱 무게(tare_g)를 전달하고 측정 결과를 응답한다.
        fb = WeighHeld.Feedback()

        def feedback(phase):
            # 워커가 알린 이동 단계(phase)를 이 Action의 진행 피드백으로 전달한다.
            fb.phase = phase
            gh.publish_feedback(fb)

        job = self.execution.runtime._submit('weigh_held', feedback, tare_g=gh.request.tare_g)
        res = WeighHeld.Result(success=not job.error and not job.cancel,
                               message=job.error or ('cancelled' if job.cancel else ''),
                               reading=job.result or WeightReading())
        gh.succeed() if res.success else (gh.canceled() if job.cancel else gh.abort())
        return res


    def _srv_set_gripper(self, req, res):
        # 그리퍼 닫기/열기와 목표 폭·힘·제한시간을 워커에 전달한다.
        # DIO 방식에서는 목표 폭·힘을 실제 장치에 설정하지 않는다.
        job = self.execution.runtime._submit('grip', close=req.close, width_mm=req.width_mm, force_n=req.force_n, timeout_s=req.timeout_s)
        if job.error:
            res.success, res.message = False, job.error
        else:
            res.success, res.final_width_mm, res.grip_inferred = job.result
        return res


    def _srv_measure(self, req, res):
        # 로봇 외력의 보정 전 표본 통계를 요청한다. 0으로 온 표본 수·안정
        # 시간은 scale 설정 기본값으로 채운다.
        p = self.get_parameter
        job = self.execution.runtime._submit('measure', samples=req.samples or int(p('scale.samples').value),
                           settle_s=req.settle_s or float(p('scale.settle_s').value))
        if job.error:
            res.valid, res.message = False, job.error
        else:
            res.force, res.fz_mean_n, res.fz_std_n, res.valid, res.message = job.result
        return res


    def _srv_safe(self, req, res):
        # 안전 자세 요청은 기존 대기 Job을 취소하고 현재 Job에도 취소를 표시한다.
        # 이어서 safe Job을 큐에 넣으며 실제 관절 이동은 워커가 수행한다.
        with self.ctx.state.job_lock:
            state = self.ctx.state
            # 작업이 취소되어 이력이 지워지기 전에 복구 후보만 보관한다.
            # 그리퍼 조작·계량 인출·스쿠핑·붓기 중단은 자동 재개 대상으로 추정하지 않는다.
            kind = state.current.kind if state.current else None
            state.resume_grip = dict(
                payload=state.held_payload, material_id=state.held_material_id,
                pending=state.pending_scoop_extract, uncertain=state.scoop_extract_uncertain,
                resumable=kind in (None, 'move', 'measure'),
            )
            state.resume_grip_ready = False
            self.execution.runtime._drain_jobs_locked('cancelled by safe_pose')
            if self.ctx.state.current:
                self.ctx.state.current.cancel = True
        job = self.execution.runtime._submit('safe', reason=req.reason)
        res.success, res.message = not job.error, job.error
        self.event('WARN', 'SAFE_POSE', req.reason)
        return res


    def _srv_restore_grip(self, req, res):
        # 센서 조회와 이력 갱신은 DSR 단일 워커에서만 실행한다.
        job = self.execution.runtime._submit('restore_grip')
        res.success = not job.error and not job.cancel
        res.payload, res.material_id = ('unknown', '') if not res.success else job.result
        res.message = job.error or ('파지 상태 복구 완료' if res.success else '복구 취소')
        return res


    def check_startup(self):
        """기존 노드 수명주기 진입점을 유지한다."""
        return self.execution.runtime.check_startup()

    def shutdown(self):
        """ROS 문맥을 해제하기 전에 단일 워커의 정지·힘제어 해제를 기다린다."""
        return self.execution.runtime.shutdown()


def main(args=None):
    # SIGINT/SIGTERM에서 먼저 ROS 문맥이 종료되면 DSR 해제 응답을 받을 수 없다.
    # ROS executor는 콜백을 처리하고 DSR 워커는 로봇 호출을 처리한다.
    # 종료할 때 워커의 정지·힘제어 해제를 기다린 뒤 ROS 노드를 파괴한다.
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
            if node.ctx.state.worker_stopped.is_set():
                callbacks_stopped = ex.shutdown(timeout_sec=node.ctx.config.shutdown_timeout_s)
                if callbacks_stopped:
                    node.destroy_node()
                    node.ctx.arm.node.destroy_node()
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
