"""V3 데이터 경계: 이전 DB, QA 메시지 역전, 실패 원본, 물리 완료·재접속."""
import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from gmp_hmi.core.db import CellDB

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = str(ROOT / 'config' / 'schema.sql')


def db_at(tmp_path):
    return CellDB(str(tmp_path / 'cell.db'), SCHEMA)


def test_old_database_migrates_preserving_all_records(tmp_path):
    path = tmp_path / 'cell.db'
    con = sqlite3.connect(path)
    old = (ROOT / 'config' / 'schema.sql').read_text()
    old = old.replace("subject TEXT NOT NULL DEFAULT '', samples INTEGER NOT NULL DEFAULT 0,", '')
    con.executescript(old)
    con.execute("INSERT INTO weights(batch_id,t,station,net_g,valid) VALUES ('B',1,'scale',3,1)")
    con.execute("INSERT INTO items(batch_id,material_id,actual_g,t) VALUES ('B','A',3,1)")
    con.execute("INSERT INTO items(batch_id,material_id,actual_g,t) VALUES ('B','A',4,2)")
    con.commit(); con.close()
    db = CellDB(str(path), SCHEMA)
    assert db.recent_weights()[0]['subject'] == ''
    assert db.recent_weights()[0]['samples'] == 0
    assert db._rows('SELECT actual_g FROM items') == [{'actual_g': 4.0}]
    archived = db._rows('SELECT payload_json FROM legacy_item_duplicates')
    assert json.loads(archived[0]['payload_json'])['actual_g'] == 3
    db.close()
    again = CellDB(str(path), SCHEMA)
    assert len(again._rows('SELECT * FROM legacy_item_duplicates')) == 1
    again.close()


def test_readonly_cannot_create_or_mutate(tmp_path):
    path = tmp_path / 'cell.db'
    with pytest.raises(sqlite3.OperationalError):
        CellDB(str(path), SCHEMA, readonly=True)
    assert not path.exists()
    writer = db_at(tmp_path)
    writer.start_batch('B', 1)
    reader = CellDB(str(path), SCHEMA, readonly=True)
    assert reader.batches()[0]['batch_id'] == 'B'
    with pytest.raises(sqlite3.OperationalError):
        reader.start_batch('X', 2)
    assert writer.batch('X') is None
    reader.close(); writer.close()


def test_results_are_idempotent_and_late_qa_pending_cannot_regress(tmp_path):
    db = db_at(tmp_path)
    db.start_batch('B', 1)
    for _ in range(3):
        db.item('B', 'A', 10, 11, 10, 2, 1, 2)
    assert len(db.batch('B')['items']) == 1
    db.deviation('D', 'B', 'A', 9, 'mismatch', True, 0, '', 2)
    db.deviation('D', 'B', 'A', 9, 'mismatch', True, 2, 'qa1', 4)
    db.deviation('D', 'B', 'A', 9, 'old pending', True, 0, '', 2)
    db.deviation('D', 'B', 'A', 9, 'repeat final', True, 2, 'qa2', 9)
    row = db.batch('B')['deviations'][0]
    assert row['kind'] == 'VERIFY_MISMATCH' and row['decision'] == 'DISCARDED'
    assert row['operator_id'] == 'qa1' and row['decided_at'] == 4 and row['raised_at'] == 2
    assert db.pending_deviations() == []
    db.finish_batch('B', 7, 'DONE')
    db.reconcile_discard('B')
    assert db.batch_status('B')['result'] == 'DISCARDED'
    assert db.batch_status('B')['finished_at'] == 7
    db.close()


def test_failure_cycle_preserves_all_raw_measurements(tmp_path):
    db = db_at(tmp_path)
    db.start_batch('B', 1)
    payload = {
        'batch_id': 'B', 'material_id': 'A', 'attempt': 1,
        'header': {'stamp': {'sec': 2, 'nanosec': 100000000}},
        'scoop_tare': {'samples': 7, 'subject': 'scoop', 'valid': True, 'net_g': 0},
        'pre_pour': {'samples': 7, 'subject': 'scoop', 'valid': True, 'net_g': 3},
        'post_pour': {'samples': 0, 'subject': 'scoop', 'valid': False, 'net_g': float('nan')},
        'pre_pour_wrench': [1, 2, 3, 4, 5, 6], 'pre_pour_wrench_std': [0.1] * 6,
        'pre_pour_wrench_samples': 7, 'post_pour_wrench_valid': False,
        'reference_valid': False, 'reference_source': '', 'outcome': 3, 'valid': False,
    }
    db.scoop_cycle(payload); db.scoop_cycle(payload)
    db.finish_batch('B', 3, 'ERROR')
    cycle = db.batch('B')['scoop_cycles']
    assert len(cycle) == 1 and cycle[0]['valid'] == 0 and cycle[0]['outcome'] == 3
    assert cycle[0]['payload']['pre_pour_wrench'] == [1, 2, 3, 4, 5, 6]
    assert cycle[0]['payload']['post_pour']['net_g'] == 'NaN'
    exported = json.loads(Path(db.export_json('B', str(tmp_path / 'out'))).read_text())
    assert exported['scoop_cycles'][0]['payload']['pre_pour']['samples'] == 7
    db.close()


