"""ROS 비의존 SQLite 접근. record_node 만 쓰고 웹은 readonly=True 로 읽는다.

시각 필터는 ROS 시각 초: start 포함, end 미포함. KPI 는 같은 배치 필터의
배치와 그 배치에 연결된 일탈/개입을 함께 사용한다. events/audit 는 append-only.
"""
import json
import math
import os
import re
import sqlite3
import threading
from pathlib import Path

LEVELS = {0: 'INFO', 1: 'WARN', 2: 'ERROR'}
VERDICTS = {0: 'OK', 1: 'UNDER', 2: 'OVER', 3: 'INVALID'}   # 3: 투입량 미측정 (계약 v1.8, #108)
DECISIONS = {0: 'PENDING', 1: 'APPROVED', 2: 'DISCARDED', 3: 'AUTO_RECOVERED', 4: 'FORCED'}   # 4: 강제 개입 종료 (v1.2.1)
KINDS = {0: 'OVERFILL', 1: 'GRIP_FAIL', 2: 'SLIP', 3: 'SAFETY_SWITCH', 4: 'SCOOP_EMPTY',
         5: 'MATERIAL_EMPTY', 6: 'FORCE_LIMIT', 7: 'TIMEOUT', 8: 'WEIGH_INVALID',
         9: 'VERIFY_MISMATCH', 10: 'BATCH_OUT_OF_SPEC', 11: 'WRONG_TOOL'}


def json_safe(value):
    """표준 JSON 에 없는 NaN/Infinity 는 문자열로 보존한다. 무효 측정도 삭제하지 않는다."""
    if isinstance(value, float) and not math.isfinite(value):
        return 'NaN' if math.isnan(value) else ('Infinity' if value > 0 else '-Infinity')
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if hasattr(value, 'tolist'):
        return json_safe(value.tolist())
    return value


