# SeatSync Web — 구현 명세 (Claude Code 작업 지시서)

> 이 파일을 레포 루트에 두고 Claude Code에 "SEATSYNC_WEB_SPEC.md를 읽고 M1부터 구현해줘"라고 지시한다.
> (또는 파일명을 `CLAUDE.md`로 바꿔 두면 매 세션 자동으로 읽힌다.)

---

## 0. Claude Code에게: 작업 규칙

1. **스택 고정**: Python 3.10+, Flask, SQLite(`sqlite3` 표준 모듈), Jinja 템플릿, 바닐라 JS, 순수 CSS. React/Vue/npm 빌드/ORM 도입 금지. 추가 pip 패키지는 `flask`, `qrcode[pil]`, `pytest`만 허용.
2. **마일스톤 순서대로**(§11) 진행하고, 각 마일스톤 끝에 `pytest`가 전부 통과해야 다음으로 넘어간다. 마일스톤마다 git 커밋 1개 이상.
3. **판정 로직(`status.py`)은 순수 함수**로 유지한다. DB·Flask·현재 시각에 의존하지 않고 인자로만 받는다. 테스트를 먼저 작성한다.
4. **하드코딩 금지 대상**: 좌석 수·배치(`config/seats.json`), 시간 기준값(`settings` 테이블). 코드에 숫자 박지 말 것.
5. **영상·이미지·얼굴 데이터를 저장하거나 받는 코드를 만들지 않는다.** 서버가 받는 건 좌석 번호와 점유 상태뿐.
6. UI 문구는 **한국어**. 모든 화면은 **모바일 폭(360px)에서 먼저** 동작해야 한다(QR로 폰에서 열림).
7. 명세가 모호하면 §13의 기본값을 따르고, 코드에 `# SPEC-ASSUMPTION:` 주석을 남긴다.
8. 시간은 DB에 **UTC epoch 초(int)** 로 저장한다. API 입출력은 ISO 8601(+09:00), 화면 표시는 KST.

---

## 1. 프로젝트 요약

공공도서관 열람실의 **예약 정보**와 카메라가 감지한 **실제 착석 상태**를 대조해, 사석화(짐만 두고 장시간 이석)와 무단 사용(미예약 착석)을 관리자 화면에 표시하는 시스템.

- 라즈베리파이(팀원 담당): 카메라 → YOLO로 사람/소지품 검출 → 좌석 영역 매핑 → 좌석별 `person | item | empty`를 웹으로 전송
- **웹(이 레포)**: 예약·체크인·반납, 좌석별 상태 판정, 사용자 화면, 관리자 대시보드
- 시스템은 표시만 하고 제재·조치는 관리자가 결정한다.

```
[Pi: 카메라 → YOLO → 좌석 매핑] --POST /api/detections (5초마다)--> [Flask: judge() + SQLite]
                                                                     ├─ 사용자 화면 (/map, /seat/<no>, /my)   ← 3초 polling
                                                                     └─ 관리자 화면 (/admin, /admin/settings) ← 3초 polling
```

---

## 2. 디렉터리 구조

```
seatsync-web/
  app.py                 # Flask 앱 생성, 블루프린트 등록, 실행 진입점
  config.py              # 환경변수 로드 (§12)
  db.py                  # 커넥션, init_db(), seed()
  schema.sql
  status.py              # judge() 순수 함수 + 상태 enum/라벨/색
  service.py             # refresh(now): sweep + 전 좌석 판정 + 전이 기록 + 알림
  auth.py                # 로그인/로그아웃/회원가입, login_required, admin_required
  routes/
    pages.py             # HTML 화면 라우트
    api_user.py          # /api/seats, /api/reservations..., /api/calls
    api_admin.py         # /api/admin/...
    api_device.py        # /api/detections
  config/seats.json
  templates/
    base.html  login.html  signup.html  map.html  seat.html  my.html
    admin.html  admin_settings.html
  static/
    css/app.css
    js/common.js  map.js  seat.js  my.js  admin.js  settings.js
  tools/
    simulate.py          # 가짜 감지 전송기 (개발용)
    make_qr.py           # 좌석별 QR PNG + 인쇄용 HTML 생성
  tests/
    test_status.py  test_reservations.py  test_detections.py  test_refresh.py
  requirements.txt
  README.md
```

