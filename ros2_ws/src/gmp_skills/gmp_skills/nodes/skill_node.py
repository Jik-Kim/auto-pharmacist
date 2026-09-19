"""로봇 스킬 서버. 로봇을 만지는 유일한 노드 (AGENTS.md 프로젝트 구조).

스레드 구조 (SOT D-02 · 교육 「두산 ROS2 동작 Sequence」 3장)
  Main   : MultiThreadedExecutor.spin(이 노드, ns cell) — Action/Service 콜백, gripper_state 10 Hz
  Worker : 큐에서 Job 을 꺼내 DsrArm/Rg2Gripper 를 블로킹 호출. **로봇 명령은 항상 직렬**
  DR_init 노드(ns dsr01) : DsrArm 이 소유, executor 에 넣지 않는다

입력  Action move_to_station·scoop·pour·weigh_container·weigh_held / Service set_gripper·measure_force·safe_pose
      /onrobot_joint_states (real) 또는 gripper_joint_states (virtual)
출력  gripper_state (10 Hz, BEST_EFFORT) · event

파라미터는 gmp_bringup/params/common.yaml 이 단일 출처. stations.yaml 경로는 파라미터 `stations_file`.

TODO([A]): I-004 취소 — 워커가 Job.cancel 플래그를 movel 사이에서만 본다. 긴 movel 은 쪼갠다.
"""
import queue
import math
import threading
import time
from dataclasses import dataclass, field

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy

from sensor_msgs.msg import JointState
from onrobot_rg_msgs.srv import SetCommand
from gmp_interfaces.action import MoveToStation, Scoop, Pour, WeighContainer, WeighHeld
from gmp_interfaces.msg import CellEvent, GripperState, WeightReading
from gmp_interfaces.srv import MeasureForce, SafePose, SetGripper
from gmp_dosing.core.scale import ScaleConfig, WeightModel

