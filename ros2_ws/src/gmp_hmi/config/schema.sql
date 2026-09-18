-- 배치 기록의 단일 쓰기 주체는 record_node. HMI 는 mode=ro 로 연다.
CREATE TABLE IF NOT EXISTS batches (
  batch_id TEXT PRIMARY KEY, product TEXT, started_at REAL NOT NULL,
  finished_at REAL, result TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT NOT NULL, material_id TEXT NOT NULL,
  target_g REAL, actual_g REAL, error_pct REAL, verdict TEXT,
  attempts INTEGER, t REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS weights (
  id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT, t REAL NOT NULL,
  station TEXT, subject TEXT NOT NULL DEFAULT '', samples INTEGER NOT NULL DEFAULT 0,
  gross_g REAL, tare_g REAL, net_g REAL, std_g REAL, valid INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS scoop_cycles (
  id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,
  material_id TEXT NOT NULL, attempt INTEGER NOT NULL, t REAL NOT NULL,
  target_g REAL, actual_before_g REAL, delivered_g REAL,
  commanded_pour_fraction REAL, outcome INTEGER, valid INTEGER NOT NULL,
  duration_s REAL, payload_json TEXT NOT NULL,
  UNIQUE(batch_id, material_id, attempt)
);
CREATE TABLE IF NOT EXISTS deviations (
  deviation_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, material_id TEXT,
  kind TEXT NOT NULL, detail TEXT, requires_decision INTEGER NOT NULL,
  decision TEXT NOT NULL, operator_id TEXT, raised_at REAL NOT NULL, decided_at REAL
);
-- events 와 audit 는 append-only. 정정은 새 이벤트로 남긴다.
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL NOT NULL, batch_id TEXT,
  level TEXT NOT NULL, code TEXT NOT NULL, text TEXT
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL NOT NULL,
  actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT, detail TEXT
);
-- 이전 버전에 쌓인 중복 분주 결과는 마이그레이션 시 여기 보존한다.
CREATE TABLE IF NOT EXISTS legacy_item_duplicates (
  original_id INTEGER PRIMARY KEY, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_items_batch ON items(batch_id);
CREATE INDEX IF NOT EXISTS ix_weights_batch ON weights(batch_id);
CREATE INDEX IF NOT EXISTS ix_cycles_batch ON scoop_cycles(batch_id);
CREATE INDEX IF NOT EXISTS ix_events_batch ON events(batch_id);
CREATE INDEX IF NOT EXISTS ix_batches_time ON batches(started_at);
CREATE INDEX IF NOT EXISTS ix_events_time ON events(t);