---

## 3. 데이터베이스 (`schema.sql`)

```sql
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
  resolution      TEXT                            -- 'handled' | 'force_returned' | 'auto'
);
-- 같은 좌석·같은 유형의 미해결 알림은 1개만 (call 제외)
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_open ON alerts(seat_no, type) WHERE resolved_at IS NULL AND type <> 'call';

CREATE TABLE IF NOT EXISTS settings (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);
```

### 3.1 settings 기본값 (seed 시 없을 때만 insert)

| key | 기본값 | 의미 |
|---|---|---|
| `grace_sec` | 60 | 미예약 좌석 착석 후 무단 사용으로 보기까지 유예(임시 점유) |
| `checkin_limit_min` | 15 | 예약 후 체크인 제한. 넘으면 `no_show`로 자동 취소 |
| `hoarding_min` | 30 | 체크인 좌석에 짐만 있는 상태 지속 → 사석화 |
| `empty_return_min` | 30 | 체크인 좌석이 완전히 빈 상태 지속 → 반납 대상 |
| `default_use_min` | 120 | 예약 1회 이용 시간 |
| `extend_min` | 60 | 연장 1회 시간 |
| `extend_window_min` | 30 | 남은 시간이 이 값 이하일 때만 연장 가능 |
| `max_extends` | 2 | 최대 연장 횟수 |
| `stale_sec` | 30 | 감지 수신이 이 시간 끊기면 `OFFLINE` |
| `auto_return_empty` | 0 | 1이면 `RETURN_DUE` 도달 시 자동 반납, 0이면 알림만 |

설정은 `/admin/settings`에서 변경되며, 다음 `refresh()`부터 즉시 반영된다. 시연 시 `hoarding_min=1`로 낮춰 쓴다.

### 3.2 seed 데이터

- `config/seats.json`의 좌석을 `seats`에 upsert (`qr_token`은 없을 때만 `secrets.token_urlsafe(8)`로 생성, 이후 유지).
- 계정: `admin / admin1234`(role=admin), `20260001`~`20260005` / `1234`(role=user, 이름 "테스트1"~"테스트5"). 비번은 `werkzeug.security.generate_password_hash`.
- `flask --app app init-db` CLI 커맨드로 스키마 생성 + seed. `--reset` 옵션은 DB 파일 삭제 후 재생성.

---

## 4. 좌석 배치 (`config/seats.json`) — 임시 배치, 현장에서 수정

```json
{
  "grid": { "cols": 5, "rows": 3 },
  "seats": [
    { "no": 1, "label": "A-1", "x": 1, "y": 1, "zone": "책상 A", "camera_id": "cam1" },
    { "no": 2, "label": "A-2", "x": 2, "y": 1, "zone": "책상 A", "camera_id": "cam1" },
    { "no": 3, "label": "A-3", "x": 1, "y": 2, "zone": "책상 A", "camera_id": "cam1" },
    { "no": 4, "label": "A-4", "x": 2, "y": 2, "zone": "책상 A", "camera_id": "cam1" },
    { "no": 5, "label": "B-1", "x": 4, "y": 1, "zone": "책상 B", "camera_id": "cam1" },
    { "no": 6, "label": "B-2", "x": 5, "y": 1, "zone": "책상 B", "camera_id": "cam1" },
    { "no": 7, "label": "B-3", "x": 4, "y": 2, "zone": "책상 B", "camera_id": "cam1" },
    { "no": 8, "label": "B-4", "x": 5, "y": 2, "zone": "책상 B", "camera_id": "cam1" }
  ],
  "fixtures": [
    { "label": "출입문", "x": 3, "y": 3 }
  ]
}
```

- 지도는 CSS Grid(`grid-template-columns: repeat(cols, 1fr)`)에 `grid-column: x; grid-row: y`로 배치. 3열은 통로(빈 칸).
- `seats.json`에서 빠진 좌석은 `active=0` 처리(삭제하지 않음, 이력 보존).
- 좌석 번호 `no`는 **Pi 매핑 담당과 공유하는 유일한 키**다.