from gmp_skills.adapters.dsr_arm import DsrArm
from gmp_skills.adapters.rg2_gripper import Rg2Gripper
from gmp_skills.core.nudge import NudgeDetector
from gmp_skills.core.stations import StationTable
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
        self.mode = g('mode')
        self.vel_scale = float(g('robot.vel_scale'))
        self.motion_timeout_s = float(g('robot.motion_timeout_s'))
        self.scoop_extract_y_mm = float(g('gripper.scoop_extract_y_mm'))
        self.stations = StationTable.from_yaml(g('stations_file'))
        self._cartesian_ready = False
        self._station_id = ''
        self._motion_anchor = None
        self._held_payload = 'unknown'
        self.transfer_joint_vel = float(g('robot.transfer_joint_vel_deg_s'))
        self.transfer_joint_acc = float(g('robot.transfer_joint_acc_deg_s2'))
        self.pose_xyz_tolerance = float(g('robot.pose_xyz_tolerance_mm'))
        self.pose_rotation_tolerance = float(g('robot.pose_rotation_tolerance_deg'))
        self.joint_tolerance = float(g('robot.joint_tolerance_deg'))
        for value in (self.pose_xyz_tolerance, self.pose_rotation_tolerance, self.joint_tolerance):
            if not math.isfinite(value) or value <= 0:
                raise ValueError('도착·출발 검증 허용오차는 유한한 양수여야 한다')
        self._pending_scoop_extract = False
        self._scoop_extract_uncertain = False
        self.arm = DsrArm(g('robot.id'), g('robot.model'), self.mode, float(g('robot.vel')), float(g('robot.acc')),
                          g('robot.tool_name'), g('robot.tcp_name'), self.get_logger(), self._now_s,
                          startup_timeout_s=float(g('robot.startup_timeout_s')),
                          virtual_tcp_name=g('robot.virtual_tcp_name'),
                          tcp_offset_mm_deg=g('robot.tcp_offset_mm_deg'))

        backend = 'virtual' if self.mode == 'virtual' else g('gripper.backend')
        self._grip_cli = self.create_client(SetCommand, '/onrobot/sendCommand')
        self.gripper = Rg2Gripper(backend, self._send_gripper_command, self.arm,
                                  float(g('gripper.grip_margin_mm')), float(g('gripper.slip_mm')),
                                  float(g('gripper.open_width_mm')), tuple(g('gripper.dio_pins')),
                                  tuple(g('gripper.din_pins')), self.get_logger(), self._now_s,
                                  float(g('gripper.state_timeout_s')),
                                  float(g('gripper.dio_settle_s')))
        js_topic = '/onrobot_joint_states' if backend == 'modbus' else f"/{g('robot.id')}/gripper_joint_states"
        self.create_subscription(JointState, js_topic, self._on_js,
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
        threading.Thread(target=self._worker, daemon=True, name='dsr-worker').start()
        startup = self._submit('startup', expect_tool=g('robot.tool_name'), expect_tcp=g('robot.tcp_name'))
        if startup.error or not startup.result[0]:
            raise RuntimeError(f'자가진단 실패 — 기동 거부: {startup.error or startup.result[1]}')
        self.event('INFO', 'SELF_CHECK', f'OK {startup.result[1]}')

        # 서버 객체를 멤버로 유지해야 가비지 컬렉션 뒤에도 ROS 그래프에 계속 남는다.
        self._action_servers = [
            ActionServer(self, MoveToStation, 'move_to_station', self._exec_move,
                         callback_group=self.cb, goal_callback=lambda _: GoalResponse.ACCEPT,
                         cancel_callback=self._on_cancel),
            ActionServer(self, Scoop, 'scoop', self._exec_scoop, callback_group=self.cb,
                         cancel_callback=self._on_cancel),
            ActionServer(self, Pour, 'pour', self._exec_pour, callback_group=self.cb,
                         cancel_callback=self._on_cancel),
            ActionServer(self, WeighContainer, 'weigh_container', self._exec_weigh,
                         callback_group=self.cb, cancel_callback=self._on_cancel),
            ActionServer(self, WeighHeld, 'weigh_held', self._exec_weigh_held,
                         callback_group=self.cb, cancel_callback=self._on_cancel),
        ]
        self.get_logger().info(
            '[ACTION_SERVERS_READY] move_to_station, scoop, pour, weigh_container, weigh_held')
        self.create_service(SetGripper, 'set_gripper', self._srv_set_gripper, callback_group=self.cb)
        self.create_service(MeasureForce, 'measure_force', self._srv_measure, callback_group=self.cb)
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
                         safety_triggered=False, force_cmd_n=self.gripper.force_cmd_n, backend=self.gripper.backend)
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_state.publish(m)
        if self.gripper.consume_slip():
            self.event('WARN', 'GRIP_SLIP', f'폭 변화가 slip_mm를 초과함: width={m.width_mm:.2f} mm')

    def _observe_force(self, force6):
        if self._nudge_enabled and self._nudge.update(force6, self._now_s()):
            magnitude_n = math.sqrt(sum(float(v) ** 2 for v in force6[:3]))
            self.event('INFO', 'NUDGE', f'외력 nudge 입력 감지: |F|={magnitude_n:.2f} N')

    def _poll_nudge(self):
        if not self._nudge_enabled:
            return
        try:
            force = self.arm.tool_force()
            if force is not None:
                self._observe_force(force)
            self._nudge_fault_logged = False
        except Exception as exc:  # noqa: BLE001 — 유휴 감시 실패가 워커를 죽이면 안 된다
            if not self._nudge_fault_logged:
                self.event('WARN', 'NUDGE_UNAVAILABLE', f'외력 감시 실패: {exc}')
                self._nudge_fault_logged = True

    def _wait_with_nudge(self, duration_s: float, job: Job):
        end_s = self._now_s() + max(0.0, duration_s)
        while self._now_s() < end_s:
            if job.cancel:
                raise RuntimeError('cancelled')
            self._poll_nudge()
            time.sleep(min(0.1, max(0.0, end_s - self._now_s())))

    # ── 워커: 로봇 명령은 여기서만 ──────────────────────────────────────
    def _submit(self, kind: str, feedback=None, **args) -> Job:
        job = Job(kind, args, feedback=feedback)
        self._q.put(job)
        job.done.wait()
        return job

    def _worker(self):
        while rclpy.ok():
            try:
                job = self._q.get(timeout=0.1)
            except queue.Empty:
                self._poll_nudge()
                continue
            with self._job_lock:
                self._current = job
            try:
                if job.kind in ('scoop', 'pour', 'weigh', 'weigh_held', 'safe'):
                    # 이 스킬들은 MoveToStation 밖에서 움직이므로 이전 출발 이력은 폐기한다.
                    self._motion_anchor = None
                if job.kind == 'weigh':
                    self._held_payload = 'unknown'
                job.result = getattr(self, f'_do_{job.kind}')(job)
            except Exception as e:  # noqa: BLE001 — 로봇 에러는 전부 결과로 돌려준다
                self._motion_anchor = None
                self._held_payload = 'unknown'
                job.error = f'{type(e).__name__}: {e}'
                self.event('ERROR', f'{job.kind.upper()}_FAIL', job.error)
            finally:
                # 취소 콜백과 완료 확정을 같은 잠금으로 묶어 마지막 도착 기록 경합을 막는다.
                with self._job_lock:
                    if job.cancel:
                        self._motion_anchor = None
                        self._held_payload = 'unknown'
                    self._current = None
                    job.done.set()

    def _do_startup(self, job: Job):
        self.arm.initialize()
        return self.arm.self_check(job.args['expect_tool'], job.args['expect_tcp'])

    def _require_scoop_extracted(self):
        if self._scoop_extract_uncertain:
            raise RuntimeError('스쿱 인출 상태가 불확실하다. SafePose 후 수동 확인이 필요하다')
        if self._pending_scoop_extract:
            raise RuntimeError('스쿱 파지 후 WeighHeld로 +Y 인출을 먼저 수행해야 한다')

    def _do_move(self, job: Job):
        self._require_scoop_extracted()
        try:
            return self._move_checked(job)
        except Exception:
            self._motion_anchor = None
            self._held_payload = 'unknown'
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

    def _move_checked(self, job: Job):
        if job.cancel:
            raise RuntimeError('cancelled')
        if job.args['approach'] not in (0, 1):
            raise ValueError('approach는 ABOVE(0) 또는 AT(1)이어야 한다')
        st = self.stations.get(job.args['station_id'])
        target = st.above(self.stations.approach_mm) if job.args['approach'] == 0 else st.posx
        vel_scale = job.args.get('vel_scale') or self.vel_scale
        if not math.isfinite(vel_scale) or not 0 < vel_scale <= 1:
            raise ValueError('vel_scale은 0 초과 1 이하여야 한다')
        route = self.stations.transfers.get((self._station_id, st.station_id))
        if route is not None:
            self._run_transfer(route, job, target, vel_scale)
            return st.station_id
        protected = any(r.destination == st.station_id for r in self.stations.transfers.values())
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
                    taught = (outgoing.start_above_posj if job.args['approach'] == 0
                              else outgoing.start_at_posj)
                    if (self._pose_matches(self.arm.current_posx(), target)
                            and joints_match(self.arm.current_posj(), taught, self.joint_tolerance)):
                        self._held_payload = 'unknown'
                        self._record_arrival(st.station_id, job.args['approach'], target)
                        self._cartesian_ready = True
                        return st.station_id
                raise ValueError('등록된 출발 이력이 없는 보호 대상 이송이다')
        safe_posj = st.extra.get('posj') if job.args['approach'] != 0 else None
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
            self.arm.movel_cancellable(target, vel_scale, lambda: job.cancel,
                                       self.motion_timeout_s)
        if job.cancel:
            raise RuntimeError('cancelled')
        self._record_arrival(st.station_id, job.args['approach'], target)
        return st.station_id

    def _require_transfer_payload(self, expected):
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

        # 이미 이탈점이면 다시 움직이지 않는다. 마지막 관절점은 목적지 ABOVE다.
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
        entry = self.stations.get(route.destination).above(self.stations.approach_mm)
        if not self._pose_matches(self.arm.current_posx(), entry):
            raise RuntimeError('마지막 관절점이 목적지 ABOVE 위치/자세와 일치하지 않는다')
        if job.args['approach'] == 1:
            self.arm.movel_cancellable(target, vel_scale, lambda: job.cancel, self.motion_timeout_s)
        checkpoint()
        self._record_arrival(route.destination, job.args['approach'], target)

    def _do_grip(self, job: Job):
        a = job.args
        self._held_payload = 'unknown'
        if a['close']:
            if self._scoop_extract_uncertain:
                raise RuntimeError('스쿱 인출 상태가 불확실하여 재파지할 수 없다')
            result = self.gripper.grip(a['width_mm'], a['force_n'], a['timeout_s'] or 3.0)
            self._pending_scoop_extract = bool(
                result[0] and result[2] and self._station_id.startswith('scoop_'))
            self._scoop_extract_uncertain = False
            if result[0] and result[2]:
                anchor = getattr(self, '_motion_anchor', None)
                if self._station_id.startswith('scoop_'):
                    self._held_payload = 'scoop'
                elif (anchor is not None and anchor.station == self._station_id and anchor.approach == 1
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
        return released, self.gripper.width_mm() or -1.0, False

    def _do_measure(self, job: Job):
        if self.get_parameter('scale.simulated').value:
            return [0.0] * 6, 0.0, 0.0, False, 'simulated'
        mean6, fz, std, valid = self.arm.measure_force(job.args['samples'], job.args['settle_s'],
                                                       observer=self._observe_force)
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
        return True

    def _do_scoop(self, job: Job):
        self._require_scoop_extracted()
        p = self.get_parameter
        station = self.stations.for_material(job.args['material_id'])
        above = station.above(self.stations.approach_mm)
        job.feedback and job.feedback('APPROACH')
        self.arm.movel(above, self.vel_scale)
        start = self.arm.current_posx()
        contact_z = None
        max_force_n = 0.0
        insertion_mm = 0.0
        deadline_s = self._now_s() + float(p('safety.scoop_timeout_s').value)
        try:
            self.arm.compliance_on(list(p('safety.compliance_stx').value))
            try:
                self.arm.force_z(float(p('safety.scoop_force_n').value))
                while self._now_s() < deadline_s:
                    if job.cancel:
                        raise RuntimeError('cancelled')
                    force = self.arm.tool_force() or [0.0] * 6
                    max_force_n = max(max_force_n, abs(float(force[2])))
                    current = self.arm.current_posx()
                    if contact_z is None and self.arm.force_over(float(p('safety.fz_max_n').value)):
                        contact_z = current[2]
                    insertion_mm = 0.0 if contact_z is None else abs(float(current[2]) - float(contact_z))
                    job.feedback and job.feedback('DIP', contact_z is not None, abs(float(force[2])), insertion_mm)
                    depth_limit = float(start[2]) - float(p('safety.dip_max_mm').value)
                    if self.arm.position_at_or_below(depth_limit):
                        break
                    time.sleep(0.05)
            finally:
                self.arm.compliance_off()
        finally:
            self.arm.movel(above, self.vel_scale)
        job.feedback and job.feedback('LIFT', contact_z is not None, max_force_n, insertion_mm)
        return {'contact_detected': contact_z is not None, 'max_contact_force_n': max_force_n,
                'insertion_depth_mm': insertion_mm}

    def _do_pour(self, job: Job):
        self._require_scoop_extracted()
        p = self.get_parameter
        fraction = max(0.0, min(1.0, float(job.args['fraction'])))
        above = self.stations.get('workbench').above(self.stations.approach_mm)
        job.feedback and job.feedback('APPROACH')
        self.arm.movel(above, self.vel_scale)
        origin = self.arm.current_posx()
        axis = int(p('pour.tilt_axis').value)
        if axis not in (3, 4, 5):
            raise ValueError(f'pour.tilt_axis는 3,4,5 중 하나여야 한다: {axis}')
        target = list(origin)
        target[axis] += float(p('pour.tilt_deg').value) * fraction
        middle = list(origin)
        middle[axis] = (origin[axis] + target[axis]) / 2.0
        return_needed = False
        try:
            job.feedback and job.feedback('TILT')
            return_needed = True
            self.arm.movesx([middle, target], self.vel_scale)
            job.feedback and job.feedback('HOLD')
            self._wait_with_nudge(float(p('pour.hold_s').value), job)
            if 0.0 < fraction < 1.0:
                amp = [0.0] * 6
                amp[axis] = float(p('pour.shake_amp_deg').value)
                period = [float(p('pour.shake_period_s').value)] * 6
                self.arm.amove_periodic(amp, period, float(p('pour.shake_atime_s').value),
                                        int(p('pour.shake_repeat').value))
                self.arm.wait_motion()
            if job.cancel:
                raise RuntimeError('cancelled')
        finally:
            if return_needed:
                job.feedback and job.feedback('RETURN')
                self.arm.movesx([middle, origin], self.vel_scale)
        return True

    def _measure_weight_reading(self, tare_g: float, subject: str) -> WeightReading:
        p = self.get_parameter
        samples = int(p('scale.samples').value)
        settle_s = float(p('scale.settle_s').value)
        method = p('scale.method').value
        if method not in ('workpiece', 'tool_force'):
            raise ValueError(f'scale.method는 workpiece 또는 tool_force여야 한다: {method}')
        if bool(p('scale.simulated').value):
            raw_mean, raw_std, valid_src = 0.0, 0.0, False
        elif method == 'workpiece':
            raw_mean, raw_std, valid_src = self.arm.measure_workpiece(
                samples, settle_s, observer=self._observe_force)
        else:
            _, raw_mean, raw_std, valid_src = self.arm.measure_force(
                samples, settle_s, observer=self._observe_force)
        model = WeightModel(ScaleConfig(
            method=method,
            gain=float(p('scale.gain').value),
            offset_g=float(p('scale.offset_g').value),
            min_resolvable_g=float(p('scale.min_resolvable_g').value),
            max_std_g=float(p('scale.max_std_g').value),
            fz_sign=float(p('scale.fz_sign').value),
        ))
        model.set_tare(tare_g)
        gross_g, _, net_g, std_g, valid = model.reading(raw_mean, raw_std, valid_src)
        reading = WeightReading(
            gross_g=gross_g,
            tare_g=tare_g,
            net_g=net_g,
            std_g=std_g,
            samples=samples,
            valid=valid,
            station='workbench',
            subject=subject,
        )
        reading.header.stamp = self.get_clock().now().to_msg()
        return reading

    def _do_weigh(self, job: Job):
        self._require_scoop_extracted()
        p = self.get_parameter
        station = self.stations.get('workbench')
        pick_posx = station.extra.get('pick_posx')
        if not isinstance(pick_posx, list) or len(pick_posx) != 6:
            raise ValueError('workbench.pick_posx 6개 좌표가 필요하다')
        measure_posx = station.posx
        self.arm.movel(measure_posx, self.vel_scale)
        if not bool(p('scale.simulated').value):
            self.arm.reset_workpiece()
        self.arm.movel(pick_posx, self.vel_scale)
        job.feedback and job.feedback('GRIP')
        grip_commanded = True
        reading = WeightReading()
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
        finally:
            if grip_commanded:
                job.feedback and job.feedback('PLACE')
                self.arm.movel(pick_posx, self.vel_scale)
                self.gripper.release(3.0)
                self.arm.movel(measure_posx, self.vel_scale)
        return reading

    def _do_weigh_held(self, job: Job):
        if self._scoop_extract_uncertain:
            raise RuntimeError('스쿱 인출 상태가 불확실하다. SafePose 후 수동 확인이 필요하다')
        state = self.gripper.state(self._now_s())
        if not state['grip_inferred']:
            raise RuntimeError('스쿱 파지 상태가 아니다')

        job.feedback and job.feedback('LIFT')
        if self._pending_scoop_extract:
            target = list(self.arm.current_posx())
            if len(target) != 6:
                raise ValueError('스쿱 인출 기준 posx는 6개여야 한다')
            target[1] += self.scoop_extract_y_mm
            self._pending_scoop_extract = False
            self._scoop_extract_uncertain = True
            self.arm.movel(target, self.vel_scale)
            self._scoop_extract_uncertain = False

        self.arm.movel(self.stations.get('workbench').posx, self.vel_scale)
        self._station_id = 'workbench'
        job.feedback and job.feedback('SETTLE')
        reading = self._measure_weight_reading(float(job.args['tare_g']), 'scoop')
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

        job = self._submit('scoop', feedback, material_id=gh.request.material_id, attempt=gh.request.attempt)
        data = job.result if isinstance(job.result, dict) else {}
        res = Scoop.Result(
            success=not job.error and not job.cancel,
            contact_detected=bool(data.get('contact_detected', job.result if not data else False)),
            max_contact_force_n=float(data.get('max_contact_force_n', 0.0)),
            insertion_depth_mm=float(data.get('insertion_depth_mm', 0.0)),
            message=job.error or ('cancelled' if job.cancel else ''),
        )
        gh.succeed() if res.success else (gh.canceled() if job.cancel else gh.abort())
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
        while not self._q.empty():      # 대기 중인 Job 은 전부 버린다
            j = self._q.get_nowait()
            j.error, _ = 'cancelled by safe_pose', j.done.set()
        with self._job_lock:
            if self._current:
                self._current.cancel = True
        job = self._submit('safe', reason=req.reason)
        res.success, res.message = not job.error, job.error
        self.event('WARN', 'SAFE_POSE', req.reason)
        return res


def main(args=None):
    rclpy.init(args=args)
    node = SkillNode()
    ex = MultiThreadedExecutor(num_threads=4)
    ex.add_node(node)            # DR_init 노드(node.arm.node)는 넣지 않는다 — D-02
    try:
        ex.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        node.arm.node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
