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

## 개발 도구

```
python tools/simulate.py --url http://localhost:5000 --key dev-key   # 대화형 가짜 감지
python tools/simulate.py --scenario demo                             # 시나리오 자동 재생
python -m pytest -q
```