def test_filters_include_start_exclude_end_and_kpi_uses_same_batches(tmp_path):
    db = db_at(tmp_path)
    for name, start, result in [('B1', 10, 'DONE'), ('B2', 20, 'ERROR'), ('B3', 30, 'DONE')]:
        db.start_batch(name, start, 'Product')
        db.finish_batch(name, start + 5, result)
        db.event(start + 1, name, 1, 'INTERVENTION_FORCED', 'test')
        db.audit(start + 1, 'qa1', 'QA_APPROVE', name, 'test')
    assert [b['batch_id'] for b in db.batches(start=10, end=30)] == ['B2', 'B1']
    assert db.kpis(start=10, end=30)['batch_success_pct'] == 50
    kpi = db.kpis(start=10, end=30, result='DONE', query='B1')
    assert kpi['batches'] == 1 and kpi['run_time_s'] == 5 and kpi['forced_interventions'] == 1
    assert db.recent_events(start=11, end=21, level='WARN')[0]['batch_id'] == 'B1'
    assert len(db.recent_audit(start=11, end=21, actor='qa1', action='QA_APPROVE')) == 1
    assert db.batches(query="' OR 1=1 --") == []
    db.close()


def test_export_batch_id_cannot_escape_directory(tmp_path):
    db = db_at(tmp_path)
    db.start_batch('../../evil', 1)
    path = Path(db.export_json('../../evil', str(tmp_path / 'out')))
    assert path.parent == tmp_path / 'out'
    db.close()


def record_type(monkeypatch):
    # ROS 가 없는 단위 테스트에서도 실제 콜백을 호출한다. DDS 시험은 별도 launch 의 책임이다.
    names = ['rclpy', 'rclpy.node', 'rclpy.qos', 'ament_index_python',
             'ament_index_python.packages', 'rosidl_runtime_py', 'rosidl_runtime_py.convert',
             'gmp_interfaces', 'gmp_interfaces.msg']
    modules = {name: types.ModuleType(name) for name in names}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    modules['rclpy.node'].Node = object
    modules['rclpy.qos'].DurabilityPolicy = types.SimpleNamespace(TRANSIENT_LOCAL=1)
    modules['rclpy.qos'].QoSProfile = lambda **kw: kw
    modules['ament_index_python.packages'].get_package_share_directory = lambda _: str(ROOT)
    modules['rosidl_runtime_py.convert'].message_to_ordereddict = lambda msg: vars(msg)
    state = type('CellState', (), dict(IDLE=0, RUNNING=1, PAUSED=2, DEVIATION=3, ERROR=4, DONE=5))
    for name in ['CellEvent', 'CellState', 'Deviation', 'DispenseResult', 'ScoopCycle', 'WeightReading']:
        setattr(modules['gmp_interfaces.msg'], name, state if name == 'CellState' else type(name, (), {}))
    spec = importlib.util.spec_from_file_location('record_v3_under_test', ROOT / 'gmp_hmi/nodes/record_node.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.RecordNode


def stamp(t):
    return types.SimpleNamespace(stamp=types.SimpleNamespace(sec=int(t), nanosec=0))


def node_at(tmp_path, monkeypatch):
    cls = record_type(monkeypatch)
    node = cls.__new__(cls)
    node.db = db_at(tmp_path)
    node.batch_id, node.active, node._state_t = '', False, None
    node._held_completion = set()
    node.get_parameter = lambda _: types.SimpleNamespace(value=str(tmp_path / 'out'))
    node.get_logger = lambda: types.SimpleNamespace(info=lambda _: None, warning=lambda _: None)
    return node


def state(node, batch, mode, step, t):
    node._on_state(types.SimpleNamespace(header=stamp(t), batch_id=batch, mode=mode, step=step, note=''))


def test_record_waits_physical_done_and_refreshes_late_data(tmp_path, monkeypatch):
    node = node_at(tmp_path, monkeypatch)
    state(node, 'B', 1, 'SCOOP', 1)
    state(node, 'B', 5, 'CARRY', 2)  # 알 수 없는 종료 단계는 보류
    assert node.db.batch_status('B')['finished_at'] is None
    assert not (tmp_path / 'out/B.json').exists()
    assert '물리적' in node.db.batch_status('B')['note']
    state(node, 'B', 5, 'DISCARDED', 4)
    assert node.db.batch_status('B')['result'] == 'DISCARDED'
    assert node.db.batch_status('B')['finished_at'] == 4
    node._on_dev(types.SimpleNamespace(header=stamp(3), batch_id='B', material_id='A',
                 deviation_id='D', kind=10, detail='bad batch', requires_decision=True, decision=2, operator_id='qa1'))
    node._on_result(types.SimpleNamespace(header=stamp(2), batch_id='B', material_id='A',
                    target_g=10, actual_g=12, error_pct=20, verdict=2, attempts=1))
    export = json.loads((tmp_path / 'out/B.json').read_text())
    assert export['result'] == 'DISCARDED' and export['finished_at'] == 4
    assert len(export['items']) == 1 and export['deviations'][0]['kind'] == 'BATCH_OUT_OF_SPEC'
    state(node, 'B', 1, 'SCOOP', 6)  # 이미 끝난 배치를 다시 열지 않는다
    assert not node.active
    node.db.close()


def test_record_restart_paused_recovers_context_without_rewriting_start(tmp_path, monkeypatch):
    node = node_at(tmp_path, monkeypatch)
    node.db.start_batch('B', 1, 'original')
    state(node, 'B', 2, 'PAUSED', 8)  # 재접속 때 latched PAUSED
    assert node.batch_id == 'B' and node.active
    assert node.db.batch_status('B')['started_at'] == 1
    node._on_weight(types.SimpleNamespace(header=stamp(9), station='scale', subject='scoop', samples=11,
                    gross_g=30, tare_g=20, net_g=10, std_g=0.1, valid=True))
    assert node.db.batch('B')['weights'][0]['samples'] == 11
    state(node, '', 0, '', 10)
    node._on_weight(types.SimpleNamespace(header=stamp(11), station='scale', subject='container', samples=0,
                    gross_g=0, tare_g=0, net_g=0, std_g=0, valid=False))
    assert node.db.recent_weights()[-1]['batch_id'] is None
    node.db.close()
