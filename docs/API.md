# SeatSync API

- 요청·응답은 JSON, 시각은 ISO 8601(+09:00). DB에는 UTC epoch 초로 저장한다.
- 브라우저 API(`/api/…`)는 로그인 세션 쿠키 + CSRF 토큰(`X-CSRFToken` 헤더)을 사용한다.
- 관리자 API(`/api/admin/…`)는 이 세션에서 **관리자 코드**를 통과해야 한다(관리자 탭 첫 진입 때 한 번, 로그아웃 전까지 유지). 아니면 `403 ADMIN_REQUIRED`.
- 디바이스 API(`/api/detections`, `/api/device/config`)는 `X-Device-Key` 헤더로 인증하고 CSRF 검사에서 제외된다.

## 에러 형식

```json
{ "error": { "code": "SEAT_TAKEN", "message": "이미 예약된 좌석입니다." } }
```

| HTTP | code | 상황 |
|---|---|---|
| 400 | `BAD_REQUEST`, `UNSUPPORTED_SCHEMA` | 필드 누락·형식 오류 |
| 401 | `UNAUTHENTICATED` | 로그인 필요 |
| 403 | `FORBIDDEN`, `ADMIN_REQUIRED`, `BAD_ADMIN_CODE`, `BAD_DEVICE_KEY`, `BAD_QR_TOKEN`, `SUSPENDED` | 권한·키·QR 불일치, 이용 정지 |
| 404 | `NOT_FOUND` | 좌석·예약·이용자 없음 |
| 409 | `SEAT_TAKEN`, `SEAT_OCCUPIED`, `SEAT_UNAVAILABLE`, `SEAT_HELD`, `ALREADY_HAS_RESERVATION`, `ALREADY_WAITING`, `INVALID_STATE`, `EXTEND_NOT_ALLOWED` | 비즈니스 규칙 위반 |
| 429 | `ADMIN_LOCKED` | 관리자 코드 5회 실패 → 5분 잠금 |

---

## 이용자 API (로그인 필요)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/seats` | 좌석 지도: 배치(grid·fixtures·zones), 좌석별 `view`(available·taken·unavailable·mine·offered·held), 내 예약, 내 좌석 상태 안내(`my_status`), 빈자리 대기(`waitlist`), 지금 혼잡도(`live`). 관리자 확인 표시는 넣지 않는다(→ `/api/admin/seats`) |
| GET | `/api/seats/{no}?t={qr_token}` | 좌석 QR 페이지: `page_mode`(mine_checkin·mine_in_use·reserve_now·reserved_by_other·unavailable), `qr_ok`, `occupied`, `held` |
| POST | `/api/reservations` | `{seat_no, qr_token?}` 예약. 올바른 QR 토큰이면 예약과 동시에 체크인 |
| POST | `/api/reservations/{id}/checkin` | `{qr_token}` 좌석 QR 체크인 |
| POST | `/api/reservations/{id}/extend` | 연장 (종료 30분 전부터, 최대 2회 — 설정값) |
| POST | `/api/reservations/{id}/return` | 반납(입실 전이면 취소) |
| POST | `/api/calls` | `{seat_no, memo?}` 관리자 호출 (60초 내 중복은 기존 호출 반환) |
| POST | `/api/waitlist` | `{zone?}` 빈자리 알림 대기 등록 (구역 생략 = 아무 자리) |
| POST | `/api/waitlist/cancel` · `/api/waitlist/decline` | 대기 취소 · 안내받은 좌석 양보 |
| GET | `/api/me/history?period=day\|week\|month` | 일(14)·주(8)·월(6)별 이용 시간과 요약 |
| GET | `/api/me/reservations` | 최근 예약 30건과 이력(예약·체크인·연장·반납…) |
| GET | `/api/me/notifications` | 내 알림 40건, 안 읽은 수 |
| POST | `/api/me/notifications/read` | `{ids?}` 읽음 처리 (생략하면 전부) |
| GET | `/api/congestion[?scope=admin]` | 지금 혼잡도, 오늘 시간대별, 요일×시간 점유율 히트맵. `scope=admin`(관리자 탭 이용 분석, 관리자 코드 통과 세션)이면 실사용률·유휴 점유·처리 필요 추가 |

