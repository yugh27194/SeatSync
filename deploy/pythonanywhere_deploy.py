#!/usr/bin/env python3
"""PythonAnywhere 웹 앱 자동 설정 — PythonAnywhere의 Bash 콘솔에서 실행한다.

    bash deploy/pythonanywhere_setup.sh          # 처음 한 번 (가상환경·설치·DB·웹 앱 설정)
    python deploy/pythonanywhere_deploy.py       # 웹 앱 설정만 다시 / --reload-only 로 재시작만

필요한 것: PythonAnywhere 계정과 API 토큰(Account → API token → Create a new API token).
하는 일:
  1) ~/.seatsync.env 에 비밀 값 저장 (없을 때만 새로 만듦: SECRET_KEY, 디바이스 키, 관리자 코드)
  2) 웹 앱 생성(없으면) → 소스·가상환경 경로 지정 → HTTPS 강제
  3) WSGI 파일 설치, /static/ 정적 파일 연결
  4) 웹 앱 재시작(reload)
"""
import argparse
import getpass
import json
import os
import secrets
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~")
PROJECT = os.path.join(HOME, "SeatSync")
VENV = os.path.join(HOME, ".virtualenvs", "seatsync")
ENV_FILE = os.path.join(HOME, ".seatsync.env")


def api_host():
    return os.environ.get("PYTHONANYWHERE_SITE") or "www.pythonanywhere.com"


def web_domain(username):
    site = api_host()
    return f"{username}.eu.pythonanywhere.com" if site.startswith("eu.") else f"{username}.pythonanywhere.com"


class PA:
    def __init__(self, username, token, dry=False):
        self.base = f"https://{api_host()}/api/v0/user/{username}"
        self.token, self.dry = token, dry

    def call(self, method, path, data=None, ok=(200, 201, 204)):
        if self.dry:
            print(f"  [dry-run] {method} {path} {data or ''}")
            return {} if method != "GET" else []
        body = urllib.parse.urlencode(data).encode() if data else None
        req = urllib.request.Request(self.base + path, data=body, method=method,
                                     headers={"Authorization": f"Token {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code in ok:
                return {}
            raise SystemExit(f"[API 오류] {method} {path} → HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")


def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k] = v
    return env


def ensure_env(admin_code, dry):
    env = load_env()
    created = []
    if "SEATSYNC_SECRET_KEY" not in env:
        env["SEATSYNC_SECRET_KEY"] = secrets.token_urlsafe(50)
        created.append("SEATSYNC_SECRET_KEY")
    if "SEATSYNC_DEVICE_KEY" not in env:
        env["SEATSYNC_DEVICE_KEY"] = secrets.token_urlsafe(16)
        created.append("SEATSYNC_DEVICE_KEY")
    if admin_code:
        env["SEATSYNC_ADMIN_CODE"] = admin_code
    env.setdefault("SEATSYNC_ADMIN_CODE", "admin")
    env["SEATSYNC_HTTPS"] = "1"
    if not dry:
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write("# SeatSync 비밀 설정 (PythonAnywhere). 바꾼 뒤에는 Web 탭에서 Reload.\n")
            for k, v in env.items():
                f.write(f"{k}={v}\n")
        os.chmod(ENV_FILE, 0o600)
    print(f"  비밀 설정: {ENV_FILE}" + (f" (새로 만듦: {', '.join(created)})" if created else " (기존 값 유지)"))
    return env


def main():
    ap = argparse.ArgumentParser(description="SeatSync → PythonAnywhere 웹 앱 설정")
    ap.add_argument("--admin-code", help="관리자 모드 코드 (지정하지 않으면 기존 값, 없으면 admin)")
    ap.add_argument("--reload-only", action="store_true", help="웹 앱 재시작만")
    ap.add_argument("--dry-run", action="store_true", help="API를 호출하지 않고 할 일만 출력")
    args = ap.parse_args()

    username = os.environ.get("USER") or getpass.getuser()
    token = os.environ.get("API_TOKEN") or ("dry-run" if args.dry_run else getpass.getpass(
        "PythonAnywhere API 토큰 (Account → API token 에서 복사, 입력은 화면에 표시되지 않음): ").strip())
    if not token:
        raise SystemExit("API 토큰이 필요합니다.")
    pa = PA(username, token, args.dry_run)
    domain = web_domain(username)
    print(f"SeatSync 배포 → https://{domain}")

    if args.reload_only:
        pa.call("POST", f"/webapps/{domain}/reload/")
        print("재시작 완료.")
        return 0

    if not os.path.isdir(PROJECT) and not args.dry_run:
        raise SystemExit(f"{PROJECT} 가 없습니다. 먼저 git clone 하세요.")
    env = ensure_env(args.admin_code, args.dry_run)

    apps = pa.call("GET", "/webapps/")
    if not any(a.get("domain_name") == domain for a in apps or []):
        pyver = f"python{sys.version_info.major}{sys.version_info.minor}"
        print(f"  웹 앱 만들기 ({pyver})")
        pa.call("POST", "/webapps/", {"domain_name": domain, "python_version": pyver})
    else:
        print("  웹 앱이 이미 있습니다 — 설정만 갱신")
    pa.call("PATCH", f"/webapps/{domain}/", {"source_directory": PROJECT, "virtualenv_path": VENV, "force_https": "true"})

    wsgi_path = f"/var/www/{domain.replace('.', '_')}_wsgi.py"
    print(f"  WSGI 파일: {wsgi_path}")
    if not args.dry_run:
        shutil.copyfile(os.path.join(PROJECT, "deploy", "pythonanywhere_wsgi.py"), wsgi_path)

    statics = pa.call("GET", f"/webapps/{domain}/static_files/")
    if not any(s.get("url") == "/static/" for s in statics or []):
        pa.call("POST", f"/webapps/{domain}/static_files/", {"url": "/static/", "path": os.path.join(PROJECT, "static")})
    print("  정적 파일: /static/ 연결")

    pa.call("POST", f"/webapps/{domain}/reload/")
    print("\n배포 완료!")
    print(f"  사이트        : https://{domain}")
    print(f"  관리자 코드   : {env['SEATSYNC_ADMIN_CODE']}  (바꾸려면: python deploy/pythonanywhere_deploy.py --admin-code 새코드)")
    print(f"  디바이스 키   : {env['SEATSYNC_DEVICE_KEY']}  (라즈베리파이 pi_bridge.py --key 에 사용)")
    print(f"  Pi 전송 주소  : https://{domain}/api/detections")
    print("  무료 계정은 3개월마다 Web 탭의 'Run until 3 months from today'를 눌러 연장해야 합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
