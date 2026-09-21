"""공정 6종 토픽의 단일 기록자. SQLite 원본과 배치 종료 JSON 사본을 관리한다.

QA 판정 자체는 종료가 아니다. C가 mode=DONE과 step=DONE/DISCARDED를
발행하면 종료 처리한다. 뒤늦게 수신한 결과도 종료 JSON에 반영한다.
"""
import json
import os

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from ament_index_python.packages import get_package_share_directory
from rosidl_runtime_py.convert import message_to_ordereddict

from gmp_interfaces.msg import CellEvent, CellState, Deviation, DispenseResult, ScoopCycle, WeightReading
from gmp_hmi.core.db import CellDB

LATCHED = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)


class RecordNode(Node):
    def __init__(self):
        super().__init__('record_node')
        self.declare_parameter('db_path', '~/auto-pharmacist/records/cell.db')
        self.declare_parameter('export_dir', '~/auto-pharmacist/records')
        schema = os.path.join(get_package_share_directory('gmp_hmi'), 'config', 'schema.sql')
        self.db = CellDB(self.get_parameter('db_path').value, schema)
        # 저장된 미완료 배치만으로 현 공정의 배치를 추측하지 않는다. latched state 가 기준이다.
        self.batch_id, self.active = '', False
        self._state_t = None
        self._held_completion = set()
        self.create_subscription(CellState, 'state', self._on_state, LATCHED)
        self.create_subscription(WeightReading, 'weight', self._on_weight, 20)
        self.create_subscription(ScoopCycle, 'scoop_cycle', self._on_cycle, 50)
        self.create_subscription(DispenseResult, 'dispense_result', self._on_result, 50)
        self.create_subscription(Deviation, 'deviation', self._on_dev,
                                 QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.create_subscription(CellEvent, 'event', self._on_event, 100)
        self.get_logger().info(f'기록 DB {os.path.expanduser(self.get_parameter("db_path").value)}')

    @staticmethod
    def _t(header):
        return header.stamp.sec + header.stamp.nanosec * 1e-9

    def _refresh_export(self, batch_id):
        row = self.db.batch_status(batch_id) if batch_id else None
        if row and row['finished_at'] is not None:
            return self.db.export_json(batch_id, os.path.expanduser(self.get_parameter('export_dir').value))
        return None

    def _ensure_batch(self, batch_id, t):
        if batch_id:
            self.db.start_batch(batch_id, t)

    def _on_state(self, m: CellState):
        t = self._t(m.header)
        if m.batch_id == self.batch_id and self._state_t is not None and t < self._state_t:
            return  # 같은 배치의 오래된 상태로 현재 문맥을 되돌리지 않는다.
        self._state_t = t
        if not m.batch_id or m.mode == CellState.IDLE:
            self.batch_id, self.active = '', False
            return
        self.batch_id = m.batch_id
        self._ensure_batch(m.batch_id, t)
        row = self.db.batch_status(m.batch_id)
        self.active = row['finished_at'] is None
        if not self.active:
            return  # 재접속 때 재수신한 DONE/RUNNING 이 완료 배치를 다시 열지 않는다.
        self.db.checkpoint(m.batch_id, t, str(m.mode), m.step, m.item_index, m.station, m.note)
        if m.mode == CellState.DONE and m.step not in ('DONE', 'DISCARDED'):
            if m.batch_id not in self._held_completion:
                note = '물리적 완료 확인 대기: C가 이송 완료 후 mode=DONE, step=DONE/DISCARDED 발행 필요'
                self.db.note_batch(m.batch_id, note)
                self.get_logger().warning(f'{m.batch_id}: {note} (수신 step={m.step})')
                self._held_completion.add(m.batch_id)
            return
        if m.mode in (CellState.DONE, CellState.ERROR):
            if m.mode == CellState.ERROR:
                result = 'ERROR'
            else:
                result = 'DISCARDED' if m.step == 'DISCARDED' or self.db.has_discard_decision(m.batch_id) else 'DONE'
            self.db.finish_batch(m.batch_id, t, result, m.note)
            # 보류 사유는 완료가 확인되면 해제한다.
            if m.batch_id in self._held_completion:
                self.db.note_batch(m.batch_id, m.note or '')
                self._held_completion.discard(m.batch_id)
            path = self._refresh_export(m.batch_id)
            self.get_logger().info(f'배치 종료 {m.batch_id} ({result}) → {path}')
            self.active = False

    def _on_weight(self, m):
        # WeightReading 은 배치 ID 가 없는 계약이다. 최근 state 의 배치와 연결한다.
        # 초기 state 수신 전/IDLE 중 측정은 NULL 로 보존하며 임의의 배치에 붙이지 않는다.
        batch_id = self.batch_id or None
        self.db.weight(batch_id, self._t(m.header), m.station, m.gross_g, m.tare_g,
                       m.net_g, m.std_g, m.valid, subject=m.subject, samples=m.samples)
        self._refresh_export(batch_id)

    def _on_result(self, m):
        batch_id, t = m.batch_id or self.batch_id, self._t(m.header)
        self._ensure_batch(batch_id, t)
        self.db.item(batch_id, m.material_id, m.target_g, m.actual_g, m.error_pct,
                     m.verdict, m.attempts, t)
        self._refresh_export(batch_id)

    def _on_cycle(self, m):
        payload = message_to_ordereddict(m)
        payload['batch_id'] = m.batch_id or self.batch_id
        t = self._t(m.header)
        self._ensure_batch(payload['batch_id'], t)
        self.db.scoop_cycle(payload, t)
        self._refresh_export(payload['batch_id'])

    def _on_dev(self, m):
        batch_id, t = m.batch_id or self.batch_id, self._t(m.header)
        self._ensure_batch(batch_id, t)
        self.db.deviation(m.deviation_id, batch_id, m.material_id, m.kind, m.detail,
                          m.requires_decision, m.decision, m.operator_id, t)
        self.db.reconcile_discard(batch_id)
        self._refresh_export(batch_id)

    def _on_event(self, m):
        t = self._t(m.header)
        # 배치 ID 없는 로그인/설정 등의 전역 HMI 감사 이벤트는 현재 배치에 붙이지 않는다.
        batch_id = m.batch_id or None
        self.db.event(t, batch_id, m.level, m.code, m.text)
        if m.code == 'BATCH_START' and batch_id:
            product = m.text
            try:
                parsed = json.loads(m.text)
                if isinstance(parsed, dict):
                    product = str(parsed.get('product', ''))
            except (ValueError, TypeError):
                pass
            self.db.start_batch(batch_id, t, product)
        if m.code == 'HMI_ORDER_CONTEXT' and batch_id:
            try:
                recipe = json.loads(m.text.partition(' ')[2])
                self.db.save_recipe_context(batch_id, t, recipe)
            except (ValueError, TypeError, KeyError, AttributeError):
                self.get_logger().warning('잘못된 HMI 레시피 문맥: ' + batch_id)
        if m.code.startswith('HMI_'):
            actor, _, detail = m.text.partition(' ')
            self.db.audit(t, actor or 'unknown', m.code[4:], batch_id or '', detail)
        self._refresh_export(batch_id)


def main(args=None):
    from rclpy.executors import ExternalShutdownException
    rclpy.init(args=args)
    node = RecordNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.db.close()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