## 관리자 탭 권한

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/admin-mode` | 이 세션이 관리자 코드를 통과했는지 |
| POST | `/api/admin-mode/unlock` | `{code}` 관리자 코드 확인 → 세션 동안 관리자 탭 허용 (틀리면 `BAD_ADMIN_CODE`, 5회 실패 시 `ADMIN_LOCKED`) |

## 관리자 API (관리자 코드 통과 세션)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/admin/seats` | 좌석별 상태 분류(`category`: normal·away·unauthorized·unknown·unavailable, `category_label`: 정상·이석(일시)·이석(장기)·무단 점유·판단 불가)·좌석 상태·세부 상태·처리 필요·경과/마감(`deadline`·`next_label`: 마감 때 바뀔 상태)·현장 상태·카메라 정보·예약자·빈자리 안내, 요약, 부여 가능한 세부 상태 목록 |
| POST | `/api/admin/seats/{no}/state` | `{detail, note?}` 세부 상태 부여: empty·using·item·unauthorized·away·hoarding·broken·maintenance·blocked |
| POST | `/api/admin/seats/{no}/feedback` | `{verdict: correct\|wrong, correct_detail?, apply?, memo?}` 판정 피드백 |
| GET | `/api/admin/feedback` | 판정 정확도(전체·출처별·상태별), 자주 틀리는 판정, 최근 피드백, 카메라 탐지 점수 평균 |
| GET | `/api/admin/cameras` | 카메라별 연결 상태, 좌석 대응(A01→A-1)과 감지 상태 |
| GET | `/api/admin/alerts?open=1` | 처리 필요 알림 목록 |
| POST | `/api/admin/alerts/{id}/resolve` | `{memo?}` 처리 완료 |
| POST | `/api/admin/reservations` | `{user_id, seat_no, checkin?, memo?}` 대리 예약·현장 배정 |
| POST | `/api/admin/reservations/{id}/checkin` · `/move` · `/extend` · `/force-return` | 대리 체크인 · `{seat_no}` 좌석 이동 · 관리자 연장 · 강제 반납 |
| GET | `/api/admin/users` | 이용자 목록(경고·정지·현재 예약) |
| POST | `/api/admin/users/{id}/notice` | `{message, seat_no?}` 사전 경고(누적 안 됨) |
| POST | `/api/admin/users/{id}/warn` · `/unwarn` · `/suspend` · `/unsuspend` | 경고(누적)·취소·`{days}` 이용 정지·해제 |
| GET | `/api/admin/log` | 처리 이력 |
| GET/PUT | `/api/admin/settings` | 판정·운영 기준값 |
| GET | `/api/admin/stats?date=YYYY-MM-DD` | 시간대별 이용·장기 이석·사석화·무단 점유 누적 |

## 디바이스 API (`X-Device-Key`)

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/detections` | 감지 결과 수신 — 감지 프로토타입 스냅샷(`schema_version: 1`) 또는 occupancy 형식. 자세한 형식은 [DATA_FLOW.md](DATA_FLOW.md#3-메시지-형식) |
| GET | `/api/device/config?camera_id=` | 카메라 좌석 ID ↔ 웹 좌석 대응표, 권장 전송 주기 |

### 관리자 탭 화면

| 경로 | 설명 |
|---|---|
| `/admin` | 대시보드 |
| `/admin/qr` | 좌석 QR 보관함 — 좌석에 붙여 둔 QR을 그대로 A4 한 장으로 보기·인쇄 (GET만, QR을 만들거나 바꾸지 않음) |
| `/admin/congestion` | 이용 분석 (실사용률·유휴 점유·처리 필요) |
| `/admin/settings` | 판정 기준 |
