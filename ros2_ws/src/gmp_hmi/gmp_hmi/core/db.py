"""SQLite 접근 계층. ROS 비의존. 스키마는 config/schema.sql (Kn1 mro_fleet/core/db.py 패턴).

기록 주체는 record_node 하나, HMI 는 읽기만 한다. events 는 append-only — UPDATE/DELETE 메서드를 두지 않는다.
"""
import json
import os
import sqlite3
import threading

LEVELS = {0: 'INFO', 1: 'WARN', 2: 'ERROR'}
VERDICTS = {0: 'OK', 1: 'UNDER', 2: 'OVER'}
DECISIONS = {0: 'PENDING', 1: 'APPROVED', 2: 'DISCARDED', 3: 'AUTO_RECOVERED'}
KINDS = {0: 'OVERFILL', 1: 'GRIP_FAIL', 2: 'SLIP', 3: 'SAFETY_SWITCH', 4: 'SCOOP_EMPTY',
         5: 'MATERIAL_EMPTY', 6: 'FORCE_LIMIT', 7: 'TIMEOUT', 8: 'WEIGH_INVALID'}


class CellDB:
    def __init__(self, path: str, schema_sql: str):
        os.makedirs(os.path.dirname(os.path.expanduser(path)) or '.', exist_ok=True)
        self.con = sqlite3.connect(os.path.expanduser(path), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with open(schema_sql, encoding='utf-8') as f:
            self.con.executescript(f.read())

    def close(self):
        with self._lock:
            self.con.close()

    # ── 쓰기 (record_node) ────────────────────────────────────────────
    def start_batch(self, batch_id, started_at, product=''):
        with self._lock, self.con:
            self.con.execute('INSERT OR IGNORE INTO batches (batch_id, product, started_at) VALUES (?,?,?)',
                             (batch_id, product, started_at))

    def finish_batch(self, batch_id, finished_at, result, note=''):
        with self._lock, self.con:
            self.con.execute('UPDATE batches SET finished_at=COALESCE(finished_at, ?), result=COALESCE(result, ?), '
                             'note=COALESCE(?, note) WHERE batch_id=?', (finished_at, result, note or None, batch_id))

    def item(self, batch_id, material_id, target_g, actual_g, error_pct, verdict, attempts, t):
        with self._lock, self.con:
            self.con.execute('INSERT INTO items (batch_id, material_id, target_g, actual_g, error_pct, verdict, attempts, t) '
                             'VALUES (?,?,?,?,?,?,?,?)',
                             (batch_id, material_id, target_g, actual_g, error_pct, VERDICTS.get(verdict, str(verdict)), attempts, t))

    def weight(self, batch_id, t, station, gross_g, tare_g, net_g, std_g, valid, subject=''):
        with self._lock, self.con:
            self.con.execute('INSERT INTO weights (batch_id, t, station, subject, gross_g, tare_g, net_g, std_g, valid) '
                             'VALUES (?,?,?,?,?,?,?,?,?)',
                             (batch_id, t, station, subject or None, gross_g, tare_g, net_g, std_g, int(valid)))

    def deviation(self, deviation_id, batch_id, material_id, kind, detail, requires_decision, decision, operator_id, t):
        """같은 deviation_id 가 다시 오면 판정만 갱신한다 (PENDING → APPROVED 등)."""
        dec = DECISIONS.get(decision, str(decision))
        with self._lock, self.con:
            self.con.execute(
                'INSERT INTO deviations (deviation_id, batch_id, material_id, kind, detail, requires_decision, decision, operator_id, raised_at, decided_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(deviation_id) DO UPDATE SET '
                'decision=excluded.decision, operator_id=COALESCE(excluded.operator_id, operator_id), '
                'decided_at=CASE WHEN excluded.decision != \'PENDING\' THEN excluded.raised_at ELSE decided_at END',
                (deviation_id, batch_id, material_id, KINDS.get(kind, str(kind)), detail, int(requires_decision), dec,
                 operator_id or None, t, t if dec != 'PENDING' else None))

    def event(self, t, batch_id, level, code, text):
        with self._lock, self.con:
            self.con.execute('INSERT INTO events (t, batch_id, level, code, text) VALUES (?,?,?,?,?)',
                             (t, batch_id, LEVELS.get(level, str(level)), code, text))

    def audit(self, t, actor, action, target='', detail=''):
        with self._lock, self.con:
            self.con.execute('INSERT INTO audit (t, actor, action, target, detail) VALUES (?,?,?,?,?)',
                             (t, actor, action, target, detail))

    # ── 읽기 (HMI·리포트) ─────────────────────────────────────────────
    def _rows(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def batches(self, limit=50):
        return self._rows('SELECT b.*, (SELECT COUNT(*) FROM items i WHERE i.batch_id=b.batch_id) AS n_items, '
                          '(SELECT COUNT(*) FROM deviations d WHERE d.batch_id=b.batch_id) AS n_dev, '
                          'CASE WHEN finished_at IS NULL THEN NULL ELSE finished_at-started_at END AS cycle_s '
                          'FROM batches b ORDER BY started_at DESC LIMIT ?', (limit,))

    def batch(self, batch_id):
        rows = self._rows('SELECT * FROM batches WHERE batch_id=?', (batch_id,))
        if not rows:
            return None
        b = rows[0]
        b['items'] = self._rows('SELECT * FROM items WHERE batch_id=? ORDER BY t', (batch_id,))
        b['weights'] = self._rows('SELECT * FROM weights WHERE batch_id=? ORDER BY t', (batch_id,))
        b['deviations'] = self._rows('SELECT * FROM deviations WHERE batch_id=? ORDER BY raised_at', (batch_id,))
        b['events'] = self._rows('SELECT * FROM events WHERE batch_id=? ORDER BY id', (batch_id,))
        return b

    def recent_weights(self, limit=200):
        return self._rows('SELECT * FROM weights ORDER BY id DESC LIMIT ?', (limit,))[::-1]

    def pending_deviations(self):
        return self._rows("SELECT * FROM deviations WHERE decision='PENDING' ORDER BY raised_at")

    def recent_audit(self, limit=50):
        return self._rows('SELECT * FROM audit ORDER BY id DESC LIMIT ?', (limit,))

    def kpis(self):
        """계약 6절. MTBI = 배치 운전시간 합 / 강제개입 수 (개입 0 이면 운전시간 그대로)."""
        done = self._rows("SELECT COUNT(*) AS n FROM batches WHERE result='DONE'")[0]['n']
        total = self._rows('SELECT COUNT(*) AS n FROM batches WHERE finished_at IS NOT NULL')[0]['n']
        dev_total = self._rows('SELECT COUNT(*) AS n FROM deviations')[0]['n']
        dev_auto = self._rows("SELECT COUNT(*) AS n FROM deviations WHERE decision='AUTO_RECOVERED'")[0]['n']
        run_s = self._rows('SELECT COALESCE(SUM(finished_at-started_at),0) AS s FROM batches WHERE finished_at IS NOT NULL')[0]['s']
        forced = self._rows("SELECT COUNT(*) AS n FROM events WHERE code='INTERVENTION_FORCED'")[0]['n']
        return {'batches': total, 'batch_success_pct': (100.0 * done / total) if total else None,
                'deviations': dev_total, 'auto_recovery_pct': (100.0 * dev_auto / dev_total) if dev_total else None,
                'run_time_s': run_s, 'forced_interventions': forced,
                'mtbi_s': (run_s / forced) if forced else run_s}

    def export_json(self, batch_id, directory):
        """배치 기록서 내보내기 (제출·인쇄용). DB 가 원본이고 이것은 사본이다."""
        b = self.batch(batch_id)
        if b is None:
            return None
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f'{batch_id}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(b, f, ensure_ascii=False, indent=2)
        return path