---

## 5. 좌석 상태 판정 (`status.py`)

### 5.1 상태 enum

| 코드 | 한국어 라벨 | 관리자 색 | 사용자 지도 분류 | 알림 유형 |
|---|---|---|---|---|
| `AVAILABLE` | 빈자리 | 초록 | `available` | – |
| `TEMP_OCCUPIED` | 임시 점유 | 연노랑 | `unavailable` | – |
| `UNAUTHORIZED` | 무단 사용 | 빨강 | `unavailable` | `unauthorized` |
| `ITEM_ONLY` | 무단 물품 점유 | 주황 | `unavailable` | `item_only` |
| `RESERVED` | 예약됨(입실 전) | 연파랑 | `taken` | – |
| `AWAITING_CHECKIN` | 체크인 대기(착석함) | 연파랑+점선 | `taken` | – |
| `IN_USE` | 정상 이용 | 파랑 | `taken` | – |
| `AWAY_WITH_ITEM` | 이석 중(짐 있음) | 노랑 | `taken` | – |
| `HOARDING` | 사석화 | 빨강 | `taken` | `hoarding` |
| `AWAY_EMPTY` | 이석 중(짐 없음) | 회청 | `taken` | – |
| `RETURN_DUE` | 반납 대상 | 주황 | `taken` | `return_due` |
| `OFFLINE` | 감지 끊김 | 회색 | 아래 규칙 | – |

사용자 지도는 3분류만 보인다: `available`(빈자리) / `taken`(예약·사용 중) / `unavailable`(사용 불가). **사석화·무단 사용 같은 판정명은 사용자 API 응답에 포함하지 않는다.** `OFFLINE`은 활성 예약이 있으면 `taken`, 없으면 `unavailable`("확인 중").

### 5.2 시그니처

```python
@dataclass(frozen=True)
class Reservation:  id: int; status: str; start_at: int; end_at: int; checked_in_at: int | None
@dataclass(frozen=True)
class Detection:    occupancy: str; since: int; updated_at: int
@dataclass(frozen=True)
class Judgement:    state: str; since: int; elapsed_sec: int; deadline: int | None

def judge(res: Reservation | None, det: Detection | None, now: int, s: Settings) -> Judgement
```

`res`는 해당 좌석의 **활성 예약**(`reserved`/`in_use`)만 넘긴다. `deadline`은 다음 상태로 넘어갈 예정 시각(관리자 화면 "n분 후 사석화" 표시용), 없으면 `None`.

### 5.3 판정 규칙 (이 순서 그대로)

```
if det is None or now - det.updated_at > s.stale_sec:
    return OFFLINE (since = det.updated_at if det else now)

occ = det.occupancy
t   = now - det.since                       # 현재 점유 상태 지속 시간

if res is None:
    person → t < grace_sec          ? TEMP_OCCUPIED (deadline = det.since + grace_sec) : UNAUTHORIZED
    item   → ITEM_ONLY
    empty  → AVAILABLE

elif res.status == 'reserved':              # 체크인 전
    person|item → AWAITING_CHECKIN
    empty       → RESERVED (deadline = res.start_at + checkin_limit_min*60)

elif res.status == 'in_use':
    person → IN_USE
    item   → t < hoarding_min*60     ? AWAY_WITH_ITEM (deadline = det.since + hoarding_min*60) : HOARDING
    empty  → t < empty_return_min*60 ? AWAY_EMPTY     (deadline = det.since + empty_return_min*60) : RETURN_DUE
```

- 체크인 직후 짐만 감지되는 경우, 이석 시작점은 `max(det.since, res.checked_in_at)`로 계산한다(체크인 전부터 짐이 있었어도 체크인 시점부터 셈).
- `since`(Judgement)는 해당 상태가 시작된 시각: 타이머 기반 상태(`UNAUTHORIZED`, `HOARDING`, `RETURN_DUE`)는 `det.since + 기준시간`, 나머지는 `det.since`.

### 5.4 `service.refresh(now)` — 판정 갱신 파이프라인

한 트랜잭션에서 순서대로:

