"""공정 노드 — ProcessFSM 의 요청을 스킬 Action/Service 로 실행하고 결과를 돌려준다.

입력  run_batch (Action 서버: 기존 FSM 실행·피드백·취소)
      submit_order · qa_decision · interlock · request_safety_recovery (Service 서버)
      event 구독 (skill_node 의 NUDGE·ROBOT_SAFETY_STOP·ROBOT_SAFETY_RECOVERY, D-21·v1.4)
      스킬 Action 클라이언트 move_to_station·scoop·pour·weigh_container·weigh_held
      스킬 Service 클라이언트 set_gripper·measure_force·safe_pose·recover_safety
출력  state (0.5 s + 전이, TRANSIENT_LOCAL) · weight · scoop_cycle · dispense_result · deviation (TRANSIENT_LOCAL) · event

구조 (docs/process_flow.md 0절): rclpy 콜백은 값만 저장한다. 배치 실행 루프는 별도 스레드(_run_loop)에서
돌며 스킬을 **한 번에 하나** 부른다. QA 판정·인터락·NUDGE 는 콜백이 값만 세우고 루프가 기다린다.

FSM 은 로봇도 ROS 도 모른다 — dict 요청을 주고 dict 결과를 받는다. 이 파일이 그 dict 를 계약 메시지로
옮기는 유일한 지점이다. 발행은 전이 뒤 `_drain()` 한 곳에서 FSM 상태 변화를 보고 판단한다.

안전 정지(v1.4, docs/interfaces.md 8절): skill_node 가 `CellEvent(ERROR, 'ROBOT_SAFETY_STOP')` 를 내면
`_safety_stop` 을 세운다 — 새 주문·대기·재시도를 전부 막는다. HMI 는 A 의 `recover_safety` 를 직접 부르지
않고 `request_safety_recovery` 로 여기를 거친다. 복구 성공(`ROBOT_SAFETY_RECOVERY` 이벤트, success 하고
manual_required 아님)도 배치 재개를 뜻하지 않는다 — 새 주문만 받아들인다.
"""
import json
import threading
import time
from gmp_process.core.safety_events import SafetyEvents
from copy import deepcopy
from datetime import datetime, timezone

import rclpy
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile

from gmp_interfaces.action import MoveToStation, Pour, ReturnMaterial, RunBatch, Scoop, WeighContainer, WeighHeld
from gmp_interfaces.msg import (CellEvent, CellState, Deviation, DispenseResult, ScoopCycle,
                                WeightReading)
from gmp_interfaces.srv import (InterlockRequest, MeasureForce, QaDecision, RecoverSafety, SafePose,
                                SetGripper, SubmitOrder)

from gmp_dosing.core.dosing import DosingConfig, verdict_of
from gmp_dosing.core.scale import ScaleConfig, WeightModel
from gmp_process.core.attempt import Attempt, Reading
from gmp_process.core.process_fsm import ProcessFSM, ToolFingerprint
from gmp_process.core.recipe import parse as parse_recipe
from gmp_process.core.station_map import StationMap

LATCHED = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
DEV_QOS = QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL)

# 사람을 기다리는 요청 — 로봇을 움직이지 않으므로 정지 게이트를 앞에 두지 않는다 (아래 _run_loop 참고)
HUMAN_WAITS = ('wait_qa', 'wait_interlock', 'wait_nudge')
# 게이트를 건너뛰는 요청 — 사람 대기 + 안전 자세. `safe` 는 "사람이 곧 들어오니 물러나라" 는 이동이라
# NUDGE·인터락 정지보다 우선한다 (ENTER 가 NUDGE 정지 중에도 safe_pose 를 부르는 것과 같은 논리)
GATE_BYPASS = HUMAN_WAITS + ('safe',)

# 일탈 → ScoopCycle.outcome. 시도가 실패로 끝난 것만 여기 있다 (OVERFILL·TIMEOUT 은 시도 자체는 끝났다)
DEV_TO_OUTCOME = {'SCOOP_EMPTY': 'SCOOP_EMPTY', 'MATERIAL_EMPTY': 'SCOOP_EMPTY',
                  'WEIGH_INVALID': 'WEIGH_INVALID'}


class SkillError(RuntimeError):
    """스킬을 부를 수 없거나(서버 없음·시간 초과) 서버가 abort 한 것. 배치를 ERROR 로 끝낸다."""


class BatchCancelled(RuntimeError):
    """다음 동작 금지. 실행 중 스킬의 종료 확인 뒤 배치를 ABORTED 로 닫는다."""


class BatchSafetyStopped(RuntimeError):
    """복구 이벤트가 빨리 와도 이미 중단된 배치를 재개하지 않는다."""


