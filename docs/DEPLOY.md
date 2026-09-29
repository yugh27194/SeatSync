# SeatSync 외부 접속 · 배포 가이드

다른 사람이 **외부 링크**로 SeatSync에 들어오게 하는 방법은 두 가지다.

| | A. 바로 공유 (Cloudflare 임시 터널) | B. 상시 배포 (PythonAnywhere) |
|---|---|---|
| 비용·가입 | 무료 · **가입 없음** | 무료 · 가입 필요 |
| 주소 | `https://임의단어.trycloudflare.com` (**켤 때마다 바뀜**) | `https://아이디.pythonanywhere.com` (**고정**) |
| 서버 위치 | 내 노트북 (노트북·창을 끄면 접속 불가) | 클라우드 (노트북을 꺼도 접속 가능) |
| 데이터 | 노트북의 `seatsync.db` | 클라우드의 `seatsync.db` (유지됨) |
| 추천 용도 | 시연·발표·팀원 테스트 | 계속 운영, 좌석 QR 인쇄 (주소가 고정이라서) |

> **보안 주의**: 외부에 공개하면 누구나 회원가입·로그인할 수 있다. 관리자 코드가 `admin`처럼 쉬우면 누구든 관리자 모드를 켤 수 있으니,
> 공개 기간에는 `SEATSYNC_ADMIN_CODE`를 추측하기 어려운 값으로 바꾸는 것을 권장한다. (코드를 5번 틀리면 5분간 잠김)

---

## A. 바로 공유 — Cloudflare 임시 터널 (Windows)

1. **cloudflared 설치** (한 번만). cmd에서:
   ```bat
   winget install --id Cloudflare.cloudflared
   ```
   설치 후 **cmd 창을 새로 연다.** (winget이 없으면 https://github.com/cloudflare/cloudflared/releases 에서
   `cloudflared-windows-amd64.exe`를 받아 `cloudflared.exe`로 이름을 바꿔 SeatSync 폴더에 넣는다.)

2. **SeatSync 폴더에서 `share.bat` 실행** (더블클릭 또는 cmd에서 `share.bat`)
   - "SeatSync 서버" 창이 따로 뜨고, 원래 창에 아래와 비슷한 주소가 나온다.
     ```
     Your quick Tunnel has been created! Visit it at:
     https://quiet-river-1234.trycloudflare.com
     ```
   - 이 **https 주소를 공유**하면 된다. 폰·다른 집 PC 어디서든 접속된다.

3. **끄기**: 두 창 모두 `Ctrl+C` 또는 창 닫기.

직접 명령으로 하려면 창 두 개에서 각각:
```bat
python manage.py serve --port 5000
cloudflared tunnel --url http://localhost:5000
```

- 주소는 **켤 때마다 바뀐다.** 좌석 QR은 그때마다 새로 만들어야 한다:
  `python tools\make_qr.py --base-url https://quiet-river-1234.trycloudflare.com`
- 관리자 코드를 바꿔서 공유하려면 `share.bat` 실행 전 같은 창에서 `set SEATSYNC_ADMIN_CODE=원하는코드`.
- ngrok을 써도 된다(가입 필요): `ngrok http 5000` → 나온 `https://….ngrok-free.app` 주소 공유.

---

## B. 상시 배포 — PythonAnywhere (무료)

### 1) 가입
https://www.pythonanywhere.com → **Pricing & signup → Create a Beginner account** (무료).
가입한 아이디가 주소가 된다: `https://아이디.pythonanywhere.com`

### 2) 코드 받기 · 설치 (Bash 콘솔)
대시보드 **Consoles → Bash** 를 열고:
```bash
git clone https://github.com/yugh27194/SeatSync.git
cd SeatSync
mkvirtualenv seatsync --python=python3.11
pip install -r requirements.txt
python manage.py init_db
python manage.py demo_history   # (선택) 혼잡도·내 기록 시연용 샘플 이력
```

### 3) 웹 앱 만들기 (Web 탭)
1. **Add a new web app → Next → Manual configuration → Python 3.11 → Next**
2. 설정 화면에서:
   - **Source code**: `/home/아이디/SeatSync`
   - **Virtualenv**: `/home/아이디/.virtualenvs/seatsync`
   - **WSGI configuration file** 링크를 눌러 내용을 전부 지우고,
     이 저장소의 [`deploy/pythonanywhere_wsgi.py`](../deploy/pythonanywhere_wsgi.py) 내용을 붙여 넣는다.
     `USERNAME`을 내 아이디로, `SEATSYNC_SECRET_KEY`를 긴 무작위 문자열로, 필요하면 `SEATSYNC_ADMIN_CODE`를 바꾼 뒤 저장.
     (무작위 문자열 만들기: Bash 콘솔에서 `python -c "import secrets;print(secrets.token_urlsafe(50))"`)
   - **Static files**: URL `/static/` → Directory `/home/아이디/SeatSync/static`
   - **Security → Force HTTPS**: 켜기
3. 맨 위 초록색 **Reload** 버튼 → `https://아이디.pythonanywhere.com` 접속.

### 4) 업데이트할 때
```bash
cd ~/SeatSync && git pull && workon seatsync && pip install -r requirements.txt && python manage.py migrate
```
그리고 Web 탭에서 **Reload**.

### 5) 알아 둘 점 (무료 계정)
- 3개월마다 Web 탭의 **"Run until 3 months from today"** 버튼을 눌러 연장해야 한다(메일로 알려 줌).
- 사용량(CPU) 제한이 있지만 이 사이트 규모(좌석 20석, 3초 polling)에서는 충분하다. 동시 접속이 아주 많으면 느려질 수 있다.
- 오류가 나면 Web 탭의 **Error log**를 확인한다.
- 좌석 QR은 고정 주소로 한 번만 만들면 된다:
  `python tools/make_qr.py --base-url https://아이디.pythonanywhere.com` → `qr/print.html`을 **Files** 탭에서 내려받아 인쇄.
- 라즈베리파이는 `https://아이디.pythonanywhere.com/api/detections` 로 감지 결과를 보내면 된다(헤더 `X-Device-Key`).

---

## 다른 주소(내 도메인 등)로 배포할 때
- `SEATSYNC_TRUSTED_ORIGINS="https://seat.example.com"` 처럼 그 주소를 환경변수로 알려 준다
  (로그인·예약 요청이 CSRF 검사에서 막히지 않게). trycloudflare·ngrok·pythonanywhere 주소는 기본으로 허용되어 있다.
- HTTPS 전용으로 운영하면 `SEATSYNC_HTTPS=1` (세션 쿠키를 HTTPS에서만 보냄).
- 실행은 개발 서버(`runserver`) 대신 `python manage.py serve` (waitress) 또는 gunicorn 등을 쓴다.
