import os
from gmp_hmi.core.db import CellDB

SCHEMA = os.path.join(os.path.dirname(__file__), '..', 'config', 'schema.sql')


def _db(tmp_path):
    return CellDB(str(tmp_path / 'cell.db'), SCHEMA)


def test_batch_lifecycle_and_kpi(tmp_path):
    db = _db(tmp_path)
    db.start_batch('B1', 100.0, 'DEMO')
    db.weight('B1', 101.0, 'scale', 80.0, 50.0, 30.0, 0.4, True)
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
