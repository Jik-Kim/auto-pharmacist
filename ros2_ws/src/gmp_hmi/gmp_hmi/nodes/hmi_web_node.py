"""웹 HMI — Flask(메인 스레드) + rclpy 노드(executor 스레드). Kn1 mro_fleet 의 monitor_web_app / fleet_ui_commands 패턴.

  브라우저 → /order /qa /interlock (POST) → ROS 서비스 (submit_order / qa_decision / interlock)
  브라우저 ← /status (0.5 s 폴링) ← 구독 스냅샷 (state · weight · dispense_result · deviation · gripper_state)
  브라우저 ← /history /batch/<id> /kpi /audit ← SQLite (읽기만 — 쓰는 쪽은 record_node)

사람의 조작은 CellEvent(code='HMI_*', text='<actor> <detail>') 로 발행해 record_node 가 audit 테이블에 남긴다.
셀 밖 QA 는 같은 네트워크의 다른 기기에서 http://<이 PC>:5000 으로 접속한다 (R23 원격 승인).

의존: python3-flask (apt). 없으면 기동 시 안내하고 종료.
TODO([D]) 9/17: 가상 모드에서 주문→상태→QA 승인→기록 조회 한 바퀴.
"""
import glob
import os
import threading
import time

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from ament_index_python.packages import get_package_share_directory

from gmp_interfaces.msg import CellEvent, CellState, Deviation, DispenseResult, GripperState, Recipe, RecipeItem, WeightReading
from gmp_interfaces.srv import InterlockRequest, QaDecision, SubmitOrder
from gmp_hmi.core.db import DECISIONS, KINDS, VERDICTS, CellDB
from gmp_process.core.recipe import load as load_recipe   # 레시피 스키마·검증 단일 출처 (C) — 여기서 다시 파싱하지 않는다

MODES = {v: k for k, v in vars(CellState).items() if k.isupper() and isinstance(v, int)}
LATCHED = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)


class HmiRosNode(Node):
    """구독 스냅샷과 서비스 클라이언트. Flask 가 이 객체를 읽고 부른다."""

    def __init__(self):
        super().__init__('hmi_web_node')
        self.declare_parameter('recipes_dir', '')
        self.declare_parameter('db_path', '~/auto-pharmacist/records/cell.db')
        self.declare_parameter('port', 5000)
        self.lock = threading.Lock()
        self.snap = {'state': {}, 'gripper': {}, 'weights': [], 'results': [], 'deviations': {}}
        self.create_subscription(CellState, 'state', self._on_state, LATCHED)
        self.create_subscription(WeightReading, 'weight', self._on_weight, 20)
        self.create_subscription(DispenseResult, 'dispense_result', self._on_result, 50)
        self.create_subscription(Deviation, 'deviation', self._on_dev, QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.create_subscription(GripperState, 'gripper_state', self._on_grip, QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.cli_order = self.create_client(SubmitOrder, 'submit_order')
        self.cli_qa = self.create_client(QaDecision, 'qa_decision')
        self.cli_lock = self.create_client(InterlockRequest, 'interlock')
        self.pub_event = self.create_publisher(CellEvent, 'event', 100)

    @staticmethod
    def _t(h):
        return h.stamp.sec + h.stamp.nanosec * 1e-9

    def _on_state(self, m):
        with self.lock:
            self.snap['state'] = {'mode': MODES.get(m.mode, '?'), 'step': m.step, 'batch_id': m.batch_id,
                                  'item_index': m.item_index, 'station': m.station, 'note': m.note, 't': self._t(m.header)}

    def _on_weight(self, m):
        with self.lock:
            self.snap['weights'].append({'t': self._t(m.header), 'net_g': m.net_g, 'std_g': m.std_g, 'valid': m.valid})
            del self.snap['weights'][:-100]

    def _on_result(self, m):
        with self.lock:
            self.snap['results'].append({'material_id': m.material_id, 'target_g': m.target_g, 'actual_g': m.actual_g,
                                         'error_pct': m.error_pct, 'verdict': VERDICTS.get(m.verdict, '?'), 'attempts': m.attempts})
            del self.snap['results'][:-20]

    def _on_dev(self, m):
        with self.lock:
            self.snap['deviations'][m.deviation_id] = {'deviation_id': m.deviation_id, 'batch_id': m.batch_id, 'kind': KINDS.get(m.kind, '?'),
                                                       'detail': m.detail, 'requires_decision': m.requires_decision,
                                                       'decision': DECISIONS.get(m.decision, '?'), 't': self._t(m.header)}

    def _on_grip(self, m):
        with self.lock:
            self.snap['gripper'] = {'width_mm': m.width_mm, 'grip': m.grip_inferred, 'backend': m.backend, 'force_n': m.force_cmd_n}

    # ── 명령 ────────────────────────────────────────────────────────
    def _call(self, client, req, timeout_s=3.0):
        if not client.wait_for_service(timeout_sec=1.0):
            return None
        fut = client.call_async(req)
        done = threading.Event()
        fut.add_done_callback(lambda _: done.set())
        if not done.wait(timeout_s) or fut.exception():
            return None
        return fut.result()

    def audit(self, action, actor, detail=''):
        m = CellEvent(level=CellEvent.INFO, code=f'HMI_{action}', text=f'{actor or "unknown"} {detail}'.strip(),
                      batch_id=self.snap['state'].get('batch_id', ''))
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_event.publish(m)

    def recipes(self):
        d = self.get_parameter('recipes_dir').value or os.path.join(get_package_share_directory('gmp_bringup'), 'params', 'recipes')
        return sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(d, '*.yaml')))

    def submit(self, name, actor):
        d = self.get_parameter('recipes_dir').value or os.path.join(get_package_share_directory('gmp_bringup'), 'params', 'recipes')
        spec = load_recipe(os.path.join(d, f'{name}.yaml'))   # 필수 필드·원료 중복·양수 검증을 여기서 통과한다
        r = Recipe(product=spec.product or name)
        r.header.stamp = self.get_clock().now().to_msg()
        for it in spec.items:
            r.items.append(RecipeItem(material_id=it.material_id, target_g=it.target_g, tol_pct=it.tol_pct))
        res = self._call(self.cli_order, SubmitOrder.Request(recipe=r))
        self.audit('ORDER', actor, f'{name} → {res.batch_id if res else "no-response"}')
        return res

    def qa(self, batch_id, deviation_id, decision, actor):
        res = self._call(self.cli_qa, QaDecision.Request(deviation_id=deviation_id, decision=int(decision), operator_id=actor))
        self.audit('QA_APPROVE' if int(decision) == Deviation.APPROVED else 'QA_DISCARD', actor, deviation_id)
        return res

    def interlock(self, request, reason, actor):
        res = self._call(self.cli_lock, InterlockRequest.Request(request=int(request), reason=reason), timeout_s=15.0)
        self.audit('INTERLOCK_ENTER' if int(request) == InterlockRequest.Request.ENTER else 'INTERLOCK_EXIT', actor, reason)
        return res


