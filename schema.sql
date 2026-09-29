PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  student_no  TEXT UNIQUE NOT NULL,
  name        TEXT NOT NULL,
  pw_hash     TEXT NOT NULL,
  role        TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
  created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS seats (
  no        INTEGER PRIMARY KEY,
  label     TEXT NOT NULL,
  x         INTEGER NOT NULL,           -- 그리드 열 (1부터)
  y         INTEGER NOT NULL,           -- 그리드 행 (1부터)
  zone      TEXT,                       -- 책상/구역 이름
  camera_id TEXT,
  qr_token  TEXT NOT NULL,              -- 좌석 QR에 들어가는 난수 (원격 체크인 방지)
  active    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS reservations (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id        INTEGER NOT NULL REFERENCES users(id),
  seat_no        INTEGER NOT NULL REFERENCES seats(no),
  status         TEXT NOT NULL CHECK (status IN ('reserved','in_use','returned','cancelled','expired','no_show','force_returned')),
  start_at       INTEGER NOT NULL,
  end_at         INTEGER NOT NULL,
  checked_in_at  INTEGER,
  ended_at       INTEGER,
  extend_count   INTEGER NOT NULL DEFAULT 0,
  source         TEXT NOT NULL DEFAULT 'map' CHECK (source IN ('map','seat_page'))
);
-- 활성 예약은 좌석당 1개, 사용자당 1개
CREATE UNIQUE INDEX IF NOT EXISTS ux_res_active_seat ON reservations(seat_no) WHERE status IN ('reserved','in_use');
CREATE UNIQUE INDEX IF NOT EXISTS ux_res_active_user ON reservations(user_id) WHERE status IN ('reserved','in_use');

CREATE TABLE IF NOT EXISTS detections (         -- 좌석당 최신 1행 (upsert)
  seat_no     INTEGER PRIMARY KEY REFERENCES seats(no),
  occupancy   TEXT NOT NULL CHECK (occupancy IN ('person','item','empty')),
  since       INTEGER NOT NULL,                  -- Pi가 그 상태를 확정한 시각
  confidence  REAL,
  camera_id   TEXT,
  updated_at  INTEGER NOT NULL                   -- 서버 수신 시각
);

CREATE TABLE IF NOT EXISTS seat_state (         -- 직전 판정 캐시 (전이 감지용)
  seat_no   INTEGER PRIMARY KEY REFERENCES seats(no),
  state     TEXT NOT NULL,
  since     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS status_log (         -- 상태 전이 이력 (통계/정확도 측정)
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  seat_no         INTEGER NOT NULL,
  state           TEXT NOT NULL,
  prev_state      TEXT,
  reservation_id  INTEGER,
  occupancy       TEXT,
  at              INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_log_seat_at ON status_log(seat_no, at);

CREATE TABLE IF NOT EXISTS alerts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  seat_no         INTEGER NOT NULL,
  type            TEXT NOT NULL CHECK (type IN ('hoarding','unauthorized','item_only','return_due','no_show','call')),
  reservation_id  INTEGER,
  memo            TEXT,
  created_at      INTEGER NOT NULL,
  resolved_at     INTEGER,
  resolved_by     INTEGER REFERENCES users(id),   -- NULL + resolved_at 있음 = 자동 해소
  resolution      TEXT,                           -- 'handled' | 'force_returned' | 'auto'
  -- SPEC-ASSUMPTION: '같은 사용자 60초 내 중복 호출' 판정을 위해 호출자를 기록한다 (call 전용).
  created_by      INTEGER REFERENCES users(id)
);
-- 같은 좌석·같은 유형의 미해결 알림은 1개만 (call 제외)
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_open ON alerts(seat_no, type) WHERE resolved_at IS NULL AND type <> 'call';

CREATE TABLE IF NOT EXISTS settings (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);