class ProcessNode(Node):
    def __init__(self, **kwargs):
        # kwargs 는 시험에서 parameter_overrides 를 넣기 위한 통로다 (test/test_process_node.py)
        super().__init__('process_node', **kwargs)
        self.declare_parameters('', [
            ('stations_file', ''),
            ('robot.tool_name', 'tool_weight'), ('robot.tcp_name', 'GripperDA_v1'),
            ('robot.vel_scale', 0.0),           # 0 이면 skill_node 의 robot.vel_scale
            ('scale.method', 'tool_force'), ('scale.gain', 0.8859), ('scale.offset_g', 247.091),
            # 런타임 값은 common.yaml 이 단일 출처다. 아래 기본값은 런치 없이 노드를 띄울 때만 쓰인다.
            # 9/21 영점 재작업의 material_3 재측정값(max_std 8.0)은 조장·A 결정 전까지 미적용이라,
            # 여기와 ScaleConfig 기본값과 common.yaml 의 숫자가 당분간 서로 다르다.
            ('scale.max_std_g', 10.0),
            # VERIFY 직전 빈 그리퍼 영점 재확인 임계 [N] — 0 이면 검사 꺼짐. B 실측 전 잠정값.
            ('scale.zero_drift_limit_n', 0.5),
            ('scale.samples', 20), ('scale.settle_s', 1.0),
            # max_attempts 는 **붓기 시도** 상한이다. 목표량÷스쿱 1회량에 비례해야 한다
            # (데모 A 200 g ÷ 40 g = 5회가 하한). max_returns 는 **초과 반환** 상한으로 성격이 다르다 (#189).
            ('dosing.max_attempts', 8), ('dosing.max_returns', 3),
            ('dosing.scoop_nominal_g', 40.0), ('dosing.min_fraction', 0.15),
            ('gripper.cup_width_mm', 60.0),
            ('gripper.open_width_mm', 100.0), ('gripper.force_n', 20.0),
            ('gripper.fingerprint_tolerance_mm', 0.0),   # [추가 1] WRONG_TOOL 폭 지문 margin. 0 이면 검사 꺼짐
            ('gripper.scoop_search_width_mm', 0.0),      # 스쿱 파지 탐색 목표 폭 — 기대 폭과 분리(A 리뷰, PR #165)
            ('skill_timeout_s', 90.0), ('server_wait_s', 20.0), ('grip_timeout_s', 5.0),
            ('safety.nudge_enabled', True),   # skill_node 와 같은 스위치 — 끄면 NUDGE 를 무시한다
        ])
        p = lambda k: self.get_parameter(k).value  # noqa: E731
        self.p = p
        # **키워드로 넘긴다** — ScaleConfig 는 min_resolvable_g 가 offset_g 와 max_std_g 사이에 있어서,
        # 위치 인자로 두면 그 필드를 뺄 때 max_std_g 가 조용히 한 칸 밀린다 (AGENTS 규칙, #211).
        self.scale = WeightModel(ScaleConfig(method=p('scale.method'), gain=p('scale.gain'),
                                             offset_g=p('scale.offset_g'), max_std_g=p('scale.max_std_g')))
        self.dosing_cfg = DosingConfig(max_attempts=p('dosing.max_attempts'),
                                       scoop_nominal_g=p('dosing.scoop_nominal_g'),
                                       min_fraction=p('dosing.min_fraction'))
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
        # HMI → 여기(현재 배치·권한 확인) → skill_node/recover_safety (v1.4, docs/interfaces.md 8절)
        self.create_service(RecoverSafety, 'request_safety_recovery', self._srv_recover_safety,
                            callback_group=self.cb)
        self.create_timer(0.5, self._pub_state, callback_group=self.cb)
        # skill_node 가 사람 접촉·안전 정지·복구 결과를 여기로 알린다 (D-21·v1.4). 우리가 내는 event 도
        # 같이 들어오므로 code 로 거른다
        self.create_subscription(CellEvent, 'event', self._on_event, 100, callback_group=self.cb)

        self.act = {
            'move': ActionClient(self, MoveToStation, 'move_to_station', callback_group=self.cb),
            'scoop': ActionClient(self, Scoop, 'scoop', callback_group=self.cb),
            'pour': ActionClient(self, Pour, 'pour', callback_group=self.cb),
            'return_material': ActionClient(self, ReturnMaterial, 'return_material', callback_group=self.cb),
            'weigh': ActionClient(self, WeighContainer, 'weigh_container', callback_group=self.cb),
            'weigh_scoop': ActionClient(self, WeighHeld, 'weigh_held', callback_group=self.cb),
        }
        self.srv = {
            'grip': self.create_client(SetGripper, 'set_gripper', callback_group=self.cb),
            'measure': self.create_client(MeasureForce, 'measure_force', callback_group=self.cb),
            'safe': self.create_client(SafePose, 'safe_pose', callback_group=self.cb),
            'recover': self.create_client(RecoverSafety, 'recover_safety', callback_group=self.cb),
        }

        self.fsm: ProcessFSM | None = None
        self.batch_id = ''
        self.station = ''            # 마지막으로 도착한 스테이션 — CellState.station
        self.note = ''
        self._qa = threading.Event(); self._qa_decision = None; self._qa_operator = ''
        self._interlock_exit = threading.Event()
        self._pause = False          # 인터락 ENTER 가 세운다. **루프만 내린다** — EXIT 핸들러가 내리면
                                     # 취소된 스킬이 돌아오기 전에 풀려 그 실패가 진짜 실패로 읽힌다
        self._nudge_paused = False   # 사람 접촉으로 멈춤 (D-21). 다음 NUDGE 가 내린다
        self._nudge_waiting = False  # 세트 끝 — nudge_wait 에서 NUDGE 를 기다리는 중 (D-23). 그때의 NUDGE 는 정지가 아니라 '다음 세트'
        self._nudge_go = threading.Event()
        self._refill_waiting = False # 루프가 REFILL 로 EXIT 를 기다리는 중 — EXIT 를 받을지 가른다
        self._nudge_lock = threading.Lock()   # 토글은 읽고-쓰기라 콜백 둘이 겹치면 뒤집히지 않는다
        # 로봇 안전 정지(v1.4) — skill_node 의 CellEvent(ERROR, ROBOT_SAFETY_STOP) 가 세운다. 새 주문·대기·
        # FORCE_LIMIT 재시도를 전부 막는다. 복구 성공 이벤트가 내린다 — 배치 재개는 아니다(새 주문만 받는다)
        self._safety_stop = False
        self._safety_events = SafetyEvents()
        self._safety_event_lock = threading.Lock()
        self._safety_stop_reason = ''
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
        # 서비스와 Action은 같은 실행 슬롯을 사용한다. goal 수락과 execute 사이도 예약한다.
        self._order_lock = threading.RLock()
        self._reserved = False
        self._reserved_recipe = None
        self._batch_handle = None
        self._batch_cancel = threading.Event()
        self._batch_safety_stop = threading.Event()
        self._batch_done = threading.Event()
        self._batch_outcome = ''
        self._last_result = DispenseResult()
        self._seq = 0
        self._used_batch_ids = set()  # 이 프로세스 세션 내 중복 ID 금지
        self._execution_uncertain = False  # 스킬 응답 유실 후 새 주문으로 겹쳐 실행하지 않는다
        self.batch_server = ActionServer(
            self, RunBatch, 'run_batch', self._execute_batch,
            goal_callback=self._goal_batch, cancel_callback=self._cancel_batch,
            handle_accepted_callback=self._accept_batch, callback_group=self.cb)

    # ── 공용 ─────────────────────────────────────────────────────────
    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def event(self, level: str, code: str, text: str = ''):
        m = CellEvent(level=getattr(CellEvent, level), code=code, text=text, batch_id=self.batch_id)
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_event.publish(m)
        self.get_logger().info(f'[{code}] {text}')

    def _on_event(self, msg):
        """skill_node 의 NUDGE·안전 정지·복구 결과 — 콜백은 값만 세운다 (D-21·v1.4).

        NUDGE 는 토글만 한다. 멈추는 것은 루프의 게이트다 — 콜백에서 멈추면 rclpy 스레드가 잠긴다.
        같은 접촉을 두 번 세지 않는 것은 skill_node 의 `nudge_cooldown_s` 가 한다.
        """
        if msg.code == 'ROBOT_SAFETY_STOP':
            self._on_safety_stop(msg)
            return
        if msg.code == 'ROBOT_SAFETY_RECOVERY':
            self._on_safety_recovery(msg)
            return
        if msg.code != 'NUDGE' or not self.get_parameter('safety.nudge_enabled').value:
            return
        with self._nudge_lock:
            if self._nudge_waiting:                    # 세트 끝 대기 — 이 접촉은 '다음 세트' 신호다 (D-23)
                self._nudge_go.set()
                self.get_logger().info('[NUDGE] 세트 대기 해제')
                return
            self._nudge_paused = not self._nudge_paused
            now = self._nudge_paused
        self.get_logger().info(f"[NUDGE] {'정지' if now else '재개'}")

    def _on_safety_stop(self, msg):
        """A 가 SAFE_STOP 류를 감지했다 (docs/interfaces.md 8절). 새 주문·대기·재시도를 막는다.

        진행 중인 루프는 여기서 끊지 않는다 — 다음 스킬 호출이 skill_node 에서 거부되어 SkillError 로
        돌아오거나, `_await`/`_gate` 의 대기가 이 플래그를 보고 스스로 깬다. 배치는 `_run_loop` 가
        FORCE_LIMIT 재시도 없이 바로 ERROR 로 끝낸다.
        """
        try:
            data = json.loads(msg.text) if msg.text else {}
        except (TypeError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        reason = data.get('reason') or msg.text or '로봇 안전 정지'
        with self._order_lock, self._safety_event_lock:
            self._safety_events.stop(data)
            self._safety_stop = True
            if getattr(self, '_reserved', False):
                self._batch_safety_stop.set()
            self._safety_stop_reason = reason
        self.get_logger().warning(f'[SAFETY_STOP] 새 주문·재시도 차단: {reason}')

    def _on_safety_recovery(self, msg):
        """A 의 복구 결과 (docs/interfaces.md 8절). 성공+수동조치 불필요일 때만 차단을 푼다.

        로봇 복구 확인이지 배치 재개가 아니다 — 끝난 배치를 되살리지 않고 새 주문만 다시 받는다.
        `manual_required` 나 실패는 차단을 유지해 다음 명시적 복구 요청을 기다린다.
        """
        try:
            data = json.loads(msg.text) if msg.text else {}
        except (TypeError, ValueError):
            data = {}
        with self._safety_event_lock:
            if not isinstance(data, dict) or not self._safety_events.accepts(data):
                return
            self._safety_stop = False
            self._safety_stop_reason = ''
        self.get_logger().info('[SAFETY_STOP] 로봇 복구 확인 — 배치 재개 아님, 새 주문부터 받는다')

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
        self._check_batch_interrupt()
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
            self._check_batch_interrupt()
            if self._pause and self._interlock_exit.is_set():
                # EXIT 를 여기서 소비한다 — _pause 를 내리는 곳은 언제나 루프다 (핸들러가 내리면
                # 취소된 스킬이 돌아오기 전에 풀려 그 실패가 진짜 실패로 읽힌다)
                self._interlock_exit.clear()
                self._pause = False
                continue
            if self._stop.is_set() or not rclpy.ok() or self._safety_stop:
                raise SkillError('정지 대기 중 종료' if not self._safety_stop else 'SAFETY_STOP')
            time.sleep(0.1)
        if took_mode and self.fsm and self.fsm.mode == 'PAUSED':
            self.fsm.mode = 'RUNNING'
        self.note = ''
        self._pub_state()
        self.event('INFO', 'RESUME', f'{reason} 해제')

    def _await(self, ev: threading.Event, what: str):
        """사람을 기다리는 대기(QA·인터락)는 상한이 없다 (D-23 반자동). 종료 요청·안전 정지가 깨운다."""
        self._check_batch_interrupt()
        while not ev.wait(0.2):
            self._check_batch_interrupt()
            if self._safety_stop:
                raise SkillError(f'{what} 대기 중 SAFETY_STOP: {self._safety_stop_reason}')
            if self._stop.is_set() or not rclpy.ok():
                raise SkillError(f'{what} 대기 중 종료')
        self._check_batch_interrupt()
        return True

    def _wait(self, fut, timeout: float, what: str):
        """rclpy Future 를 루프 스레드에서 기다린다. spin 은 executor 가 한다 (콜백 그룹 Reentrant)."""
        ev = threading.Event()
        fut.add_done_callback(lambda _f: ev.set())
        if not ev.wait(timeout):
            raise SkillError(f'{what} 응답 없음 ({timeout:.0f}s)')
        return fut.result()

    # ── RunBatch / SubmitOrder 공통 접수 ──────────────────────────────
    def _check_batch_interrupt(self):
        if self._batch_safety_stop.is_set() or self._safety_stop:
            raise BatchSafetyStopped('SAFETY_STOP: ' + self._safety_stop_reason)
        if self._batch_cancel.is_set():
            raise BatchCancelled('RunBatch 취소 요청 — 자동 재개 없음')
        if self._stop.is_set() or not rclpy.ok():
            raise BatchCancelled('공정 노드 종료 — 자동 재개 없음')

    def _reserve_batch(self, recipe):
        """_order_lock 안에서 호출. 검증 실패 시 슬롯·현재 배치를 변경하지 않는다."""
        if self._stop.is_set() or not rclpy.ok():
            raise ValueError('공정 노드 종료 중')
        if self._safety_stop:
            raise ValueError('로봇 안전 정지 — 복구 필요: ' + self._safety_stop_reason)
        if self._execution_uncertain:
            raise ValueError('이전 스킬 종료 미확인 — 현장 확인 및 노드 재기동 필요')
        if (self._reserved or (self._thread and self._thread.is_alive()) or
                (self.fsm and self.fsm.mode in ('RUNNING', 'PAUSED', 'DEVIATION'))):
            if self.fsm and self.fsm.state == 'NUDGE_WAIT':
                raise ValueError('세트 완료 — 로봇을 건드리면 다음 주문을 받는다 (NUDGE_WAIT)')
            raise ValueError('기존 배치 실행 / 종료 처리 중')
        if self._pause or self._nudge_paused:
            raise ValueError('구역 진입 / 일시 정지 중에는 새 주문을 받지 않습니다')
        spec = parse_recipe({'product': recipe.product,
                             'items': [{'material_id': i.material_id, 'target_g': i.target_g,
                                        'tol_pct': i.tol_pct} for i in recipe.items]})
        self.smap.check([i.material_id for i in spec.items])
        if len(spec.items) > 255:
            raise ValueError('RunBatch 완료 원료 수(uint8)를 초과하는 레시피')
        batch_id = recipe.batch_id.strip()
        if not batch_id:
            # 기록과 같은 ROS clock 사용. 자동 ID의 중복 방지는 기동 세션 범위다.
            day = datetime.fromtimestamp(self._now(), timezone.utc).strftime('%Y%m%d')
            while True:
                self._seq += 1
                batch_id = f'B-{day}-{self._seq:03d}'
                if batch_id not in self._used_batch_ids:
                    break
        if batch_id in self._used_batch_ids:
            raise ValueError('이 세션에서 이미 사용한 batch_id')
        self._reserved_recipe = (spec, batch_id)
        self._interlock_exit.clear()
        self._batch_cancel.clear()
        self._batch_safety_stop.clear()
        self._batch_done.clear()
        self._batch_outcome = ''
        self._reserved = True

    def _start_reserved_batch(self):
        spec, self.batch_id = self._reserved_recipe
        self._used_batch_ids.add(self.batch_id)
        self._reset_batch()
        self._last_result = DispenseResult()
        fingerprint = ToolFingerprint(scoop_widths_mm=self.smap.widths, cup_width_mm=self.p('gripper.cup_width_mm'),
                                      tolerance_mm=self.p('gripper.fingerprint_tolerance_mm'))
        self.fsm = ProcessFSM(spec, self.dosing_cfg, self.scale, fingerprint=fingerprint,
                              max_returns=int(self.p('dosing.max_returns')),
                              zero_drift_limit_n=float(self.p('scale.zero_drift_limit_n')))
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name='process-run')
        self._thread.start()

    def _goal_batch(self, request):
        with self._order_lock:
            try:
                self._reserve_batch(request.recipe)
            except (ValueError, KeyError) as e:
                self.get_logger().warning(f'RunBatch 거부: {e}')
                return GoalResponse.REJECT
            return GoalResponse.ACCEPT

    def _accept_batch(self, handle):
        with self._order_lock:
            self._batch_handle = handle
        # rclpy execute callback이 시작한 뒤 worker를 시작한다.
        # 수락 직후 cancel이 와도 _batch_cancel을 초기화하지 않는다.
        handle.execute()

    def _cancel_batch(self, handle):
        with self._order_lock:
            if (handle is not self._batch_handle or self._batch_done.is_set() or
                    not self._reserved):
                return CancelResponse.REJECT
            self._batch_cancel.set()
            return CancelResponse.ACCEPT

    def _execute_batch(self, handle):
        try:
            with self._order_lock:
                self._start_reserved_batch()
            self._thread.join()  # 스킬 응답 / 기록 최종 발행까지 슬롯을 유지한다
            if not self._batch_done.is_set():
                raise RuntimeError('배치 루프 최종 기록 완료를 확인하지 못했습니다')
            with self._order_lock:
                outcome = self._batch_outcome or 'ERROR'
                result = RunBatch.Result(
                    success=outcome in ('DONE', 'DONE_UNMEASURED'), items_done=min(255, len(self.fsm.results)),
                    deviations=min(255, len(self.fsm.deviations)), result=outcome,
                    message=self.note or outcome)
                if outcome == 'ABORTED' and self._batch_cancel.is_set():
                    # cancel_callback 응답 뒤 rclpy가 CANCELING으로 전이할 틈을 준다.
                    deadline = time.monotonic() + float(self.p('server_wait_s'))
                    while handle.is_active and not handle.is_cancel_requested and time.monotonic() < deadline:
                        time.sleep(0.01)
                if outcome == 'ABORTED' and handle.is_cancel_requested:
                    handle.canceled()
                elif outcome in ('DONE', 'DONE_UNMEASURED', 'DISCARDED'):
                    handle.succeed()  # DISCARDED는 완료된 실행이며 result.success는 false
                else:
                    handle.abort()
                return result
        except Exception as e:
            self._execution_uncertain = True
            self.get_logger().error(f'RunBatch 종료 실패: {e}')
            if handle.is_active:
                handle.abort()
            return RunBatch.Result(success=False, result='ERROR', message=str(e))
        finally:
            with self._order_lock:
                self._batch_handle = None
                self._reserved = False
                self._reserved_recipe = None

    def _srv_submit(self, req, res):
        with self._order_lock:
            try:
                self._reserve_batch(req.recipe)
            except (ValueError, KeyError) as e:
                res.accepted, res.message = False, str(e).strip("'")
                return res
            try:
                self._start_reserved_batch()
            except Exception as e:
                self._reserved = False
                self._reserved_recipe = None
                self._execution_uncertain = True
                res.accepted, res.message = False, f'배치 시작 실패: {e}'
                return res
            res.accepted, res.batch_id, res.message = True, self.batch_id, 'accepted'
            return res

    def _reset_batch(self):
        self._dev_msgs = []
        self._published_devs = self._published_results = 0
        self._attempt = self._scoop_tare = None
        self._try_no = {}
        self._qa.clear(); self._qa_decision = None; self._qa_operator = ''
        # 예약 후 들어온 ENTER/NUDGE/EXIT를 지우지 않는다. 이전 EXIT는 예약 때 지운다.
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
            if self._pause or (self.fsm and self.fsm.mode == 'PAUSED' and not self._nudge_paused
                               and not self._nudge_waiting):
                # 이미 **안전 자세로 가서** 기다리는 중 (REFILL 대기 또는 앞선 ENTER). safe_pose 를 다시 부르거나
                # _interlock_exit 를 다시 지우면 EXIT 를 두 번 눌러야 풀린다 — 멱등하게 받는다.
                # NUDGE 정지는 PAUSED 지만 **그 자리에 선 것**이라 안전 자세가 아니다 → 여기 걸리면 안 된다.
                # 세트 끝 NUDGE_WAIT 도 같다 — nudge_wait 스테이션이지 안전 자세가 아니다
                if not self._pause and not self._refill_waiting:
                    # FSM의 PAUSED는 SafePose 완료보다 먼저 보일 수 있다.
                    # 안전 자세 성공과 EXIT 수신 준비가 확인되기 전에는 진입 허가하지 않는다.
                    deadline = time.monotonic() + float(self.p('server_wait_s'))
                    while not self._refill_waiting and time.monotonic() < deadline:
                        if self._stop.is_set() or self._safety_stop or self._batch_cancel.is_set():
                            break
                        if not self.fsm or self.fsm.mode != 'PAUSED':
                            break
                        time.sleep(0.01)
                    if not self._refill_waiting:
                        res.granted, res.message = False, '안전 자세 완료 미확인 — 진입 불가'
                        return res
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

    def _srv_recover_safety(self, req, res):
        """HMI → 여기 → skill_node/recover_safety 중계 (v1.4, docs/interfaces.md 8절).

        skill_node 가 상태·중복 요청·경합의 최종 판단자다 — 여기서는 필드만 검증하고 넘긴다. A 의
        `_safety_latched` 가 더 최신일 수 있어(이벤트 전파 지연) `_safety_stop` 으로 미리 막지 않는다.
        """
        if not req.request_id.strip() or not req.operator_id.strip() or not req.operator_confirmed:
            res.success, res.manual_required, res.robot_state = False, True, -1
            res.message = '작업자·고유 요청 ID·원인 제거 확인이 필요합니다'
            return res
        try:
            r = self._call_srv('recover', RecoverSafety.Request(
                request_id=req.request_id, operator_id=req.operator_id,
                expected_state=req.expected_state, operator_confirmed=req.operator_confirmed))
        except SkillError as e:
            res.success, res.manual_required, res.robot_state = False, True, -1
            res.message = str(e)
            return res
        res.success, res.manual_required = bool(r.success), bool(r.manual_required)
        res.robot_state, res.message = int(r.robot_state), r.message
        return res

    # ── 스킬 호출 ─────────────────────────────────────────────────────
    def _call_srv(self, key: str, request):
        if key not in ('safe', 'recover'):
            self._check_batch_interrupt()
        cli = self.srv[key]
        if not cli.wait_for_service(timeout_sec=float(self.p('server_wait_s'))):
            raise SkillError(f'{key} 서비스 없음')
        if key not in ('safe', 'recover'):
            self._check_batch_interrupt()
        try:
            return self._wait(cli.call_async(request), float(self.p('skill_timeout_s')), key)
        except Exception as e:
            if key != 'recover':
                self._execution_uncertain = True
            raise SkillError(f'{key} 응답 미확인: {e}') from e

    def _call_act(self, key: str, goal):
        self._check_batch_interrupt()
        cli = self.act[key]
        if not cli.wait_for_server(timeout_sec=float(self.p('server_wait_s'))):
            raise SkillError(f'{key} 액션 서버 없음')
        self._check_batch_interrupt()
        # 늦게 수락된 스킬도 놓치지 않는다. timeout이면 다음 주문을 막는다.
        def cancel_late(future):
            try:
                gh = future.result()
                if gh.accepted and (self._execution_uncertain or self._batch_cancel.is_set() or
                                    self._batch_safety_stop.is_set() or self._stop.is_set()):
                    gh.cancel_goal_async()
            except Exception as e:
                self.get_logger().warning(f'{key} 늦은 Goal 취소 실패: {e}')
        pending = cli.send_goal_async(goal)
        pending.add_done_callback(cancel_late)
        gh = None
        try:
            gh = self._wait(pending, float(self.p('server_wait_s')), f'{key} goal')
            if not gh.accepted:
                raise SkillError(f'{key} 목표 거부')
            future = gh.get_result_async()
            deadline = time.monotonic() + float(self.p('skill_timeout_s'))
            canceled = False
            while not future.done():
                if not canceled and (self._batch_cancel.is_set() or self._batch_safety_stop.is_set()
                                     or self._stop.is_set()):
                    gh.cancel_goal_async()
                    canceled = True
                if time.monotonic() >= deadline:
                    gh.cancel_goal_async()
                    self._execution_uncertain = True
                    raise SkillError(f'{key} 종료 미확인 (시간 초과)')
                time.sleep(0.02)
            result = future.result().result
            self._check_batch_interrupt()
            return result
        except SkillError:
            if gh is None:
                self._execution_uncertain = True
                if pending.done():
                    cancel_late(pending)
            raise
        except (BatchCancelled, BatchSafetyStopped):
            raise
        except Exception as e:
            self._execution_uncertain = True
            raise SkillError(f'{key} 통신 종료 미확인: {e}') from e

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
                          samples=msg.samples, valid=msg.valid, station=msg.station or 'unknown',
                          subject=subject)   # subject 는 어느 액션을 불렀는지 아는 이쪽이 채운다 (I-007 c)
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_weight.publish(m)
        return {'gross_g': float(m.gross_g), 'tare_g': float(m.tare_g), 'net_g': float(m.net_g),
                'std_g': float(m.std_g), 'samples': int(m.samples), 'valid': bool(m.valid),
                'station': str(m.station)}

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
            self._check_batch_interrupt()
            try:
                res = self._dispatch(req)
            except SkillError as e:
                self._check_batch_interrupt()
                if self._execution_uncertain or not self._pause:
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
                self.note = ''          # 위 `safe` 가 실은 정지 사유를 여기서 내린다
            self._interlock_exit.clear()
            self._pause = False
            self._pub_state()
            return {}
        if k == 'wait_nudge':
            # 세트 끝. 로봇은 nudge_wait 에 서 있다 — 여기까지 오는 동안의 접촉으로 남은 '정지'는 뜻이 없다
            # (이미 서 있다). 지운 뒤 다음 접촉을 '다음 세트' 로 받는다. FSM 이 mode 를 PAUSED 로 올려 두었다.
            # 정지(PAUSE/RESUME)가 아니라 세트 경계(SET_DONE/SET_NEXT)다 — 반자동 운전의 설계된 대기 (D-23).
            if not self.get_parameter('safety.nudge_enabled').value:
                self.event('INFO', 'SET_DONE', '세트 완료 — nudge 비활성이라 대기 없이 종료')
                return {}
            with self._nudge_lock:
                self._nudge_paused = False
                self._nudge_go.clear()
                self._nudge_waiting = True
            self.note = 'NUDGE_WAIT — 세트 완료, 건드리면 다음 세트'
            self._pub_state()
            self.event('INFO', 'SET_DONE', self.note)
            try:
                self._await(self._nudge_go, 'NUDGE (다음 세트)')
            finally:
                with self._nudge_lock:
                    self._nudge_waiting = False
                    self._nudge_go.clear()
            self.note = ''
            self.event('INFO', 'SET_NEXT', '사람이 건드림 — 세트 종료, 다음 주문을 받는다')
            return {}
        if k == 'measure':
            r = self._call_srv('measure', MeasureForce.Request(samples=int(self.p('scale.samples')),
                                                               settle_s=float(self.p('scale.settle_s'))))
            return {'valid': bool(r.valid), 'fz_mean_n': float(r.fz_mean_n), 'fz_std_n': float(r.fz_std_n)}
        if k == 'safe':
            reason = req.get('reason', '')
            r = self._call_srv('safe', SafePose.Request(reason=reason))
            if r.success and req.get('then') == 'wait_interlock':
                # 성공 후 즉시 EXIT를 받을 준비를 한다. 다음 dispatch까지의 틈에도 유지한다.
                self._refill_waiting = True
                # FSM 이 만든 사유(REFILL 등)를 note **앞머리**에 싣는다 — HMI 는 note 앞머리로
                # 정지 사유를 가른다 (gmp_hmi pause_context.pause_reason 이 `^REFILL\b`). 여기서
                # 안 실으면 대기 내내 note 가 비어 HMI 의 REFILL 분기가 영영 안 뜬다 (#191).
                # 사유를 하드코딩하지 않는 것은 뒤에 사유가 늘어도(HEIGHT_LOW 등, #192) 그대로
                # 흐르게 하기 위해서다.
                #
                # `_pause_reason()` 은 건드리지 않는다 — `_gate()` 가 `while self._pause_reason():`
                # 로 도는데 그 루프가 내릴 수 있는 건 `_pause` 뿐이다. REFILL 을 거기 넣으면
                # `_refill_waiting` 을 내리는 자리가 다음 dispatch(`wait_interlock`)뿐이라
                # 그 dispatch 에 닿기도 전에 루프에 갇힌다.
                self.note = f'{reason or "REFILL"} 정지 — 보충 후 EXIT 로 재개'
                self._pub_state()
            return {'success': bool(r.success)}
        if k == 'move':
            self._move(self._station_of(req),
                       MoveToStation.Goal.AT if req.get('approach') == 'AT' else MoveToStation.Goal.ABOVE)
            return {'success': True, 'reached': self.station}
        if k == 'grip':
            if not req.get('close'):
                width = self.p('gripper.open_width_mm')
            elif req.get('target') == 'scoop':
                # 탐색 목표 폭(9/21) — 기대 폭(WRONG_TOOL 판정용)과 분리한다. 기대 폭을 명령폭으로
                # 쓰면 그보다 더 가는 손잡이는 접촉조차 못 해 GRIP_FAIL 로 빠지고 WRONG_TOOL 판정까지
                # 가지도 못한다(A 리뷰, PR #165). 원료 상관없이 확실히 더 좁게 명령해 항상 접촉시키고,
                # 실제로 닿아 멈춘 폭(final_width_mm)을 기대 폭과 비교하는 건 FSM 몫이다.
                width = self.p('gripper.scoop_search_width_mm')
            else:
                width = self.p('gripper.cup_width_mm')
            return self._grip(bool(req.get('close')), width)
        if k == 'carry':
            return self._carry(req)
        if k == 'scoop':
            r = self._call_act('scoop', Scoop.Goal(material_id=req['material_id'],
                                                   attempt=min(255, int(req.get('attempt', 1))),
                                                   depth_fraction=float(req.get('fraction', 1.0))))
            self._check('scoop', r.success, r.message)
            return {'contact_detected': bool(r.contact_detected),
                    'max_contact_force_n': float(r.max_contact_force_n),
                    'insertion_depth_mm': float(r.insertion_depth_mm)}
        if k == 'pour':
            r = self._call_act('pour', Pour.Goal(fraction=float(req.get('fraction', 1.0))))
            self._check('pour', r.success, r.message)
            return {'success': True}
        if k == 'return_material':
            r = self._call_act('return_material', ReturnMaterial.Goal(material_id=req['material_id']))
            self._check('return_material', r.success, r.message)
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
        try:
            self.event('INFO', 'BATCH_START', fsm.spec.product)
            verify_logged = False      # VERIFY 수치 이벤트는 배치당 한 번 (무효 재계량으로 여러 번 돌 수 있다)
            self._pub_state()
            self._check_batch_interrupt()
            req = fsm.start()
            while req is not None and rclpy.ok() and not self._stop.is_set():
                self._check_batch_interrupt()
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
                    # 안전 자세까지 실패하면 더 물러설 곳이 없으니 거기서 멈춘다. 로봇 안전 정지(v1.4)는
                    # 재시도해도 다시 거부될 뿐이고 사람 개입(RecoverSafety)이 필요하므로 FORCE_LIMIT 을
                    # 거치지 않고 바로 ERROR 로 끝낸다 (docs/interfaces.md 8절 — 일반 재시도 경로와 분리).
                    self._check_batch_interrupt()
                    if req['kind'] == 'safe' or self._safety_stop or self._execution_uncertain:
                        self._fail(f'안전 정지: {e}' if self._safety_stop else f'안전 자세 실패: {e}')
                        break
                    self.event('WARN', 'SKILL_FAIL', f'{step} {req["kind"]}: {e}')
                    req = fsm.skill_failed(req, str(e))
                    self._drain()
                    continue
                self._check_batch_interrupt()
                nxt = fsm.on_result(req, res)
                self._after(step, req, res)
                self._drain()
                if step == 'VERIFY' and fsm.verify_detail and not verify_logged:
                    # ② 폐지(9/22) 뒤에도 회계 수치는 남긴다. 판정은 ① 만 하지만, 끈 것이
                    # 「배치 기록 교차검증」이라 무엇을 포기했는지 감사 추적에서 보여야 한다.
                    verify_logged = True
                    self.event('INFO', 'VERIFY', fsm.verify_detail)
                self.event('INFO', 'STEP', f'{step} → {fsm.state}')
                req = nxt
            self._check_batch_interrupt()
        except BatchCancelled as e:
            self.note = str(e)
            fsm.state, fsm.mode = 'ABORTED', 'ERROR'
            self.event('WARN', 'BATCH_CANCELLED', self.note)
        except BatchSafetyStopped as e:
            self.note = str(e)
            fsm.state, fsm.mode = 'ERROR', 'ERROR'
            self.event('ERROR', 'BATCH_SAFETY_STOP', self.note)
        except Exception as e:                       # 전이표 밖 등 — 조용히 죽지 않는다
            self._fail(f'{type(e).__name__}: {e}')
        finally:
            try:
                # cancel/안전정지가 마지막 결과와 경합해도 완료 판정 전 다시 검사한다.
                with self._order_lock:
                    if self._execution_uncertain:
                        fsm.state, fsm.mode = 'ERROR', 'ERROR'
                        self.note = '스킬 종료 미확인 — 현장 확인 및 노드 재기동 필요'
                    elif self._batch_safety_stop.is_set():
                        fsm.state, fsm.mode = 'ERROR', 'ERROR'
                        self.note = 'SAFETY_STOP — 배치 자동 재개 없음'
                    elif self._batch_cancel.is_set() and not self._execution_uncertain:
                        fsm.state, fsm.mode = 'ABORTED', 'ERROR'
                        self.note = 'RunBatch 취소 — 배치 자동 재개 없음'
                    self._batch_outcome = (fsm.state if fsm.state in ('DONE', 'DISCARDED', 'ABORTED')
                                           else 'ERROR')
                    if self._batch_outcome == 'DONE' and fsm.verify_unmeasured:
                        # 완료품이지만 **최종 순량을 모른다** — QA 가 값 없이 승인했다 (#213).
                        # `result` 는 문자열 필드라 값을 늘려도 계약 변경이 아니다.
                        self._batch_outcome = 'DONE_UNMEASURED'
                    self._close_attempt('ABORTED')
                    self._drain()
                    self.event('INFO', 'BATCH_END', f'{fsm.mode} / {fsm.state}')
                    self._batch_done.set()
            except Exception as e:
                self._execution_uncertain = True
                self._batch_outcome = 'ERROR'
                fsm.state, fsm.mode = 'ERROR', 'ERROR'
                self.note = f'배치 종료 기록 실패: {e}'
                self.get_logger().error(self.note)
                self._batch_done.set()
            finally:
                with self._order_lock:
                    if self._batch_handle is None:  # SubmitOrder의 슬롯도 루프 종료까지 유지
                        self._reserved = False
                        self._reserved_recipe = None

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
        if step == 'RETURN_MATERIAL' and req['kind'] == 'return_material' and self._attempt:
            self._attempt._extra['return_requested'] = True

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
        elif step == 'RETURN_MATERIAL' and req['kind'] == 'return_material' and a is not None:
            # 반환 성공 뒤에만 닫는다. 실패는 _drain에서 RETURN_FAILED로 기록한다.
            self._close_attempt('RETURNED')
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
        return_failed = any(d['step'] == 'RETURN_MATERIAL' and d['action'] == 'FORCED' for d in new_devs)
        if return_failed:
            # 원료통 반환 자체가 실패하면 재시도·재투입 경로로 되돌리지 않는다.
            self._close_attempt('RETURN_FAILED')
        elif outcome:
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
        # 판정은 세 단계다 (v1.8, #108).
        #   ① 계량 무효로 **투입량을 모르면** INVALID — 목표와 비교할 수 있는 값이 아니다.
        #   ② `decide()` 가 낸 판정.
        #   ③ `decide()` 를 못 거쳤으면 같은 규칙으로 되매김한다 — 빈 verdict 를 'OK' 로
        #      떨어뜨리면 **목표 100 g · 실제 0 g · 오차 −100 % 인데 판정 OK** 가 나간다.
        # ③ 을 INVALID 로 합치지 않는 이유: `verdict` 가 비는 경우가 미측정만이 아니다.
        # 첫 사이클에서 반환 한도를 넘겨 TIMEOUT 이 나면 퍼낸 것을 **전부 되돌린 뒤**라
        # `actual_g` 0 이 참값이다 — 「안 들어갔다」지 「모른다」가 아니므로 INVALID 는 거짓이
        # 된다(실측 확인: TIMEOUT ×2 → verdict '' · unmeasured 0 · actual 0). UNDER 가 맞다.
        # target 0 은 `error_pct` 와 같은 방식으로 막는다.
        if r.unmeasured:
            m.verdict = DispenseResult.INVALID
        else:
            fallback = verdict_of(r.target_g, r.actual_g, r.tol_pct)[0] if r.target_g else 'OK'
            m.verdict = getattr(DispenseResult, r.verdict or fallback, DispenseResult.OK)
        m.attempts = min(255, int(r.attempts))
        m.duration_s = max(0.0, self._now() - self._item_t0) if self._item_t0 else 0.0
        self._item_t0 = 0.0
        self._last_result = deepcopy(m)
        self.pub_result.publish(m)
        if r.unmeasured:
            # verdict 는 위에서 INVALID 로 나갔다. 열거값은 「모른다」만 말하고 **몇 번 모르는지,
            # `actual_g` 를 왜 믿으면 안 되는지**는 못 말하므로 이벤트로 남긴다 —
            # `record_node` 가 배치 기록에 넣어 감사 추적이 된다.
            # #213 결정 3 이 WEIGH_RESIDUAL 무효를 QA 로 보내면서 이 경로가 처음 열렸다.
            self.event('WARN', 'DISPENSE_UNMEASURED',
                       f'{r.material_id}: 계량 무효 {r.unmeasured}회로 투입량 불확실 — '
                       f'verdict=INVALID. actual_g {m.actual_g:.1f} 은 미측정분이 빠진 값이라 '
                       f'실제보다 작고 목표와 비교할 수 없다 (#108)')

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
        # WeighHeld 결과가 실제로 측정한 material_N 위치를 갖는다. 고정 workbench 로
        # 기록하면 스쿱 계량 자세 변경 뒤 감사 기록이 거짓이 된다.
        m.weigh_pose_id = a.weigh_pose_id()
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
            return WeightReading(valid=False, subject='scoop', station='unknown')
        return WeightReading(gross_g=r.gross_g, tare_g=r.tare_g, net_g=r.net_g, std_g=r.std_g,
                             samples=r.samples, valid=r.valid, subject='scoop', station=r.station or 'unknown')

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
        # 실행 루프가 없는 유휴 상태에서도 주문 차단 사유를 HMI에 전달한다.
        idle = not self._reserved and not (self._thread and self._thread.is_alive())
        if idle and not self._safety_stop and not self._execution_uncertain:
            if self._pause:
                m.note = '구역 진입 요청 유지 — 인터락 EXIT 확인 후 새 주문 가능'
            elif self._nudge_paused:
                m.note = 'NUDGE 일시 정지 — 다시 건드리면 해제, 이후 새 주문 가능'
        self.pub_state.publish(m)
        handle = getattr(self, '_batch_handle', None)
        if handle is not None and handle.is_active and not self._batch_done.is_set():
            try:
                handle.publish_feedback(RunBatch.Feedback(state=m, last_result=deepcopy(self._last_result)))
            except Exception as e:
                # 연결이 끊긴 HMI로의 피드백 실패가 공정 재실행을 유발하지 않게 한다.
                self.get_logger().warning(f'RunBatch 피드백 전송 실패: {e}')


def main(args=None):
    rclpy.init(args=args)
    node = ProcessNode()
    ex = MultiThreadedExecutor(num_threads=6)
    ex.add_node(node)
    try:
        ex.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()          # 실행 루프를 먼저 세운다 — 죽은 노드로 발행하지 않게
        # ex.spin() 이 멈춘 뒤라 더 이상 응답을 받을 수 없다 — SafePose 응답을 기다리려면 직접 한 번 더 spin.
        # skill_node 의 _do_safe 가 compliance_off() 를 먼저 부르므로, 로봇이 Scoop 중 힘제어를 켠 채
        # 이 프로세스만 죽는 경우를 막는다. 같은 SIGINT 로 skill_node 도 동시에 죽고 있으면 이것만으론
        # 못 막는다 — 그 쪽 종료 훅은 gmp_skills 담당(A) 몫 (docs/todo.md, 9/21).
        try:
            fut = node.srv['safe'].call_async(SafePose.Request(reason='SHUTDOWN'))
            rclpy.spin_until_future_complete(node, fut, timeout_sec=5.0)
        except Exception:  # noqa: BLE001 — 종료 경로, 여기서 또 막히면 안 된다
            pass
        node.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