def build_app(node: HmiRosNode, db: CellDB):
    from flask import Flask, jsonify, render_template, request
    app = Flask(__name__, template_folder=os.path.join(get_package_share_directory('gmp_hmi'), 'templates'))

    @app.route('/')
    def index():
        return render_template('index.html', recipes=node.recipes())

    @app.route('/status')
    def status():
        with node.lock:
            s = {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in node.snap.items()}
        s['deviations'] = list(s['deviations'].values())
        s['now'] = time.time()
        return jsonify(s)

    @app.route('/order', methods=['POST'])
    def order():
        try:
            res = node.submit(request.form['recipe'], request.form.get('actor', ''))
        except (ValueError, KeyError, OSError) as e:      # 레시피 yaml 이 스키마에 안 맞음
            return jsonify(ok=False, message=f'레시피 오류: {e}', batch_id='')
        return jsonify(ok=bool(res and res.accepted), message=(res.message if res else 'process_node 응답 없음'),
                       batch_id=(res.batch_id if res else ''))

    @app.route('/qa', methods=['POST'])
    def qa():
        res = node.qa(request.form['batch_id'], request.form['deviation_id'], request.form['decision'], request.form.get('actor', ''))
        return jsonify(ok=bool(res and res.accepted), message=(res.message if res else '응답 없음'))

    @app.route('/interlock', methods=['POST'])
    def interlock():
        res = node.interlock(request.form['request'], request.form.get('reason', ''), request.form.get('actor', ''))
        return jsonify(ok=bool(res and res.granted), message=(res.message if res else '응답 없음'))

    @app.route('/history')
    def history():
        return jsonify(db.batches(50))

    @app.route('/batch/<batch_id>')
    def batch(batch_id):
        b = db.batch(batch_id)
        return (jsonify(b), 200) if b else (jsonify(ok=False, message='없음'), 404)

    @app.route('/kpi')
    def kpi():
        return jsonify(db.kpis())

    @app.route('/audit')
    def audit():
        return jsonify(db.recent_audit(50))

    return app


def main(args=None):
    rclpy.init(args=args)
    node = HmiRosNode()
    try:
        import flask  # noqa: F401
    except ImportError:
        node.get_logger().error('flask 없음 — sudo apt install python3-flask (docs/setup.md)')
        rclpy.shutdown(); return
    schema = os.path.join(get_package_share_directory('gmp_hmi'), 'config', 'schema.sql')
    db = CellDB(node.get_parameter('db_path').value, schema)
    ex = MultiThreadedExecutor(num_threads=2)
    ex.add_node(node)
    threading.Thread(target=ex.spin, daemon=True, name='rclpy-executor').start()
    app = build_app(node, db)
    port = int(node.get_parameter('port').value)
    node.get_logger().info(f'HMI http://0.0.0.0:{port}  (셀 밖 QA 는 같은 네트워크의 다른 기기에서 접속)')
    try:
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    finally:
        db.close(); ex.shutdown(); node.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
