"""V3 인증·권한·최신 메시지 매핑·Flask 통합 검사. 실제 DDS 통신 검사는 아니다.

실행 시 gmp_process의 공통 레시피 모듈도 PYTHONPATH에 있어야 한다.
"""
from concurrent.futures import Future
import importlib.util
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

import pytest

from gmp_hmi.core.db import CellDB
from gmp_hmi.core.session_inventory import SessionInventory

PACKAGE = Path(__file__).resolve().parents[1]


class Message:
    def __init__(self, **values):
        self.header = SimpleNamespace(stamp=SimpleNamespace(sec=100, nanosec=0))
        self.items = []
        self.__dict__.update(values)


class Client:
    def __init__(self):
        self.ready = True
        self.response = SimpleNamespace(accepted=True, granted=True, batch_id='B1', message='ok')
        self.calls = []

    def service_is_ready(self):
        return self.ready

    def wait_for_service(self, **_):
        return self.ready

    def call_async(self, request):
        self.calls.append(request)
        result = Future()
        result.set_result(self.response)
        return result


class GoalHandle:
    def __init__(self, result_type):
        self.accepted = True
        self.result_type = result_type

    def get_result_async(self):
        future = Future()
        future.set_result(SimpleNamespace(result=self.result_type(
            success=True, items_done=3, deviations=0,
            result='DONE', message='ok')))
        return future


class ActionClientStub:
    def __init__(self, _node, action_type, _name):
        self.ready = True
        self.action_type = action_type
        self.calls = []

    def server_is_ready(self):
        return self.ready

    def wait_for_server(self, **_):
        return self.ready

    def send_goal_async(self, goal, feedback_callback=None):
        self.calls.append(goal)
        result = Future()
        result.set_result(GoalHandle(self.action_type.Result))
        return result


class ActionServerStub:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class RosStub:
    def __init__(self, *_):
        self.params = {}
        self.published = []

    def declare_parameter(self, key, value):
        self.params[key] = value

    def get_parameter(self, key):
        return SimpleNamespace(value=self.params[key])

    def create_subscription(self, *args):
        return args

    def create_client(self, *_):
        return Client()

    def create_publisher(self, *_):
        return SimpleNamespace(publish=self.published.append)

    def get_namespace(self):
        return '/hmi_test'

    def get_clock(self):
        return SimpleNamespace(now=lambda: SimpleNamespace(
            nanoseconds=100_000_000_000,
            to_msg=lambda: SimpleNamespace(sec=100, nanosec=0)))

    def get_logger(self):
        return SimpleNamespace(warning=lambda *_: None)


