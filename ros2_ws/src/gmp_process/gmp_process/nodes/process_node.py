"""공정 노드 — ProcessFSM 의 요청을 스킬 Action/Service 로 실행하고 결과를 돌려준다.

입력  submit_order · qa_decision · interlock (Service 서버) · event 구독 (skill_node 의 NUDGE, D-21)
      스킬 Action 클라이언트 move_to_station·scoop·pour·weigh_container·weigh_held
      스킬 Service 클라이언트 set_gripper·measure_force·safe_pose
출력  state (0.5 s + 전이, TRANSIENT_LOCAL) · weight · scoop_cycle · dispense_result · deviation (TRANSIENT_LOCAL) · event

구조 (docs/process_flow.md 0절): rclpy 콜백은 값만 저장한다. 배치 실행 루프는 별도 스레드(_run_loop)에서
돌며 스킬을 **한 번에 하나** 부른다. QA 판정·인터락·NUDGE 는 콜백이 값만 세우고 루프가 기다린다.

FSM 은 로봇도 ROS 도 모른다 — dict 요청을 주고 dict 결과를 받는다. 이 파일이 그 dict 를 계약 메시지로
옮기는 유일한 지점이다. 발행은 전이 뒤 `_drain()` 한 곳에서 FSM 상태 변화를 보고 판단한다.
"""
import threading
import time
from datetime import datetime

import rclpy
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile

from gmp_interfaces.action import MoveToStation, Pour, Scoop, WeighContainer, WeighHeld
from gmp_interfaces.msg import (CellEvent, CellState, Deviation, DispenseResult, ScoopCycle,
                                WeightReading)
from gmp_interfaces.srv import (InterlockRequest, MeasureForce, QaDecision, SafePose, SetGripper,
                                SubmitOrder)

from gmp_dosing.core.dosing import DosingConfig
from gmp_dosing.core.scale import ScaleConfig, WeightModel
from gmp_process.core.attempt import Attempt, Reading
from gmp_process.core.process_fsm import ProcessFSM
from gmp_process.core.recipe import parse as parse_recipe
from gmp_process.core.station_map import StationMap

LATCHED = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
DEV_QOS = QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL)

# 사람을 기다리는 요청 — 로봇을 움직이지 않으므로 정지 게이트를 앞에 두지 않는다 (아래 _run_loop 참고)
HUMAN_WAITS = ('wait_qa', 'wait_interlock')
# 게이트를 건너뛰는 요청 — 사람 대기 + 안전 자세. `safe` 는 "사람이 곧 들어오니 물러나라" 는 이동이라
# NUDGE·인터락 정지보다 우선한다 (ENTER 가 NUDGE 정지 중에도 safe_pose 를 부르는 것과 같은 논리)
GATE_BYPASS = HUMAN_WAITS + ('safe',)

# 일탈 → ScoopCycle.outcome. 시도가 실패로 끝난 것만 여기 있다 (OVERFILL·TIMEOUT 은 시도 자체는 끝났다)
DEV_TO_OUTCOME = {'SCOOP_EMPTY': 'SCOOP_EMPTY', 'MATERIAL_EMPTY': 'SCOOP_EMPTY',
                  'WEIGH_INVALID': 'WEIGH_INVALID'}


class SkillError(RuntimeError):
    """스킬을 부를 수 없거나(서버 없음·시간 초과) 서버가 abort 한 것. 배치를 ERROR 로 끝낸다."""


