-- 배치 기록 DB (SQLite). GMP 배치 기록·추적성·감사 추적의 단일 저장소. 기록 주체는 record_node 하나다 (docs/interfaces.md 7절).
CREATE TABLE IF NOT EXISTS batches (
  batch_id    TEXT PRIMARY KEY,
  product     TEXT,
  started_at  REAL NOT NULL,          -- ROS 시각(s)
  finished_at REAL,
  result      TEXT,                   -- DONE | DISCARDED | ERROR | ABORTED
  note        TEXT
);
CREATE TABLE IF NOT EXISTS items (   -- 원료 1종 분주 결과 (DispenseResult)
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id    TEXT NOT NULL,
  material_id TEXT NOT NULL,
  target_g    REAL, actual_g REAL, error_pct REAL,
  verdict     TEXT,                   -- OK | UNDER | OVER
  attempts    INTEGER,
  t           REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS weights ( -- 계량 1회 (WeightReading) — 그래프·분해능 근거
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id  TEXT,
  t         REAL NOT NULL,
  station   TEXT,
  subject   TEXT,   -- "scoop" | "container" (v1.2). 배치 1건에 스쿱 9회·용기 2회라 섞으면 그래프가 못 읽힌다
  gross_g   REAL, tare_g REAL, net_g REAL, std_g REAL,
  valid     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS deviations ( -- 일탈 (Deviation). 판정자·시각이 감사 추적
  deviation_id      TEXT PRIMARY KEY,
  batch_id          TEXT NOT NULL,
  material_id       TEXT,
  kind              TEXT NOT NULL,
  detail            TEXT,
  requires_decision INTEGER NOT NULL,
  decision          TEXT NOT NULL,     -- PENDING | APPROVED | DISCARDED | AUTO_RECOVERED
  operator_id       TEXT,
  raised_at         REAL NOT NULL,
  decided_at        REAL
);
CREATE TABLE IF NOT EXISTS events (  -- append-only. MTBI·자동복구율 원천. 수정·삭제 금지
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  t        REAL NOT NULL,
  batch_id TEXT,
  level    TEXT NOT NULL,             -- INFO | WARN | ERROR
  code     TEXT NOT NULL,
  text     TEXT
);
CREATE TABLE IF NOT EXISTS audit (   -- 사람의 조작만 (HMI_* 이벤트): 누가·언제·무엇을
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  t      REAL NOT NULL,
  actor  TEXT NOT NULL,
  action TEXT NOT NULL,               -- ORDER | QA_APPROVE | QA_DISCARD | INTERLOCK_ENTER | INTERLOCK_EXIT
  target TEXT,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_items_batch   ON items(batch_id);
CREATE INDEX IF NOT EXISTS ix_weights_batch ON weights(batch_id);
CREATE INDEX IF NOT EXISTS ix_events_batch  ON events(batch_id);
