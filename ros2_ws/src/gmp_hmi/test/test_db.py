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
    assert db.batch('B3')['weights'][0]['subject'] in (None, '')


def test_228_미측정_근거가_있으면_DONE_UNMEASURED(tmp_path):
    """SOT D-32 — record_node 는 종료 때 `has_unmeasured` 로 DONE 과 DONE_UNMEASURED 를 가른다.
    근거는 C 의 BATCH_UNMEASURED 이벤트(원료·VERIFY 모두) 또는 원료 verdict INVALID(보조)."""
    db = _db(tmp_path)
    for b in ('OK1', 'EV1', 'IV1'):
        db.start_batch(b, 0.0)
        db.item(b, 'A', 40.0, 40.2, 0.5, 0, 1, 1.0)
    assert not db.has_unmeasured('OK1')
    db.event(2.0, 'EV1', 1, 'BATCH_UNMEASURED', "미측정 원료 없음 / VERIFY 미측정 True")
    assert db.has_unmeasured('EV1')                     # VERIFY 만 미측정 — 원료 verdict 로는 못 잡는다
    db.item('IV1', 'B', 40.0, 12.0, -70.0, 3, 2, 2.0)   # 3 = INVALID
    assert db.has_unmeasured('IV1')                     # 이벤트를 놓쳐도 원료 미측정은 잡는다
    db.event(2.0, 'OK1', 1, 'BATCH_UNMEASURED', '다른 배치')   # 배치 ID 로만 묶는다
    assert db.has_unmeasured('OK1')
    assert not db.has_unmeasured('NONE')


def test_228_이벤트가_종료_뒤에_와도_DONE_UNMEASURED_로_고친다(tmp_path):
    """C 는 이벤트 순서를 보장하지 않는다 (PR #257) — 이미 DONE 으로 닫힌 행을 UPDATE 로 흡수한다."""
    db = _db(tmp_path)
    db.start_batch('B1', 0.0)
    db.item('B1', 'A', 40.0, 40.2, 0.5, 0, 1, 1.0)
    db.finish_batch('B1', 10.0, 'DONE')
    db.reconcile_unmeasured('B1')
    assert db.batch('B1')['result'] == 'DONE'           # 근거 없으면 그대로
    db.event(11.0, 'B1', 1, 'BATCH_UNMEASURED', "미측정 원료 ['A'] / VERIFY 미측정 False")
    db.reconcile_unmeasured('B1')
    b = db.batch('B1')
    assert b['result'] == 'DONE_UNMEASURED' and b['finished_at'] == 10.0   # 종료 시각은 유지


def test_228_미측정은_폐기와_오류를_덮지_않는다(tmp_path):
    db = _db(tmp_path)
    for b, result in (('D1', 'DISCARDED'), ('E1', 'ERROR')):
        db.start_batch(b, 0.0)
        db.finish_batch(b, 10.0, result)
        db.event(11.0, b, 1, 'BATCH_UNMEASURED', 'x')
        db.reconcile_unmeasured(b)
        assert db.batch(b)['result'] == result


def test_228_미측정_배치도_QA_폐기되면_DISCARDED(tmp_path):
    """종전 reconcile_discard 는 result='DONE' 정확 일치라 미측정 배치를 폐기해도 완료로 남겼다."""
    db = _db(tmp_path)
    db.start_batch('B1', 0.0)
    db.finish_batch('B1', 10.0, 'DONE_UNMEASURED')
    db.deviation('D-B1-1', 'B1', '', 8, 'weigh invalid', True, 2, 'qa1', 12.0)   # DISCARDED
    db.reconcile_discard('B1')
    assert db.batch('B1')['result'] == 'DISCARDED'


def test_228_KPI_는_계량_검증_완료와_미측정_승인_완료를_나눈다(tmp_path):
    """SOT D-32 (5) — DONE 만 계량 검증 완료율, DONE_UNMEASURED 는 따로, 완주율은 둘의 합."""
    db = _db(tmp_path)
    for i, result in enumerate(('DONE', 'DONE_UNMEASURED', 'DISCARDED', 'ERROR')):
        db.start_batch(f'B{i}', float(i))
        db.finish_batch(f'B{i}', float(i) + 1, result)
    k = db.kpis()
    assert k['batches'] == 4
    assert k['batch_success_pct'] == 25.0            # DONE 만
    assert k['unmeasured_done'] == 1 and k['unmeasured_done_pct'] == 25.0
    assert k['run_complete_pct'] == 50.0             # DONE + DONE_UNMEASURED
    assert db.kpis(result='DONE_UNMEASURED')['batches'] == 1   # 조회 필터
    empty = CellDB(str(tmp_path / 'empty.db'), SCHEMA).kpis()
    assert empty['unmeasured_done'] == 0 and empty['run_complete_pct'] is None