@pytest.fixture
def backend(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("GMP_HMI_ADMIN_PASSWORD", raising=False)
    # 스텁을 명시적으로 주입한다. DDS/ROS 서비스 전송의 성공으로 해석하지 않는다.
    modules = {}
    for name in ('rclpy', 'rclpy.action', 'rclpy.callback_groups',
                 'rclpy.executors', 'rclpy.node', 'rclpy.qos',
                 'ament_index_python', 'ament_index_python.packages',
                 'gmp_interfaces', 'gmp_interfaces.action', 'gmp_interfaces.msg', 'gmp_interfaces.srv',
                 'std_msgs', 'std_msgs.msg', 'std_srvs', 'std_srvs.srv'):
        modules[name] = ModuleType(name)
        monkeypatch.setitem(sys.modules, name, modules[name])
    modules['std_msgs.msg'].String = Message
    modules['std_srvs.srv'].Trigger = SimpleNamespace(Request=Message)
    modules['rclpy.action'].ActionClient = ActionClientStub
    modules['rclpy.action'].ActionServer = ActionServerStub
    modules['rclpy.action'].GoalResponse = SimpleNamespace(ACCEPT=1, REJECT=0)
    modules['rclpy.action'].CancelResponse = SimpleNamespace(ACCEPT=1, REJECT=0)
    modules['rclpy.callback_groups'].ReentrantCallbackGroup = lambda: object()
    modules['rclpy.node'].Node = RosStub
    modules['rclpy.executors'].MultiThreadedExecutor = object
    modules['rclpy.qos'].DurabilityPolicy = SimpleNamespace(TRANSIENT_LOCAL=1)
    modules['rclpy.qos'].ReliabilityPolicy = SimpleNamespace(BEST_EFFORT=1)
    modules['rclpy.qos'].QoSProfile = lambda **values: values
    modules['ament_index_python.packages'].get_package_share_directory = lambda _: str(PACKAGE)
    constants = {
        'CellState': dict(IDLE=0, RUNNING=1, PAUSED=2, DEVIATION=3, ERROR=4, DONE=5),
        'Deviation': dict(PENDING=0, APPROVED=1, DISCARDED=2), 'CellEvent': dict(INFO=0),
    }
    for name in ('CellEvent', 'CellState', 'Deviation', 'DispenseResult',
                 'GripperState', 'Recipe', 'RecipeItem', 'WeightReading', 'ScoopCycle'):
        if name == 'CellState':
            # rosidl처럼 클래스 dict의 정수가 아니라 메타클래스 property로 상수를 노출한다.
            meta = type('CellStateMeta', (type,), {
                key: property(lambda cls, value=value: value)
                for key, value in constants[name].items()})
            generated = meta(name, (Message,), {})
            assert 'RUNNING' not in vars(generated) and generated.RUNNING == 1
        else:
            generated = type(name, (Message,), constants.get(name, {}))
        setattr(modules['gmp_interfaces.msg'], name, generated)
    modules['gmp_interfaces.action'].RunBatch = type('RunBatch', (), {
        'Goal': type('Goal', (Message,), {}),
        'Result': type('Result', (Message,), {}),
        'Feedback': type('Feedback', (Message,), {})})
    for name in ('QaDecision', 'InterlockRequest', 'RecoverSafety'):
        response_constants = dict(ENTER=1, EXIT=2) if name == 'InterlockRequest' else {}
        # .srv의 응답 영역에 있는 상수는 실제 생성 코드와 같이 Response에만 둔다.
        setattr(modules['gmp_interfaces.srv'], name, type(name, (), {
            'Request': type('Request', (Message,), {}),
            'Response': type('Response', (Message,), response_constants)}))
    spec = importlib.util.spec_from_file_location('hmi_backend_under_test',
                                                 PACKAGE / 'gmp_hmi/nodes/hmi_web_node.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def node(backend, tmp_path):
    node = backend.HmiRosNode()
    node.params['recipes_dir'] = str(tmp_path)
    (tmp_path / 'demo_batch.yaml').write_text(
        'product: DEMO-01\nitems:\n'
        '  - {material_id: A, target_g: 200, tol_pct: 5}\n'
        '  - {material_id: B, target_g: 150, tol_pct: 5}\n'
        '  - {material_id: C, target_g: 100, tol_pct: 5, grade: ACTIVE}\n', encoding='utf-8')
    return node


def state(batch='B1', mode=1):
    return Message(batch_id=batch, mode=mode, step='SCOOP', item_index=0,
                   station='material_1', note='')


def result(batch='B1', actual=50.0, verdict=0):
    return Message(batch_id=batch, material_id='A', target_g=50.0, actual_g=actual,
                   error_pct=0.0, verdict=verdict, attempts=1)


def test_inventory_unknown_then_cumulative_dedup_and_discard():
    unknown = SessionInventory(['A'], [0.0], [-1.0]).snapshot()
    assert unknown['mode'] == 'unconfigured'
    assert unknown['items'][0]['remaining_g'] is None
    assert unknown['items'][0]['percent'] is None
    inventory = SessionInventory(['A'], [500.0], [500.0])
    assert not inventory.observe('B1', 'A', 40, 'UNDER')
    assert inventory.observe('B1', 'A', 50, 'OK')
    assert not inventory.observe('B1', 'A', 50, 'OK')
    assert not inventory.observe('B1', 'A', 45, 'OVER')
    assert inventory.observe('B1', 'A', 55, 'OVER')
    assert inventory.snapshot()['items'][0]['remaining_g'] == 445
    assert inventory.observe('B2', 'A', 100, 'OK')
    assert inventory.snapshot()['items'][0]['remaining_g'] == 345
    for value in (-1, float('nan'), float('inf'), 'not a number'):
        assert not inventory.observe('B3', 'A', value, 'OK')
    assert inventory.snapshot()['items'][0]['consumed_g'] == 155
    assert not inventory.observe('B3', 'UNKNOWN', 20, 'OK')


@pytest.mark.parametrize('capacity,initial', [(float('nan'), 10), (500, float('inf')),
                                             (-1, 0), (500, -2), (500, 501), (0, 100)])
def test_invalid_inventory_configuration(capacity, initial):
    with pytest.raises(ValueError):
        SessionInventory(['A'], [capacity], [initial])



PASSWORD = 'temporary-test-secret-42'


def login(client, username='admin', password=PASSWORD):
    token = client.get('/auth/session').json['csrf_token']
    response = client.post('/auth/login', json={'username': username, 'password': password},
                           headers={'X-CSRF-Token': token})
    assert response.status_code == 200, response.json
    return response.json['csrf_token']


def post(client, token, path, data):
    return client.post(path, json=data, headers={'X-CSRF-Token': token})


@pytest.fixture
def app_db(backend, node, tmp_path):
    node.admin_store.create_user('admin', PASSWORD, 'admin')
    db = CellDB(str(tmp_path / 'cell.db'), str(PACKAGE / 'config/schema.sql'))
    app = backend.build_app(node, db)
    app.testing = True
    yield app, db
    db.close()


def enable_test_inventory(node):
    import json
    from gmp_hmi.core.trial_inventory import TrialInventory
    node.test_inventory_enabled = True
    node.cli_test_refill = {mid: Client() for mid in ('A', 'B', 'C')}
    node.pub_test_height = SimpleNamespace(publish=node.published.append)
    node._on_test_inventory(Message(data=json.dumps(TrialInventory(['A', 'B', 'C'], [1000.] * 3, [1000.] * 3).snapshot(True))))


def test_recipe_contract_no_removed_fields(node, tmp_path):
    enable_test_inventory(node)
    catalog = node.recipe_catalog()
    assert catalog[0]['product'] == 'DEMO-01'
    assert catalog[0]['total_g'] == 450
    assert set(catalog[0]['items'][0]) == {'material_id', 'target_g', 'tol_pct'}
    node._on_state(state('', 0))
    for name in ('../demo_batch', '/tmp/demo_batch', 'missing', 'demo_batch.yaml'):
        with pytest.raises(ValueError):
            node.submit(name, 'operator')
    node.submit('demo_batch', 'operator')
    fields = vars(node.act_batch.calls[-1].recipe.items[0])
    assert 'grade' not in fields and 'scoop_id' not in fields
    assert node.snapshot()['active_recipe'] is None
    node._on_state(state(node.act_batch.calls[-1].recipe.batch_id))
    assert node.snapshot()['active_recipe']['name'] == 'demo_batch'
    node._on_state(state('EXTERNAL'))
    assert node.snapshot()['active_recipe'] is None


def test_auth_rbac_csrf_actor_and_revocation(app_db, node):
    enable_test_inventory(node)
    app, _ = app_db
    admin = app.test_client()
    assert admin.get('/status').status_code == 401
    assert admin.post('/auth/login', json={'username': 'admin', 'password': PASSWORD}).status_code == 403
    token = login(admin)
    for name, role in [('view', 'viewer'), ('op', 'operator'), ('qa', 'qa')]:
        assert post(admin, token, '/users', dict(username=name, password=PASSWORD, role=role)).status_code == 201
    viewer = app.test_client()
    view_token = login(viewer, 'view')
    assert viewer.get('/status').status_code == 200
    assert viewer.get('/users').status_code == 403
    assert post(viewer, view_token, '/order', {'recipe': 'demo_batch'}).status_code == 403
    op = app.test_client()
    op_token = login(op, 'op')
    assert post(op, op_token, '/qa', {'deviation_id': 'D1', 'decision': 1}).status_code == 403
    node._on_state(state('', 0))
    assert post(op, op_token, '/order', {'recipe': 'demo_batch', 'actor': 'spoof-admin'}).json['ok']
    assert node.published[-1].text.startswith('op ')
    assert 'spoof-admin' not in node.published[-1].text
    assert post(admin, token, '/users/op', {'role': 'viewer'}).json['ok']
    assert op.get('/status').status_code == 401
    assert post(admin, token, '/users/admin', {'active': False}).status_code == 400
    listing = admin.get('/users').get_data(as_text=True)
    assert 'password' not in listing and 'pbkdf2' not in listing
    assert PASSWORD not in node.admin_store.path.read_text()


def test_password_and_disabled_user_invalidate_sessions(app_db):
    app, _ = app_db
    admin = app.test_client(); token = login(admin)
    post(admin, token, '/users', dict(username='operator', password=PASSWORD, role='operator'))
    first = app.test_client(); login(first, 'operator')
    assert post(admin, token, '/users/operator', {'password': PASSWORD + 'new'}).json['ok']
    assert first.get('/status').status_code == 401
    second = app.test_client(); login(second, 'operator', PASSWORD + 'new')
    assert post(admin, token, '/users/operator', {'active': False}).json['ok']
    assert second.get('/status').status_code == 401


def test_new_weight_subject_scoop_cycle_and_unavailable_telemetry(node):
    node._on_state(state())
    weight = Message(net_g=50, gross_g=65, tare_g=15, std_g=.2, valid=True,
                     station='scale', subject='scoop', samples=20)
    node._on_weight(weight)
    node._on_cycle(Message(batch_id='B1', material_id='A', attempt=1, pre_pour=weight,
                           delivered_g=45, valid=True))
    snapshot = node.snapshot()
    reading = snapshot['weights'][0]
    assert reading['subject'] == 'scoop' and reading['samples'] == 20
    assert not {'batch_id', 'material_id', 'phase'} & set(reading)
    assert snapshot['scoop_cycles'][0]['pre_pour']['subject'] == 'scoop'
    assert snapshot['telemetry']['emergency_stop']['available'] is False
    assert snapshot['telemetry']['robot_speed']['value'] is None
    assert snapshot['interlock']['entry_granted'] is None


def test_qa_contract_pending_check_and_command_staleness(app_db, node):
    enable_test_inventory(node)
    app, _ = app_db; client = app.test_client(); token = login(client)
    node._on_state(state())
    node._on_dev(Message(deviation_id='D1', batch_id='B1', kind=9, material_id='',
                         detail='verify mismatch', requires_decision=True, decision=0, operator_id=''))
    assert post(client, token, '/qa', {'deviation_id': 'D1', 'decision': 1, 'batch_id': 'wrong'}).status_code == 400
    assert post(client, token, '/qa', {'deviation_id': 'D1', 'decision': 1.5}).status_code == 400
    assert post(client, token, '/interlock', {'request': True}).status_code == 400
    assert post(client, token, '/qa', {'deviation_id': 'D1', 'decision': 1, 'actor': 'spoof'}).json['ok']
    request = node.cli_qa.calls[-1]
    assert not hasattr(request, 'batch_id')
    assert request.operator_id == 'admin' and request.decision == 1
    assert node.published[-1].batch_id == 'B1'
    node.received['state'] -= 20
    before = len(node.act_batch.calls)
    assert post(client, token, '/order', {'recipe': 'demo_batch'}).status_code == 503
    assert len(node.act_batch.calls) == before
    node._on_state(state())
    node.act_batch.ready = False
    assert post(client, token, '/order', {'recipe': 'demo_batch'}).status_code == 503
    node.act_batch.ready = True
    node._on_state(state(mode=0, batch=''))
    node._send_batch_goal = lambda *args, **kwargs: None
    response = post(client, token, '/order', {'recipe': 'demo_batch'})
    assert response.status_code == 503 and response.json['uncertain'] is True


def test_settings_persist_without_resetting_consumption(app_db, node, backend):
    app, _ = app_db; client = app.test_client(); token = login(client)
    config = dict(inventory_material_ids=['A'], inventory_capacity_g=[500], inventory_initial_g=[500])
    assert post(client, token, '/settings', config).json['scope'] == 'local_hmi_only'
    node._on_result(result(actual=50))
    assert node.snapshot()['inventory']['items'][0]['remaining_g'] == 450
    assert post(client, token, '/settings', {'inventory_low_pct': 30}).json['ok']
    assert node.snapshot()['inventory']['items'][0]['remaining_g'] == 450
    assert backend.AdminStore(node.admin_store.path).settings()['inventory_low_pct'] == 30
    assert post(client, token, '/settings', {'ui_stale_after_s': 0}).status_code == 400
    assert post(client, token, '/settings', {'inventory_initial_g': [501]}).status_code == 400
    assert post(client, token, '/settings', {'robot_speed': 200}).status_code == 400


def test_filtered_reports_downloads_and_readonly_recovery(backend, node, tmp_path):
    node.admin_store.create_user('admin', PASSWORD, 'admin')
    path = tmp_path / 'not_created.db'
    reader = backend.ReadOnlyCellDB(str(path))
    client = backend.build_app(node, reader).test_client(); login(client)
    assert client.get('/history').status_code == 503
    assert not path.exists()
    writer = CellDB(str(path), str(PACKAGE / 'config/schema.sql'))
    writer.start_batch('B1', 100, 'One')
    writer.finish_batch('B1', 120, 'DONE')
    writer.start_batch('B2', 200, 'Two')
    writer.finish_batch('B2', 240, 'ERROR')
    assert len(client.get('/history?start=100&end=200&result=DONE').json) == 1
    assert client.get('/kpi?start=100&end=200').json['batches'] == 1
    assert client.get('/history?start=200&end=100').status_code == 400
    assert client.get('/history?start=NaN').status_code == 400
    csv = client.get('/history.csv?query=One')
    assert csv.status_code == 200 and 'attachment' in csv.headers['Content-Disposition']
    assert 'B1' in csv.text and 'B2' not in csv.text
    assert client.get('/batch/B1/download').json['batch_id'] == 'B1'
    assert client.get('/events').json == []
    assert client.get('/audit').json == []
    with pytest.raises(sqlite3.OperationalError):
        reader._rows('DELETE FROM batches')
    assert len(writer.batches()) == 2
    writer.close()


def test_entry_ack_never_resurrects_and_resolved_deviation_never_regresses(node):
    node._on_state(state(mode=2))
    node.interlock(1, 'REFILL', 'operator')
    assert node.snapshot()['interlock']['entry_granted'] is True
    node.received['state'] -= 10
    assert node.snapshot()['interlock']['entry_granted'] is None
    node._on_state(state(mode=2))
    assert node.snapshot()['interlock']['entry_granted'] is None
    node.interlock(1, 'REFILL', 'operator')
    node._on_state(state(mode=1))
    assert node.snapshot()['interlock']['entry_granted'] is None
    node._on_state(state(mode=2))
    node.interlock(1, 'REFILL', 'operator')
    node._on_state(state('different', mode=2))
    assert node.snapshot()['interlock']['entry_granted'] is None
    dev = Message(deviation_id='D1', batch_id='B1', kind=0, material_id='A',
                  detail='resolved', requires_decision=True, decision=1, operator_id='qa')
    node._on_dev(dev)
    dev.decision = 0
    dev.header.stamp.sec = 101
    node._on_dev(dev)
    assert node.snapshot()['deviations'][0]['decision'] == 'APPROVED'


def test_interlock_reply_before_paused_has_bounded_pending_ack(node):
    node._on_state(state(mode=1))
    response = node.interlock(1, 'REFILL', 'operator')
    assert response.granted is True
    assert node.snapshot()['interlock']['entry_granted'] is None
    node._on_state(state(mode=1))  # 응답 직후 이전 단계 heartbeat가 먼저 올 수 있다.
    node._on_state(state(mode=2))
    assert node.snapshot()['interlock']['entry_granted'] is True
    node._on_state(state(mode=1))
    node._on_state(state(mode=2))
    assert node.snapshot()['interlock']['entry_granted'] is None

    node._on_state(state(mode=1))
    node.interlock(1, 'REFILL', 'operator')
    node._pending_entry['expires_at'] -= 60
    node._on_state(state(mode=2))
    assert node.snapshot()['interlock']['entry_granted'] is None

    node._on_state(state(mode=1))
    node.interlock(1, 'REFILL', 'operator')
    node.received['state'] -= 60
    node._on_state(state(mode=2))
    assert node.snapshot()['interlock']['entry_granted'] is None

    node._on_state(state(mode=1))
    node.interlock(1, 'REFILL', 'operator')
    node.interlock(2, 'REFILL', 'operator')
    node._on_state(state(mode=2))
    assert node.snapshot()['interlock']['entry_granted'] is None

    node._on_state(state(mode=1))
    node.interlock(1, 'REFILL', 'operator')
    node._on_state(state('new-batch', mode=2))
    assert node.snapshot()['interlock']['entry_granted'] is None


def test_order_rejects_unknown_done_step_but_accepts_discarded(app_db, node):
    enable_test_inventory(node)
    app, _ = app_db
    client = app.test_client(); token = login(client)
    early = state(mode=5)
    early.step = 'CARRY'
    node._on_state(early)
    response = post(client, token, '/order', {'recipe': 'demo_batch'})
    assert response.status_code == 503
    assert '물리적 완료 확인 대기' in response.json['message']
    assert node.act_batch.calls == []
    # 같은 mode 값이어도 실제 이송 종료 step 확인 후 주문 가능.
    early.step = 'DISCARDED'
    node._on_state(early)
    assert post(client, token, '/order', {'recipe': 'demo_batch'}).json['ok']


def test_qa_enter_grant_survives_deviation_heartbeat_until_exit(node):
    node._on_state(state(mode=3))
    assert node.interlock(1, 'QA', 'qa').granted
    assert node.snapshot()['interlock']['entry_granted'] is True
    node._on_state(state(mode=3))
    assert node.snapshot()['interlock']['entry_granted'] is True
    node.interlock(2, 'QA', 'qa')
    assert node.snapshot()['interlock']['entry_granted'] is None


@pytest.mark.parametrize('mode,step,note,expected', [
    (2, 'SCOOP', 'NUDGE 정지 — 다시 건드리면 재개', 'NUDGE'),
    (0, '', 'NUDGE 일시 정지 — 다시 건드리면 해제, 이후 새 주문 가능', 'NUDGE'),
    (2, 'NUDGE_WAIT', 'NUDGE_WAIT — 세트 완료, 건드리면 다음 세트', 'SET_COMPLETE'),
    (2, 'PAUSED', 'REFILL 대기', 'REFILL'),
    (2, 'PAUSED', '인터락 ENTER (REFILL)', 'INTERLOCK'),
    (2, 'PAUSED', '', ''),
    (2, 'PAUSED', '원인 미확인', ''),
    (4, 'ERROR', 'NUDGE 정지', ''),
    (1, 'SCOOP', 'NUDGE 정지', ''),
])
def test_ros_state_pause_display_context(node, mode, step, note, expected):
    msg = state(mode=mode)
    msg.step, msg.note = step, note
    node._on_state(msg)
    observed = node.snapshot()['state']
    assert observed['pause_reason'] == expected
    assert observed['note'] == note
    # 다음 정상 상태에서 과거 사유를 유지하지 않는다.
    node._on_state(state(mode=1))
    assert node.snapshot()['state']['pause_reason'] == ''
