# SeatSync Web

공공도서관 열람실의 예약 정보와 카메라 착석 감지 결과를 대조해 사석화·무단 사용을 관리자 화면에 표시하는 웹 서버.
명세: [SEATSYNC_WEB_SPEC.md](SEATSYNC_WEB_SPEC.md)

## 실행 방법

```
pip install -r requirements.txt
flask --app app init-db          # --reset: DB 삭제 후 재생성
python app.py                    # 0.0.0.0:5000
```

테스트 계정: `admin / admin1234`(관리자), `20260001`~`20260005 / 1234`(사용자)

## 현재 구현 범위

- M1: 골격, 스키마, init-db/seed, 로그인·회원가입·역할 분기
- M2: 판정 로직(`status.py`) + 테스트, `POST /api/detections`, `service.refresh`, 관리자 판정 API, `tools/simulate.py`
- M3: `/map` 실시간 좌석 지도, 예약/반납, `/seat/<no>` QR 페이지(체크인·바로 예약·관리자 호출), `tools/make_qr.py`
- M4: `/admin` 대시보드 — 12개 상태 지도·상세 패널·요약 칩·문제 좌석 목록(처리 완료·강제 반납)
- M5: `/my`(카운트다운·연장·반납·관리자 호출), 새 알림 배너+비프, 예약 대조 표, `/admin/settings`(시연 모드·기본값 복원)

## 개발 도구

```
python tools/simulate.py --url http://localhost:5000 --key dev-key   # 대화형 가짜 감지
python tools/simulate.py --scenario demo                             # 시나리오 자동 재생
python -m pytest -q
```

## 좌석 QR 만들기

```
python tools/make_qr.py --base-url http://<노트북IP>:5000   # qr/seat_<no>.png, qr/print.html
```
