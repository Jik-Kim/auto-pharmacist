import os
from gmp_hmi.core.db import CellDB

SCHEMA = os.path.join(os.path.dirname(__file__), '..', 'config', 'schema.sql')


def _db(tmp_path):
    return CellDB(str(tmp_path / 'cell.db'), SCHEMA)


def test_batch_lifecycle_and_kpi(tmp_path):
    db = _db(tmp_path)
    db.start_batch('B1', 100.0, 'DEMO')
    db.weight('B1', 101.0, 'workbench', 80.0, 50.0, 30.0, 0.4, True)
    db.item('B1', 'A', 30.0, 30.4, 1.33, 0, 1, 102.0)
    db.deviation('D-B1-1', 'B1', 'B', 0, 'over 12%', True, 0, '', 103.0)      # PENDING
    db.deviation('D-B1-1', 'B1', 'B', 0, 'over 12%', True, 1, 'qa1', 110.0)   # APPROVED by qa1
    db.event(104.0, 'B1', 0, 'BATCH_START', 'DEMO')
    db.finish_batch('B1', 120.0, 'DONE')
    b = db.batch('B1')
    assert b['result'] == 'DONE' and len(b['items']) == 1 and b['deviations'][0]['decision'] == 'APPROVED'
    assert b['deviations'][0]['operator_id'] == 'qa1' and b['deviations'][0]['decided_at'] == 110.0
    k = db.kpis()
    assert k['batches'] == 1 and k['batch_success_pct'] == 100.0 and k['mtbi_s'] == 20.0
    assert db.batches()[0]['cycle_s'] == 20.0


def test_audit_and_export(tmp_path):
    db = _db(tmp_path)
    db.start_batch('B2', 0.0)
    db.audit(1.0, 'qa1', 'QA_APPROVE', 'B2', 'D-B2-1')
    assert db.recent_audit()[0]['actor'] == 'qa1'
    path = db.export_json('B2', str(tmp_path / 'out'))
    assert path and os.path.exists(path)
    assert db.export_json('nope', str(tmp_path)) is None


def test_weight_subject_로_스쿱과_용기를_가른다(tmp_path):
    """D-22 배치 1건 = 스쿱 9회(3원료×3) + 용기 2회(TARE·VERIFY). 섞이면 그래프가 못 읽힌다.
    스쿱도 용기도 같은 workbench 자세에서 재므로 station 으로는 갈리지 않는다 (계약 v1.2)."""
    db = _db(tmp_path)
    db.start_batch('B2', 200.0, 'DEMO')
    db.weight('B2', 201.0, 'workbench', 70.0, 20.0, 50.0, 0.3, True, 'scoop')       # 붓기 전 스쿱
    db.weight('B2', 202.0, 'workbench', 22.0, 20.0, 2.0, 0.3, True, 'scoop')        # 붓기 후 잔량
    db.weight('B2', 203.0, 'workbench', 480.0, 30.0, 450.0, 0.5, True, 'container')  # VERIFY
    ws = db.batch('B2')['weights']
    assert [w['subject'] for w in ws] == ['scoop', 'scoop', 'container']
    assert sum(1 for w in ws if w['subject'] == 'scoop') == 2


def test_subject_를_안_주면_비어_있다(tmp_path):
    """v1.2 이전 발행자와 섞여 돌아도 깨지지 않아야 한다."""
    db = _db(tmp_path)
    db.start_batch('B3', 300.0, 'DEMO')
    db.weight('B3', 301.0, 'workbench', 80.0, 50.0, 30.0, 0.4, True)
    assert db.batch('B3')['weights'][0]['subject'] is None
