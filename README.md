# SeatSync Web

공공도서관 열람실의 **예약 정보**와 카메라가 감지한 **실제 착석 상태**를 대조해
사석화(짐만 두고 장시간 이석)와 무단 사용(미예약 착석)을 관리자 화면에 표시하는 웹 서버.
시스템은 표시만 하고, 제재·조치는 관리자가 결정한다.

- 명세: [SEATSYNC_WEB_SPEC.md](SEATSYNC_WEB_SPEC.md)
- 스택: Python 3.10+, Flask, SQLite(`sqlite3`), Jinja, 바닐라 JS, 순수 CSS

```
[Pi: 카메라 → YOLO → 좌석 매핑] --POST /api/detections (5초마다)--> [Flask: judge() + SQLite]
                                                                     ├─ 사용자 화면 (/map, /seat/<no>, /my)   ← 3초 polling
                                                                     └─ 관리자 화면 (/admin, /admin/settings) ← 3초 polling
```

## 실행 방법

```
pip install -r requirements.txt
flask --app app init-db            # 스키마 생성 + seed (--reset: DB 삭제 후 재생성)
python app.py                      # 0.0.0.0:5000, threaded
```

폰·Pi는 노트북과 같은 Wi-Fi(또는 휴대폰 핫스팟)에서 `http://<노트북IP>:5000` 으로 접속한다.

| 계정 | 비밀번호 | 역할 |
|---|---|---|
| `admin` | `admin1234` | 관리자 → `/admin` |
| `20260001` ~ `20260005` | `1234` | 사용자(테스트1~5) → `/map` |

### 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `SEATSYNC_SECRET_KEY` | 개발용 값 | Flask 세션 키 (운영 시 반드시 변경) |
| `SEATSYNC_DEVICE_KEY` | `dev-key` | Pi 인증 키 (`X-Device-Key` 헤더, 팀원과 공유) |
| `SEATSYNC_DB` | `seatsync.db` | SQLite 파일 경로 |
| `SEATSYNC_TZ` | `Asia/Seoul` | 표시용 타임존 |

### 좌석 배치

`config/seats.json`을 수정한 뒤 `flask --app app init-db`를 다시 실행하면 좌석이 갱신된다(`qr_token`은 유지,
빠진 좌석은 비활성 처리). 좌석 번호 `no`는 Pi 매핑과 공유하는 유일한 키다.

### 좌석 QR 만들기

```
python tools/make_qr.py --base-url http://<노트북IP>:5000   # qr/seat_<no>.png + qr/print.html (A4 인쇄용)
```

### 개발 도구 · 테스트

```
python tools/simulate.py --url http://localhost:5000 --key dev-key   # 대화형 가짜 감지 (set 3 person / set 5 item -40m / all empty / show)
python tools/simulate.py --scenario demo                             # 정상 이용·사석화·무단 사용·미입실 시나리오 자동 재생
python -m pytest -q
```

시연 전 `/admin/settings` → [시연 모드] → [저장]으로 기준 시간을 짧게(사석화 1분 등) 바꿔 두면 빠르게 확인할 수 있다.
(시뮬레이터는 개발용이며 시연에 쓰지 않는다.)

## Pi → 웹 계약 (요약)

`POST /api/detections` · 헤더 `X-Device-Key` · 5초마다 전체 좌석 전송

```json
{ "camera_id": "cam1", "ts": "2026-09-30T14:03:12+09:00",
  "seats": [ { "seat_no": 1, "occupancy": "person", "since": "2026-09-30T13:40:02+09:00", "confidence": 0.87 } ] }
```

- `occupancy`: `person | item | empty` (Pi에서 필터링을 마친 확정값)
- 서버-Pi 시계 차이가 5초를 넘으면 `since`를 서버 시각 기준으로 보정
- 알 수 없는 좌석 번호는 무시하고 응답 `ignored`에 담는다
- 서버는 영상·이미지를 받거나 저장하지 않는다 — 좌석 번호와 점유 상태만 받는다

## 현재 구현 범위

- [x] M1: 골격, 스키마, `init-db`/seed, 로그인·회원가입·역할 분기
- [x] M2: 판정 로직(`status.py`, 순수 함수) + 테스트, `POST /api/detections`, `service.refresh`, `tools/simulate.py`
- [x] M3: `/map` 실시간 좌석 지도, 예약/반납, `/seat/<no>` QR 페이지(체크인·바로 예약·관리자 호출), `tools/make_qr.py`
- [x] M4: `/admin` 대시보드 — 12개 상태 지도, 상세 패널, 요약 칩, 문제 좌석 목록(처리 완료·강제 반납)
- [x] M5: `/my`(카운트다운·연장·반납·관리자 호출), 새 알림 배너+비프, 예약 대조 표, `/admin/settings`
- [x] M6: `GET /api/admin/stats` + 관리자 화면 시간대별 사석화율 막대 차트

### 명세 해석 메모 (`# SPEC-ASSUMPTION:` 주석 참고)

- 알림은 해당 상태로 **전이할 때만** 생성한다 — 관리자가 처리 완료한 뒤 같은 상태가 이어져도 다시 뜨지 않는다.
- `OFFLINE`(감지 끊김)은 실제 상태를 모르는 것이므로 기존 알림을 자동 해소하지 않는다.
- 호출 중복 판정(60초)을 위해 `alerts.created_by` 컬럼을 추가했다.
- 감지가 끊긴 미예약 좌석은 사용자 지도에 '확인 중'(사용 불가 색 + 빗금)으로 보이며, 확인 후 예약할 수 있다.
- `/api/seats/<no>`의 감지 끊김은 별도 `page_mode` 대신 `offline: true` 플래그로 준다.
- 사석화율 = 사석화 시간 / (정상 이용 + 사석화 시간).