1. **sweep**
   - `reserved`이고 `now > start_at + checkin_limit_min*60` → `no_show`, `ended_at=now`, 알림 `no_show` 생성(정보성).
   - `in_use`/`reserved`이고 `now >= end_at` → `expired`, `ended_at=now`.
2. **판정**: 활성 좌석마다 `judge()`.
3. **전이 기록**: `seat_state`와 비교해 state가 바뀐 좌석만 `status_log` insert + `seat_state` 갱신.
4. **알림**
   - 새 state가 알림 유형을 가지면 `INSERT OR IGNORE`로 미해결 알림 생성(유니크 인덱스로 중복 방지).
   - 좌석 state가 해당 알림 유형의 상태를 벗어나면 그 미해결 알림을 `resolution='auto'`로 자동 해소.
5. `auto_return_empty=1`이고 `RETURN_DUE`면 예약을 `returned`로 종료.

호출 지점: `POST /api/detections`, `GET /api/seats`, `GET /api/admin/seats`, 모든 예약 변경 API 직후. 별도 스케줄러/스레드는 만들지 않는다(화면이 3초마다 polling하므로 사실상 주기 실행). 동시성은 `BEGIN IMMEDIATE`로 직렬화.

---

## 6. 인증

- Flask `session`(서명 쿠키). `SECRET_KEY`는 환경변수.
- `/login` POST(student_no, password) 성공 시 role이 `admin`이면 `/admin`, 아니면 `next` 파라미터 또는 `/map`으로 redirect.
- `/signup`: student_no, name, password(4자 이상). 가입 즉시 로그인. role은 항상 `user`.
- 데코레이터: `login_required`(페이지 → `/login?next=...` redirect, API → 401 JSON), `admin_required`(비관리자 → 페이지 403, API 403 JSON).
- 관리자도 사용자 화면(`/map`)은 볼 수 있다. 헤더에 "관리자 화면" 링크.

---

## 7. API 명세

공통: JSON 요청/응답, 시간은 ISO 8601(+09:00). 에러 형식:

```json
{ "error": { "code": "SEAT_TAKEN", "message": "이미 예약된 좌석입니다." } }
```

| HTTP | code | 상황 |
|---|---|---|
| 400 | `BAD_REQUEST` | 필드 누락/형식 오류 |
| 401 | `UNAUTHENTICATED` | 로그인 필요 |
| 403 | `FORBIDDEN` / `BAD_DEVICE_KEY` / `BAD_QR_TOKEN` | 권한·키·QR 토큰 불일치 |
| 404 | `NOT_FOUND` | 좌석/예약 없음 |
| 409 | `SEAT_TAKEN` / `SEAT_OCCUPIED` / `ALREADY_HAS_RESERVATION` / `INVALID_STATE` / `EXTEND_NOT_ALLOWED` | 비즈니스 규칙 위반 |

### 7.1 디바이스 → 웹 (팀원과 합의된 계약)

`POST /api/detections` — 헤더 `X-Device-Key: <SEATSYNC_DEVICE_KEY>`

```json
{
  "camera_id": "cam1",
  "ts": "2026-09-30T14:03:12+09:00",
  "seats": [
    { "seat_no": 1, "occupancy": "person", "since": "2026-09-30T13:40:02+09:00", "confidence": 0.87 },
    { "seat_no": 2, "occupancy": "item",   "since": "2026-09-30T13:55:40+09:00", "confidence": 0.64 }
  ]
}
```

- `occupancy`: Pi에서 상태 유지 필터(착석 ~15초, 이석 3~5분)를 거친 **확정값**. 웹은 재필터링하지 않는다.
- `since`: 해당 occupancy가 확정된 시각. `confidence`는 선택.
- Pi는 변화가 없어도 **5초마다 전체 좌석을 전송**(heartbeat).
- **시계 보정**: `offset = server_now - ts`를 계산해 `|offset| > 5초`면 `since += offset`으로 보정 후 저장.
- 알 수 없는 `seat_no`는 무시하고 응답의 `ignored`에 담는다(전체 실패 아님).
- 처리: detections upsert(`updated_at=server_now`) → `refresh(now)`.

