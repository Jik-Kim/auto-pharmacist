"""배치 기록 노드 — 단일 기록자. state·weight·dispense_result·deviation·event 를 받아 SQLite 에 쓴다.

DB 에는 record_node 만 쓴다. HMI 는 읽기만. events 는 append-only.
배치 시작·종료는 CellState.mode 전이로 판정한다 (RUNNING 진입 / DONE·ERROR 진입).
제품명은 process_node 가 내는 BATCH_START 이벤트의 text 에서 읽는다 (TODO([C]) — 없으면 빈 값).

TODO([D]) 9/18: 가상에서 레시피 1건 돌려 5개 테이블이 채워지는지, export_json 확인.
"""
import os

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from ament_index_python.packages import get_package_share_directory

from gmp_interfaces.msg import CellEvent, CellState, Deviation, DispenseResult, WeightReading
from gmp_hmi.core.db import CellDB

LATCHED = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)


class RecordNode(Node):
    def __init__(self):
        super().__init__('record_node')
        self.declare_parameter('db_path', '~/auto-pharmacist/records/cell.db')
        self.declare_parameter('export_dir', '~/auto-pharmacist/records')
        schema = os.path.join(get_package_share_directory('gmp_hmi'), 'config', 'schema.sql')
        self.db = CellDB(self.get_parameter('db_path').value, schema)
        self.batch_id, self.active = '', False
        self.create_subscription(CellState, 'state', self._on_state, LATCHED)
        self.create_subscription(WeightReading, 'weight', self._on_weight, 20)
        self.create_subscription(DispenseResult, 'dispense_result', self._on_result, 50)
        self.create_subscription(Deviation, 'deviation', self._on_dev, QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.create_subscription(CellEvent, 'event', self._on_event, 100)
        self.get_logger().info(f'기록 DB {os.path.expanduser(self.get_parameter("db_path").value)}')

    @staticmethod
    def _t(header):
        return header.stamp.sec + header.stamp.nanosec * 1e-9

    def _on_state(self, m: CellState):
        if m.mode == CellState.RUNNING and m.batch_id and m.batch_id != self.batch_id:
            self.batch_id, self.active = m.batch_id, True
            self.db.start_batch(m.batch_id, self._t(m.header))
        if self.active and m.mode in (CellState.DONE, CellState.ERROR):
            self.db.finish_batch(self.batch_id, self._t(m.header), m.step or ('DONE' if m.mode == CellState.DONE else 'ERROR'))
            path = self.db.export_json(self.batch_id, os.path.expanduser(self.get_parameter('export_dir').value))
            self.get_logger().info(f'배치 종료 {self.batch_id} → {path}')
            self.active = False

    def _on_weight(self, m):
        self.db.weight(self.batch_id or None, self._t(m.header), m.station, m.gross_g, m.tare_g, m.net_g, m.std_g, m.valid)

    def _on_result(self, m):
        self.db.item(m.batch_id or self.batch_id, m.material_id, m.target_g, m.actual_g, m.error_pct, m.verdict, m.attempts, self._t(m.header))

    def _on_dev(self, m):
        self.db.deviation(m.deviation_id, m.batch_id or self.batch_id, m.material_id, m.kind, m.detail,
                          m.requires_decision, m.decision, m.operator_id, self._t(m.header))

    def _on_event(self, m):
        t = self._t(m.header)
        self.db.event(t, m.batch_id or self.batch_id or None, m.level, m.code, m.text)
        if m.code.startswith('HMI_'):                     # 사람의 조작 → 감사 추적
            actor, _, detail = m.text.partition(' ')
            self.db.audit(t, actor or 'unknown', m.code[4:], m.batch_id or self.batch_id, detail)


def main(args=None):
    rclpy.init(args=args)
    n = RecordNode()
    try:
        rclpy.spin(n)
    except KeyboardInterrupt:
        pass
    finally:
        n.db.close(); n.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
