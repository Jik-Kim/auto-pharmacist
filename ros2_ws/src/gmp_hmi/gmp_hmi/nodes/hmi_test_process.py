"""ROS 통신 확인 전용 공정 대역. 실제 로봇/도징/스킬 코드는 호출하지 않는다.

공식 gmp_interfaces 메시지·서비스·액션을 사용하며 /hmi_test에서만 시작한다.
숫자와 안전 자세는 시험용으로 생성된다. 이 노드는 실제 C의 구현 검증이 아니다.

단계 이름·순서·정지 사유 문구는 실제 공정(gmp_process process_fsm·process_node, 9/26 main + #287)을 따른다.
  SELF_CHECK → PICK_CONTAINER → TARE
  → 원료마다 PICK_SCOOP → SCOOP_TARE → (SCOOP → WEIGH_SCOOP → POUR → WEIGH_RESIDUAL) × 스쿱 수 → RETURN_SCOOP
  → VERIFY → FINISH → NUDGE_WAIT(사람이 건드릴 때까지 대기) → DONE
사람 접촉은 실제처럼 `event` 토픽의 code='NUDGE' 로 받는다 (skill_node 대역). 시험에서는 사람이
`ros2 topic pub --once -w 3 /hmi_test/event gmp_interfaces/msg/CellEvent "{code: NUDGE}"` 로 낸다.
  · 운전 중 NUDGE — 정지(PAUSED)·재개 토글
  · 세트 끝 NUDGE_WAIT 의 NUDGE — 다음 세트(배치 종료)
  · 유휴 중 NUDGE — 새 주문 차단 토글
원료 소진은 실제처럼 QA 가 아니다 — SCOOP_EMPTY 자동 재시도 3회 → MATERIAL_EMPTY 보충 대기(PAUSED) → EXIT 로 재개.
원료 높이(test_height)·개별 보충(test_refill_*)은 실제 계약이 없는 시험 전용 기능이다.
"""
import math
import json
import threading
import time
import uuid

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

from gmp_interfaces.action import RunBatch
from gmp_interfaces.msg import CellEvent, CellState, Deviation, DispenseResult, GripperState, ScoopCycle, WeightReading
from gmp_interfaces.srv import InterlockRequest, QaDecision
from std_msgs.msg import String
from std_srvs.srv import Trigger
from gmp_hmi.core.trial_inventory import TrialInventory

# overfill 시나리오의 과다 투입 배율. 허용오차(±10 %, D-33·D-35)를 확실히 넘어야 OVER 가 말이 된다 —
# 옛 1.10 은 ±5 % 시절 값이라 ±10 % 에서는 경계값이 된다.
OVERFILL_RATIO = 1.15
# 원료 밖 단계 하나(SELF_CHECK·PICK_CONTAINER·TARE·VERIFY·FINISH·이동)의 시험 시간 [s].
BATCH_STEP_S = 0.4
# gmp_process deviation.RULES['SCOOP_EMPTY'] 의 재시도 상한과 같게 둔다 — 넘으면 MATERIAL_EMPTY 보충 대기.
SCOOP_EMPTY_RETRIES = 3
EMPTY_SCOOP_G = 2.0      # common.yaml dosing.empty_scoop_g (#287) — 빈 스쿱 판정 문턱
EMPTY_SCOOP_NET_G = 0.4  # 시험에서 「빈 스쿱」으로 생성하는 순중량
RESIDUAL_G = 3.0         # 붓고 난 스쿱 잔량
CUP_G = 35.0             # 빈 약통 무게
# 원료 한 종에 속한 단계 — note 에 원료·목표량을 싣는다.
ITEM_STEPS = ('PICK_SCOOP', 'SCOOP_TARE', 'SCOOP', 'WEIGH_SCOOP', 'POUR', 'WEIGH_RESIDUAL', 'RETURN_SCOOP')


