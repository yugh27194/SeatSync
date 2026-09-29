#!/usr/bin/env python3
"""라즈베리파이 감지 프로토타입(seat_monitor) → SeatSync 웹 브리지.

seat_monitor는 좌석 상태를 output/status.json에만 쓰고 네트워크로 보내지 않는다.
이 스크립트가 그 파일(또는 표준 출력의 JSON 줄)을 읽어 camera_id를 붙인 뒤 웹의 POST /api/detections로 보낸다.
표준 라이브러리만 사용하므로 Pi에 추가 설치가 필요 없다. 영상·이미지는 다루지 않는다.

    # 1) 파일 감시 (seat_monitor를 따로 실행 중일 때)
    python3 pi_bridge.py --url https://아이디.pythonanywhere.com --key <DEVICE_KEY> --camera-id cam1 \
        --status-file ~/SKKU_MakerHackerton/output/status.json --seats-config ~/SKKU_MakerHackerton/config/seats.json

    # 2) 파이프 (seat_monitor 출력을 바로 연결)
    python -m seat_monitor run | python3 pi_bridge.py --stdin --url ... --key ... --camera-id cam1

    # 연결 확인만: 웹의 좌석 대응표와 Pi의 좌석 설정 비교
    python3 pi_bridge.py --check --url ... --key ... --camera-id cam1 --seats-config config/seats.json
"""
import argparse
import json
import os
import select
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

