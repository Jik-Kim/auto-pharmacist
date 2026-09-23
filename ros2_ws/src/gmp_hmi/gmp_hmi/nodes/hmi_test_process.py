"""ROS 통신 확인 전용 공정 대역. 실제 로봇/도징/스킬 코드는 호출하지 않는다.

공식 gmp_interfaces 메시지·서비스·액션을 사용하며 /hmi_test에서만 시작한다.
숫자와 안전 자세는 시험용으로 생성된다. 이 노드는 실제 C의 구현 검증이 아니다.
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


class HmiTestProcess(Node):
    def __init__(self):
        super().__init__('hmi_test_process')
        if self.get_namespace() != '/hmi_test':
            raise RuntimeError('통신 시험 노드는 /hmi_test 네임스페이스에서만 실행할 수 있습니다.')
        self.declare_parameter('scenario', 'normal')
        self.declare_parameter('item_duration_s', 3.0)
        self.declare_parameter('test_material_ids', ['A', 'B', 'C'])
        self.declare_parameter('test_capacity_g', [1000.0, 1000.0, 1000.0])
        self.declare_parameter('test_initial_g', [1000.0, 1000.0, 1000.0])
        self.declare_parameter('test_height_low_pct', 20.0)
        self.declare_parameter('test_scoop_nominal_g', 40.0)
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
        self.action_group = ReentrantCallbackGroup()
        self.run_batch = ActionServer(
            self, RunBatch, 'run_batch', execute_callback=self._execute_batch,
            goal_callback=self._goal_batch, cancel_callback=self._cancel_batch,
            callback_group=self.action_group)
        self.create_service(QaDecision, 'qa_decision', self._qa)
        self.create_service(InterlockRequest, 'interlock', self._interlock)
        self.mode, self.step, self.station = CellState.IDLE, 'IDLE', 'test_safe'
        self.batch_id, self.product, self.items = '', '', []
        self.index, self.elapsed, self.last_weight = 0, 0.0, -1.0
        self.note = 'ROS 통신 시험 대기 · 실제 로봇 연결 없음'
        self.phase, self.active_scenario, self.pending = 'idle', 'normal', None
        self.item_duration, self.finish_result = 3.0, 'DONE'
        self.items_done, self.deviation_count = 0, 0
        self.last_result = DispenseResult()
        self.batch_done = threading.Event()
        self.previous = None
        self._previous_tick = time.monotonic()
        self.create_timer(0.5, self._state)
        self.create_timer(0.1, self._tick)
        self._state()
        self.get_logger().info('시험 전용 /hmi_test: 로봇 호출 없음, 실제 안전 허가를 의미하지 않음')

    def _stamp(self, message):
        message.header.stamp = self.get_clock().now().to_msg()
        return message

    def _can_refill(self):
        return self.mode in (CellState.IDLE, CellState.DONE) or (
            self.mode == CellState.PAUSED and self.previous is not None and self.station == 'test_safe')

    def _publish_inventory(self):
        self.pub_inventory.publish(String(data=json.dumps(self.inventory.snapshot(self._can_refill()), allow_nan=False)))

    def _height(self, message):
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
        if not self._can_refill():
            res.message = '시험 만충 보충 거부: 대기·종료 또는 ENTER로 정지한 상태에서만 가능합니다.'
            self._event('TEST_REFILL_REJECTED', res.message, CellEvent.WARN)
            return res
        try:
            added = self.inventory.refill(material_id)
        except ValueError as exc:
            res.message = str(exc)
            return res
        if not self.inventory.blocked_materials:
            self.note = ('시험 보충 완료 · EXIT 재개 요청 대기' if self.mode == CellState.PAUSED
                         else '시험 보충 완료 · 새 주문 가능 · 실제 장치 미연결')
        self._state()
        self._event('TEST_REFILL_COMPLETE', 'TEST_ONLY ' + json.dumps(dict(material_id=material_id, added_g=added), ensure_ascii=False))
        res.success, res.message = True, f'시험 원료 {material_id} 만충 보충 완료. 실제 장치 미연결 · 자동 재개하지 않습니다.'
        return res

    def _state_message(self):
        return self._stamp(CellState(
            mode=self.mode, batch_id=self.batch_id, step=self.step,
            item_index=self.index, station=self.station, note=self.note))

    def _state(self):
        self._publish_inventory()
        self.pub_state.publish(self._state_message())

    def _event(self, code, text, level=CellEvent.INFO):
        self.pub_event.publish(self._stamp(CellEvent(
            batch_id=self.batch_id, code=code, text=text, level=level)))

    def _validate_batch(self, recipe):
        if self.mode not in (CellState.IDLE, CellState.DONE):
            return False, '시험 배치가 이미 실행 또는 대기 중입니다.', None
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
            1.10 if scenario == 'overfill' and i == min(1, len(items) - 1) else 1.0))
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
        ok, message, _ = self._validate_batch(goal_request.recipe)
        if not ok:
            self._event('TEST_ORDER_REJECTED', message, CellEvent.WARN)
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    @staticmethod
    def _cancel_batch(_goal_handle):
        return CancelResponse.ACCEPT

    def _start_batch(self, recipe):
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
        self.item_duration, self.index = duration, 0
        self.pending, self.previous = None, None
        self.items_done, self.deviation_count = 0, 0
        self.last_result = DispenseResult()
        self.batch_done.clear()
        self.delivered_total = 0.0
        self.unmeasured_done = False   # 이 배치에 투입량 미측정 승인이 있었나 (D-32)
        self.mode, self.step, self.station = CellState.RUNNING, 'SELF_CHECK', 'test_safe'
        self.phase, self.elapsed, self.last_weight = 'start', 0.0, -1.0
        self.note = f'시험 주문 접수 · {scenario} · 실제 로봇 연결 없음'
        self._state()
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
        while not self.batch_done.wait(0.25):
            if goal_handle.is_cancel_requested:
                self.inventory.release()
                self.mode, self.step, self.phase = CellState.ERROR, 'ERROR', 'idle'
                self.finish_result = 'ABORTED'
                self.note = '시험 RunBatch 취소'
                self._state()
                goal_handle.canceled()
                return self._batch_result(False, 'ABORTED', self.note)
            goal_handle.publish_feedback(RunBatch.Feedback(
                state=self._state_message(), last_result=self.last_result))
        goal_handle.publish_feedback(RunBatch.Feedback(
            state=self._state_message(), last_result=self.last_result))
        goal_handle.succeed()
        return self._batch_result(
            self.finish_result == 'DONE', self.finish_result,
            '시험 배치 종료 · ' + self.finish_result)

    def _qa(self, req, res):
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
        self.pub_dev.publish(self._stamp(self.pending))
        self._event('QA_APPROVED' if req.decision == Deviation.APPROVED else 'QA_DISCARDED', req.operator_id)
        self.pending = None
        self.mode = CellState.RUNNING
        if req.decision == Deviation.APPROVED:
            if self.active_scenario == 'batch_out_of_spec':
                self._finish('DONE')
            elif self.active_scenario in ('wrong_tool', 'material_empty'):
                self.phase, self.elapsed = 'item', 0.0
                self.active_scenario = 'normal'
            elif self.active_scenario == 'weigh_invalid':
                # 계량을 못 믿는 채 QA 가 승인했다 → 이 원료는 투입량을 모른다 (계약 v1.8, #213).
                self._invalid_result(self.items[self.index])
                self.active_scenario = 'normal'
                self._next_item()
            else:
                self._next_item()
        else:
            self._finish('DISCARDED')
        self._state()
        res.accepted, res.message = True, '시험 QA 판정을 반영했습니다.'
        return res

    def _interlock(self, req, res):
        if req.request == getattr(InterlockRequest.Request, 'ENTER', 1):
            if self.mode not in (CellState.RUNNING, CellState.DEVIATION):
                res.message = '시험 운전 또는 QA 대기 중에만 ENTER 가능합니다.'
                return res
            self.previous = (self.mode, self.step, self.station, self.note)
            # 가상 안전 자세 이동 완료 후에만 응답한다. 실제 하드웨어 안전 검증은 아니다.
            self.step, self.station = 'MOVING_TO_SAFE', 'test_moving'
            self._state()
            time.sleep(0.3)
            self._previous_tick = time.monotonic()
            self.mode, self.step, self.station = CellState.PAUSED, 'INTERLOCK', 'test_safe'
            self.note = '시험 안전 위치를 가정한 일시정지 · 실제 구역 진입 허가 아님'
            self._event('INTERLOCK_ENTER', 'TEST_ONLY ' + req.reason)
            self._state()
            res.granted, res.message = True, '통신 시험: 가상 안전 이동 완료. 실제 진입 허가 아님.'
        elif req.request == getattr(InterlockRequest.Request, 'EXIT', 2):
            if self.inventory.blocked_materials:
                res.message = '원료 높이 부족: ' + ', '.join(self.inventory.blocked_materials) + ' · 해당 원료 보충 완료 전에는 재개할 수 없습니다'
                return res
            if self.mode != CellState.PAUSED or self.previous is None:
                res.message = 'ENTER로 일시정지된 시험 배치가 없습니다.'
                return res
            self.mode, self.step, self.station, self.note = self.previous
            self.previous = None
            if '원료 높이 부족:' in self.note:
                self.note = '시험 보충 완료 · 이전 단계 재개'
            self._event('INTERLOCK_EXIT', 'TEST_ONLY ' + req.reason)
            self._state()
            res.granted, res.message = True, '통신 시험: 일시정지 이전 단계로 복귀했습니다.'
        else:
            res.message = 'ENTER(1) 또는 EXIT(2)가 필요합니다.'
        return res

    @staticmethod
    def _material_station(material_id):
        # 최신 stations.yaml ID와 화면 표시만 맞춘다. 좌표나 로봇 명령은 만들지 않는다.
        return {'A': 'material_1', 'B': 'material_2', 'C': 'material_3'}.get(material_id, 'test_material_unknown')

    def _reading(self, net_g, subject='scoop'):
        return self._stamp(WeightReading(
            gross_g=float(net_g + 35.0), tare_g=35.0, net_g=float(net_g),
            std_g=0.15, samples=20, valid=True, station='workbench', subject=subject))

    def _weight(self, net_g, subject='scoop'):
        reading = self._reading(net_g, subject)
        self.pub_weight.publish(reading)
        return reading

    def _cycle(self, item, actual, attempt=1, actual_before=0.0, duration_s=None,
               outcome=None, valid=True):
        residual = 3.0
        cycle = self._stamp(ScoopCycle(
            batch_id=self.batch_id, material_id=item.material_id, attempt=attempt,
            target_g=float(item.target_g), actual_before_g=float(actual_before),
            scoop_tare=self._reading(0.0), pre_pour=self._reading(actual + residual),
            post_pour=self._reading(residual), commanded_pour_fraction=float(actual / (actual + residual)),
            delivered_g=float(actual), weigh_method=ScoopCycle.WEIGH_METHOD_WORKPIECE,
            weigh_pose_id='workbench', tool_name='TEST_TOOL', tcp_name='TEST_TCP',
            contact_detected=True, max_contact_force_n=5.0, insertion_depth_mm=20.0,
            grip_width_mm=32.4, valid=bool(valid),
            outcome=ScoopCycle.COMPLETE if outcome is None else outcome,
            duration_s=float(self.item_duration if duration_s is None else duration_s)))
        for name in ('tare', 'pre_pour', 'post_pour'):
            setattr(cycle, name + '_wrench', [0.0, 0.0, -1.0, 0.0, 0.0, 0.0])
            setattr(cycle, name + '_wrench_std', [0.01] * 6)
            setattr(cycle, name + '_wrench_samples', 20)
            setattr(cycle, name + '_wrench_valid', True)
        # 독립 실측 정답은 생성하지 않는다.
        cycle.reference_valid = False
        self.pub_cycle.publish(cycle)

    def _invalid_reading(self, subject='scoop'):
        """유효성 게이트를 못 넘은 계량. 값은 남기되 `valid=false` 로 「못 믿는다」를 표시한다.

        NaN 을 쓰지 않는다 — SQLite 가 NaN 을 NULL 로 바꿔 검증이 흔들린다. 무효 판정의
        근거는 흩어짐(`std_g`)이고 `valid` 가 그 결론이다.
        """
        reading = self._stamp(WeightReading(
            gross_g=47.0, tare_g=35.0, net_g=12.0,
            std_g=99.0, samples=20, valid=False, station='workbench', subject=subject))
        self.pub_weight.publish(reading)
        return reading

    def _invalid_result(self, item):
        """투입량을 모르는 채 QA 승인으로 넘어간 원료 (계약 v1.8 `verdict=INVALID`).

        실제 공정은 이때 누적 투입량에 아무것도 더하지 않는다(#213) — 0 을 더하는 것과 다르다.
        `BATCH_UNMEASURED` 는 record_node 가 배치 결과를 `DONE_UNMEASURED` 로 남기는 근거다 (D-32).
        """
        self.unmeasured_done = True
        self.last_result = self._stamp(DispenseResult(
            batch_id=self.batch_id, material_id=item.material_id,
            target_g=float(item.target_g), actual_g=0.0, error_pct=0.0,
            verdict=DispenseResult.INVALID, attempts=1,
            duration_s=float(self.item_duration)))
        self.pub_result.publish(self.last_result)
        self.items_done += 1
        self._event('BATCH_UNMEASURED',
                    'TEST_ONLY 투입량 미측정 승인 · ' + item.material_id, CellEvent.WARN)

    def _deviation(self, kind, text):
        self.deviation_count += 1
        self.pending = Deviation(
            deviation_id='D-' + self.batch_id + '-1', batch_id=self.batch_id,
            material_id=self.items[self.index].material_id if kind != Deviation.BATCH_OUT_OF_SPEC else '',
            kind=kind, detail='TEST_ONLY ' + text, requires_decision=True,
            decision=Deviation.PENDING, operator_id='')
        self.pub_dev.publish(self._stamp(self.pending))
        self.mode, self.step, self.phase = CellState.DEVIATION, 'WAIT_QA', 'qa'
        self.note = '시험 일탈 · QA 승인 또는 폐기 대기'
        self._state()

    def _finish(self, result):
        self.phase, self.elapsed, self.finish_result = 'finish', 0.0, result
        self.step, self.station = ('DISCARD_MOVING', 'reject_bin') if result == 'DISCARDED' else ('FINISH', 'passbox_done')
        self.note = '시험 배치 결과 정리 중 · ' + result
        self._state()  # RUNNING을 유지한다. 가상 반송 완료는 다음 tick에서만 확정한다.

    def _next_item(self):
        self.index += 1
        if self.index >= len(self.items):
            self.index = len(self.items) - 1
            self._weight(self.delivered_total + (50.0 if self.active_scenario == 'batch_out_of_spec' else 0.0), 'container')
            if self.active_scenario == 'batch_out_of_spec':
                self._deviation(Deviation.BATCH_OUT_OF_SPEC, '배치 끝 용기 순량이 총 목표량 허용폭 밖 시험')
            else:
                self._finish('DONE')
        else:
            self.phase, self.elapsed, self.last_weight = 'item', 0.0, -1.0
            self.step, self.station = 'SCOOP', self._material_station(self.items[self.index].material_id)

    def _tick(self):
        now = time.monotonic()
        dt, self._previous_tick = min(now - self._previous_tick, 0.5), now
        self.pub_grip.publish(self._stamp(GripperState(
            width_mm=32.4, busy=self.mode == CellState.RUNNING,
            grip_inferred=self.mode in (CellState.RUNNING, CellState.DEVIATION),
            safety_triggered=False, force_cmd_n=20.0, backend='virtual')))
        if self.mode != CellState.RUNNING or self.inventory.blocked_materials:
            return
        self.elapsed += dt
        if self.phase == 'start':
            if self.elapsed >= 1.0:
                self._event('BATCH_START', self.product)
                self.phase, self.elapsed, self.last_weight = 'item', 0.0, -1.0
                self.step, self.station = 'SCOOP', self._material_station(self.items[0].material_id)
                if self.active_scenario == 'wrong_tool':
                    self._deviation(Deviation.WRONG_TOOL, '스쿱 폭 지문 불일치 시험')
                self._state()
                self.batch_done.set()
            return
        if self.phase == 'finish':
            if self.elapsed >= 1.0:
                self._event('DISCARD_COMPLETE' if self.finish_result == 'DISCARDED' else 'TRANSFER_COMPLETE', 'TEST_ONLY 가상 반송 완료')
                self._event('BATCH_END', 'TEST_ONLY ' + self.finish_result)
                self.inventory.release()  # 미사용 예약만 해제. 이미 소비한 양은 폐기해도 복구하지 않는다.
                self.mode, self.step, self.phase = CellState.DONE, 'DONE', 'idle'
                self.note = '시험 배치 종료 · ' + self.finish_result
                self._state()
            return
        if self.phase != 'item':
            return
        item = self.items[self.index]
        overfill = self.active_scenario == 'overfill' and self.index == min(1, len(self.items) - 1)
        actual = float(item.target_g * (1.10 if overfill else 1.0))
        fraction = min(1.0, self.elapsed / self.item_duration)
        self.step = 'SCOOP' if fraction < 0.4 else ('POUR' if fraction < 0.7 else 'WEIGH')
        self.station = self._material_station(item.material_id) if fraction < 0.4 else 'workbench'
        self.note = f'시험 {item.material_id} · 목표 {item.target_g:g} g · 생성 계량값'
        if self.elapsed - self.last_weight >= 0.4:
            self._weight(actual * fraction)
            self.last_weight = self.elapsed
        if fraction < 1.0:
            return
        if self.active_scenario == 'weigh_invalid':
            # 재시도 상한을 넘긴 무효 계량 → WEIGH_INVALID. 투입량을 모르므로 재고도 차감하지 않는다.
            # 실패한 시도도 ScoopCycle 로 남긴다 — 투입량 0, valid=false (계약 outcome 2).
            self._invalid_reading()
            self._cycle(item, 0.0, 1, 0.0, outcome=ScoopCycle.WEIGH_INVALID, valid=False)
            self._deviation(Deviation.WEIGH_INVALID, '스쿱 계량 무효 반복 시험')
            return
        if self.active_scenario == 'material_empty':
            # SCOOP_EMPTY 가 연속으로 나 원료가 소진됐다고 본 경우 (kind 5, #233).
            self._deviation(Deviation.MATERIAL_EMPTY, '원료 소진 시험')
            return
        self.inventory.consume(item.material_id, actual)
        self._publish_inventory()
        self._weight(actual)
        attempts = max(1, math.ceil(actual / self.test_scoop_nominal_g))
        delivered_before = 0.0
        # 원료 완료 시 40 g 기준 시험 사이클을 생성한다. 실제 로봇 계량/횟수 검증은 아니다.
        for attempt in range(1, attempts + 1):
            portion = min(self.test_scoop_nominal_g, actual - delivered_before)
            self._cycle(item, portion, attempt, delivered_before, self.item_duration / attempts)
            delivered_before += portion
        self.delivered_total += actual
        self.last_result = self._stamp(DispenseResult(
            batch_id=self.batch_id, material_id=item.material_id,
            target_g=float(item.target_g), actual_g=actual,
            error_pct=float((actual - item.target_g) / item.target_g * 100.0),
            verdict=DispenseResult.OVER if overfill else DispenseResult.OK,
            attempts=attempts, duration_s=float(self.item_duration)))
        self.pub_result.publish(self.last_result)
        self.items_done += 1
        if overfill:
            self._deviation(Deviation.OVERFILL, '원료 10% 초과 투입 시험')
        else:
            self._next_item()
        self._state()


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