응답 200:
```json
{ "ok": true, "accepted": 2, "ignored": [], "server_time": "2026-09-30T14:03:12+09:00" }
```

### 7.2 사용자 API

**`GET /api/seats`** (login) — 지도 polling용
```json
{
  "server_time": "...",
  "grid": { "cols": 5, "rows": 3 },
  "fixtures": [ { "label": "출입문", "x": 3, "y": 3 } ],
  "seats": [ { "no": 1, "label": "A-1", "x": 1, "y": 1, "zone": "책상 A", "view": "available" } ],
  "my_reservation": null | {
    "id": 12, "seat_no": 3, "seat_label": "A-3", "status": "in_use",
    "start_at": "...", "end_at": "...", "checked_in_at": "...",
    "checkin_deadline": "..." | null, "can_extend": true, "extend_count": 0
  }
}
```
`view` ∈ `available | taken | unavailable | mine`.

**`POST /api/reservations`** (login) — body `{ "seat_no": 3, "qr_token": "…"? }`
- 사용자에게 활성 예약이 있으면 409 `ALREADY_HAS_RESERVATION`.
- 좌석에 활성 예약이 있으면 409 `SEAT_TAKEN`.
- 좌석 판정이 `TEMP_OCCUPIED/UNAUTHORIZED/ITEM_ONLY`인데 `qr_token`이 없거나 틀리면 409 `SEAT_OCCUPIED` ("현재 다른 이용자가 앉아 있는 좌석입니다"). **올바른 `qr_token`이 있으면 허용** — 좌석에 앉은 본인이 QR로 예약하는 경로.
- `qr_token`이 올바르면 `source='seat_page'`, `status='in_use'`, `checked_in_at=now`(예약 즉시 체크인). 아니면 `source='map'`, `status='reserved'`.
- `start_at=now`, `end_at=now+default_use_min*60`. 트랜잭션은 `BEGIN IMMEDIATE`, 유니크 인덱스 위반은 409로 변환.
- 201 → `my_reservation`과 같은 형태.

**`POST /api/reservations/<id>/checkin`** (본인) — body `{ "qr_token": "…" }`
- 토큰이 해당 좌석 `qr_token`과 다르면 403 `BAD_QR_TOKEN`. status가 `reserved`가 아니면 409 `INVALID_STATE`.
- → `in_use`, `checked_in_at=now`.

**`POST /api/reservations/<id>/extend`** (본인)
- `in_use`이고 `end_at - now <= extend_window_min*60`이고 `extend_count < max_extends`일 때만. 아니면 409 `EXTEND_NOT_ALLOWED`(message에 사유).
- `end_at += extend_min*60`, `extend_count += 1`.

**`POST /api/reservations/<id>/return`** (본인) — `reserved`/`in_use` → `returned` (`reserved`면 `cancelled`), `ended_at=now`.

**`POST /api/calls`** (login) — body `{ "seat_no": 3, "memo": "제 예약석에 다른 분이 앉아 계세요" }` → `alerts(type='call')` 생성. 같은 사용자 60초 내 중복 호출은 429 아닌 200 + 기존 알림 반환.

**`GET /api/seats/<no>`** (login) — 좌석 페이지용
```json
{ "no": 3, "label": "A-3", "view": "unavailable",
  "page_mode": "mine_checkin | mine_in_use | reserve_now | occupied_unreserved | reserved_by_other | offline",
  "my_reservation": { ... } | null }
```
`page_mode` 결정 규칙은 §8 `/seat/<no>` 참조.

### 7.3 관리자 API (admin)

**`GET /api/admin/seats`**
```json
{
  "server_time": "...",
  "grid": {...}, "fixtures": [...],
  "summary": { "AVAILABLE": 3, "IN_USE": 2, "HOARDING": 1, "UNAUTHORIZED": 1, "...": 0 },
  "seats": [ {
    "no": 1, "label": "A-1", "x": 1, "y": 1,
    "state": "HOARDING", "state_label": "사석화", "since": "...", "elapsed_sec": 2140, "deadline": null,
    "occupancy": "item", "detection_updated_at": "...",
    "reservation": { "id": 7, "user_name": "테스트2", "student_no": "20260002", "status": "in_use",
                     "start_at": "...", "end_at": "...", "checked_in_at": "..." } | null
  } ]
}
```

