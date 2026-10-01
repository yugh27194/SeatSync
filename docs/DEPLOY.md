# SeatSync 외부 접속 · 배포 가이드

다른 사람이 **외부 링크**로 SeatSync에 들어오게 하는 방법은 두 가지다. (소개 페이지는 [C](#c-소개-페이지--github-pages-httpsyugh27194githubioseatsync) 참고)

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
  관리자 화면 **[좌석 QR]** 에서 주소를 넣고 [QR 파일 다시 만들기], 또는
  `python manage.py make_qr --base-url https://quiet-river-1234.trycloudflare.com`
- 관리자 코드를 바꿔서 공유하려면 `share.bat` 실행 전 같은 창에서 `set SEATSYNC_ADMIN_CODE=원하는코드`.
- ngrok을 써도 된다(가입 필요): `ngrok http 5000` → 나온 `https://….ngrok-free.app` 주소 공유.

---

## B. 상시 배포 — PythonAnywhere (무료)

가입과 API 토큰 발급만 직접 하고, 나머지(가상환경·설치·DB·웹 앱 생성·WSGI·정적 파일·HTTPS·재시작)는 스크립트가 한다.

### 1) 가입과 API 토큰 (브라우저, 약 3분)
1. https://www.pythonanywhere.com → **Pricing & signup → Create a Beginner account** (무료).
   가입한 아이디가 주소가 된다: `https://아이디.pythonanywhere.com`
2. 오른쪽 위 **Account → API token → Create a new API token** → 토큰 문자열 복사.

### 2) 설치·배포 (PythonAnywhere의 Bash 콘솔에서 명령 3줄)
대시보드 **Consoles → Bash** 를 열고:
```bash
git clone https://github.com/yugh27194/SeatSync.git
cd SeatSync
bash deploy/pythonanywhere_setup.sh --admin-code admin
```
- API 토큰을 물으면 붙여 넣는다(화면에 표시되지 않음).
- `python3.11`이 없다는 오류가 나면 있는 버전으로: `PY=python3.10 bash deploy/pythonanywhere_setup.sh` (3.10 이상).
- 끝나면 사이트 주소, **관리자 코드**, 라즈베리파이용 **디바이스 키**, Pi 전송 주소가 출력된다.
- 비밀 값(SECRET_KEY, 디바이스 키, 관리자 코드)은 `~/.seatsync.env`에 저장된다(저장소에 올라가지 않음).
- (선택) 시연용 샘플 이력: `~/.virtualenvs/seatsync/bin/python manage.py demo_history`

### 3) 업데이트
```bash
cd ~/SeatSync && bash deploy/pythonanywhere_update.sh
```
`git pull` → 패키지 설치 → DB 마이그레이션·좌석 배치 반영 → 웹 앱 재시작.

### 4) 설정 바꾸기
- 관리자 코드: `~/.virtualenvs/seatsync/bin/python deploy/pythonanywhere_deploy.py --admin-code 새코드`
- 그 밖의 값: `~/.seatsync.env`를 고친 뒤 `python deploy/pythonanywhere_deploy.py --reload-only`

### 5) 알아 둘 점 (무료 계정)
- 3개월마다 Web 탭의 **"Run until 3 months from today"** 버튼을 눌러 연장해야 한다(메일로 알려 줌).
- 사용량(CPU) 제한이 있지만 이 사이트 규모(좌석 8석, 3초 polling)에서는 충분하다.
- 오류가 나면 Web 탭의 **Error log**를 확인한다.
- 좌석 QR은 고정 주소로 한 번만 만들면 된다. 관리자 모드 → **관리** → **[좌석 QR]** → 주소 확인 → **[QR 파일 만들기]**
  → **[A4 한 장 PDF 내려받기]** 로 인쇄(배율 100%), 점선을 따라 잘라 좌석에 붙인다.
  콘솔에서는 `~/.virtualenvs/seatsync/bin/python manage.py make_qr --base-url https://아이디.pythonanywhere.com`
  (파일은 `~/SeatSync/qr/`에 저장 — **Files** 탭에서도 내려받을 수 있다).
  카드 안내 문구를 한글로 넣으려면 한글 글꼴 경로를 `SEATSYNC_QR_FONT`에 지정한다(없으면 영문 "SCAN TO CHECK IN").
- 라즈베리파이 연동: `pi_bridge.py --url https://아이디.pythonanywhere.com --key <디바이스 키> --camera-id cam1`
  ([DATA_FLOW.md](DATA_FLOW.md#7-pi-설치실행)).

### 수동으로 설정하려면 (스크립트 대신)
Web 탭 → **Add a new web app → Manual configuration → Python 3.11** →
Source code `~/SeatSync`, Virtualenv `~/.virtualenvs/seatsync`,
WSGI 파일 내용을 [`deploy/pythonanywhere_wsgi.py`](../deploy/pythonanywhere_wsgi.py)로 교체,
Static files `/static/` → `~/SeatSync/static`, Force HTTPS 켜기 → Reload.
비밀 값은 `~/.seatsync.env`에 `SEATSYNC_SECRET_KEY=…` 형식으로 적는다.

### 사이트가 빈 화면일 때
아이디에 대문자가 있으면(예: `JiYujin`) 예전 스크립트가 WSGI 파일을 잘못된 이름으로 만들었다. 주소는 항상 소문자다.
```bash
cd ~/SeatSync && git pull
~/.virtualenvs/seatsync/bin/python deploy/pythonanywhere_deploy.py
```
→ `https://jiyujin.pythonanywhere.com` 처럼 소문자 주소로 접속한다.

---

## C. 소개 페이지 — GitHub Pages (`https://yugh27194.github.io/SeatSync/`)

`pages/` 폴더는 SeatSync를 소개하고 **[SeatSync 바로가기]** 버튼으로 실제 서비스(PythonAnywhere)에 연결하는 정적 페이지다.
`pages/go/`는 바로 서비스로 넘어가는 짧은 주소다(`…/SeatSync/go/`).

1. 저장소 **Settings → Pages → Build and deployment → Source: GitHub Actions** (한 번만)
2. `pages/**`가 바뀌어 push되면 `.github/workflows/pages.yml`이 자동 배포한다
   (처음에는 **Actions → pages → Run workflow**로 직접 실행).
3. 서비스 주소가 바뀌면 `pages/config.js`의 `SEATSYNC_APP_URL`만 고친다.

**공식 소개 주소: https://seatsync-skku.github.io** (서비스 바로가기: `https://seatsync-skku.github.io/go/`)
— [SeatSync-SKKU/SeatSync-SKKU.github.io](https://github.com/SeatSync-SKKU/SeatSync-SKKU.github.io) 저장소에 `pages/` 내용을 복사해 둔 것이다.
`pages/`를 고치면 그 저장소에도 같은 파일을 올려야 반영된다.

---

## 다른 주소(내 도메인 등)로 배포할 때
- `SEATSYNC_TRUSTED_ORIGINS="https://seat.example.com"` 처럼 그 주소를 환경변수로 알려 준다
  (로그인·예약 요청이 CSRF 검사에서 막히지 않게). trycloudflare·ngrok·pythonanywhere 주소는 기본으로 허용되어 있다.
- HTTPS 전용으로 운영하면 `SEATSYNC_HTTPS=1` (세션 쿠키를 HTTPS에서만 보냄).
- 실행은 개발 서버(`runserver`) 대신 `python manage.py serve` (waitress) 또는 gunicorn 등을 쓴다.