class HmiTestProcess(Node):
    def __init__(self):
        super().__init__('hmi_test_process')
        self.lock = threading.RLock()   # 타이머·서비스·액션·구독 콜백이 같은 상태를 바꾼다
        if self.get_namespace() != '/hmi_test':
            raise RuntimeError('통신 시험 노드는 /hmi_test 네임스페이스에서만 실행할 수 있습니다.')
        self.declare_parameter('scenario', 'normal')
        self.declare_parameter('item_duration_s', 3.0)
        self.declare_parameter('test_material_ids', ['A', 'B', 'C'])
        self.declare_parameter('test_capacity_g', [1000.0, 1000.0, 1000.0])
        self.declare_parameter('test_initial_g', [1000.0, 1000.0, 1000.0])
        self.declare_parameter('test_height_low_pct', 20.0)
        # SOT D-35 한 스쿱 79 g. 통신 시험 launch 도 같은 값을 넘긴다.
        self.declare_parameter('test_scoop_nominal_g', 79.0)
        self.test_scoop_nominal_g = float(self.get_parameter('test_scoop_nominal_g').value)
        if not math.isfinite(self.test_scoop_nominal_g) or self.test_scoop_nominal_g <= 0:
            raise ValueError('test_scoop_nominal_g는 양수여야 합니다')
        self.inventory = TrialInventory(
            self.get_parameter('test_material_ids').value,
            self.get_parameter('test_capacity_g').value,
            self.get_parameter('test_initial_g').value,
            self.get_parameter('test_height_low_pct').value)
        self.pub_inventory = self.create_publisher(
            String, 'test_inventory', QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        for mid in self.inventory.items:
            self.create_service(Trigger, 'test_refill_' + mid,
                                lambda req, res, material=mid: self._refill(material, req, res))
        self.create_subscription(String, 'test_height', self._height, 10)

        self.pub_state = self.create_publisher(
            CellState, 'state', QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.pub_weight = self.create_publisher(WeightReading, 'weight', 20)
        self.pub_result = self.create_publisher(DispenseResult, 'dispense_result', 50)
        self.pub_cycle = self.create_publisher(ScoopCycle, 'scoop_cycle', 50)
        self.pub_dev = self.create_publisher(
            Deviation, 'deviation', QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.pub_event = self.create_publisher(CellEvent, 'event', 100)
        self.pub_grip = self.create_publisher(
            GripperState, 'gripper_state', QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        # skill_node 의 NUDGE 대역 — 실제 process_node 도 같은 토픽에서 code='NUDGE' 를 받는다.
        self.create_subscription(CellEvent, 'event', self._on_event, 100)
        self.action_group = ReentrantCallbackGroup()
        self.run_batch = ActionServer(
            self, RunBatch, 'run_batch', execute_callback=self._execute_batch,
            goal_callback=self._goal_batch, cancel_callback=self._cancel_batch,
            callback_group=self.action_group)
        self.create_service(QaDecision, 'qa_decision', self._qa)
        self.create_service(InterlockRequest, 'interlock', self._interlock)
        self.mode, self.step, self.station = CellState.IDLE, 'IDLE', 'test_safe'
        self.batch_id, self.product, self.items = '', '', []
        self.index, self.elapsed = 0, 0.0
        self.note = 'ROS 통신 시험 대기 · 실제 로봇 연결 없음'
        self.active_scenario, self.item_duration = 'normal', 3.0
        self.plan = []                # 남은 단계 [{step, station, duration, done, index}]
        self.pending, self.qa_next = None, None
        self.previous = None          # 인터락 ENTER 전 (mode, step, station, note)
        self.refill_waiting = False   # MATERIAL_EMPTY 보충 대기 — EXIT 로 재개
        self.nudge_paused = False     # 사람 접촉으로 멈춤 (D-21). 다음 NUDGE 가 내린다
        self.nudge_saved = ''         # 접촉 정지 전 note
        self.nudge_waiting = False    # 세트 끝 NUDGE_WAIT (D-23)
        self.idle_nudge, self.idle_note = False, ''   # 유휴 중 접촉 — 새 주문 차단
        self.holding_scoop = False
        self.final_step, self.finish_result = 'DONE', 'DONE'
        self.items_done, self.deviation_count = 0, 0
        self.delivered_total, self.unmeasured = 0.0, []
        self.scoop_empty_count, self.cycle_attempts = 0, {}
        self.last_result = DispenseResult()
        self.batch_done = threading.Event()
        self._previous_tick = time.monotonic()
        self.create_timer(0.5, self._state)
        self.create_timer(0.1, self._tick)
        self._state()
        self.get_logger().info('시험 전용 /hmi_test: 로봇 호출 없음, 실제 안전 허가를 의미하지 않음')

    def _stamp(self, message):
        message.header.stamp = self.get_clock().now().to_msg()
        return message

    # ── 시험 재고 (실제 계약 없음) ────────────────────────────────────
    def _can_refill(self):
        return self.mode in (CellState.IDLE, CellState.DONE) or (
            self.mode == CellState.PAUSED and (self.previous is not None or self.refill_waiting)
            and self.station == 'test_safe')

    def _publish_inventory(self):
        self.pub_inventory.publish(String(data=json.dumps(self.inventory.snapshot(self._can_refill()), allow_nan=False)))

    def _height(self, message):
        with self.lock:
            self._height_locked(message)

    def _height_locked(self, message):
        try:
            data = json.loads(message.data)
            if not isinstance(data, dict):
                raise ValueError('시험 높이 JSON 객체가 필요합니다')
            self.inventory.report_height(data.get('material_id'), data.get('height_pct'))
        except (ValueError, TypeError) as exc:
            self._event('TEST_HEIGHT_REJECTED', str(exc), CellEvent.WARN)
            return
        blocked = self.inventory.blocked_materials
        self._event('TEST_HEIGHT_LOW' if blocked else 'TEST_HEIGHT_REPORTED',
                    'TEST_ONLY ' + json.dumps(data, ensure_ascii=False),
                    CellEvent.WARN if blocked else CellEvent.INFO)
        if blocked:
            self.note = '원료 높이 부족: ' + ', '.join(blocked) + ' · ENTER 후 해당 원료 만충 보충 확인 필요'
        self._state()

    def _refill(self, material_id, req, res):
        with self.lock:
            return self._refill_locked(material_id, req, res)

    def _refill_locked(self, material_id, req, res):
        if not self._can_refill():
            res.message = '시험 만충 보충 거부: 대기·종료 또는 ENTER·보충 대기로 정지한 상태에서만 가능합니다.'
            self._event('TEST_REFILL_REJECTED', res.message, CellEvent.WARN)
            return res
        try:
            added = self.inventory.refill(material_id)
        except ValueError as exc:
            res.message = str(exc)
            return res
        if not self.inventory.blocked_materials and not self.refill_waiting:
            self.note = ('시험 보충 완료 · EXIT 재개 요청 대기' if self.mode == CellState.PAUSED
                         else '시험 보충 완료 · 새 주문 가능 · 실제 장치 미연결')
        self._state()
        self._event('TEST_REFILL_COMPLETE', 'TEST_ONLY ' + json.dumps(dict(material_id=material_id, added_g=added), ensure_ascii=False))
        res.success, res.message = True, f'시험 원료 {material_id} 만충 보충 완료. 실제 장치 미연결 · 자동 재개하지 않습니다.'
        return res

    # ── 발행 ─────────────────────────────────────────────────────────
    def _state_message(self):
        return self._stamp(CellState(
            mode=self.mode, batch_id=self.batch_id, step=self.step,
            item_index=min(255, self.index), station=self.station, note=self.note))

    def _state(self):
        self._publish_inventory()
        self.pub_state.publish(self._state_message())

    def _event(self, code, text, level=CellEvent.INFO):
        self.pub_event.publish(self._stamp(CellEvent(
            batch_id=self.batch_id, code=code, text=text, level=level)))

    @staticmethod
    def _material_station(material_id):
        # 최신 stations.yaml ID와 화면 표시만 맞춘다. 좌표나 로봇 명령은 만들지 않는다.
        return {'A': 'material_1', 'B': 'material_2', 'C': 'material_3'}.get(material_id, 'test_material_unknown')

    @staticmethod
    def _scoop_station(material_id):
        return {'A': 'scoop_1', 'B': 'scoop_2', 'C': 'scoop_3'}.get(material_id, 'test_scoop_unknown')

    def _reading(self, gross_g, tare_g, subject, station, valid=True):
        return self._stamp(WeightReading(
            gross_g=float(gross_g), tare_g=float(tare_g), net_g=float(gross_g - tare_g),
            std_g=0.15 if valid else 99.0, samples=20, valid=valid, station=station, subject=subject))

    def _scoop_reading(self, net_g, material_id, valid=True):
        # 스쿱 계량은 원료통 위 material_N 자세에서 한다 (process_fsm._weigh_scoop, process_node 1115행).
        return self._reading(net_g, 0.0, 'scoop', self._material_station(material_id), valid)

    def _publish_weight(self, reading):
        self.pub_weight.publish(reading)
        return reading

    # ── 주문 ─────────────────────────────────────────────────────────
    def _validate_batch(self, recipe):
        if self.nudge_waiting:
            return False, '세트 완료 — 로봇을 건드리면 다음 주문을 받는다 (NUDGE_WAIT)', None
        if self.mode not in (CellState.IDLE, CellState.DONE, CellState.ERROR):
            return False, '시험 배치가 이미 실행 또는 대기 중입니다.', None
        if self.idle_nudge:
            return False, '구역 진입 / 일시 정지 중에는 새 주문을 받지 않습니다', None
        scenario = self.get_parameter('scenario').value
        # batch_out_of_spec 은 옛 verify_mismatch 를 대신한다 — VERIFY_MISMATCH 는 9/22 폐지라
        # 실제 FSM 이 내지 않는다(Deviation.msg:21). 배치 끝 규격 판정은 BATCH_OUT_OF_SPEC 이다.
        if scenario not in ('normal', 'overfill', 'batch_out_of_spec', 'wrong_tool',
                            'weigh_invalid', 'material_empty'):
            return False, ('scenario는 normal/overfill/batch_out_of_spec/wrong_tool/'
                           'weigh_invalid/material_empty를 지원합니다.'), None
        items = list(recipe.items)
        if not 1 <= len(items) <= 255:
            return False, '원료 1~255개가 필요합니다.', None
        ids = [item.material_id for item in items]
        if len(ids) != len(set(ids)) or any(not mid for mid in ids):
            return False, '원료 ID가 비어 있거나 중복되었습니다.', None
        if any(not math.isfinite(item.target_g) or item.target_g <= 0.0 or
               not math.isfinite(item.tol_pct) or item.tol_pct < 0.0 for item in items):
            return False, '목표량은 양수, 허용 오차는 0 이상이어야 합니다.', None
        duration = float(self.get_parameter('item_duration_s').value)
        if not math.isfinite(duration) or duration < 2.0:
            return False, '시험 item_duration_s는 2초 이상이어야 합니다.', None
        # 과다 투입 시나리오도 실제 생성할 시험량 전체를 미리 예약한다.
        requirements = {item.material_id: float(item.target_g * (
            OVERFILL_RATIO if scenario == 'overfill' and i == min(1, len(items) - 1) else 1.0))
            for i, item in enumerate(items)}
        if self.inventory.blocked_materials:
            return False, ('원료 높이 부족: ' + ', '.join(self.inventory.blocked_materials) +
                           ' · 해당 원료 만충 보충 완료가 필요합니다'), None
        for material_id, amount in requirements.items():
            stock = self.inventory.items.get(material_id)
            if stock is None:
                return False, f'시험 재고 미등록 원료: {material_id}', None
            if stock['remaining_g'] + 1e-8 < amount:
                return False, (f'시험 원료 부족: {material_id} 필요 {amount:g} g / '
                               f'잔량 {stock["remaining_g"]:g} g · 만충 보충 후 주문하세요'), None
        return True, '', (scenario, items, duration, requirements)

    def _goal_batch(self, goal_request):
        with self.lock:
            ok, message, _ = self._validate_batch(goal_request.recipe)
        if not ok:
            self._event('TEST_ORDER_REJECTED', message, CellEvent.WARN)
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    @staticmethod
    def _cancel_batch(_goal_handle):
        return CancelResponse.ACCEPT

    def _start_batch(self, recipe):
        with self.lock:
            ok, message, values = self._validate_batch(recipe)
            if not ok:
                self._event('TEST_ORDER_REJECTED', message, CellEvent.WARN)
                return False, message
            scenario, items, duration, requirements = values
            batch_id = recipe.batch_id or 'TEST-' + uuid.uuid4().hex[:12].upper()
            try:
                self.inventory.reserve(requirements, batch_id)
            except ValueError as exc:
                self._event('TEST_ORDER_REJECTED', str(exc), CellEvent.WARN)
                return False, str(exc)
            self.batch_id = batch_id
            self.product, self.items, self.active_scenario = recipe.product, items, scenario
            self.item_duration, self.index, self.elapsed = duration, 0, 0.0
            self.plan, self.pending, self.qa_next, self.previous = [], None, None, None
            self.refill_waiting = self.nudge_paused = self.nudge_waiting = self.holding_scoop = False
            self.final_step, self.finish_result = 'DONE', 'DONE'
            self.items_done, self.deviation_count = 0, 0
            self.delivered_total, self.unmeasured = 0.0, []
            self.scoop_empty_count, self.cycle_attempts = 0, {}
            self.last_result = DispenseResult()
            self.batch_done.clear()
            self._event('BATCH_START', self.product)
            self.mode = CellState.RUNNING
            self._add('SELF_CHECK', 'test_safe', BATCH_STEP_S)
            self._add('PICK_CONTAINER', 'passbox_empty', BATCH_STEP_S)
            self._add('TARE', 'workbench', BATCH_STEP_S, done=self._tare_done)
            self._enter()
            return True, '시험 배치 접수 완료. 실제 로봇은 움직이지 않습니다.'

    def _batch_result(self, success, result, message):
        return RunBatch.Result(
            success=success, items_done=self.items_done,
            deviations=self.deviation_count, result=result, message=message)

    def _execute_batch(self, goal_handle):
        ok, message = self._start_batch(goal_handle.request.recipe)
        if not ok:
            goal_handle.abort()
            return self._batch_result(False, 'ABORTED', message)
        # 실제 process_node 처럼 세트가 끝날 때(NUDGE_WAIT 뒤)까지 Goal 을 붙들고 Feedback 을 낸다.
        while not self.batch_done.wait(0.25):
            if goal_handle.is_cancel_requested:
                with self.lock:
                    self._abort('RunBatch 취소 — 배치 자동 재개 없음')
                goal_handle.canceled()
                return self._batch_result(False, 'ABORTED', self.note)
            goal_handle.publish_feedback(RunBatch.Feedback(
                state=self._state_message(), last_result=self.last_result))
        goal_handle.publish_feedback(RunBatch.Feedback(
            state=self._state_message(), last_result=self.last_result))
        goal_handle.succeed()
        return self._batch_result(
            self.finish_result in ('DONE', 'DONE_UNMEASURED'), self.finish_result,
            '시험 배치 종료 · ' + self.finish_result)

    def _abort(self, note):
        self.inventory.release()
        self.plan, self.pending, self.qa_next, self.previous = [], None, None, None
        self.refill_waiting = self.nudge_paused = self.nudge_waiting = self.holding_scoop = False
        self.mode, self.step, self.note = CellState.ERROR, 'ABORTED', note
        self.finish_result = 'ABORTED'
        self._event('BATCH_CANCELLED', note, CellEvent.WARN)
        self._state()
        self.batch_done.set()

    # ── 단계 계획 ─────────────────────────────────────────────────────
    def _add(self, step, station, duration, done=None, index=None, front=False):
        entry = dict(step=step, station=station, duration=duration, done=done,
                     index=self.index if index is None else index)
        if front:
            self.plan.insert(0, entry)
        else:
            self.plan.append(entry)

    def _enter(self):
        """다음 단계로 들어간다. 단계 이름이 바뀌면 실제처럼 STEP 이벤트를 남긴다."""
        if not self.plan:
            self._state()
            return
        current = self.plan[0]
        previous = self.step
        self.step, self.station, self.index = current['step'], current['station'], current['index']
        item = self.items[self.index] if self.items and self.index < len(self.items) else None
        self.note = (f'시험 {item.material_id} · 목표 {item.target_g:g} g · 생성 계량값'
                     if item is not None and self.step in ITEM_STEPS else f'시험 {self.step} · 실제 로봇 연결 없음')
        if previous != self.step:
            self._event('STEP', f'{previous} → {self.step}')
        self._state()

    def _resume_running(self):
        """사람 대기(QA·EXIT)가 끝나 진행할 차례. 그 사이 접촉이 있었으면 실제 _gate 처럼 NUDGE 로 멈춘다."""
        if self.nudge_paused:
            self.mode = CellState.PAUSED
            self.nudge_saved = ''
            self.note = 'NUDGE 정지 — 다시 건드리면 재개'
            self._event('PAUSE', self.note, CellEvent.WARN)
            self._state()
            return
        self.mode = CellState.RUNNING
        self._enter()

    def _tare_done(self):
        # 빈 약통을 tare 0 으로 들어 잰다 — 순량이 곧 약통 무게다 (process_fsm TARE).
        self._publish_weight(self._reading(CUP_G, 0.0, 'container', 'workbench'))
        self._plan_item(0)

    def _plan_item(self, i):
        item = self.items[i]
        mid = item.material_id
        overfill = self.active_scenario == 'overfill' and i == min(1, len(self.items) - 1)
        actual = float(item.target_g * (OVERFILL_RATIO if overfill else 1.0))
        portions, left = [], actual
        while left > 1e-9:
            portions.append(min(self.test_scoop_nominal_g, left))
            left -= portions[-1]
        per_step = self.item_duration / (3 + 4 * len(portions))
        scoop_st, material_st = self._scoop_station(mid), self._material_station(mid)
        self._add('PICK_SCOOP', scoop_st, per_step, done=lambda: self._pick_scoop_done(i), index=i)
        self._add('SCOOP_TARE', material_st, per_step,
                  done=lambda: self._publish_weight(self._scoop_reading(0.0, mid)), index=i)
        before = 0.0
        for attempt, portion in enumerate(portions, 1):
            last = attempt == len(portions)
            self._add('SCOOP', material_st, per_step, index=i)
            self._add('WEIGH_SCOOP', material_st, per_step,
                      done=lambda p=portion: self._weigh_scoop_done(i, p, per_step), index=i)
            self._add('POUR', 'workbench', per_step, index=i)
            self._add('WEIGH_RESIDUAL', material_st, per_step,
                      done=lambda a=attempt, p=portion, b=before, e=last: self._weigh_residual_done(
                          i, a, p, b, e, actual, len(portions), overfill), index=i)
            before += portion
        self._add('RETURN_SCOOP', scoop_st, per_step, done=lambda: self._return_scoop_done(i), index=i)

    def _pick_scoop_done(self, i):
        self.holding_scoop = True
        if self.active_scenario == 'wrong_tool' and i == 0:
            # 스쿱 폭 지문 불일치 — 스쿱을 쥔 채 QA 로 간다 (process_fsm PICK_SCOOP).
            self._deviation(Deviation.WRONG_TOOL, 'PICK_SCOOP · QA · 1회 · 스쿱 폭 지문 불일치 시험',
                            approve=self._resume_running)

    def _next_cycle_attempt(self, material_id):
        # ScoopCycle.attempt 는 process_node 가 원료별로 따로 센다 — 빈 스쿱 재시도도 한 번으로 센다.
        self.cycle_attempts[material_id] = self.cycle_attempts.get(material_id, 0) + 1
        return self.cycle_attempts[material_id]

    def _weigh_scoop_done(self, i, portion, per_step):
        item = self.items[i]
        if self.active_scenario == 'material_empty' and i == 0:
            self._scoop_empty(i, portion, per_step)
            return
        self._publish_weight(self._scoop_reading(portion + RESIDUAL_G, item.material_id))

    def _scoop_empty(self, i, portion, per_step):
        """빈 스쿱 — 순중량이 문턱 이하. 3회까지는 같은 스쿱을 자동 재시도, 그다음은 보충 대기 (#287·#111 A안)."""
        item = self.items[i]
        mid = item.material_id
        self._publish_weight(self._scoop_reading(EMPTY_SCOOP_NET_G, mid))
        self._cycle(item, 0.0, self._next_cycle_attempt(mid), 0.0, outcome=ScoopCycle.SCOOP_EMPTY, valid=False,
                    net_pre=EMPTY_SCOOP_NET_G, net_post=EMPTY_SCOOP_NET_G)
        self.scoop_empty_count += 1
        why = f'퍼낸 순중량 {EMPTY_SCOOP_NET_G:g} g ≤ 빈 스쿱 문턱 {EMPTY_SCOOP_G:g} g'
        material_st = self._material_station(mid)
        # 같은 스쿱을 다시 푼다 — 보충 대기 뒤에도 같은 자리에서 이어간다
        self._add('WEIGH_SCOOP', material_st, per_step,
                  done=lambda: self._weigh_scoop_done(i, portion, per_step), index=i, front=True)
        self._add('SCOOP', material_st, per_step, index=i, front=True)
        if self.scoop_empty_count <= SCOOP_EMPTY_RETRIES:
            self._auto_deviation(Deviation.SCOOP_EMPTY, f'SCOOP · RETRY · {self.scoop_empty_count}회 · {why}')
            return
        self._auto_deviation(Deviation.MATERIAL_EMPTY,
                             f'SCOOP · REFILL · 1회 · 재시도 {SCOOP_EMPTY_RETRIES}회 뒤에도 빈 스쿱 — {why} — 보충 필요')
        # 보충 뒤에는 정상으로 퍼지게 한다 — 시험은 사람이 원료를 채웠다고 본다.
        self.active_scenario = 'normal'
        self.refill_waiting = True
        self.mode, self.step, self.station = CellState.PAUSED, 'PAUSED', 'test_safe'
        # HMI 는 note 앞머리로 정지 사유를 가른다 (pause_context `^REFILL\b`, process_node 793행과 같은 문구).
        self.note = 'REFILL 정지 — 보충 후 EXIT 로 재개'
        self._state()

    def _weigh_residual_done(self, i, attempt, portion, before, last, actual, attempts, overfill):
        item = self.items[i]
        mid = item.material_id
        if self.active_scenario == 'weigh_invalid' and i == 0 and attempt == 1:
            # 재계량 상한을 넘긴 무효 계량 → WEIGH_INVALID(QA). 투입량을 모르므로 재고도 차감하지 않는다.
            # 실패한 시도도 ScoopCycle 로 남긴다 — 투입량 0, valid=false (계약 outcome 2).
            self._publish_weight(self._scoop_reading(12.0, mid, valid=False))
            self._cycle(item, 0.0, self._next_cycle_attempt(mid), before, outcome=ScoopCycle.WEIGH_INVALID,
                        valid=False, net_pre=portion + RESIDUAL_G, net_post=12.0)
            self._deviation(Deviation.WEIGH_INVALID, 'WEIGH_RESIDUAL · QA · 1회 · 스쿱 계량 무효 반복 시험',
                            approve=lambda: self._approve_unmeasured(i))
            return
        self._publish_weight(self._scoop_reading(RESIDUAL_G, mid))
        self._cycle(item, portion, self._next_cycle_attempt(mid), before,
                    net_pre=portion + RESIDUAL_G, net_post=RESIDUAL_G)
        if not last:
            return
        self.inventory.consume(mid, actual)
        self._publish_inventory()
        self.delivered_total += actual
        self.last_result = self._stamp(DispenseResult(
            batch_id=self.batch_id, material_id=mid,
            target_g=float(item.target_g), actual_g=actual,
            error_pct=float((actual - item.target_g) / item.target_g * 100.0),
            verdict=DispenseResult.OVER if overfill else DispenseResult.OK,
            attempts=attempts, duration_s=float(self.item_duration)))
        self.pub_result.publish(self.last_result)
        self.items_done += 1
        if overfill:
            self._deviation(Deviation.OVERFILL, 'WEIGH_RESIDUAL · QA · 1회 · 원료 10% 초과 투입 시험',
                            approve=self._resume_running)

    def _approve_unmeasured(self, i):
        """계량을 못 믿는 채 QA 가 승인 → 이 원료는 투입량을 모른다 (계약 v1.8, #213).

        실제 공정은 이때 누적 투입량에 아무것도 더하지 않는다 — 0 을 더하는 것과 다르다.
        남은 스쿱은 건너뛰고 스쿱을 반납한다 (process_fsm _after_qa → RETURN_SCOOP).
        """
        item = self.items[i]
        self.unmeasured.append(item.material_id)
        self.last_result = self._stamp(DispenseResult(
            batch_id=self.batch_id, material_id=item.material_id,
            target_g=float(item.target_g), actual_g=0.0, error_pct=0.0,
            verdict=DispenseResult.INVALID, attempts=1,
            duration_s=float(self.item_duration)))
        self.pub_result.publish(self.last_result)
        self.items_done += 1
        self._event('DISPENSE_UNMEASURED',
                    f'TEST_ONLY {item.material_id}: 계량 무효로 투입량 불확실 — verdict=INVALID', CellEvent.WARN)
        while self.plan and self.plan[0]['step'] != 'RETURN_SCOOP':
            self.plan.pop(0)
        self._resume_running()

    def _return_scoop_done(self, i):
        self.holding_scoop = False
        if i + 1 < len(self.items):
            self._plan_item(i + 1)
            return
        self._add('VERIFY', 'workbench', BATCH_STEP_S, done=self._verify_done, index=i)

    def _verify_done(self):
        out_of_spec = self.active_scenario == 'batch_out_of_spec'
        net = self.delivered_total + (50.0 if out_of_spec else 0.0)
        self._publish_weight(self._reading(net + CUP_G, CUP_G, 'container', 'workbench'))
        total = sum(float(item.target_g) for item in self.items)
        tol = sum(float(item.target_g) * float(item.tol_pct) / 100.0 for item in self.items)
        detail = f'net {net:.1f} g · ①규격 {net - total:+.1f}/{tol:.1f} · TEST_ONLY'
        self._event('VERIFY', detail)
        if out_of_spec:
            self._deviation(Deviation.BATCH_OUT_OF_SPEC, 'VERIFY · QA · 1회 · ' + detail,
                            approve=self._plan_finish, material_id='')
            return
        self._plan_finish(resume=False)

    def _plan_finish(self, resume=True):
        self._add('FINISH', 'passbox_done', BATCH_STEP_S)
        self._plan_park('DONE')
        if resume:
            self._resume_running()

    def _plan_park(self, final):
        """세트 끝 — nudge_wait 로 물러나 NUDGE 를 기다린다. 폐기도 세트의 끝이다 (process_fsm _park)."""
        self.final_step = final
        self._add('NUDGE_WAIT', 'nudge_wait', BATCH_STEP_S, done=self._nudge_wait_arrived)

    def _nudge_wait_arrived(self):
        self.nudge_waiting = True
        self.mode = CellState.PAUSED   # 로봇은 섰다 — 주문은 거부, HMI 는 사유를 본다
        self.note = 'NUDGE_WAIT — 세트 완료, 건드리면 다음 세트'
        self._event('SET_DONE', self.note)
        self._state()

    def _discard(self):
        """QA 폐기 — 스쿱을 들고 있으면 먼저 반납하고 용기째 폐기함으로 옮긴 뒤 세트 끝으로 간다.

        실제 process_fsm 은 폐기 판정 즉시 mode=DONE·state=DISCARDED 를 내고 반송·NUDGE_WAIT 로 간다.
        시험은 record_node·HMI 가 전제하는 「물리 종료 뒤 DONE」을 지켜 반송 동안 RUNNING 으로 둔다.
        """
        self.plan = []
        if self.holding_scoop and self.items:
            mid = self.items[self.index].material_id
            self._add('RETURN_SCOOP', self._scoop_station(mid), BATCH_STEP_S,
                      done=lambda: setattr(self, 'holding_scoop', False))
        self._add('DISCARDED', 'reject_bin', BATCH_STEP_S)
        self._plan_park('DISCARDED')
        self._resume_running()

    def _complete(self):
        """NUDGE 로 세트가 끝났다. 폐기가 아니고 미측정 원료가 있으면 DONE_UNMEASURED 다 (D-32)."""
        outcome = self.final_step
        if outcome == 'DONE' and self.unmeasured:
            outcome = 'DONE_UNMEASURED'
            self._event('BATCH_UNMEASURED', f'TEST_ONLY 미측정 원료 {self.unmeasured}', CellEvent.WARN)
        self.inventory.release()  # 미사용 예약만 해제. 이미 소비한 양은 폐기해도 복구하지 않는다.
        self.finish_result = outcome
        self.mode, self.step = CellState.DONE, self.final_step
        self.note = '시험 배치 종료 · ' + outcome
        self._event('BATCH_END', f'DONE / {self.final_step} / {outcome}')
        self._state()
        self.batch_done.set()

    # ── 기록 ─────────────────────────────────────────────────────────
    def _cycle(self, item, delivered, attempt, actual_before, outcome=None, valid=True,
               net_pre=0.0, net_post=0.0):
        mid = item.material_id
        cycle = self._stamp(ScoopCycle(
            batch_id=self.batch_id, material_id=mid, attempt=attempt,
            target_g=float(item.target_g), actual_before_g=float(actual_before),
            scoop_tare=self._scoop_reading(0.0, mid),
            pre_pour=self._scoop_reading(net_pre, mid),
            post_pour=self._scoop_reading(net_post, mid, valid=valid or outcome != ScoopCycle.WEIGH_INVALID),
            commanded_pour_fraction=1.0,   # 전량 붓기 (계약 v1.3)
            delivered_g=float(delivered), weigh_method=ScoopCycle.WEIGH_METHOD_TOOL_FORCE,
            weigh_pose_id=self._material_station(mid), tool_name='tool_weight', tcp_name='GripperDA_v1',
            # 고정 티칭 경로는 힘을 재지 않는다 — false 는 「안 닿았다」가 아니라 「안 재봤다」다 (D-34, #277).
            contact_detected=False, max_contact_force_n=0.0, insertion_depth_mm=0.0,
            grip_width_mm=32.4, valid=bool(valid),
            outcome=ScoopCycle.COMPLETE if outcome is None else outcome,
            duration_s=float(self.item_duration / 3.0)))
        for name in ('tare', 'pre_pour', 'post_pour'):
            setattr(cycle, name + '_wrench', [0.0, 0.0, -1.0, 0.0, 0.0, 0.0])
            setattr(cycle, name + '_wrench_std', [0.01] * 6)
            setattr(cycle, name + '_wrench_samples', 20)
            setattr(cycle, name + '_wrench_valid', True)
        # 독립 실측 정답은 생성하지 않는다.
        cycle.reference_valid = False
        self.pub_cycle.publish(cycle)

    def _new_deviation(self, kind, detail, requires_decision, decision, material_id=None):
        self.deviation_count += 1
        deviation = Deviation(
            deviation_id=f'D-{self.batch_id}-{self.deviation_count}', batch_id=self.batch_id,
            material_id=(self.items[self.index].material_id if material_id is None else material_id),
            kind=kind, detail='TEST_ONLY ' + detail, requires_decision=requires_decision,
            decision=decision, operator_id='')
        self.pub_dev.publish(self._stamp(deviation))
        self._event('DEVIATION', f'{deviation.deviation_id} {kind}', CellEvent.WARN)
        return deviation

    def _auto_deviation(self, kind, detail):
        # RETRY·REFILL 은 로봇이 스스로 넘어가는 것 → AUTO_RECOVERED (process_node _publish_deviation).
        self._new_deviation(kind, detail, False, Deviation.AUTO_RECOVERED)

    def _deviation(self, kind, detail, approve, material_id=None):
        self.pending = self._new_deviation(kind, detail, True, Deviation.PENDING, material_id)
        self.qa_next = approve
        self.mode, self.step = CellState.DEVIATION, 'DEVIATION'
        self.note = '시험 일탈 · QA 승인 또는 폐기 대기'
        self._state()

    # ── 사람 입력 ─────────────────────────────────────────────────────
    def _qa(self, req, res):
        with self.lock:
            if self.inventory.blocked_materials:
                res.message = '원료 높이 부족: 보충 완료 후 QA 판정을 진행하세요'
                return res
            if self.mode != CellState.DEVIATION or self.pending is None:
                res.message = '시험에서 판정을 기다리는 일탈이 없습니다.'
                return res
            if req.deviation_id != self.pending.deviation_id:
                res.message = '배치 또는 일탈 ID가 현재 대기 건과 다릅니다.'
                return res
            if req.decision not in (Deviation.APPROVED, Deviation.DISCARDED) or not req.operator_id.strip():
                res.message = '승인/폐기 판정과 판정자 ID가 필요합니다.'
                return res
            self.pending.decision = req.decision
            self.pending.operator_id = req.operator_id.strip()
            self.pending.requires_decision = False
            self.pub_dev.publish(self._stamp(self.pending))   # 같은 deviation_id 로 재발행 → record 가 upsert
            approve, self.pending, self.qa_next = self.qa_next, None, None
            if req.decision == Deviation.APPROVED:
                approve()
            else:
                self._discard()
            res.accepted, res.message = True, '시험 QA 판정을 반영했습니다.'
            return res

    def _interlock(self, req, res):
        with self.lock:
            if req.request == getattr(InterlockRequest.Request, 'ENTER', 1):
                return self._enter_request(req, res)
            if req.request == getattr(InterlockRequest.Request, 'EXIT', 2):
                return self._exit_request(req, res)
            res.message = 'ENTER(1) 또는 EXIT(2)가 필요합니다.'
            return res

    def _enter_request(self, req, res):
        if self.refill_waiting or self.previous is not None:
            # 이미 안전 자세로 가서 기다리는 중 — 멱등하게 받는다 (process_node _srv_interlock).
            res.granted, res.message = True, '이미 대기 중 (안전 자세)'
            return res
        nudge_stop = self.mode == CellState.PAUSED and self.nudge_paused
        if self.mode not in (CellState.RUNNING, CellState.DEVIATION) and not nudge_stop:
            res.message = '시험 운전·QA 대기·접촉 정지 중에만 ENTER 가능합니다.'
            return res
        self.previous = (self.mode, self.step, self.station, self.note)
        # 가상 안전 자세 이동 완료 후에만 응답한다. 실제 하드웨어 안전 검증은 아니다.
        # 단계 이름은 실제 FSM 과 같게 둔다 — 실제는 이동 중에도 단계를 바꾸지 않는다.
        self.station = 'test_safe'
        self._state()
        time.sleep(0.3)
        self._previous_tick = time.monotonic()
        self.mode, self.step = CellState.PAUSED, 'PAUSED'
        # HMI 는 note 앞머리 「인터락 ENTER」로 정지 사유를 가른다 (pause_context).
        self.note = f'인터락 ENTER ({req.reason or "-"}) · 시험 안전 위치 가정 · 실제 구역 진입 허가 아님'
        self._event('INTERLOCK_ENTER', 'TEST_ONLY ' + req.reason, CellEvent.WARN)
        self._state()
        res.granted, res.message = True, '통신 시험: 가상 안전 이동 완료. 실제 진입 허가 아님.'
        return res

    def _exit_request(self, req, res):
        if self.inventory.blocked_materials:
            res.message = '원료 높이 부족: ' + ', '.join(self.inventory.blocked_materials) + ' · 해당 원료 보충 완료 전에는 재개할 수 없습니다'
            return res
        if self.refill_waiting:
            self.refill_waiting = False
            self.previous = None
            self._event('INTERLOCK_EXIT', 'TEST_ONLY ' + req.reason)
            self._resume_running()
            res.granted, res.message = True, 'resume'
            return res
        if self.mode != CellState.PAUSED or self.previous is None:
            # 아무도 안 기다리는데 EXIT — 실제처럼 무시하고 수락 응답만 한다.
            res.granted, res.message = True, '대기 중이 아니다 (무시)'
            return res
        mode, self.step, self.station, note = self.previous
        self.previous = None
        self._event('INTERLOCK_EXIT', 'TEST_ONLY ' + req.reason)
        if mode == CellState.RUNNING:
            self._resume_running()
        else:
            self.mode, self.note = mode, ('시험 보충 완료 · 이전 단계 재개' if '원료 높이 부족:' in note else note)
            self._state()
        res.granted, res.message = True, '통신 시험: 일시정지 이전 단계로 복귀했습니다.'
        return res

    def _on_event(self, message):
        if message.code != 'NUDGE':
            return
        with self.lock:
            self._nudge()

    def _nudge(self):
        """사람 접촉 (D-21·D-23). 실제 process_node._on_event 와 같은 세 갈래."""
        if self.nudge_waiting:
            # 세트 끝 대기 — 이 접촉은 「다음 세트」 신호다
            self.nudge_waiting = False
            self._event('SET_NEXT', '사람이 건드림 — 세트 종료, 다음 주문을 받는다')
            self._complete()
            return
        if self.mode in (CellState.IDLE, CellState.DONE, CellState.ERROR):
            # 유휴 중 접촉 — 다시 건드릴 때까지 새 주문을 받지 않는다
            self.idle_nudge = not self.idle_nudge
            if self.idle_nudge:
                self.idle_note = self.note
                self.note = 'NUDGE 일시 정지 — 다시 건드리면 해제, 이후 새 주문 가능'
            else:
                self.note = self.idle_note
            self._state()
            return
        if self.mode == CellState.RUNNING:
            self.nudge_paused = True
            self.nudge_saved = self.note
            self.mode = CellState.PAUSED
            self.note = 'NUDGE 정지 — 다시 건드리면 재개'
            self._event('PAUSE', self.note, CellEvent.WARN)
            self._state()
            return
        if (self.nudge_paused and self.mode == CellState.PAUSED and
                self.previous is None and not self.refill_waiting):
            self.nudge_paused = False
            self.mode = CellState.RUNNING
            self.note = self.nudge_saved or self.note
            self._event('RESUME', 'NUDGE 해제')
            self._state()
            return
        # QA 대기·인터락·보충 대기 중 — 표시만 토글하고, 진행할 차례가 되면 _resume_running 이 잡는다.
        self.nudge_paused = not self.nudge_paused

    # ── 시간 진행 ─────────────────────────────────────────────────────
    def _tick(self):
        now = time.monotonic()
        dt, self._previous_tick = min(now - self._previous_tick, 0.5), now
        self.pub_grip.publish(self._stamp(GripperState(
            width_mm=32.4, busy=self.mode == CellState.RUNNING,
            grip_inferred=self.holding_scoop or self.mode == CellState.DEVIATION,
            safety_triggered=False, force_cmd_n=20.0, backend='virtual')))
        with self.lock:
            if (self.mode != CellState.RUNNING or self.inventory.blocked_materials or
                    self.nudge_paused or not self.plan):
                return
            self.elapsed += dt
            current = self.plan[0]
            if self.elapsed + 1e-9 < current['duration']:
                return
            self.plan.pop(0)
            self.elapsed = 0.0
            if current['done'] is not None:
                current['done']()
            if self.mode == CellState.RUNNING:
                self._enter()


def main(args=None):
    rclpy.init(args=args)
    node = None
    executor = None
    try:
        node = HmiTestProcess()
        executor = MultiThreadedExecutor(num_threads=4)
        executor.add_node(node)
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        if executor is not None:
            executor.shutdown()
        if node is not None:
            node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