**`GET /api/admin/alerts?open=1`** — 미해결 알림, 오래된 순. 각 항목에 `seat_label`, `type_label`, `created_at`, `elapsed_sec`, `reservation` 요약, `memo`.

**`POST /api/admin/alerts/<id>/resolve`** — `resolved_at=now`, `resolved_by=admin`, `resolution='handled'`.

**`POST /api/admin/reservations/<id>/force-return`** — 예약 `force_returned`, 해당 좌석 미해결 알림 전부 `resolution='force_returned'`로 해소.

**`GET /api/admin/settings`** / **`PUT /api/admin/settings`** — body는 §3.1 키의 부분 집합. 정수 범위 검증(0 이상, `grace_sec` 10~600 등 상식선). 알 수 없는 키는 400.

**`GET /api/admin/stats?date=YYYY-MM-DD`** (P2) — `status_log`로 시간대(0~23시)별 좌석·분 기준 `HOARDING`, `UNAUTHORIZED`, `IN_USE` 누적 시간과 비율.
```json
{ "date": "2026-10-01", "hours": [ { "hour": 14, "in_use_min": 210, "hoarding_min": 35, "unauthorized_min": 12, "hoarding_rate": 0.14 } ] }
```

---

## 8. 화면 명세

공통: `base.html`(상단바: 로고 "SeatSync", 사용자 이름, 로그아웃, 관리자면 "관리자" 링크). `common.js`에 `api(method, url, body)` 헬퍼(에러 JSON → 토스트 표시), `poll(fn, ms)`(탭이 숨겨지면 멈춤, `document.visibilityState`), `fmtRemain(sec)`("1시간 12분").

### `/login`, `/signup`
단순 폼. 실패 시 폼 위에 에러 문구.

### `/map` — 실시간 좌석 지도 (P0)
- 상단 "내 자리" 바: 예약 없으면 "빈 좌석을 눌러 예약하세요". 있으면 "A-3 · 남은 시간 1시간 12분 · [내 자리 관리]". `reserved`면 "10분 안에 좌석 QR로 체크인하세요"(체크인 마감 카운트다운).
- 그리드: 좌석 칸에 라벨. 색 — available 초록, taken 파랑, unavailable 회색, mine 굵은 테두리+별표. 범례 표시.
- 빈자리 클릭 → 확인 모달("A-2 좌석을 예약할까요? 이용 시간 2시간, 15분 안에 체크인 필요") → `POST /api/reservations` → 성공 토스트 + 즉시 재조회.
- 다른 좌석 클릭 → 이유 토스트("예약·사용 중인 좌석입니다" / "현재 사용할 수 없는 좌석입니다").
- 3초 polling. 폰 폭에서 좌석 칸 최소 56px.

### `/seat/<no>?t=<qr_token>` — 좌석 QR 도착 페이지 (P0/P1)
로그인 안 됐으면 로그인 후 같은 URL로 복귀. `t`는 페이지 JS가 API 호출 시 `qr_token`으로 전달. `t`가 틀리면 "좌석 QR을 다시 스캔해 주세요".

| page_mode | 조건 | 화면 |
|---|---|---|
| `mine_checkin` | 내 예약 좌석 + `reserved` | 큰 **[체크인]** 버튼 |
| `mine_in_use` | 내 예약 좌석 + `in_use` | "이용 중 · 남은 시간" + [연장] [반납] |
| `reserve_now` | 활성 예약 없음 (빈자리 또는 누군가 착석 = 대개 본인) | **"이 좌석을 배정받아 주세요"** 안내 + **[바로 예약하기]** (예약 즉시 체크인). 내가 다른 좌석을 예약 중이면 "현재 A-1 예약 중입니다 · [반납하고 이 좌석 예약]" |
| `reserved_by_other` | 다른 사람의 활성 예약 | "예약된 좌석입니다. 예약자라면 본인 계정으로 로그인하세요." + [관리자 호출] |
| `offline` | OFFLINE | 예약 정보 기준으로 위 모드 중 하나를 쓰되 상단에 "좌석 감지 일시 중단" 표시 |