class CellDB:
    def __init__(self, path: str, schema_sql: str = '', readonly=False):
        self.path = os.path.abspath(os.path.expanduser(path))
        self.readonly = readonly
        self._lock = threading.RLock()
        if readonly:
            # 파일이 없으면 예외: 웹이 재시도한다. 웹이 빈 DB 를 만들지 않는다.
            self.con = sqlite3.connect(Path(self.path).as_uri() + '?mode=ro', uri=True,
                                       check_same_thread=False, timeout=5)
        else:
            os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
            self.con = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self.con.row_factory = sqlite3.Row
        self.con.execute('PRAGMA busy_timeout=5000')
        if readonly:
            self.con.execute('PRAGMA query_only=ON')
        else:
            self.con.execute('PRAGMA journal_mode=WAL')
            with open(schema_sql, encoding='utf-8') as f:
                self.con.executescript(f.read())
            self._migrate()

    def _migrate(self):
        """기존 DB 를 보존하며 v3 열을 추가한다. 과거 중복 결과 원본도 별도 보존한다."""
        with self._lock, self.con:
            columns = {r['name'] for r in self.con.execute('PRAGMA table_info(weights)')}
            for name, definition in (('subject', "TEXT NOT NULL DEFAULT ''"),
                                     ('samples', 'INTEGER NOT NULL DEFAULT 0')):
                if name not in columns:
                    self.con.execute(f'ALTER TABLE weights ADD COLUMN {name} {definition}')
            duplicates = self.con.execute(
                'SELECT * FROM items WHERE id NOT IN '
                '(SELECT id FROM items i WHERE id=(SELECT j.id FROM items j '
                'WHERE j.batch_id=i.batch_id AND j.material_id=i.material_id '
                'ORDER BY j.t DESC,j.id DESC LIMIT 1))').fetchall()
            for row in duplicates:
                self.con.execute('INSERT OR IGNORE INTO legacy_item_duplicates VALUES (?,?)',
                                 (row['id'], json.dumps(dict(row), ensure_ascii=False)))
                self.con.execute('DELETE FROM items WHERE id=?', (row['id'],))
            self.con.execute('CREATE UNIQUE INDEX IF NOT EXISTS ux_items_material ON items(batch_id, material_id)')
            self.con.execute('PRAGMA user_version=4')

    def close(self):
        with self._lock:
            self.con.close()

    def start_batch(self, batch_id, started_at, product=''):
        if not batch_id:
            return
        with self._lock, self.con:
            self.con.execute(
                'INSERT INTO batches (batch_id,product,started_at) VALUES (?,?,?) '
                'ON CONFLICT(batch_id) DO UPDATE SET '
                'started_at=MIN(batches.started_at,excluded.started_at), '
                "product=CASE WHEN excluded.product!='' THEN excluded.product ELSE batches.product END",
                (batch_id, product, started_at))

    def finish_batch(self, batch_id, finished_at, result, note=''):
        with self._lock, self.con:
            self.con.execute(
                'UPDATE batches SET finished_at=COALESCE(finished_at,?),result=COALESCE(result,?),'
                'note=COALESCE(?,note) WHERE batch_id=?', (finished_at, result, note or None, batch_id))

    def note_batch(self, batch_id, note):
        with self._lock, self.con:
            self.con.execute('UPDATE batches SET note=? WHERE batch_id=?', (note, batch_id))

    def reconcile_discard(self, batch_id):
        """토픽 순서가 바뀌어 QA 폐기 판정이 DONE 뒤 도착해도 종료 시각은 유지한다.

        미측정 승인 완료(`DONE_UNMEASURED`)도 폐기되면 DISCARDED 다 (#228) — 종전 `result='DONE'`
        정확 일치는 미측정 배치를 QA 가 폐기해도 완료로 남겼다.
        """
        with self._lock, self.con:
            self.con.execute(
                "UPDATE batches SET result='DISCARDED' WHERE batch_id=? AND result IN ('DONE','DONE_UNMEASURED') "
                "AND finished_at IS NOT NULL AND EXISTS(SELECT 1 FROM deviations "
                "WHERE batch_id=? AND decision='DISCARDED')", (batch_id, batch_id))

    def has_unmeasured(self, batch_id):
        """투입량이나 최종 순량을 모르는 채 승인된 배치인가 (SOT D-32, #228).

        근거 둘 중 하나면 된다: C 의 `BATCH_UNMEASURED` 이벤트(원료·VERIFY 미측정 모두), 또는
        원료 결과 `verdict='INVALID'`(보조 — 이벤트를 놓쳐도 원료 미측정은 잡는다).
        메모리가 아니라 DB 에서 보므로 record_node 가 재시작해도 판정이 같다.
        """
        return bool(self._rows(
            "SELECT 1 WHERE EXISTS(SELECT 1 FROM events WHERE batch_id=? AND code='BATCH_UNMEASURED') "
            "OR EXISTS(SELECT 1 FROM items WHERE batch_id=? AND verdict='INVALID')", (batch_id, batch_id)))

    def reconcile_unmeasured(self, batch_id):
        """미측정 근거가 DONE 기록 **뒤**에 도착해도 `DONE_UNMEASURED` 로 바로잡는다.

        C 는 `BATCH_UNMEASURED` 를 최종 CellState(DONE) 보다 먼저 보내려 하지만 순서를 보장하지 않는다
        (PR #257). 폐기(DISCARDED)·오류(ERROR)는 건드리지 않는다 — 폐기가 미측정보다 우선이다.
        """
        with self._lock, self.con:
            self.con.execute(
                "UPDATE batches SET result='DONE_UNMEASURED' WHERE batch_id=? AND result='DONE' "
                "AND finished_at IS NOT NULL AND (EXISTS(SELECT 1 FROM events WHERE batch_id=? "
                "AND code='BATCH_UNMEASURED') OR EXISTS(SELECT 1 FROM items WHERE batch_id=? "
                "AND verdict='INVALID'))", (batch_id, batch_id, batch_id))

    def has_discard_decision(self, batch_id):
        return bool(self._rows("SELECT 1 FROM deviations WHERE batch_id=? AND decision='DISCARDED' LIMIT 1",
                               (batch_id,)))

    def item(self, batch_id, material_id, target_g, actual_g, error_pct, verdict, attempts, t):
        with self._lock, self.con:
            self.con.execute(
                'INSERT INTO items (batch_id,material_id,target_g,actual_g,error_pct,verdict,attempts,t) '
                'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(batch_id,material_id) DO UPDATE SET '
                'target_g=excluded.target_g,actual_g=excluded.actual_g,error_pct=excluded.error_pct,'
                'verdict=excluded.verdict,attempts=excluded.attempts,t=excluded.t WHERE excluded.t>items.t',
                (batch_id, material_id, target_g, actual_g, error_pct,
                 VERDICTS.get(verdict, str(verdict)), attempts, t))

    def weight(self, batch_id, t, station, gross_g, tare_g, net_g, std_g, valid, subject='', samples=0):
        with self._lock, self.con:
            self.con.execute(
                'INSERT INTO weights (batch_id,t,station,gross_g,tare_g,net_g,std_g,valid,subject,samples) '
                'VALUES (?,?,?,?,?,?,?,?,?,?)',
                (batch_id, t, station, gross_g, tare_g, net_g, std_g, int(valid), subject, int(samples)))

    def scoop_cycle(self, payload, t=None):
        """ScoopCycle 전체 원본. 실패/무효 시도도 같은 경로로 기록한다."""
        data = json_safe(dict(payload))
        if t is None:
            stamp = data.get('header', {}).get('stamp', {})
            t = stamp.get('sec', 0) + stamp.get('nanosec', 0) * 1e-9
        with self._lock, self.con:
            self.con.execute(
                'INSERT INTO scoop_cycles (batch_id,material_id,attempt,t,target_g,actual_before_g,'
                'delivered_g,commanded_pour_fraction,outcome,valid,duration_s,payload_json) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(batch_id,material_id,attempt) DO NOTHING',
                (data['batch_id'], data['material_id'], int(data['attempt']), t,
                 data.get('target_g'), data.get('actual_before_g'), data.get('delivered_g'),
                 data.get('commanded_pour_fraction'), data.get('outcome'), int(data.get('valid', False)),
                 data.get('duration_s'), json.dumps(data, ensure_ascii=False, allow_nan=False)))

    def deviation(self, deviation_id, batch_id, material_id, kind, detail, requires_decision, decision, operator_id, t):
        """최초 일탈·최종 판정을 보존. 늦은 PENDING 과 중복 최종 판정은 역행시키지 않는다."""
        dec = DECISIONS.get(decision, str(decision))
        with self._lock, self.con:
            self.con.execute(
                'INSERT INTO deviations (deviation_id,batch_id,material_id,kind,detail,requires_decision,'
                'decision,operator_id,raised_at,decided_at) VALUES (?,?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(deviation_id) DO UPDATE SET '
                'raised_at=MIN(deviations.raised_at,excluded.raised_at), '
                "decision=CASE WHEN deviations.decision='PENDING' THEN excluded.decision ELSE deviations.decision END, "
                "operator_id=CASE WHEN deviations.decision='PENDING' AND excluded.decision!='PENDING' "
                'THEN COALESCE(excluded.operator_id,deviations.operator_id) ELSE deviations.operator_id END, '
                "decided_at=CASE WHEN deviations.decision='PENDING' AND excluded.decision!='PENDING' "
                'THEN excluded.decided_at ELSE deviations.decided_at END '
                'WHERE deviations.batch_id=excluded.batch_id',
                (deviation_id, batch_id, material_id, KINDS.get(kind, str(kind)), detail,
                 int(requires_decision), dec, operator_id or None, t, t if dec != 'PENDING' else None))

    def event(self, t, batch_id, level, code, text):
        with self._lock, self.con:
            self.con.execute('INSERT INTO events (t,batch_id,level,code,text) VALUES (?,?,?,?,?)',
                             (t, batch_id, LEVELS.get(level, str(level)), code, text))

    def audit(self, t, actor, action, target='', detail=''):
        with self._lock, self.con:
            self.con.execute('INSERT INTO audit (t,actor,action,target,detail) VALUES (?,?,?,?,?)',
                             (t, actor, action, target, detail))

    def _rows(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    @staticmethod
    def _limit(limit):
        return max(1, min(int(limit), 10000))

    @staticmethod
    def _period(column, start, end):
        clauses, args = [], []
        if start is not None:
            clauses.append(f'{column}>=?'); args.append(float(start))
        if end is not None:
            clauses.append(f'{column}<?'); args.append(float(end))
        return clauses, args

    @classmethod
    def _batch_filter(cls, start=None, end=None, result=None, query=None):
        clauses, args = cls._period('b.started_at', start, end)
        if result:
            if result == 'ACTIVE':
                clauses.append('b.finished_at IS NULL')
            else:
                clauses.append('b.result=?'); args.append(result)
        if query:
            clauses.append('(b.batch_id LIKE ? OR b.product LIKE ?)')
            args.extend([f'%{query}%', f'%{query}%'])
        return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), args

    def batches(self, limit=50, start=None, end=None, result=None, query=None):
        where, args = self._batch_filter(start, end, result, query)
        return self._rows(
            'SELECT b.*, (SELECT COUNT(*) FROM items i WHERE i.batch_id=b.batch_id) AS n_items, '
            '(SELECT COUNT(*) FROM deviations d WHERE d.batch_id=b.batch_id) AS n_dev, '
            '(SELECT COUNT(*) FROM scoop_cycles s WHERE s.batch_id=b.batch_id) AS n_cycles, '
            'CASE WHEN finished_at IS NULL THEN NULL ELSE finished_at-started_at END AS cycle_s '
            'FROM batches b' + where + ' ORDER BY started_at DESC,batch_id DESC LIMIT ?',
            [*args, self._limit(limit)])

    def batch(self, batch_id):
        rows = self._rows('SELECT * FROM batches WHERE batch_id=?', (batch_id,))
        if not rows:
            return None
        b = rows[0]
        for name, ordering in (('items', 't'), ('weights', 't'), ('scoop_cycles', 't'),
                               ('deviations', 'raised_at'), ('events', 'id')):
            b[name] = self._rows(f'SELECT * FROM {name} WHERE batch_id=? ORDER BY {ordering}', (batch_id,))
        for cycle in b['scoop_cycles']:
            cycle['payload'] = json.loads(cycle.pop('payload_json'))
        b['audit'] = self._rows('SELECT * FROM audit WHERE target=? ORDER BY id', (batch_id,))
        return b

    def checkpoint(self, batch_id, t, mode, step, item_index, station, note):
        """같은 상태의 주기 발행은 생략. 역순 수신으로 관측 이력을 되돌리지 않는다."""
        if not batch_id:
            return
        with self._lock, self.con:
            previous = self.con.execute(
                'SELECT * FROM state_checkpoints WHERE batch_id=? ORDER BY t DESC,id DESC LIMIT 1',
                (batch_id,)).fetchone()
            fields = (mode, step, item_index, station, note)
            if previous and (t < previous['t'] or fields == tuple(
                    previous[k] for k in ('mode', 'step', 'item_index', 'station', 'note'))):
                return
            self.con.execute('INSERT INTO state_checkpoints '
                             '(batch_id,t,mode,step,item_index,station,note) VALUES (?,?,?,?,?,?,?)',
                             (batch_id, t, *fields))

    def save_recipe_context(self, batch_id, t, recipe):
        from gmp_hmi.core.measurement_context import validate_recipe_context
        recipe = validate_recipe_context(recipe)
        with self._lock, self.con:
            # 한 배치의 수락된 레시피는 뒤늦은 중복 이벤트로 교체하지 않는다.
            self.con.execute('INSERT OR IGNORE INTO batch_recipes VALUES (?,?,?)',
                             (batch_id, t, json.dumps(recipe, ensure_ascii=False, allow_nan=False)))

    def recipe_context(self, batch_id):
        rows = self._rows('SELECT payload_json FROM batch_recipes WHERE batch_id=?', (batch_id,))
        return json.loads(rows[0]['payload_json']) if rows else None

    def restart_records(self, limit=20):
        records = self._rows('SELECT * FROM batches WHERE finished_at IS NULL '
                             'ORDER BY started_at DESC LIMIT ?', (self._limit(limit),))
        for row in records:
            batch_id = row['batch_id']
            checkpoints = self._rows('SELECT * FROM state_checkpoints WHERE batch_id=? '
                                     'ORDER BY t DESC,id DESC LIMIT 1', (batch_id,))
            row['checkpoint'] = checkpoints[0] if checkpoints else None
            row['recipe'] = self.recipe_context(batch_id)
            row['last_measurements'] = self._rows(
                'SELECT * FROM weights w WHERE batch_id=? AND id=(SELECT w2.id FROM weights w2 '
                'WHERE w2.batch_id=w.batch_id AND w2.subject=w.subject ORDER BY t DESC,id DESC LIMIT 1)',
                (batch_id,))
            row['resume_supported'] = False
        return records

    def batch_status(self, batch_id):
        rows = self._rows('SELECT * FROM batches WHERE batch_id=?', (batch_id,))
        return rows[0] if rows else None

    def recent_weights(self, limit=200):
        return self._rows('SELECT * FROM weights ORDER BY id DESC LIMIT ?', (self._limit(limit),))[::-1]

    def pending_deviations(self):
        return self._rows("SELECT * FROM deviations WHERE decision='PENDING' AND requires_decision=1 ORDER BY raised_at")

    def recent_events(self, limit=100, level=None, query=None, start=None, end=None):
        clauses, args = self._period('t', start, end)
        if level:
            clauses.append('level=?'); args.append(LEVELS.get(level, str(level)))
        if query:
            clauses.append('(code LIKE ? OR text LIKE ? OR batch_id LIKE ?)')
            args.extend([f'%{query}%'] * 3)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        return self._rows('SELECT * FROM events' + where + ' ORDER BY id DESC LIMIT ?',
                          [*args, self._limit(limit)])

    def recent_audit(self, limit=50, actor=None, action=None, query=None, start=None, end=None):
        clauses, args = self._period('t', start, end)
        for name, value in (('actor', actor), ('action', action)):
            if value:
                clauses.append(f'{name}=?'); args.append(value)
        if query:
            clauses.append('(actor LIKE ? OR action LIKE ? OR target LIKE ? OR detail LIKE ?)')
            args.extend([f'%{query}%'] * 4)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        return self._rows('SELECT * FROM audit' + where + ' ORDER BY id DESC LIMIT ?',
                          [*args, self._limit(limit)])

    def kpis(self, start=None, end=None, result=None, query=None):
        """기존 KPI 정의 유지. 운전시간은 완료 배치 경과시간의 합(정지 구간 미분리)."""
        where, args = self._batch_filter(start, end, result, query)
        selected = 'SELECT b.batch_id FROM batches b' + where
        # KPI 두 지표 (SOT D-32): DONE 만 「계량 검증 완료」, DONE_UNMEASURED 는 「미측정 승인 완료」.
        # `batch_success_pct` 는 기존 키를 유지하되 뜻은 계량 검증 완료율이다. 실행 완주율은 둘의 합.
        batch = self._rows(
            "SELECT COUNT(*) AS total, COALESCE(SUM(result='DONE'),0) AS done, "
            "COALESCE(SUM(result='DONE_UNMEASURED'),0) AS unmeasured, "
            'COALESCE(SUM(MAX(0,finished_at-started_at)),0) AS run_s FROM batches '
            f'WHERE finished_at IS NOT NULL AND batch_id IN ({selected})', args)[0]
        dev = self._rows("SELECT COUNT(*) AS total,COALESCE(SUM(decision='AUTO_RECOVERED'),0) AS auto "
                         f'FROM deviations WHERE batch_id IN ({selected})', args)[0]
        forced = self._rows("SELECT COUNT(*) AS n FROM events WHERE code='INTERVENTION_FORCED' "
                            f'AND batch_id IN ({selected})', args)[0]['n']
        total, done, run_s = batch['total'], batch['done'], batch['run_s']
        unmeasured = batch['unmeasured']
        return {'batches': total, 'batch_success_pct': 100.0 * done / total if total else None,
                'unmeasured_done': unmeasured,
                'unmeasured_done_pct': 100.0 * unmeasured / total if total else None,
                'run_complete_pct': 100.0 * (done + unmeasured) / total if total else None,
                'deviations': dev['total'],
                'auto_recovery_pct': 100.0 * dev['auto'] / dev['total'] if dev['total'] else None,
                'run_time_s': run_s, 'forced_interventions': forced,
                'mtbi_s': run_s / forced if forced else run_s}

    def export_json(self, batch_id, directory):
        """DB 사본을 원자적으로 교체한다. batch_id 는 파일 경로로 신뢰하지 않는다."""
        b = self.batch(batch_id)
        if b is None:
            return None
        os.makedirs(directory, exist_ok=True)
        safe_id = re.sub(r'[^A-Za-z0-9_.-]', '_', batch_id).strip('.') or 'batch'
        if safe_id != batch_id:
            import hashlib
            safe_id += '-' + hashlib.sha256(batch_id.encode()).hexdigest()[:10]
        path = os.path.join(directory, safe_id + '.json')
        temp = path + '.tmp'
        with self._lock:
            with open(temp, 'w', encoding='utf-8') as f:
                json.dump(json_safe(b), f, ensure_ascii=False, indent=2, allow_nan=False)
            os.replace(temp, path)
        return path
