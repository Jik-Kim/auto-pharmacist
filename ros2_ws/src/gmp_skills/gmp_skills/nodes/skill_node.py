"""로봇 스킬 서버. 로봇을 만지는 유일한 노드 (AGENTS.md 프로젝트 구조).

스레드 구조 (SOT D-02 · 교육 「두산 ROS2 동작 Sequence」 3장)
  Main   : MultiThreadedExecutor.spin(이 노드, ns cell) — Action/Service 콜백, gripper_state 10 Hz
  Worker : 큐에서 Job 을 꺼내 DsrArm/Rg2Gripper 를 블로킹 호출. **로봇 명령은 항상 직렬**
  DR_init 노드(ns dsr01) : DsrArm 이 소유, executor 에 넣지 않는다

입력  Action move_to_station·scoop·pour·weigh_container / Service set_gripper·measure_force·safe_pose
      /onrobot_joint_states (real) 또는 gripper_joint_states (virtual)
출력  gripper_state (10 Hz, BEST_EFFORT) · event

파라미터는 gmp_bringup/params/common.yaml 이 단일 출처. stations.yaml 경로는 파라미터 `stations_file`.

TODO([A]): Scoop/Pour/WeighContainer 본문 (9/18). 지금은 MoveToStation·SetGripper·MeasureForce·SafePose 골격만.
TODO([A]): I-004 취소 — 워커가 Job.cancel 플래그를 movel 사이에서만 본다. 긴 movel 은 쪼갠다.
"""
import queue
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
from gmp_interfaces.action import MoveToStation, Scoop, Pour, WeighContainer
from gmp_interfaces.msg import CellEvent, GripperState, WeightReading
from gmp_interfaces.srv import MeasureForce, SafePose, SetGripper