### `/my` — 내 자리 관리 (P1)
좌석 라벨, 상태, 시작/종료 시각, 큰 남은 시간 카운트다운(1초 로컬 갱신, 30초마다 서버 재동기화), [연장](조건 미충족 시 비활성 + 사유 문구 "종료 30분 전부터 연장할 수 있어요"), [조기 반납](확인 모달), [관리자 호출](메모 입력 모달).

### `/admin` — 관리자 대시보드 (P0/P1)
레이아웃(데스크톱 2단, 모바일 1단):
- **상단 알림 배너**: 직전 polling 이후 새로 생긴 미해결 알림이 있으면 "B-2 사석화 발생" 식으로 노출 + 짧은 비프(사용자 상호작용 후에만 재생 가능하므로 "소리 켜기" 토글).
- **요약 칩**: 상태별 개수(`summary`).
- **좌석 지도**: 12개 상태 색 + 좌석 칸 안에 라벨·상태명·경과 시간("사석화 · 35분", "이석 중 · 12분 뒤 사석화"). 클릭 시 오른쪽 상세 패널(예약자 이름/학번, 예약 시각, 체크인 시각, 감지 상태·마지막 수신 시각).
- **문제 좌석 목록**: `GET /api/admin/alerts?open=1`. 항목 = 좌석, 유형 배지, 발생 후 경과, 예약자, 메모. 버튼 **[처리 완료] [강제 반납]**(예약 있을 때만).
- **예약 대조 표**(P1): 모든 좌석 행 — 좌석 | 예약 상태(예약자·시간) | 감지(person/item/empty, 수신 n초 전) | 판정. 불일치 행(판정이 알림 유형인 행) 강조.
- 3초 polling.

### `/admin/settings` (P1)
§3.1 키마다 라벨·단위·설명이 있는 숫자 입력. [저장] → `PUT`. [시연 모드] 버튼: `grace_sec=15, hoarding_min=1, empty_return_min=1, checkin_limit_min=2`를 채워 넣음(저장은 수동). [기본값 복원].

---

## 9. 도구

### `tools/simulate.py` (개발용, 시연 금지)
```
python tools/simulate.py --url http://localhost:5000 --key dev-key            # 대화형
  > set 3 person        # 3번 좌석을 person으로 (since=지금)
  > set 5 item -40m     # 5번 좌석 item, since=40분 전
  > all empty
  > show
python tools/simulate.py --scenario demo    # 정해진 시나리오 자동 재생
```
백그라운드 스레드가 현재 상태 전체를 5초마다 `/api/detections`로 전송(heartbeat 흉내). `--scenario demo`: 1번 정상 이용, 2번 사석화 진행, 3번 미예약 착석, 4번 미입실을 순서대로 만든다(예약은 API로 테스트 계정 로그인 후 생성).

### `tools/make_qr.py`
`--base-url http://<서버IP>:5000` 받아 `qr/seat_<no>.png` 생성(URL = `{base}/seat/{no}?t={qr_token}`), 인쇄용 `qr/print.html`(A4에 좌석 라벨 + QR + "스캔해서 체크인/예약" 문구) 생성.

---

## 10. 테스트 (pytest)

- `test_status.py`: 예약 3종(None/reserved/in_use) × 점유 3종 = 9조합 각 1개 이상 + 경계값(`grace_sec` 직전/직후, `hoarding_min` 직전/직후, `empty_return_min`, stale 경계, 체크인 전 짐 → 체크인 시점부터 이석 계산).
- `test_reservations.py`: 예약 성공, 좌석 중복 409, 사용자 중복 409, `SEAT_OCCUPIED`와 qr_token 예외, 체크인 토큰 오류 403, 연장 창 밖 409 / 창 안 성공 / 횟수 초과 409, 반납, 체크인 마감 후 sweep → `no_show`, 종료 시각 후 → `expired`.
- `test_detections.py`: 키 없음/오류 403, 알 수 없는 좌석 ignored, upsert, 시계 보정.
- `test_refresh.py`: 상태 전이 시에만 `status_log` 기록, 알림 생성·중복 방지·자동 해소, 강제 반납 시 알림 해소, `auto_return_empty`.
- 시간은 `now`를 인자로 주입해 테스트한다(`time.time()` 직접 호출은 라우트 계층에서만).