MIN_GAP = 0.5          # 상태가 바뀌어도 이 간격(초)보다 자주 보내지 않는다
MAX_BACKOFF = 30.0     # 전송 실패 시 재시도 간격 상한


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Web:
    def __init__(self, url, key, camera_id, timeout=5):
        self.url, self.key, self.camera_id, self.timeout = url.rstrip("/"), key, camera_id, timeout

    def _req(self, method, path, body=None):
        headers = {"X-Device-Key": self.key, "X-Camera-Id": self.camera_id, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None

    def config(self):
        return self._req("GET", f"/api/device/config?camera_id={urllib.request.quote(self.camera_id)}")

    def send(self, snapshot):
        # sent_at: Pi 시계 기준 전송 시각. 웹은 이것으로 시계 차이만 보정하고, 스냅샷이 오래됐는지는 valid_until로 판단한다.
        sent_at = datetime.now(timezone.utc).isoformat()
        return self._req("POST", "/api/detections", {**snapshot, "camera_id": self.camera_id, "sent_at": sent_at})


def pi_seat_ids(path):
    if not path:
        return None
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        return [s["id"] for s in json.load(f).get("seats", [])]


def check(web, seats_config):
    cfg = web.config()
    mapping = {s["camera_seat"]: s for s in cfg["seats"]}
    log(f"웹 연결 OK · {web.camera_id} 대응 좌석 {len(mapping)}개 · 서버 시각 {cfg['server_time']}")
    for sid, s in sorted(mapping.items()):
        print(f"    {sid} → {s['label']} ({s['zone']})")
    ids = pi_seat_ids(seats_config)
    if ids is not None:
        unmapped = [i for i in ids if i not in mapping]
        missing = [i for i in mapping if i not in ids]
        if unmapped:
            log(f"[주의] Pi에는 있지만 웹 대응표에 없는 좌석(무시됨): {', '.join(unmapped)}")
        if missing:
            log(f"[주의] 웹 대응표에는 있지만 Pi 설정에 없는 좌석(확인 불가로 표시됨): {', '.join(missing)}")
        if not unmapped and not missing:
            log("Pi 좌석 설정과 웹 대응표가 일치합니다.")
    return cfg


def read_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        return None  # 쓰는 중이면 다음 주기에 다시 읽는다 (seat_monitor는 원자적으로 교체하므로 드묾)


def summarize(snap, res):
    states = {}
    for s in snap.get("seats", []):
        states[s.get("state")] = states.get(s.get("state"), 0) + 1
    extra = ""
    if res.get("ignored"):
        extra += f" · 대응표에 없음 {res['ignored']}"
    if res.get("missing"):
        extra += f" · 누락 {res['missing']}"
    return (f"전송 OK · health={snap.get('health')} · "
            + ", ".join(f"{k} {v}" for k, v in sorted(states.items(), key=lambda x: str(x[0]))) + extra)


def run(web, args):
    interval = args.interval
    path = os.path.expanduser(args.status_file) if not args.stdin else None
    last_sent, last_key, last_snapshot, backoff, next_try = 0.0, None, None, 1.0, 0.0
    last_summary, waiting_logged = None, False
    while True:
        snap = None
        if args.stdin:
            ready, _, _ = select.select([sys.stdin], [], [], 0.25) if hasattr(select, "select") else ([sys.stdin], [], [])
            if ready:
                line = sys.stdin.readline()
                if not line:
                    log("입력이 끝났습니다(seat_monitor 종료).")
                    return 0
                try:
                    snap = json.loads(line)
                except json.JSONDecodeError:
                    snap = None
            snap = snap or last_snapshot
        else:
            snap = read_file(path)
            if snap is None:
                if not waiting_logged:
                    log(f"상태 파일을 기다리는 중: {path} (seat_monitor run 실행 여부 확인)")
                    waiting_logged = True
                time.sleep(0.5)
                continue
            waiting_logged = False
            time.sleep(0.25)
        if snap is None:
            continue
        last_snapshot = snap
        key = (snap.get("observed_at"), snap.get("health"),
               tuple((s.get("seat_id"), s.get("state")) for s in snap.get("seats", [])))
        now = time.monotonic()
        changed = key[1:] != (last_key[1:] if last_key else None)
        due = now - last_sent >= interval or (changed and now - last_sent >= MIN_GAP)
        if not due or now < next_try:
            continue
        try:
            res = web.send(snap)
            last_sent, last_key, backoff = now, key, 1.0
            summary = summarize(snap, res)
            if summary != last_summary or args.verbose:
                log(summary)
                last_summary = summary
        except Exception as e:  # 네트워크 끊김 등: 최신 스냅샷만 의미가 있으므로 쌓아 두지 않고 재시도
            log(f"[전송 실패] {e} · {backoff:.0f}초 후 재시도")
            next_try = now + backoff
            backoff = min(backoff * 2, MAX_BACKOFF)
        if args.once:
            return 0


def main():
    ap = argparse.ArgumentParser(description="seat_monitor(status.json) → SeatSync 웹 브리지")
    ap.add_argument("--url", required=True, help="SeatSync 주소 (예: https://아이디.pythonanywhere.com)")
    ap.add_argument("--key", default=os.environ.get("SEATSYNC_DEVICE_KEY", "dev-key"), help="디바이스 키")
    ap.add_argument("--camera-id", required=True, help="웹 seats.json의 camera_id (예: cam1)")
    ap.add_argument("--status-file", default="output/status.json", help="seat_monitor 상태 파일 경로")
    ap.add_argument("--stdin", action="store_true", help="파일 대신 표준 입력(JSON 줄)을 읽는다")
    ap.add_argument("--seats-config", help="seat_monitor의 config/seats.json (좌석 ID 대조용)")
    ap.add_argument("--interval", type=float, default=2.0, help="변화가 없어도 보내는 주기(초, heartbeat)")
    ap.add_argument("--check", action="store_true", help="웹 연결·좌석 대응만 확인하고 종료")
    ap.add_argument("--once", action="store_true", help="한 번만 보내고 종료")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    web = Web(args.url, args.key, args.camera_id)
    try:
        cfg = check(web, args.seats_config)
    except Exception as e:
        log(f"[연결 실패] {e}")
        if args.check:
            return 1
        cfg = None
    if args.check:
        return 0
    if cfg and cfg.get("post_interval_sec") and args.interval == 2.0:
        args.interval = float(cfg["post_interval_sec"])
    log(f"전송 시작 → {web.url}/api/detections (camera_id={web.camera_id}, 주기 {args.interval:g}초). 끄려면 Ctrl+C")
    try:
        return run(web, args)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
