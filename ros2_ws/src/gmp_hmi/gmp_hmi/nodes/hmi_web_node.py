"""웹 HMI — Flask(메인 스레드) + rclpy 노드(executor 스레드). Kn1 mro_fleet 의 monitor_web_app / fleet_ui_commands 패턴.

  브라우저 → /order /qa /interlock (POST) → ROS 서비스 (submit_order / qa_decision / interlock)
  브라우저 ← /status (0.5 s 폴링) ← 구독 스냅샷 (state · weight · dispense_result · deviation · gripper_state)
  브라우저 ← /history /batch/<id> /kpi /audit ← SQLite (읽기만 — 쓰는 쪽은 record_node)

사람의 조작은 CellEvent(code='HMI_*', text='<actor> <detail>') 로 발행해 record_node 가 audit 테이블에 남긴다.
셀 밖 QA 는 같은 네트워크의 다른 기기에서 http://<이 PC>:5000 으로 접속한다 (R23 원격 승인).

의존: python3-flask (apt). 없으면 기동 시 안내하고 종료.
TODO([D]) 9/17: 가상 모드에서 주문→상태→QA 승인→기록 조회 한 바퀴.
"""
import copy
import csv
import io
import json
import secrets
import hmac
from functools import wraps
from datetime import timedelta
import glob
import math
import os
import sqlite3
import threading
import time
from pathlib import Path

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from ament_index_python.packages import get_package_share_directory

from gmp_interfaces.msg import CellEvent, CellState, Deviation, DispenseResult, GripperState, Recipe, RecipeItem, WeightReading, ScoopCycle
from gmp_interfaces.srv import InterlockRequest, QaDecision, SubmitOrder
from gmp_hmi.core.db import DECISIONS, KINDS, VERDICTS, CellDB
from gmp_hmi.core.session_inventory import SessionInventory
from gmp_hmi.core.trial_inventory import validate_trial_snapshot
from gmp_hmi.core.admin_store import AdminStore, DEFAULT_SETTINGS
from gmp_process.core.recipe import load as load_recipe   # 레시피 스키마·검증 단일 출처 (C) — 여기서 다시 파싱하지 않는다

# rosidl은 상수를 메타클래스 property로 노출하므로 vars()에서 정수를 찾지 않는다.
MODES = {getattr(CellState, name): name
         for name in ('IDLE', 'RUNNING', 'PAUSED', 'DEVIATION', 'ERROR', 'DONE')}
LATCHED = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
TOPICS = ('state', 'weight', 'gripper', 'dispense_result', 'deviation', 'scoop_cycle', 'event')
LEVELS = {0: 'INFO', 1: 'WARN', 2: 'ERROR'}


class CommandUnavailable(RuntimeError):
    pass


def command_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)) or str(value) not in ('1', '2'):
        raise ValueError('명령값은 1 또는 2여야 합니다')
    return int(value)


def ros_payload(message):
    """생성 메시지의 전체 필드를 손실 없이 JSON 자료형으로 변환한다."""
    if hasattr(message, 'get_fields_and_field_types'):
        return {name: ros_payload(getattr(message, name)) for name in message.get_fields_and_field_types()}
    if hasattr(message, '__dict__'):
        return {name: ros_payload(value) for name, value in vars(message).items() if not name.startswith('_')}
    if hasattr(message, 'tolist'):
        return message.tolist()
    if isinstance(message, (list, tuple)):
        return [ros_payload(item) for item in message]
    return message


def json_finite(value):
    """ROS의 NaN/무한대는 JSON 표준에 없는 값이므로 미확인(null)으로 전달한다."""
    if isinstance(value, dict):
        return {key: json_finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_finite(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class ReadOnlyCellDB(CellDB):
    """기록 노드가 만든 DB를 읽기 전용으로 연다. HMI가 스키마를 생성하지 않는다."""

    def __init__(self, path):
        self.readonly = True
        self._uri = Path(os.path.expanduser(path)).resolve().as_uri() + '?mode=ro'

    def _rows(self, sql, args=()):
        # 조회 단위 연결이므로 기록 노드의 늦은 시작/DB 재생성도 다음 요청에 반영된다.
        connection = sqlite3.connect(self._uri, uri=True, timeout=1.0)
        try:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql, args).fetchall()]
        finally:
            connection.close()

    def close(self):
        pass