---

## 11. 마일스톤과 완료 기준

| M | 범위 | 완료 기준 |
|---|---|---|
| M1 | 골격: 폴더, `schema.sql`, `init-db`/seed, 로그인·회원가입·role 분기, base 템플릿 | admin 로그인 → `/admin`(빈 페이지), user 로그인 → `/map`(빈 페이지) |
| M2 | `status.py` + 테스트, `/api/detections`, `service.refresh`, `simulate.py` | `pytest` 통과. 시뮬레이터로 보낸 상태가 `GET /api/admin/seats` JSON에 올바른 판정으로 나옴 |
| M3 | `/map` + `/api/seats`, 예약/반납, `/seat/<no>` + 체크인·바로 예약, `make_qr.py` | 폰으로 예약 → QR 스캔 체크인 → 지도 색 변경까지 한 바퀴 |
| M4 | `/admin` 지도·상세·문제 목록·처리 완료·강제 반납 | 시뮬레이터로 사석화/무단 사용을 만들면 3초 내 목록에 뜨고 처리 가능 → **여기까지 P0** |
| M5 | `/my`(연장·반납·호출), 알림 배너, 예약 대조 표, `/admin/settings` | 설정 변경이 다음 polling에 판정에 반영됨 |
| M6 (선택) | `/api/admin/stats` + 관리자 화면 시간대별 막대 차트(순수 SVG 또는 CSS) | 하루치 `status_log`로 시간대별 사석화율 표시 |

각 마일스톤 끝에 README의 "실행 방법"과 "현재 구현 범위"를 갱신한다.

---

## 12. 실행·환경

```
pip install -r requirements.txt
flask --app app init-db
python app.py            # 0.0.0.0:5000, threaded=True
```

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `SEATSYNC_SECRET_KEY` | 개발용 임의값 | Flask 세션 키 |
| `SEATSYNC_DEVICE_KEY` | `dev-key` | Pi 인증 키 (팀원과 공유) |
| `SEATSYNC_DB` | `seatsync.db` | SQLite 파일 경로 |
| `SEATSYNC_TZ` | `Asia/Seoul` | 표시용 타임존 (`zoneinfo`) |

- 서버는 노트북 실행을 기본으로 가정. Pi와 폰은 같은 Wi-Fi(또는 휴대폰 핫스팟)에서 `http://<노트북IP>:5000` 접속.
- HTTPS 불필요(로컬 네트워크). 폰 기본 카메라 앱으로 QR 스캔 → 브라우저로 열림.

---

## 13. 미확정 사항과 기본값 (확정되면 이 표만 갱신)

| 항목 | 현재 기본값 | 결정 주체 |
|---|---|---|
| 판정 시간값(`grace_sec`, `hoarding_min` 등) | §3.1 | 정책 담당 팀원 → 관리자 설정 화면에서 반영 |
| 실제 좌석 배치 | §4 임시 8석 | 현장 확인 후 `seats.json` 수정 |
| 웹 서버 실행 위치 | 노트북 | 팀 합의 |
| 열람실 입장 QR | 구현 안 함 | 좌석 QR로 미예약 착석 처리 가능, 개인 추적 성격이 강해 MVP 제외 |
| 휴대폰 푸시 알림 | 구현 안 함 | 관리자 화면 모바일 반응형 + 배너로 대체 |
| `RETURN_DUE` 자동 반납 | 끔(`auto_return_empty=0`) | 관리자가 판단 (기획안: 조치는 관리자 결정) |

## 14. 범위 밖 (만들지 말 것)

카메라 영상 수신·저장·스트리밍, 얼굴/개인 식별, YOLO 추론 코드, 좌석 영역 좌표 편집 도구(Pi 쪽 담당), 결제, 외부 로그인(SSO), 이메일/SMS 발송, 다중 열람실(단일 열람실 가정).
