PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  student_no      TEXT UNIQUE NOT NULL,           -- 학번(로그인 아이디)
  name            TEXT NOT NULL,
  pw_hash         TEXT NOT NULL,
  role            TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),  -- 미사용: 관리자 권한은 관리자 코드로 해금
  warnings        INTEGER NOT NULL DEFAULT 0,      -- 누적 경고 수
  suspended_until INTEGER,                         -- 이용 정지 종료 시각 (NULL = 정지 아님)
  created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS seats (
  no            INTEGER PRIMARY KEY,
  label         TEXT NOT NULL,
  x             INTEGER NOT NULL,           -- 그리드 열 (1부터)
  y             INTEGER NOT NULL,           -- 그리드 행 (1부터)
  zone          TEXT,                       -- 책상/구역 이름
  camera_id     TEXT,
  qr_token      TEXT NOT NULL,              -- 좌석 QR에 들어가는 난수 (원격 체크인 방지)
  active        INTEGER NOT NULL DEFAULT 1,
  -- 현장 상태: 카메라 연동 전까지 관리자가 임시로 배분한다
  state         TEXT NOT NULL DEFAULT 'empty' CHECK (state IN ('empty','occupied','item','unavailable')),
  state_since   INTEGER NOT NULL DEFAULT 0,
  state_source  TEXT NOT NULL DEFAULT 'manual',  -- manual | camera | checkin | return | seed
  state_note    TEXT                              -- 사용불가 사유 등
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
  source         TEXT NOT NULL DEFAULT 'map' CHECK (source IN ('map','seat_page','admin'))
);
-- 활성 예약은 좌석당 1개, 사용자당 1개
CREATE UNIQUE INDEX IF NOT EXISTS ux_res_active_seat ON reservations(seat_no) WHERE status IN ('reserved','in_use');
CREATE UNIQUE INDEX IF NOT EXISTS ux_res_active_user ON reservations(user_id) WHERE status IN ('reserved','in_use');

CREATE TABLE IF NOT EXISTS seat_state (         -- 직전 대조 결과 캐시 (전이 감지용)
  seat_no     INTEGER PRIMARY KEY REFERENCES seats(no),
  seat_state  TEXT NOT NULL,
  situation   TEXT NOT NULL,
  since       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS status_log (         -- 상태·상황 전이 이력 (통계)
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  seat_no         INTEGER NOT NULL,
  seat_state      TEXT NOT NULL,
  situation       TEXT NOT NULL,
  prev_situation  TEXT,
  reservation_id  INTEGER,
  actual          TEXT,
  at              INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_log_seat_at ON status_log(seat_no, at);

CREATE TABLE IF NOT EXISTS alerts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  seat_no         INTEGER NOT NULL,
  type            TEXT NOT NULL CHECK (type IN ('unauthorized','no_checkin','away','hoarding','seat_unavailable','no_show','call')),
  reservation_id  INTEGER,
  memo            TEXT,
  created_at      INTEGER NOT NULL,
  created_by      INTEGER REFERENCES users(id),   -- 호출(call)한 이용자
  resolved_at     INTEGER,
  resolved_by     INTEGER REFERENCES users(id),   -- NULL + resolved_at 있음 = 자동 해소
  resolution      TEXT                            -- 'handled' | 'force_returned' | 'auto' | 'reset' ...
);
-- 같은 좌석·같은 유형의 미해결 알림은 1개만 (call 제외)
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_open ON alerts(seat_no, type) WHERE resolved_at IS NULL AND type <> 'call';

CREATE TABLE IF NOT EXISTS admin_log (          -- 관리자 처리 이력 (경고·정지 포함)
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  admin_id        INTEGER REFERENCES users(id),   -- 관리자 코드로 권한을 해금한 로그인 사용자
  action          TEXT NOT NULL,
  seat_no         INTEGER,
  reservation_id  INTEGER,
  target_user_id  INTEGER REFERENCES users(id),
  alert_id        INTEGER,
  memo            TEXT,
  at              INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_admin_log_at ON admin_log(at);

CREATE TABLE IF NOT EXISTS settings (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);

-- 스키마 버전 (구조가 바뀌면 올린다. db.SCHEMA_VERSION과 같아야 함)
PRAGMA user_version = 3;