class HmiRosNode(Node):
    """구독 스냅샷과 서비스 클라이언트. Flask 가 이 객체를 읽고 부른다."""

    def __init__(self):
        super().__init__('hmi_web_node')
        self.declare_parameter('recipes_dir', '')
        self.declare_parameter('db_path', '~/auto-pharmacist/records/cell.db')
        self.declare_parameter('port', 5000)
        self.declare_parameter('admin_store_path', '~/.config/gmp_hmi/admin.json')
        self.declare_parameter('ui_stale_after_s', 3.0)  # 화면 관측 신선도, 물리 안전 판단 아님
        self.declare_parameter('inventory_material_ids', ['A', 'B', 'C'])
        self.declare_parameter('inventory_capacity_g', [0.0, 0.0, 0.0])
        self.declare_parameter('inventory_initial_g', [-1.0, -1.0, -1.0])
        self.declare_parameter('inventory_low_pct', 20.0)
        self.declare_parameter('test_inventory_enabled', False)
        self.test_inventory_enabled = self.get_parameter('test_inventory_enabled').value
        if self.test_inventory_enabled and self.get_namespace() != '/hmi_test':
            raise RuntimeError('시험 재고 기능은 /hmi_test에서만 활성화할 수 있습니다')
        self.test_inventory_data = None
        self.test_inventory_received = None
        self.test_inventory_error = ''
        self.cli_test_refill = {}
        self.pub_test_height = None
        self.admin_store = AdminStore(self.get_parameter('admin_store_path').value,
            {key: self.get_parameter(key).value for key in DEFAULT_SETTINGS})
        if self.admin_store.setup_required() and os.environ.get('GMP_HMI_ADMIN_PASSWORD'):
            self.admin_store.create_user(os.environ.get('GMP_HMI_ADMIN_USER', 'admin'),
                                         os.environ['GMP_HMI_ADMIN_PASSWORD'], 'admin')
        self.local_settings = self.admin_store.settings()
        self.inventory = self._inventory_from(self.local_settings)
        self.command_lock = threading.Lock()
        self.entry_granted = None
        self.entry_batch_id = ''
        self._pending_entry = None
        self.received = dict.fromkeys(TOPICS)
        self.received_counts = dict.fromkeys(TOPICS, 0)
        self.active_recipe = None
        self.active_recipe_batch_id = ''
        self.lock = threading.Lock()
        self.snap = {'state': {}, 'gripper': {}, 'weights': [], 'results': [], 'deviations': {}, 'events': [], 'scoop_cycles': []}
        self.create_subscription(CellState, 'state', self._on_state, LATCHED)
        self.create_subscription(WeightReading, 'weight', self._on_weight, 20)
        self.create_subscription(DispenseResult, 'dispense_result', self._on_result, 50)
        self.create_subscription(Deviation, 'deviation', self._on_dev, QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.create_subscription(GripperState, 'gripper_state', self._on_grip, QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.create_subscription(ScoopCycle, 'scoop_cycle', self._on_cycle, 50)
        self.create_subscription(CellEvent, 'event', self._on_event, 100)
        self.cli_order = self.create_client(SubmitOrder, 'submit_order')
        self.cli_qa = self.create_client(QaDecision, 'qa_decision')
        self.cli_lock = self.create_client(InterlockRequest, 'interlock')
        self.pub_event = self.create_publisher(CellEvent, 'event', 100)
        if self.test_inventory_enabled:
            from std_msgs.msg import String
            from std_srvs.srv import Trigger
            self.create_subscription(String, 'test_inventory', self._on_test_inventory, LATCHED)
            self.cli_test_refill = {mid: self.create_client(Trigger, 'test_refill_' + mid)
                                    for mid in self.get_parameter('inventory_material_ids').value}
            self.pub_test_height = self.create_publisher(String, 'test_height', 10)

    def _on_test_inventory(self, message):
        try:
            value = validate_trial_snapshot(json.loads(message.data))
        except (ValueError, TypeError, KeyError) as exc:
            with self.lock:
                self.test_inventory_data = None
                self.test_inventory_received = None
                self.test_inventory_error = str(exc)
            return
        with self.lock:
            old = self.test_inventory_data
            if old and old['instance_id'] == value['instance_id'] and old['revision'] > value['revision']:
                return
            self.test_inventory_data = value
            self.test_inventory_received = time.monotonic()
            self.test_inventory_error = ''

    def _test_stock_locked(self):
        # self.lock 보유 상태에서만 호출한다. 오래된 재고를 세션 추정으로 대체하지 않는다.
        age = None if self.test_inventory_received is None else time.monotonic() - self.test_inventory_received
        fresh = age is not None and age <= self.local_settings['ui_stale_after_s']
        data = copy.deepcopy(self.test_inventory_data) if self.test_inventory_data else {'items': []}
        data.update(mode='test_process', scope='test_only', enforced=True, fresh=fresh,
                    age_s=age, refill_supported=True,
                    refill_ready=bool(self.cli_test_refill) and any(
                        client.service_is_ready() for client in self.cli_test_refill.values()),
                    note='시험 공정의 가상 재고 · 실제 C 미연결. 주문 시 예약, 분주 시 차감. 시험 공정 재시작 시 초기화됩니다.')
        data['can_refill'] = fresh and bool(data.get('can_refill'))
        data.setdefault('blocked_materials', [])
        data['order_allowed'] = fresh and not data['blocked_materials']
        data['order_block_reason'] = ('시험 재고 미수신/지연' if not fresh else
                                      ('원료 높이 부족: ' + ', '.join(data['blocked_materials'])
                                       if data['blocked_materials'] else ''))
        if not fresh:
            data['note'] = '시험 재고 미수신/지연 · 새 주문·보충 차단. ' + self.test_inventory_error
        for item in data['items']:
            item['percent'] = 100.0 * item['remaining_g'] / item['capacity_g']
            item['low'] = item['percent'] < self.local_settings['inventory_low_pct']
            client = self.cli_test_refill.get(item['material_id'])
            item['refill_ready'] = bool(client and client.service_is_ready())
        return data

    def refill_test(self, actor, material_id, confirmed_full):
        if not self.test_inventory_enabled or self.get_namespace() != '/hmi_test':
            raise CommandUnavailable('실제 C의 보충 계약은 미연결입니다. 시험 보충은 /hmi_test 전용입니다')
        if not isinstance(material_id, str) or material_id not in self.cli_test_refill:
            raise ValueError('보충할 원료 A/B/C 중 하나를 지정하세요')
        if confirmed_full is not True:
            raise ValueError('선택한 원료를 만충 보충했는지 확인하세요 (confirmed_full=true)')
        client = self.cli_test_refill[material_id]
        self._guard_command(client)
        with self.lock:
            inv = self._test_stock_locked()
        if not inv['fresh'] or not inv['can_refill']:
            raise CommandUnavailable('시험 보충은 최신 재고 수신 후 대기·종료 또는 ENTER 정지 상태에서 가능합니다')
        from std_srvs.srv import Trigger
        response = self._call(client, Trigger.Request())
        # 서비스 응답만으로 장부를 바꾸지 않는다. 공정의 후속 재고 스냅샷으로만 갱신한다.
        outcome = 'unknown' if response is None else str(bool(response.success)).lower()
        self.audit('TEST_REFILL', actor, f'test_only material_id={material_id} confirmed_full=true success={outcome}')
        return response

    def report_test_height(self, actor, material_id, height_pct):
        if not self.test_inventory_enabled or self.get_namespace() != '/hmi_test' or self.pub_test_height is None:
            raise CommandUnavailable('시험 높이 주입은 /hmi_test 전용입니다. 실제 C 높이 계약은 미연결입니다')
        if not isinstance(material_id, str) or material_id not in self.cli_test_refill:
            raise ValueError('시험 높이를 주입할 원료 A/B/C 중 하나를 지정하세요')
        if type(height_pct) not in (int, float) or not math.isfinite(height_pct) or not 0 <= height_pct <= 100:
            raise ValueError('원료 높이는 0~100 사이의 유한한 숫자여야 합니다')
        self._guard_command(self.cli_order)
        from std_msgs.msg import String
        self.pub_test_height.publish(String(data=json.dumps(dict(material_id=material_id, height_pct=height_pct), allow_nan=False)))
        self.audit('TEST_HEIGHT', actor, f'test_only material_id={material_id} height_pct={height_pct:g}')
        # publish 성공은 공정 반영 완료가 아니다. 화면은 authoritative snapshot을 기다린다.

    @staticmethod
    def _inventory_from(settings):
        return SessionInventory(settings['inventory_material_ids'], settings['inventory_capacity_g'],
                                settings['inventory_initial_g'], settings['inventory_low_pct'])

    def apply_settings(self, settings):
        inventory = self._inventory_from(settings)
        with self.lock:
            # 화면 기준값 변경은 보충 완료가 아니다. 관측된 세션 소비량을 보존한다.
            for key in inventory._items:
                if key in self.inventory._items:
                    inventory._items[key]['consumed_g'] = self.inventory._items[key]['consumed_g']
            inventory._greatest = dict(self.inventory._greatest)
            self.inventory = inventory
            self.local_settings = copy.deepcopy(settings)

    @staticmethod
    def _t(h):
        return h.stamp.sec + h.stamp.nanosec * 1e-9

    def _received(self, topic):
        # 호출자가 공유 잠금을 잡은 상태에서만 갱신한다.
        self.received[topic] = time.monotonic()
        self.received_counts[topic] += 1

    def _on_state(self, m):
        with self.lock:
            now = time.monotonic()
            previous = self.received['state']
            stale = previous is None or now - previous > self.local_settings['ui_stale_after_s']
            if stale or m.batch_id != self.entry_batch_id:
                self.entry_granted = None
                self._pending_entry = None
            elif self._pending_entry:
                # 서비스 응답이 PAUSED 토픽보다 먼저 도착한 경우만 짧게 기다린다.
                # 현재 허가 응답 + 동일 배치 + 신선한 PAUSED/DEVIATION에서 허가로 표시한다.
                if now > self._pending_entry['expires_at']:
                    self._pending_entry = None
                elif m.mode in (CellState.PAUSED, CellState.DEVIATION):
                    self.entry_granted = True
                    self._pending_entry = None
                elif m.mode != CellState.RUNNING:
                    self._pending_entry = None
            elif m.mode not in (CellState.PAUSED, CellState.DEVIATION):
                self.entry_granted = None
            self._received('state')
            self.snap['state'] = {'mode': MODES.get(m.mode, '?'), 'step': m.step, 'batch_id': m.batch_id,
                                  'item_index': m.item_index, 'station': m.station, 'note': m.note, 't': self._t(m.header)}

    def _on_weight(self, m):
        with self.lock:
            self._received('weight')
            self.snap['weights'].append({'t': self._t(m.header), 'net_g': m.net_g, 'std_g': m.std_g, 'valid': m.valid,
                                         'gross_g': m.gross_g, 'tare_g': m.tare_g, 'station': m.station,
                                         'subject': m.subject, 'samples': m.samples})
            del self.snap['weights'][:-100]

    def _on_result(self, m):
        with self.lock:
            self._received('dispense_result')
            self.snap['results'].append({'batch_id': m.batch_id, 'material_id': m.material_id, 'target_g': m.target_g, 'actual_g': m.actual_g,
                                         'error_pct': m.error_pct, 'verdict': VERDICTS.get(m.verdict, '?'), 'attempts': m.attempts})
            del self.snap['results'][:-20]
            self.inventory.observe(m.batch_id, m.material_id, m.actual_g, VERDICTS.get(m.verdict, '?'))

    def _on_dev(self, m):
        with self.lock:
            self._received('deviation')
            previous = self.snap['deviations'].get(m.deviation_id)
            if previous and (self._t(m.header) < previous['t'] or
                    (previous['decision'] != 'PENDING' and m.decision == Deviation.PENDING)):
                return
            self.snap['deviations'][m.deviation_id] = {'deviation_id': m.deviation_id, 'batch_id': m.batch_id, 'kind': KINDS.get(m.kind, '?'),
                                                       'material_id': m.material_id, 'detail': m.detail, 'requires_decision': m.requires_decision,
                                                       'decision': DECISIONS.get(m.decision, '?'), 'operator_id': m.operator_id, 't': self._t(m.header)}

    def _on_grip(self, m):
        with self.lock:
            self._received('gripper')
            self.snap['gripper'] = {'width_mm': m.width_mm, 'grip': m.grip_inferred, 'backend': m.backend, 'force_n': m.force_cmd_n,
                                    'busy': m.busy, 'safety_triggered': m.safety_triggered}

    def _on_event(self, m):
        with self.lock:
            self._received('event')
            self.snap['events'].append(dict(t=self._t(m.header), level=LEVELS.get(m.level, '?'),
                code=m.code, text=m.text, batch_id=m.batch_id))
            del self.snap['events'][:-100]

    def _on_cycle(self, m):
        with self.lock:
            self._received('scoop_cycle')
            self.snap['scoop_cycles'].append(dict(t=self._t(m.header), **ros_payload(m)))
            del self.snap['scoop_cycles'][:-50]

    def _guard_command(self, client):
        with self.lock:
            received = self.received['state']
            threshold = self.local_settings['ui_stale_after_s']
        if received is None or time.monotonic() - received > threshold:
            raise CommandUnavailable('공정 상태 수신이 지연되어 요청을 차단했습니다. 연결을 확인하세요')
        if not client.service_is_ready():
            raise CommandUnavailable('공정 서비스가 연결되지 않아 요청을 차단했습니다')

    # ── 명령 ────────────────────────────────────────────────────────
    def _call(self, client, req, timeout_s=3.0):
        if not client.wait_for_service(timeout_sec=1.0):
            return None
        try:
            fut = client.call_async(req)
        except Exception as exc:
            self.get_logger().warning(f'서비스 요청 실패: {exc}')
            return None
        done = threading.Event()
        fut.add_done_callback(lambda _: done.set())
        if not done.wait(timeout_s) or fut.cancelled() or fut.exception():
            return None
        return fut.result()

    def audit(self, action, actor, detail='', batch_id=None):
        if batch_id is None:
            with self.lock:
                batch_id = self.snap['state'].get('batch_id', '')
        m = CellEvent(level=CellEvent.INFO, code=f'HMI_{action}', text=f'{actor or "unknown"} {detail}'.strip(),
                      batch_id=batch_id)
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_event.publish(m)

    def _recipe_paths(self):
        directory = self.get_parameter('recipes_dir').value
        if not directory:
            directory = os.path.join(get_package_share_directory('gmp_bringup'), 'params', 'recipes')
        directory = os.path.realpath(os.path.expanduser(directory))
        paths = {}
        for path in sorted(glob.glob(os.path.join(directory, '*.yaml'))):
            resolved = os.path.realpath(path)
            # 외부 파일을 가리키는 심볼릭 링크도 주문 대상에서 제외한다.
            if os.path.isfile(resolved) and os.path.commonpath((directory, resolved)) == directory:
                paths[os.path.splitext(os.path.basename(path))[0]] = resolved
        return paths

    def _recipe(self, name):
        paths = self._recipe_paths()
        if not isinstance(name, str) or name not in paths:
            raise ValueError('선택 목록에 있는 레시피 이름을 사용하세요')
        try:
            spec = load_recipe(paths[name])  # C의 공통 스키마·검증만 사용한다.
            if any(not math.isfinite(it.target_g) or not math.isfinite(it.tol_pct) for it in spec.items):
                raise ValueError('목표량/허용 오차는 유한한 수여야 해요')
        except Exception as exc:
            raise ValueError(f'{name}: 레시피를 읽거나 검증할 수 없어요 ({exc})') from exc
        detail = {'name': name, 'product': spec.product or name,
                  'total_g': sum(it.target_g for it in spec.items),
                  'items': [{'material_id': it.material_id, 'target_g': it.target_g,
                             'tol_pct': it.tol_pct}
                            for it in spec.items]}
        return spec, detail

    def recipe_catalog(self):
        catalog = []
        for name in self._recipe_paths():
            try:
                catalog.append(self._recipe(name)[1])
            except ValueError as exc:
                self.get_logger().warning(str(exc))
        return catalog

    def recipes(self):
        return [recipe['name'] for recipe in self.recipe_catalog()]

    def snapshot(self):
        now = time.monotonic()
        with self.lock:
            data = copy.deepcopy(self.snap)
            ages = {key: None if value is None else max(0.0, now - value)
                    for key, value in self.received.items()}
            counts = dict(self.received_counts)
            fresh = ages['state'] is not None and ages['state'] <= self.local_settings['ui_stale_after_s']
            if not fresh:
                self.entry_granted = None
                self._pending_entry = None
            if self._pending_entry and now > self._pending_entry['expires_at']:
                self._pending_entry = None
            entry_granted = self.entry_granted if fresh and self.entry_batch_id == data['state'].get('batch_id', '') else None
            stale_after = self.local_settings['ui_stale_after_s']
            data['inventory'] = self._test_stock_locked() if self.test_inventory_enabled else self.inventory.snapshot()
            data['active_recipe'] = copy.deepcopy(self.active_recipe) if (
                self.active_recipe_batch_id and
                data['state'].get('batch_id') == self.active_recipe_batch_id) else None
        data['freshness'] = {f'{key}_age_s': value for key, value in ages.items()}
        data['freshness']['stale_after_s'] = stale_after
        data['interlock'] = {'entry_granted': entry_granted, 'source': 'this_hmi_service_response',
                             'note': '이 HMI가 받은 요청 응답. 다른 HMI의 조작 상태는 별도 계약이 없어 알 수 없습니다'}
        data['telemetry'] = {key: {'available': False, 'value': None, 'reason': '현재 공정 인터페이스에 없음'}
                             for key in ('robot_speed', 'emergency_stop', 'joint_positions', 'tcp_pose')}
        if not self.test_inventory_enabled:
            data['inventory']['scope'] = 'local_hmi_only'
            data['inventory']['enforced'] = False
            data['inventory']['refill_supported'] = False
            data['inventory'].update(order_allowed=True,
                                     order_block_reason='',
                                     blocked_materials=[], can_refill=False, fresh=False)
            data['inventory']['note'] += ' · 참고용 잔량: 주문 수락 여부는 C 공정이 판단합니다'
            for item in data['inventory']['items']:
                item.update(height_pct=None, height_low_latched=False, refill_ready=False)
        data['diagnostics'] = {
            'namespace': self.get_namespace(),
            'services': {'submit_order': bool(self.cli_order.service_is_ready()),
                         'qa_decision': bool(self.cli_qa.service_is_ready()),
                         'interlock': bool(self.cli_lock.service_is_ready())},
            'topics': {key: {'age_s': ages[key], 'count': counts[key]} for key in TOPICS}}
        if self.test_inventory_enabled:
            data['diagnostics']['services'].update({
                'test_refill_' + mid: bool(client.service_is_ready())
                for mid, client in self.cli_test_refill.items()})
            data['diagnostics']['topics']['test_inventory'] = {'age_s': data['inventory']['age_s']}
        data['deviations'] = list(data['deviations'].values())
        data['now'] = self.get_clock().now().nanoseconds / 1e9
        return json_finite(data)

    def submit(self, name, actor):
        self._guard_command(self.cli_order)
        with self.lock:
            state = self.snap['state']
            if state.get('mode') == 'DONE' and state.get('step') not in ('DONE', 'DISCARDED'):
                raise CommandUnavailable('물리적 완료 확인 대기: 공정의 최종 DONE 또는 DISCARDED 수신 후 주문하세요')
        spec, detail = self._recipe(name)
        if self.test_inventory_enabled:
            with self.lock:
                inv = self._test_stock_locked()
            if not inv['fresh']:
                self.audit('ORDER_REJECTED', actor, '시험 재고 미수신/지연', batch_id='')
                raise CommandUnavailable('시험 재고 미수신/지연: 주문을 차단했습니다')
            if inv['blocked_materials']:
                self.audit('ORDER_REJECTED', actor, '원료 높이 부족 ' + ','.join(inv['blocked_materials']), batch_id='')
                raise ValueError('원료 높이 부족: ' + ', '.join(inv['blocked_materials']) + ' · 해당 원료 만충 보충 완료가 필요합니다')
            items = {it['material_id']: it for it in inv['items']}
            missing = [it.material_id for it in spec.items if it.material_id not in items or
                       items[it.material_id]['available_g'] + 1e-8 < it.target_g]
            if missing:
                self.audit('ORDER_REJECTED', actor, '시험 원료 부족 ' + ','.join(missing), batch_id='')
                raise ValueError('시험 원료 부족/미등록: ' + ', '.join(missing) + ' · 만충 보충 후 주문하세요')
        r = Recipe(product=spec.product or name)
        r.header.stamp = self.get_clock().now().to_msg()
        for it in spec.items:
            r.items.append(RecipeItem(material_id=it.material_id, target_g=it.target_g, tol_pct=it.tol_pct))
        res = self._call(self.cli_order, SubmitOrder.Request(recipe=r))
        if res and res.accepted and res.batch_id:
            with self.lock:
                self.active_recipe = detail
                self.active_recipe_batch_id = res.batch_id
        self.audit('ORDER', actor, f'{name} → {res.batch_id if res else "no-response"} accepted={bool(res and res.accepted)}',
                   batch_id=res.batch_id if res and res.accepted else '')
        return res

    def qa(self, batch_id, deviation_id, decision, actor):
        self._guard_command(self.cli_qa)
        decision = command_number(decision)
        if decision not in (Deviation.APPROVED, Deviation.DISCARDED) or not deviation_id.strip():
            raise ValueError('일탈 ID와 승인(1) 또는 폐기(2)를 입력하세요')
        with self.lock:
            deviation = copy.deepcopy(self.snap['deviations'].get(deviation_id))
            current = self.snap['state'].get('batch_id', '')
        if not deviation or not deviation['requires_decision'] or deviation['decision'] != 'PENDING':
            raise ValueError('수신된 판정 대기 일탈이 없습니다')
        if deviation['batch_id'] != current or (batch_id and batch_id != deviation['batch_id']):
            raise ValueError('현재 배치와 일탈의 배치가 일치하지 않습니다')
        # v1.2에는 batch_id 요청 필드가 없다. 수신한 일탈로 로컬 검증만 수행한다.
        res = self._call(self.cli_qa, QaDecision.Request(deviation_id=deviation_id, decision=decision, operator_id=actor))
        self.audit('QA_APPROVE' if decision == Deviation.APPROVED else 'QA_DISCARD',
                   actor, f'{deviation_id} accepted={bool(res and res.accepted)}', batch_id=deviation['batch_id'])
        return res

    def interlock(self, request, reason, actor):
        self._guard_command(self.cli_lock)
        request = command_number(request)
        if request not in (getattr(InterlockRequest.Request, 'ENTER', 1), getattr(InterlockRequest.Request, 'EXIT', 2)):
            raise ValueError('인터락 요청은 진입(1) 또는 복귀(2)여야 해요')
        with self.lock:
            request_batch = self.snap['state'].get('batch_id', '')
            self.entry_granted = None
            self._pending_entry = None
        res = self._call(self.cli_lock, InterlockRequest.Request(request=request, reason=reason), timeout_s=15.0)
        if res and res.granted and request == getattr(InterlockRequest.Request, 'ENTER', 1):
            with self.lock:
                now = time.monotonic()
                received = self.received['state']
                threshold = self.local_settings['ui_stale_after_s']
                if (self.snap['state'].get('batch_id', '') == request_batch and
                        received is not None and now - received <= threshold):
                    self.entry_batch_id = request_batch
                    if self.snap['state'].get('mode') in ('PAUSED', 'DEVIATION'):
                        self.entry_granted = True
                    else:
                        self._pending_entry = {'expires_at': now + threshold}
        self.audit('INTERLOCK_ENTER' if request == getattr(InterlockRequest.Request, 'ENTER', 1) else 'INTERLOCK_EXIT', actor, f'{reason} granted={bool(res and res.granted)}')
        return res


def build_app(node: HmiRosNode, db: CellDB, admin_store=None):
    from flask import Flask, jsonify, render_template, request, session, g, Response
    share = get_package_share_directory('gmp_hmi')
    app = Flask(__name__, template_folder=os.path.join(share, 'templates'),
                static_folder=os.path.join(share, 'static'))
    app.secret_key = secrets.token_hex(32)  # 재시작하면 모든 이전 로그인 세션 폐기
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=32 * 1024)
    store = admin_store or node.admin_store
    app.extensions['admin_store'] = store
    login_attempts = {}
    login_lock = threading.Lock()
    settings_lock = threading.Lock()
    command_lock = getattr(node, 'command_lock', threading.Lock())

    def payload():
        data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
        if not isinstance(data, dict):
            raise ValueError('JSON 객체 또는 폼으로 입력하세요')
        return data

    def csrf_token():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        return session['csrf']

    def session_state(**extra):
        return dict(authenticated=g.user is not None, user=g.user, csrf_token=csrf_token(),
                    setup_required=store.setup_required(), **extra)

    @app.before_request
    def authenticate_request():
        g.user = None
        username = session.get('username')
        if username:
            user = store.user(username)
            if user and user['active'] and user['version'] == session.get('version'):
                g.user = user
            else:
                session.clear()
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            given = request.headers.get('X-CSRF-Token', '')
            expected = session.get('csrf', '')
            if not given or not expected or not given.isascii() or not hmac.compare_digest(given, expected):
                return jsonify(ok=False, message='세션 확인이 필요합니다. 새로고침 후 다시 시도하세요'), 403

    @app.after_request
    def response_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    def requires(*roles):
        def decorate(fn):
            @wraps(fn)
            def checked(*args, **kwargs):
                if not g.user:
                    return jsonify(ok=False, message='로그인이 필요합니다'), 401
                if roles and g.user['role'] not in roles:
                    return jsonify(ok=False, message='이 기능을 사용할 권한이 없습니다'), 403
                return fn(*args, **kwargs)
            return checked
        return decorate

    @app.errorhandler(sqlite3.Error)
    def database_unavailable(exc):
        return jsonify(ok=False, message='기록 DB 조회 대기: record_node와 DB 경로를 확인하세요'), 503

    @app.errorhandler(ValueError)
    def invalid_input(exc):
        return jsonify(ok=False, message=str(exc)), 400

    @app.errorhandler(CommandUnavailable)
    def command_unavailable(exc):
        return jsonify(ok=False, message=str(exc)), 503

    @app.route('/')
    def index():
        return render_template('index.html', hmi_config={'demo': False, 'recipes': []})

    @app.route('/demo')
    def demo():
        return render_template('index.html', hmi_config={'demo': True, 'recipes': ['demo_batch']})

    @app.get('/auth/session')
    def auth_session():
        return jsonify(session_state())

    @app.post('/auth/login')
    def login():
        data = payload()
        username, password = data.get('username', ''), data.get('password', '')
        if not isinstance(username, str) or not isinstance(password, str):
            raise ValueError('계정과 비밀번호를 문자열로 입력하세요')
        key = (request.remote_addr, username[:64])
        now = time.monotonic()
        with login_lock:
            recent = [t for t in login_attempts.get(key, []) if now - t < 60]
            if len(recent) >= 10:
                return jsonify(ok=False, message='로그인 시도가 많습니다. 1분 후 다시 시도하세요'), 429
            # 키 개수도 제한하여 임의 계정 이름 반복으로 메모리가 늘지 않게 한다.
            if len(login_attempts) > 1024:
                login_attempts.clear()
            login_attempts[key] = recent + [now]
        user = store.authenticate(username, password)
        if not user:
            return jsonify(ok=False, message='계정 또는 비밀번호를 확인하세요'), 401
        with login_lock:
            login_attempts.pop(key, None)
        session.clear()
        session.update(username=user['username'], version=user['version'])
        session.permanent = True
        g.user = user
        node.audit('LOGIN', user['username'], 'session login', batch_id='')
        return jsonify(session_state(ok=True, message='로그인되었습니다'))

    @app.post('/auth/logout')
    def logout():
        if g.user:
            node.audit('LOGOUT', g.user['username'], 'session logout', batch_id='')
        session.clear()
        g.user = None
        return jsonify(session_state(ok=True, message='로그아웃되었습니다'))

    @app.get('/users')
    @requires('admin')
    def users():
        return jsonify(store.users())

    @app.post('/users')
    @requires('admin')
    def create_user():
        data = payload()
        active = data.get('active', True)
        if active in ('true', 'false'):
            active = active == 'true'
        user = store.create_user(data.get('username'), data.get('password'), data.get('role', 'viewer'), active)
        node.audit('USER_CREATE', g.user['username'], f"user={user['username']} role={user['role']}", batch_id='')
        return jsonify(ok=True, user=user, message='계정을 생성했습니다'), 201

    @app.post('/users/<username>')
    @requires('admin')
    def update_user(username):
        changes = payload()
        if changes.get('active') in ('true', 'false'):
            changes['active'] = changes['active'] == 'true'
        user = store.update_user(username, changes)
        node.audit('USER_UPDATE', g.user['username'],
                   f"user={username} fields={','.join(sorted(changes))}", batch_id='')
        return jsonify(ok=True, user=user, message='계정을 수정했습니다. 해당 계정은 다시 로그인해야 합니다')

    def settings_public(settings):
        return dict(settings, scope='local_hmi_only',
                    note='HMI 화면 기준 설정입니다. 실제 원료 재고·보충·로봇 설정을 변경하지 않습니다. 세션 소비량은 재시작 시 초기화됩니다.')

    @app.get('/settings')
    @requires()
    def settings():
        return jsonify(settings_public(store.settings()))

    @app.post('/settings')
    @requires('admin')
    def save_settings():
        changes = payload()
        changes.pop('scope', None)
        changes.pop('note', None)
        with settings_lock:
            settings = store.update_settings(changes)
            node.apply_settings(settings)
        node.audit('SETTINGS', g.user['username'], f"local_hmi_only fields={','.join(sorted(changes))}", batch_id='')
        return jsonify(ok=True, message='화면 기준 설정을 저장했습니다', **settings_public(settings))

    @app.get('/status')
    @requires()
    def status():
        return jsonify(node.snapshot())

    @app.get('/recipes')
    @requires()
    def recipes():
        return jsonify(node.recipe_catalog())

    def command(call):
        if not command_lock.acquire(blocking=False):
            return jsonify(ok=False, message='다른 공정 요청을 처리 중입니다. 응답을 기다리세요'), 409
        try:
            res = call()
        finally:
            command_lock.release()
        if res is None:
            return jsonify(ok=False, message='응답 없음 · 처리 결과 미확인. 재전송하지 말고 상태를 확인하세요', uncertain=True), 503
        accepted = getattr(res, 'accepted', getattr(res, 'granted', getattr(res, 'success', False)))
        body = dict(ok=bool(accepted), message=res.message)
        if hasattr(res, 'batch_id'):
            body['batch_id'] = res.batch_id
        if hasattr(res, 'granted'):
            body['granted'] = bool(res.granted)
        return jsonify(body)

    @app.post('/order')
    @requires('operator', 'admin')
    def order():
        data = payload()
        recipe = data.get('recipe', '')
        if not isinstance(recipe, str) or not recipe:
            raise ValueError('레시피를 선택하세요')
        return command(lambda: node.submit(recipe, g.user['username']))

    @app.post('/test/refill')
    @requires('operator', 'admin')
    def test_refill():
        data = payload()
        return command(lambda: node.refill_test(g.user['username'], data.get('material_id'), data.get('confirmed_full')))

    @app.post('/test/height')
    @requires('operator', 'admin')
    def test_height():
        data = payload()
        node.report_test_height(g.user['username'], data.get('material_id'), data.get('height_pct'))
        return jsonify(ok=True, message='시험 높이 신호를 전송했습니다. 공정의 재고 갱신을 확인하세요', applied=False)

    @app.post('/qa')
    @requires('qa', 'admin')
    def qa():
        data = payload()
        try:
            decision = command_number(data.get('decision', -1))
        except (ValueError, TypeError):
            raise ValueError('승인(1) 또는 폐기(2)를 선택하세요')
        batch_id, deviation_id = data.get('batch_id', ''), data.get('deviation_id', '')
        if not isinstance(batch_id, str) or not isinstance(deviation_id, str):
            raise ValueError('배치·일탈 ID는 문자열이어야 합니다')
        return command(lambda: node.qa(batch_id, deviation_id, decision, g.user['username']))

    @app.post('/collection-confirm')
    @requires('qa', 'admin')
    def collection_confirm():
        data = payload()
        if data.get('passbox_done_empty') is not True or data.get('reject_bin_empty') is not True:
            raise ValueError('완성품 패스박스와 폐기함을 모두 비웠음을 확인하세요')
        # 적재 카운터의 권위자는 아직 C 공정이다. HMI는 사람의 회수 확인만 감사 기록으로 남긴다.
        node.audit('COLLECTION_CONFIRMED', g.user['username'],
                   'passbox_done_empty=true reject_bin_empty=true counter_reset=not_connected', batch_id='')
        return jsonify(ok=True, message='회수 확인을 기록했습니다. 공정 적재 카운터 초기화 연동은 아직 준비 중입니다.')

    @app.post('/interlock')
    @requires('operator', 'admin')
    def interlock():
        data = payload()
        try:
            req = command_number(data.get('request', -1))
        except (ValueError, TypeError):
            raise ValueError('진입(1) 또는 복귀(2)를 선택하세요')
        reason = data.get('reason', '')
        if not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError('사유는 1000자 이내 문자열이어야 합니다')
        return command(lambda: node.interlock(req, reason, g.user['username']))

    def filters(extra=()):
        result = {}
        for key in ('start', 'end'):
            value = request.args.get(key)
            if value not in (None, ''):
                try:
                    parsed = float(value)
                except (TypeError, ValueError):
                    raise ValueError('검색 시각은 epoch 초 숫자여야 합니다')
                if not math.isfinite(parsed):
                    raise ValueError('검색 시각은 유한한 숫자여야 합니다')
                result[key] = parsed
        if 'start' in result and 'end' in result and result['start'] >= result['end']:
            raise ValueError('검색 종료 시각은 시작보다 뒤여야 합니다')
        for key in ('query',) + extra:
            value = request.args.get(key)
            if value:
                if len(value) > 200:
                    raise ValueError('검색어는 200자 이내로 입력하세요')
                result[key] = value
        return result

    def safe_filename(value):
        return ''.join(c if (c.isascii() and c.isalnum()) or c in '-_.' else '_' for c in value)[:100] or 'batch'

    @app.get('/history')
    @requires()
    def history():
        return jsonify(json_finite(db.batches(500, **filters(('result',)))))

    @app.get('/history.csv')
    @requires()
    def history_csv():
        rows = db.batches(10000, **filters(('result',)))
        fields = ['batch_id', 'product', 'started_at', 'finished_at', 'result', 'cycle_s', 'n_items', 'n_dev', 'n_cycles', 'note']
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            # 스프레드시트 수식 주입을 막고 원래 숫자는 숫자로 보존한다.
            writer.writerow({key: "'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')) else value
                             for key, value in row.items()})
        return Response('\ufeff' + output.getvalue(), mimetype='text/csv',
                        headers={'Content-Disposition': 'attachment; filename="hmi_history.csv"'})

    @app.get('/batch/<batch_id>')
    @requires()
    def batch(batch_id):
        data = db.batch(batch_id)
        return (jsonify(json_finite(data)), 200) if data else (jsonify(ok=False, message='배치가 없습니다'), 404)

    @app.get('/batch/<batch_id>/download')
    @requires()
    def batch_download(batch_id):
        data = db.batch(batch_id)
        if not data:
            return jsonify(ok=False, message='배치가 없습니다'), 404
        text = json.dumps(json_finite(data), ensure_ascii=False, indent=2, allow_nan=False)
        return Response(text, mimetype='application/json',
                        headers={'Content-Disposition': f'attachment; filename="{safe_filename(batch_id)}.json"'})

    @app.get('/kpi')
    @requires()
    def kpi():
        return jsonify(json_finite(db.kpis(**filters(('result',)))))

    @app.get('/audit')
    @requires()
    def audit():
        return jsonify(json_finite(db.recent_audit(500, **filters(('actor', 'action')))))

    @app.get('/events')
    @requires()
    def events():
        return jsonify(json_finite(db.recent_events(500, **filters(('level',)))))

    return app

def main(args=None):
    rclpy.init(args=args)
    node = HmiRosNode()
    try:
        import flask  # noqa: F401
    except ImportError:
        node.get_logger().error('flask 없음 — sudo apt install python3-flask (docs/setup.md)')
        rclpy.shutdown(); return
    db = ReadOnlyCellDB(node.get_parameter('db_path').value)
    ex = MultiThreadedExecutor(num_threads=2)
    ex.add_node(node)
    threading.Thread(target=ex.spin, daemon=True, name='rclpy-executor').start()
    app = build_app(node, db)
    port = int(node.get_parameter('port').value)
    if node.admin_store.setup_required():
        node.get_logger().warning('최초 관리자 계정이 없습니다. 별도 터미널에서 python3 -m gmp_hmi.core.admin_store 로 초기화 후 HMI를 재시작하세요')
    node.get_logger().info(f'HMI http://0.0.0.0:{port}  (셀 밖 QA 는 같은 네트워크의 다른 기기에서 접속)')
    try:
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    finally:
        db.close(); ex.shutdown(); node.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