class ProcessNode(Node):
    def __init__(self, **kwargs):
        # kwargs 는 시험에서 parameter_overrides 를 넣기 위한 통로다 (test/test_process_node.py)
        super().__init__('process_node', **kwargs)
        self.declare_parameters('', [
            ('stations_file', ''),
            ('robot.tool_name', 'tool_weight'), ('robot.tcp_name', 'GripperDA_v1'),
            ('robot.vel_scale', 0.0),           # 0 이면 skill_node 의 robot.vel_scale
            ('scale.method', 'workpiece'), ('scale.gain', 1.0), ('scale.offset_g', 0.0),
            ('scale.min_resolvable_g', 30.0), ('scale.max_std_g', 10.0),
            ('scale.samples', 20), ('scale.settle_s', 1.0),
            ('dosing.max_attempts', 3), ('dosing.scoop_nominal_g', 40.0), ('dosing.min_fraction', 0.15),
            ('gripper.scoop_width_mm', 18.0), ('gripper.cup_width_mm', 60.0),
            ('gripper.open_width_mm', 100.0), ('gripper.force_n', 20.0),
            ('skill_timeout_s', 90.0), ('server_wait_s', 20.0), ('grip_timeout_s', 5.0),
            ('safety.nudge_enabled', True),   # skill_node 와 같은 스위치 — 끄면 NUDGE 를 무시한다
        ])
        p = lambda k: self.get_parameter(k).value  # noqa: E731
        self.p = p
        self.scale = WeightModel(ScaleConfig(p('scale.method'), p('scale.gain'), p('scale.offset_g'),
                                             p('scale.min_resolvable_g'), p('scale.max_std_g')))
        self.dosing_cfg = DosingConfig(p('dosing.max_attempts'), p('dosing.scoop_nominal_g'),
                                       p('dosing.min_fraction'))
        # 스테이션 이름표 — 없으면 원료 → scoop_N 을 못 찾는다. 경로가 비면 주문 때 거부한다
        self.smap = StationMap.from_yaml(p('stations_file')) if p('stations_file') else StationMap()

        self.pub_state = self.create_publisher(CellState, 'state', LATCHED)
        self.pub_weight = self.create_publisher(WeightReading, 'weight', 20)
        self.pub_cycle = self.create_publisher(ScoopCycle, 'scoop_cycle', 50)
        self.pub_result = self.create_publisher(DispenseResult, 'dispense_result', 50)
        self.pub_dev = self.create_publisher(Deviation, 'deviation', DEV_QOS)
        self.pub_event = self.create_publisher(CellEvent, 'event', 100)

        self.cb = ReentrantCallbackGroup()
        self.create_service(SubmitOrder, 'submit_order', self._srv_submit, callback_group=self.cb)
        self.create_service(QaDecision, 'qa_decision', self._srv_qa, callback_group=self.cb)
        self.create_service(InterlockRequest, 'interlock', self._srv_interlock, callback_group=self.cb)
        self.create_timer(0.5, self._pub_state, callback_group=self.cb)
        # skill_node 가 사람 접촉을 여기로 알린다 (D-21). 우리가 내는 event 도 같이 들어오므로 code 로 거른다
        self.create_subscription(CellEvent, 'event', self._on_event, 100, callback_group=self.cb)

        self.act = {
            'move': ActionClient(self, MoveToStation, 'move_to_station', callback_group=self.cb),
            'scoop': ActionClient(self, Scoop, 'scoop', callback_group=self.cb),
            'pour': ActionClient(self, Pour, 'pour', callback_group=self.cb),
            'weigh': ActionClient(self, WeighContainer, 'weigh_container', callback_group=self.cb),
            'weigh_scoop': ActionClient(self, WeighHeld, 'weigh_held', callback_group=self.cb),
        }
        self.srv = {
            'grip': self.create_client(SetGripper, 'set_gripper', callback_group=self.cb),
            'measure': self.create_client(MeasureForce, 'measure_force', callback_group=self.cb),
            'safe': self.create_client(SafePose, 'safe_pose', callback_group=self.cb),
        }

        self.fsm: ProcessFSM | None = None
        self.batch_id = ''
        self.station = ''            # 마지막으로 도착한 스테이션 — CellState.station
        self.note = ''
        self._seq = 0                # 배치 일련번호 (B-YYYYMMDD-NNN)
        self._qa = threading.Event(); self._qa_decision = None; self._qa_operator = ''
        self._interlock_exit = threading.Event()
        self._pause = False          # 인터락 ENTER 가 세운다. **루프만 내린다** — EXIT 핸들러가 내리면
                                     # 취소된 스킬이 돌아오기 전에 풀려 그 실패가 진짜 실패로 읽힌다
        self._nudge_paused = False   # 사람 접촉으로 멈춤 (D-21). 다음 NUDGE 가 내린다
        self._refill_waiting = False # 루프가 REFILL 로 EXIT 를 기다리는 중 — EXIT 를 받을지 가른다
        self._nudge_lock = threading.Lock()   # 토글은 읽고-쓰기라 콜백 둘이 겹치면 뒤집히지 않는다
        self._stop = threading.Event()   # 종료 요청 — 무한 대기(QA·인터락)를 깨운다
        self._thread = None
        self._dev_msgs: list = []    # 발행한 Deviation — QA 판정 후 같은 deviation_id 로 재발행한다
        self._published_devs = 0
        self._published_results = 0
        self._attempt: Attempt | None = None
        self._scoop_tare: Reading | None = None   # 원료마다 1회 (D-22) — 시도마다 다시 쓴다
        self._grip_width = 0.0                    # 마지막 SetGripper 정지 폭 (ScoopCycle 용)
        self._try_no: dict = {}                   # 원료 → ScoopCycle 시도 번호 (빈 스쿱 재시도도 센다)
        self._item_t0 = 0.0

    # ── 공용 ─────────────────────────────────────────────────────────
    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def event(self, level: str, code: str, text: str = ''):
        m = CellEvent(level=getattr(CellEvent, level), code=code, text=text, batch_id=self.batch_id)
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_event.publish(m)
        self.get_logger().info(f'[{code}] {text}')

    def _on_event(self, msg):
        """skill_node 의 NUDGE — 건드리면 정지, 다시 건드리면 재개 (D-21).

        토글만 한다. 멈추는 것은 루프의 게이트다 — 콜백에서 멈추면 rclpy 스레드가 잠긴다.
        같은 접촉을 두 번 세지 않는 것은 skill_node 의 `nudge_cooldown_s` 가 한다.
        """
        if msg.code != 'NUDGE' or not self.get_parameter('safety.nudge_enabled').value:
            return
        with self._nudge_lock:
            self._nudge_paused = not self._nudge_paused
            now = self._nudge_paused
        self.get_logger().info(f"[NUDGE] {'정지' if now else '재개'}")

    def _pause_reason(self) -> str:
        """지금 멈춰 있어야 하는 이유. 둘 다면 NUDGE 를 먼저 보여 준다 (사람이 방금 한 행동이므로)."""
        if self._nudge_paused:
            return 'NUDGE'
        if self._pause:
            return 'INTERLOCK'
        return ''

    def _gate(self, detail: str = ''):
        """요청 **사이**에서만 멈춘다 (D-21 · process_flow 6절).

        스킬 중간에는 끊지 않는다 — 블로킹 `movel` 은 취소가 안 되고(I-004), 애초에 그 구간에서는
        skill_node 가 NUDGE 를 감지하지도 않는다. 감지되는 자리(유휴·계량 settle·붓기 대기)에서
        울린 NUDGE 는 늦어도 다음 요청 전에 여기서 잡힌다. FSM 은 이 정지를 모른다.

        폴링으로 기다린다 — 깨우는 신호가 둘(두 번째 NUDGE, 인터락 EXIT)이라 Event 하나로는
        「스킬 도는 동안 정지·재개가 다 지나간」 경우에 신호가 남아 다음 정지를 즉시 풀어 버린다.
        """
        reason = self._pause_reason()
        if not reason:
            return
        # **판정 대기 중인 일탈이 있으면 mode 를 건드리지 않는다** — _srv_qa 는 mode==DEVIATION 일 때만
        # 받으므로 PAUSED 로 덮으면 QA 가 영영 거부되고 루프는 QA 만 기다리는 교착이 된다 (PR #18 리뷰 1번).
        # 판정이 들어온 뒤(대기 없음)라면 DEVIATION 이어도 PAUSED 로 올린다 — 그래야 사람이 멈춘 걸 본다
        took_mode = bool(self.fsm and self.fsm.mode != 'PAUSED' and self._pending_dev() is None)
        if took_mode:
            self.fsm.mode = 'PAUSED'
        self.note = f'{reason} 정지 — ' + ('다시 건드리면 재개' if reason == 'NUDGE' else 'EXIT 로 재개')
        if detail:
            self.note += f' ({detail})'
        self._pub_state()
        self.event('WARN', 'PAUSE', self.note)
        while self._pause_reason():
            if self._pause and self._interlock_exit.is_set():
                # EXIT 를 여기서 소비한다 — _pause 를 내리는 곳은 언제나 루프다 (핸들러가 내리면
                # 취소된 스킬이 돌아오기 전에 풀려 그 실패가 진짜 실패로 읽힌다)
                self._interlock_exit.clear()
                self._pause = False
                continue
            if self._stop.is_set() or not rclpy.ok():
                raise SkillError('정지 대기 중 종료')
            time.sleep(0.1)
        if took_mode and self.fsm and self.fsm.mode == 'PAUSED':
            self.fsm.mode = 'RUNNING'
        self.note = ''
        self._pub_state()
        self.event('INFO', 'RESUME', f'{reason} 해제')

    def _await(self, ev: threading.Event, what: str):
        """사람을 기다리는 대기(QA·인터락)는 상한이 없다 (D-23 반자동). 종료 요청만이 깨운다."""
        while not ev.wait(0.2):
            if self._stop.is_set() or not rclpy.ok():
                raise SkillError(f'{what} 대기 중 종료')
        return True

    def _wait(self, fut, timeout: float, what: str):
        """rclpy Future 를 루프 스레드에서 기다린다. spin 은 executor 가 한다 (콜백 그룹 Reentrant)."""
        ev = threading.Event()
        fut.add_done_callback(lambda _f: ev.set())
        if not ev.wait(timeout):
            raise SkillError(f'{what} 응답 없음 ({timeout:.0f}s)')
        return fut.result()

    # ── 서비스 ───────────────────────────────────────────────────────
    def _srv_submit(self, req, res):
        if self.fsm and self.fsm.mode in ('RUNNING', 'PAUSED', 'DEVIATION'):
            res.accepted, res.message = False, f'실행 중 ({self.fsm.state})'
            return res
        # 검증은 core/recipe.parse 단일 출처 — 필수 필드·중복 원료·양수·유한값. HMI 가 yaml 을 읽을 때와 같은 규칙이다.
        # target_g=0 이 통과하면 verdict_of 의 나눗셈에서 죽고, tol 이 NaN 이면 판정이 늘 실패한다
        try:
            spec = parse_recipe({'product': req.recipe.product,
                                 'items': [{'material_id': i.material_id, 'target_g': i.target_g, 'tol_pct': i.tol_pct}
                                           for i in req.recipe.items]})
            self.smap.check([i.material_id for i in spec.items])   # 배치 중간에 서는 것보다 주문 거부가 낫다
        except (ValueError, KeyError) as e:
            res.accepted, res.message = False, str(e).strip("'")
            return res
        self._seq += 1
        self.batch_id = req.recipe.batch_id or f'B-{datetime.now():%Y%m%d}-{self._seq:03d}'
        self._reset_batch()
        self.fsm = ProcessFSM(spec, self.dosing_cfg, self.scale)
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name='process-run')
        self._thread.start()
        res.accepted, res.batch_id, res.message = True, self.batch_id, 'accepted'
        return res

    def _reset_batch(self):
        self.scale.tare_g = 0.0
        self._dev_msgs = []
        self._published_devs = self._published_results = 0
        self._attempt = self._scoop_tare = None
        self._try_no = {}
        self._qa.clear(); self._qa_decision = None; self._qa_operator = ''
        self._interlock_exit.clear(); self._pause = False   # 지난 배치의 EXIT 가 새 배치로 새지 않게
        self._nudge_paused = False
        self._refill_waiting = False

    def _pending_dev(self) -> Deviation | None:
        for m in reversed(self._dev_msgs):
            if m.requires_decision and m.decision == Deviation.PENDING:
                return m
        return None

    def _srv_qa(self, req, res):
        if not (self.fsm and self.fsm.mode == 'DEVIATION'):
            res.accepted, res.message = False, '판정 대기 중인 일탈 없음'
            return res
        pend = self._pending_dev()
        if pend is None:
            res.accepted, res.message = False, '판정 대기 중인 일탈 없음'
            return res
        if req.deviation_id and req.deviation_id != pend.deviation_id:
            res.accepted, res.message = False, f'대기 중인 일탈은 {pend.deviation_id} 이다'
            return res
        if req.decision not in (Deviation.APPROVED, Deviation.DISCARDED):
            # 폐기는 되돌릴 수 없다 — 승인·폐기 외의 값(0 PENDING, 3 AUTO_RECOVERED, 오타)은 아무것도 하지 않는다
            res.accepted, res.message = False, f'판정값 {req.decision} 은 APPROVED(1)·DISCARDED(2) 가 아니다'
            return res
        self._qa_decision = 'APPROVED' if req.decision == Deviation.APPROVED else 'DISCARDED'
        pend.decision = req.decision
        pend.operator_id = req.operator_id
        self.pub_dev.publish(pend)                 # 같은 deviation_id 로 재발행 → record 가 upsert
        self._qa_operator = req.operator_id
        self._qa.set()
        res.accepted, res.message = True, f'{pend.deviation_id} {self._qa_decision}'
        return res

    def _srv_interlock(self, req, res):
        if req.request == InterlockRequest.Request.ENTER:
            if self._pause or (self.fsm and self.fsm.mode == 'PAUSED' and not self._nudge_paused):
                # 이미 **안전 자세로 가서** 기다리는 중 (REFILL 대기 또는 앞선 ENTER). safe_pose 를 다시 부르거나
                # _interlock_exit 를 다시 지우면 EXIT 를 두 번 눌러야 풀린다 — 멱등하게 받는다.
                # NUDGE 정지는 PAUSED 지만 **그 자리에 선 것**이라 안전 자세가 아니다 → 여기 걸리면 안 된다
                res.granted, res.message = True, '이미 대기 중 (안전 자세)'
                return res
            # 진행 중인 스킬을 취소하는 것은 skill_node 다 (SafePose 계약: 대기 Job 은 버리고 진행 Job 에 cancel).
            # 그래서 그 스킬은 success=false 로 돌아오고, 루프가 _pause 를 보고 실패가 아니라 취소로 읽는다.
            self._pause = True
            self._interlock_exit.clear()
            try:
                out = self._call_srv('safe', SafePose.Request(reason=req.reason or 'INTERLOCK'))
                res.granted = bool(out.success)
                res.message = out.message or 'safe pose'
            except SkillError as e:
                self._pause = False
                res.granted, res.message = False, str(e)
                return res
            if not res.granted:
                self._pause = False
                return res
            # 여기서 바로 PAUSED 로 올린다 — 루프가 취소된 스킬을 받아 PAUSED 를 세우기까지의 틈에
            # EXIT 가 들어오면 아래 게이트에 걸려 무시되고, 그러면 영영 안 깨어난다.
            # 단, QA 대기(DEVIATION) 중이면 덮지 않는다 — _srv_qa 가 mode==DEVIATION 만 받으므로 덮으면
            # QA 가 영영 거부되고 루프는 QA 만 기다리는 교착이 된다. 사람은 note 로 알리고, QA 판정이
            # 오면 _execute 가 _pause 를 보고 EXIT 까지 멈춘다.
            if self.fsm and self.fsm.mode == 'RUNNING':
                self.fsm.mode = 'PAUSED'
            self.note = f'인터락 ENTER ({req.reason or "-"})' + (' — QA 판정 대기 중' if self.fsm and self.fsm.mode == 'DEVIATION' else '')
            self._pub_state()
            self.event('WARN', 'INTERLOCK_ENTER', req.reason)
            return res
        if not (self._pause or self._refill_waiting):
            # 아무도 안 기다리는데 set 하면 다음 REFILL 대기가 즉시 풀린다 — 보충 없이 재개되는 셈.
            # mode==PAUSED 로 가르면 안 된다 — NUDGE 정지도 PAUSED 라서 그때 눌린 EXIT 가 신호로 남는다
            res.granted, res.message = True, '대기 중이 아니다 (무시)'
            return res
        self._interlock_exit.set()
        self.event('INFO', 'INTERLOCK_EXIT', req.reason)
        res.granted, res.message = True, 'resume'
        return res

    # ── 스킬 호출 ─────────────────────────────────────────────────────
    def _call_srv(self, key: str, request):
        cli = self.srv[key]
        if not cli.wait_for_service(timeout_sec=float(self.p('server_wait_s'))):
            raise SkillError(f'{key} 서비스 없음')
        return self._wait(cli.call_async(request), float(self.p('skill_timeout_s')), key)

    def _call_act(self, key: str, goal):
        cli = self.act[key]
        if not cli.wait_for_server(timeout_sec=float(self.p('server_wait_s'))):
            raise SkillError(f'{key} 액션 서버 없음')
        gh = self._wait(cli.send_goal_async(goal), float(self.p('server_wait_s')), f'{key} goal')
        if not gh.accepted:
            raise SkillError(f'{key} 목표 거부')
        return self._wait(gh.get_result_async(), float(self.p('skill_timeout_s')), key).result

    def _check(self, key: str, ok: bool, message: str):
        if not ok:
            raise SkillError(f'{key} 실패: {message}')

    def _move(self, station_id: str, approach: int):
        g = MoveToStation.Goal(station_id=station_id, approach=approach,
                               vel_scale=float(self.p('robot.vel_scale')))
        r = self._call_act('move', g)
        self._check('move_to_station', r.success, r.message)
        self.station = r.reached or station_id

    def _grip(self, close: bool, width_mm: float) -> dict:
        r = self._call_srv('grip', SetGripper.Request(close=close, width_mm=float(width_mm),
                                                      force_n=float(self.p('gripper.force_n')),
                                                      timeout_s=float(self.p('grip_timeout_s'))))
        self._check('set_gripper', r.success, r.message)
        return {'grip_inferred': bool(r.grip_inferred), 'final_width_mm': float(r.final_width_mm)}

    def _reading(self, msg: WeightReading, subject: str) -> dict:
        """WeightReading → FSM 이 읽는 dict. 같은 값을 weight 토픽으로도 낸다."""
        m = WeightReading(gross_g=msg.gross_g, tare_g=msg.tare_g, net_g=msg.net_g, std_g=msg.std_g,
                          samples=msg.samples, valid=msg.valid, station=msg.station or 'workbench',
                          subject=subject)   # subject 는 어느 액션을 불렀는지 아는 이쪽이 채운다 (I-007 c)
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_weight.publish(m)
        return {'gross_g': float(m.gross_g), 'tare_g': float(m.tare_g), 'net_g': float(m.net_g),
                'std_g': float(m.std_g), 'samples': int(m.samples), 'valid': bool(m.valid)}

    def _station_of(self, req: dict) -> str:
        s = req.get('station', '')
        return self.smap.scoop_of(req['material_id']) if s == 'scoop' else s

    # ── 요청 실행 ─────────────────────────────────────────────────────
    def _execute(self, req: dict) -> dict:
        """정지가 걸려 있으면 풀릴 때까지 멈춘다 (게이트 하나로 NUDGE·인터락을 같이 본다).

        인터락이 스킬을 끊어 실패했으면 **같은 요청을 처음부터 다시** 부른다 — 부분 실행은 버린다
        (`carry` 중간이었다면 접근점부터). 취소가 안 걸리고 그냥 끝났으면 **결과를 FSM 에 넘긴 뒤** 다음
        로봇 동작 요청 앞(_run_loop 의 게이트)에서 멈춘다 — 여기서 먼저 멈추면 FSM 이 `safe` 를 내야 하는
        상황(원료 소진·강제 개입)에서도 결정을 못 하고 서 버린다.
        """
        while True:
            try:
                res = self._dispatch(req)
            except SkillError as e:
                if not self._pause:
                    raise                          # 진짜 실패 — 루프가 FORCE_LIMIT 으로 보낸다
                self._gate(f'스킬 중단: {e}')
                continue                           # 같은 요청을 다시
            return res                             # 정지는 다음 요청 앞의 게이트가 잡는다

    def _dispatch(self, req: dict) -> dict:
        k = req['kind']
        if k == 'wait_qa':
            # 기다리기 **전에** clear 하면, _deviate 가 DEVIATION 을 세운 직후 들어온 판정을 지운다.
            # 판정은 _qa_decision 에 남으므로 소비한 뒤에 비운다.
            self._await(self._qa, 'QA 판정')
            decision, self._qa_decision = self._qa_decision, None
            self._qa.clear()
            return {'decision': decision, 'operator_id': self._qa_operator}
        if k == 'wait_interlock':
            # FSM 이 REFILL 로 세운 대기 (mode 는 이미 PAUSED). 그 사이 ENTER 가 또 왔어도(_pause) EXIT 한 번이면
            # 사람이 나온 것이므로 _pause 도 여기서 같이 내린다 — 안 그러면 뒤의 _gate 가 EXIT 를 한 번 더 요구한다
            self._refill_waiting = True
            try:
                self._await(self._interlock_exit, '인터락 EXIT')
            finally:
                self._refill_waiting = False
            self._interlock_exit.clear()
            self._pause = False
            return {}
        if k == 'measure':
            r = self._call_srv('measure', MeasureForce.Request(samples=int(self.p('scale.samples')),
                                                               settle_s=float(self.p('scale.settle_s'))))
            return {'valid': bool(r.valid), 'fz_mean_n': float(r.fz_mean_n), 'fz_std_n': float(r.fz_std_n)}
        if k == 'safe':
            r = self._call_srv('safe', SafePose.Request(reason=req.get('reason', '')))
            return {'success': bool(r.success)}
        if k == 'move':
            self._move(self._station_of(req),
                       MoveToStation.Goal.AT if req.get('approach') == 'AT' else MoveToStation.Goal.ABOVE)
            return {'success': True, 'reached': self.station}
        if k == 'grip':
            width = (self.p('gripper.scoop_width_mm') if req.get('target') == 'scoop'
                     else self.p('gripper.cup_width_mm')) if req.get('close') else self.p('gripper.open_width_mm')
            return self._grip(bool(req.get('close')), width)
        if k == 'carry':
            return self._carry(req)
        if k == 'scoop':
            r = self._call_act('scoop', Scoop.Goal(material_id=req['material_id'],
                                                   attempt=min(255, int(req.get('attempt', 1)))))
            self._check('scoop', r.success, r.message)
            return {'contact_detected': bool(r.contact_detected),
                    'max_contact_force_n': float(r.max_contact_force_n),
                    'insertion_depth_mm': float(r.insertion_depth_mm)}
        if k == 'pour':
            r = self._call_act('pour', Pour.Goal(fraction=float(req.get('fraction', 1.0))))
            self._check('pour', r.success, r.message)
            return {'success': True}
        if k == 'weigh':
            r = self._call_act('weigh', WeighContainer.Goal(tare_g=float(req.get('tare_g', 0.0))))
            self._check('weigh_container', r.success, r.message)
            return self._reading(r.reading, 'container')
        if k == 'weigh_scoop':
            r = self._call_act('weigh_scoop', WeighHeld.Goal(tare_g=float(req.get('tare_g', 0.0))))
            self._check('weigh_held', r.success, r.message)
            return self._reading(r.reading, 'scoop')
        raise SkillError(f'모르는 요청 {k!r} — FSM 과 노드가 어긋났다')

    def _carry(self, req: dict) -> dict:
        """용기 반송 (D-18). 파지에 실패하면 목적지로 가지 않고 그리퍼를 열고 돌아온다 — FSM 이 재시도한다.

        슬롯: Pass Box 는 칸마다 1개(stations.yaml slots: 1)라 slot 오프셋을 쓰지 않는다.
        여러 칸을 쓰게 되면 MoveToStation 에 슬롯 인자를 넣는 계약 변경이 먼저다.
        """
        src, dst = req['src'], req['dst']
        cup = float(self.p('gripper.cup_width_mm'))
        self._move(src, MoveToStation.Goal.ABOVE)
        self._move(src, MoveToStation.Goal.AT)
        g = self._grip(True, cup)
        self._move(src, MoveToStation.Goal.ABOVE)
        if not g['grip_inferred']:
            self._grip(False, self.p('gripper.open_width_mm'))
            return g
        self._move(dst, MoveToStation.Goal.ABOVE)
        self._move(dst, MoveToStation.Goal.AT)
        self._grip(False, self.p('gripper.open_width_mm'))
        self._move(dst, MoveToStation.Goal.ABOVE)
        return g

    # ── 실행 루프 ─────────────────────────────────────────────────────
    def _run_loop(self):
        fsm = self.fsm
        self.event('INFO', 'BATCH_START', fsm.spec.product)
        self._pub_state()
        try:
            req = fsm.start()
            while req is not None and rclpy.ok() and not self._stop.is_set():
                step = fsm.state
                if req['kind'] not in GATE_BYPASS:
                    # 다음 **로봇 동작**을 시작하기 전에 멈춘다 — 정지를 잡는 자리는 여기 하나뿐이다.
                    # 사람을 기다리는 요청·safe 앞에서는 멈추지 않는다: 로봇이 움직이지 않거나(대기) 물러나는
                    # 이동(safe)이라 멈출 이유가 없고, QA 대기 앞에서 잡으면 판정을 못 받은 채 서 버린다
                    # (판정 대기 중에는 mode 를 PAUSED 로 못 올리므로 사람은 이유도 못 본다).
                    self._gate()
                self._before(step, req)
                try:
                    res = self._execute(req)
                except SkillError as e:
                    # 스킬 실패는 FORCE_LIMIT 일탈로 넘긴다 (docs/process_flow.md 8절) — 1회 재시도 후 ERROR.
                    # 안전 자세까지 실패하면 더 물러설 곳이 없으니 거기서 멈춘다.
                    if req['kind'] == 'safe':
                        self._fail(f'안전 자세 실패: {e}')
                        break
                    self.event('WARN', 'SKILL_FAIL', f'{step} {req["kind"]}: {e}')
                    req = fsm.skill_failed(req, str(e))
                    self._drain()
                    continue
                nxt = fsm.on_result(req, res)
                self._after(step, req, res)
                self._drain()
                self.event('INFO', 'STEP', f'{step} → {fsm.state}')
                req = nxt
        except Exception as e:                       # 전이표 밖 등 — 조용히 죽지 않는다
            self._fail(f'{type(e).__name__}: {e}')
        finally:
            self._close_attempt('ABORTED')
            self._drain()
            self._pub_state()
            self.event('INFO', 'BATCH_END', f'{fsm.mode} / {fsm.state}')

    def _fail(self, message: str):
        self.note = message
        self.get_logger().error(message)
        if self.fsm:
            self.fsm.state, self.fsm.mode = 'ERROR', 'ERROR'
        self.event('ERROR', 'INTERVENTION_FORCED', message)
        try:
            self._call_srv('safe', SafePose.Request(reason='RECOVERY'))
        except SkillError:
            pass

    # ── 관측·발행 ─────────────────────────────────────────────────────
    def _before(self, step: str, req: dict):
        if step == 'SCOOP' and req['kind'] == 'scoop':
            if self._attempt is not None:
                # 앞 시도가 닫히지 않은 채 새 시도가 시작됐다 — 스킬 실패(FORCE_LIMIT) 재시도가 여기로 온다.
                # 덮어쓰면 ScoopCycle 1건이 사라지므로 먼저 닫는다. 붓기까지 갔다가 실패했으면 POUR_FAILED
                a = self._attempt
                self._close_attempt('POUR_FAILED' if (a.pre_pour is not None and a.post_pour is None
                                                      and a.commanded_pour_fraction) else 'ABORTED')
            # ScoopCycle.attempt 는 "원료별 1부터 시작하는 시도 번호" — FSM 의 attempt 는 붓기까지 간 횟수만 세서
            # SCOOP_EMPTY 재시도가 같은 번호로 반복된다. 여기서는 시도가 열릴 때마다 올린다
            mid = req['material_id']
            self._try_no[mid] = self._try_no.get(mid, 0) + 1
            self._attempt = Attempt(material_id=mid, attempt=self._try_no[mid],
                                    target_g=self.fsm.cur.target_g, actual_before_g=self.fsm.cur.actual_g,
                                    t0=self._now(), scoop_tare=self._scoop_tare)
            if not self._item_t0:
                self._item_t0 = self._now()
        if step == 'POUR' and req['kind'] == 'pour' and self._attempt:
            self._attempt.commanded_pour_fraction = float(req.get('fraction', 1.0))

    def _after(self, step: str, req: dict, res: dict):
        a = self._attempt
        if step == 'SCOOP_TARE' and res.get('valid'):
            self._scoop_tare = Reading(**res)
        elif step == 'SCOOP' and a is not None:
            a.contact_detected = bool(res.get('contact_detected', False))
            a.max_contact_force_n = float(res.get('max_contact_force_n', 0.0))
            a.insertion_depth_mm = float(res.get('insertion_depth_mm', 0.0))
        elif step == 'WEIGH_SCOOP' and a is not None and res.get('valid'):
            a.pre_pour = Reading(**res)
        elif step == 'WEIGH_RESIDUAL' and a is not None and res.get('valid'):
            a.post_pour = Reading(**res)
        elif step == 'PICK_SCOOP' and req['kind'] == 'grip':
            self._scoop_tare = None                       # 원료가 바뀌면 빈 스쿱 무게도 다시 잰다
            self._grip_width = float(res.get('final_width_mm', 0.0))

    def _drain(self):
        """전이 뒤 FSM 이 늘린 것만 발행한다 — 일탈·원료 결과·시도 기록."""
        fsm = self.fsm
        new_devs = fsm.deviations[self._published_devs:]
        for d in new_devs:
            self._publish_deviation(d)
        self._published_devs = len(fsm.deviations)

        kinds = [d['kind'] for d in new_devs]
        outcome = next((DEV_TO_OUTCOME[k] for k in kinds if k in DEV_TO_OUTCOME), '')
        if outcome:
            self._close_attempt(outcome)
        elif self._attempt is not None and self._attempt.post_pour is not None:
            self._close_attempt('COMPLETE')

        for r in fsm.results[self._published_results:]:
            self._publish_result(r)
        self._published_results = len(fsm.results)
        self._pub_state()

    def _publish_deviation(self, d: dict):
        m = Deviation()
        m.header.stamp = self.get_clock().now().to_msg()
        m.deviation_id = f'D-{self.batch_id}-{len(self._dev_msgs) + 1}'
        m.batch_id = self.batch_id
        m.material_id = d.get('material_id') or ''
        m.kind = getattr(Deviation, d['kind'], Deviation.TIMEOUT)
        m.detail = ' · '.join(x for x in (d['step'], d['action'], f"{d['count']}회", d.get('detail')) if x)
        m.requires_decision = d['action'] == 'QA'
        # RETRY·REFILL 은 로봇이 스스로 넘어가는 것 → AUTO_RECOVERED. FORCED(강제 개입, 배치 ERROR)는 자동 복구가
        # 아니다 → Deviation.FORCED (계약 v1.2.1, PR #19). 그 계약 전 빌드에서는 PENDING 으로 떨어진다 — detail 의
        # 'FORCED' 로 가를 수 있다. 어느 쪽이든 자동 복구율 분자에서 빠진다
        if m.requires_decision:
            m.decision = Deviation.PENDING
        elif d['action'] == 'FORCED':
            m.decision = getattr(Deviation, 'FORCED', Deviation.PENDING)
        else:
            m.decision = Deviation.AUTO_RECOVERED
        self._dev_msgs.append(m)
        self.pub_dev.publish(m)
        self.event('WARN', 'DEVIATION', f"{m.deviation_id} {d['kind']} @{d['step']}")

    def _publish_result(self, r):
        m = DispenseResult()
        m.header.stamp = self.get_clock().now().to_msg()
        m.batch_id, m.material_id = self.batch_id, r.material_id
        m.target_g, m.actual_g = float(r.target_g), float(r.actual_g)
        m.error_pct = (r.actual_g - r.target_g) / r.target_g * 100.0 if r.target_g else 0.0
        # DispenseResult 는 OK/UNDER/OVER 뿐이라 QA 승인된 'INVALID' 는 담을 곳이 없다 → OK 로 떨어진다 (I-008)
        m.verdict = getattr(DispenseResult, r.verdict or 'OK', DispenseResult.OK)
        m.attempts = min(255, int(r.attempts))
        m.duration_s = max(0.0, self._now() - self._item_t0) if self._item_t0 else 0.0
        self._item_t0 = 0.0
        self.pub_result.publish(m)

    def _close_attempt(self, outcome: str):
        a, self._attempt = self._attempt, None
        if a is None:
            return
        a.outcome = outcome
        m = ScoopCycle()
        m.header.stamp = self.get_clock().now().to_msg()
        m.batch_id, m.material_id, m.attempt = self.batch_id, a.material_id, min(255, a.attempt)
        m.target_g, m.actual_before_g = float(a.target_g), float(a.actual_before_g)
        m.scoop_tare = self._reading_msg(a.scoop_tare)
        m.pre_pour = self._reading_msg(a.pre_pour)
        m.post_pour = self._reading_msg(a.post_pour)
        m.commanded_pour_fraction = float(a.commanded_pour_fraction)
        m.delivered_g = float(a.delivered_g())
        m.weigh_method = (ScoopCycle.WEIGH_METHOD_WORKPIECE if self.p('scale.method') == 'workpiece'
                          else ScoopCycle.WEIGH_METHOD_TOOL_FORCE)
        m.weigh_pose_id = 'workbench'
        m.tool_name, m.tcp_name = self.p('robot.tool_name'), self.p('robot.tcp_name')
        m.contact_detected = bool(a.contact_detected)
        m.max_contact_force_n = float(a.max_contact_force_n)
        m.insertion_depth_mm = float(a.insertion_depth_mm)
        m.grip_width_mm = float(self._grip_width)
        # 6축 wrench 통계는 계량 스킬이 돌려주지 않는다 (WeighHeld/WeighContainer 는 WeightReading 만) — I-008.
        # 채울 수 없는 값을 0 으로 두면 학습에서 진짜 0 과 구분되지 않으므로 valid=false 로 남긴다.
        m.tare_wrench_valid = m.pre_pour_wrench_valid = m.post_pour_wrench_valid = False
        m.reference_valid = False
        m.outcome = getattr(ScoopCycle, outcome, ScoopCycle.ABORTED)
        m.valid = a.is_valid()
        m.duration_s = a.duration_s(self._now())
        self.pub_cycle.publish(m)

    @staticmethod
    def _reading_msg(r: Reading | None) -> WeightReading:
        if r is None:
            return WeightReading(valid=False, subject='scoop', station='workbench')
        return WeightReading(gross_g=r.gross_g, tare_g=r.tare_g, net_g=r.net_g, std_g=r.std_g,
                             samples=r.samples, valid=r.valid, subject='scoop', station='workbench')

    def shutdown(self, timeout: float = 3.0):
        """실행 루프를 세우고 기다린다. QA·인터락 대기도 깨운다 (Ctrl+C·시험 정리 공통)."""
        self._stop.set()
        self._qa.set(); self._interlock_exit.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _pub_state(self):
        m = CellState()
        m.header.stamp = self.get_clock().now().to_msg()
        if self.fsm:
            m.mode = getattr(CellState, self.fsm.mode, CellState.IDLE)
            m.step, m.item_index, m.batch_id = self.fsm.state, min(255, self.fsm.idx), self.batch_id
        m.station, m.note = self.station, self.note
        self.pub_state.publish(m)


def main(args=None):
    rclpy.init(args=args)
    node = ProcessNode()
    ex = MultiThreadedExecutor(num_threads=4)
    ex.add_node(node)
    try:
        ex.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()          # 실행 루프를 먼저 세운다 — 죽은 노드로 발행하지 않게
        node.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