from gmp_skills.adapters.dsr_arm import DsrArm
from gmp_skills.adapters.rg2_gripper import Rg2Gripper
from gmp_skills.core.stations import StationTable


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
        super().__init__('skill_node')
        p = self.declare_parameters('', [
            ('mode', 'virtual'), ('robot.id', 'dsr01'), ('robot.model', 'm0609'),
            ('robot.tool_name', 'tool_weight'), ('robot.tcp_name', 'GripperDA_v1'),
            ('robot.vel', 60.0), ('robot.acc', 60.0), ('robot.vel_scale', 0.3),
            ('gripper.backend', 'modbus'), ('gripper.open_width_mm', 100.0), ('gripper.grip_margin_mm', 2.0),
            ('gripper.slip_mm', 1.5), ('gripper.dio_pins', [1, 2]), ('gripper.din_pins', [0]),
            ('scale.method', 'workpiece'), ('scale.samples', 20), ('scale.settle_s', 1.0), ('scale.simulated', False),
            ('safety.fz_max_n', 15.0), ('stations_file', ''),
        ])
        g = lambda k: self.get_parameter(k).value  # noqa: E731
        self.mode = g('mode')
        self.vel_scale = float(g('robot.vel_scale'))
        self.stations = StationTable.from_yaml(g('stations_file'))
        self.arm = DsrArm(g('robot.id'), g('robot.model'), self.mode, float(g('robot.vel')), float(g('robot.acc')),
                          g('robot.tool_name'), g('robot.tcp_name'), self.get_logger())

        backend = 'virtual' if self.mode == 'virtual' else g('gripper.backend')
        self._grip_cli = self.create_client(SetCommand, '/onrobot/sendCommand')
        self.gripper = Rg2Gripper(backend, self._send_gripper_command, self.arm,
                                  float(g('gripper.grip_margin_mm')), float(g('gripper.slip_mm')),
                                  float(g('gripper.open_width_mm')), tuple(g('gripper.dio_pins')),
                                  tuple(g('gripper.din_pins')), self.get_logger())
        js_topic = '/onrobot_joint_states' if backend == 'modbus' else f"/{g('robot.id')}/gripper_joint_states"
        self.create_subscription(JointState, js_topic, self._on_js,
                                 QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))

        self.cb = ReentrantCallbackGroup()
        self.pub_state = self.create_publisher(GripperState, 'gripper_state',
                                               QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.pub_event = self.create_publisher(CellEvent, 'event', 100)
        self.create_timer(0.1, self._pub_gripper_state, callback_group=self.cb)

        ActionServer(self, MoveToStation, 'move_to_station', self._exec_move, callback_group=self.cb,
                     goal_callback=lambda _: GoalResponse.ACCEPT, cancel_callback=self._on_cancel)
        ActionServer(self, Scoop, 'scoop', self._exec_scoop, callback_group=self.cb)
        ActionServer(self, Pour, 'pour', self._exec_pour, callback_group=self.cb)
        ActionServer(self, WeighContainer, 'weigh_container', self._exec_weigh, callback_group=self.cb)
        self.create_service(SetGripper, 'set_gripper', self._srv_set_gripper, callback_group=self.cb)
        self.create_service(MeasureForce, 'measure_force', self._srv_measure, callback_group=self.cb)
        self.create_service(SafePose, 'safe_pose', self._srv_safe, callback_group=self.cb)

        self._q: 'queue.Queue[Job]' = queue.Queue()
        self._current: Job | None = None
        threading.Thread(target=self._worker, daemon=True, name='dsr-worker').start()

        ok, msg = self.arm.self_check(g('robot.tool_name'), g('robot.tcp_name'))
        self.event('INFO' if ok else 'ERROR', 'SELF_CHECK', f'{"OK" if ok else "FAIL"} {msg}')
        if not ok:
            raise RuntimeError(f'자가진단 실패 — 기동 거부: {msg}')

    # ── 공용 ────────────────────────────────────────────────────────────
    def event(self, level: str, code: str, text: str, batch_id: str = ''):
        m = CellEvent(level=getattr(CellEvent, level), code=code, text=text, batch_id=batch_id)
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_event.publish(m)
        (self.get_logger().error if level == 'ERROR' else self.get_logger().info)(f'[{code}] {text}')

    def _send_gripper_command(self, cmd: str) -> bool:
        if not self._grip_cli.wait_for_service(timeout_sec=2.0):
            self.event('ERROR', 'GRIPPER_SVC', '/onrobot/sendCommand 없음')
            return False
        fut = self._grip_cli.call_async(SetCommand.Request(command=cmd))
        t0 = time.time()
        while not fut.done() and time.time() - t0 < 3.0:
            time.sleep(0.01)          # 워커 스레드에서 호출되므로 spin 하지 않는다 — executor 가 돌린다
        return bool(fut.done() and fut.result().success)

    def _on_js(self, msg: JointState):
        for n, pos in zip(msg.name, msg.position):
            if n.endswith('finger_joint'):
                self.gripper.on_joint_state(pos, time.time())
                break

    def _pub_gripper_state(self):
        w = self.gripper.width_mm()
        m = GripperState(width_mm=-1.0 if w is None else w, busy=False, grip_inferred=False,
                         safety_triggered=False, force_cmd_n=self.gripper.force_cmd_n, backend=self.gripper.backend)
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_state.publish(m)

    # ── 워커: 로봇 명령은 여기서만 ──────────────────────────────────────
    def _submit(self, kind: str, feedback=None, **args) -> Job:
        job = Job(kind, args, feedback=feedback)
        self._q.put(job)
        job.done.wait()
        return job

    def _worker(self):
        while rclpy.ok():
            job = self._q.get()
            self._current = job
            try:
                job.result = getattr(self, f'_do_{job.kind}')(job)
            except Exception as e:  # noqa: BLE001 — 로봇 에러는 전부 결과로 돌려준다
                job.error = f'{type(e).__name__}: {e}'
                self.event('ERROR', f'{job.kind.upper()}_FAIL', job.error)
            finally:
                self._current = None
                job.done.set()

    def _do_move(self, job: Job):
        st = self.stations.get(job.args['station_id'])
        target = st.above(self.stations.approach_mm) if job.args['approach'] == MoveToStation.Goal.ABOVE else st.posx
        job.feedback and job.feedback('MOVING')
        self.arm.movel(target, job.args.get('vel_scale') or self.vel_scale)
        return st.station_id

    def _do_grip(self, job: Job):
        a = job.args
        if a['close']:
            return self.gripper.grip(a['width_mm'], a['force_n'], a['timeout_s'] or 3.0)
        return self.gripper.release(a['timeout_s'] or 3.0), self.gripper.width_mm() or -1.0, False

    def _do_measure(self, job: Job):
        if self.get_parameter('scale.simulated').value:
            return [0.0] * 6, 0.0, 0.0, False, 'simulated'
        mean6, fz, std, valid = self.arm.measure_force(job.args['samples'], job.args['settle_s'])
        return mean6, fz, std, valid, ''

    def _do_safe(self, job: Job):
        try:
            self.arm.compliance_off()
        except Exception:  # noqa: BLE001 — 힘제어 중이 아니었으면 무시
            pass
        self.arm.movel(self.stations.get('safe').posx, 0.3)
        return True

    def _do_scoop(self, job: Job):
        raise NotImplementedError('TODO([A]) 9/18: 접근 → compliance_on → force_z → force_over 접촉 → 깊이 상한 → 들어올림 → finally compliance_off')

    def _do_pour(self, job: Job):
        raise NotImplementedError('TODO([A]) 9/18: 접근 → 기울임 movel/movesx → fraction<1 이면 amove_periodic 털어내기 → 복귀')

    def _do_weigh(self, job: Job):
        raise NotImplementedError('TODO([A]) 9/18: grip → 계량 자세 → measure_workpiece/measure_force → place')

    # ── 콜백: 큐에 넣고 기다린다 ────────────────────────────────────────
    def _on_cancel(self, _goal):
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
        res = MoveToStation.Result(success=not job.error, message=job.error, reached=job.result or '')
        gh.succeed() if res.success else gh.abort()
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
            success=not job.error,
            contact_detected=bool(data.get('contact_detected', job.result if not data else False)),
            max_contact_force_n=float(data.get('max_contact_force_n', 0.0)),
            insertion_depth_mm=float(data.get('insertion_depth_mm', 0.0)),
            message=job.error,
        )
        gh.succeed() if res.success else gh.abort()
        return res

    def _exec_pour(self, gh):
        job = self._submit('pour', fraction=gh.request.fraction)
        res = Pour.Result(success=not job.error, message=job.error)
        gh.succeed() if res.success else gh.abort()
        return res

    def _exec_weigh(self, gh):
        job = self._submit('weigh', tare_g=gh.request.tare_g)
        res = WeighContainer.Result(success=not job.error, message=job.error, reading=job.result or WeightReading())
        gh.succeed() if res.success else gh.abort()
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
