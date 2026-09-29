#!/usr/bin/env python3
"""가짜 감지 전송기 (개발용 — 시연에 쓰지 말 것).

주의: 카메라처럼 모든 좌석의 현장 상태를 덮어쓴다(관리자가 '사용불가'로 지정한 좌석 제외).
관리자 화면에서 현장 상태를 직접 배분해 쓰는 동안에는 실행하지 말 것.

    python tools/simulate.py --url http://localhost:5000 --key dev-key     # 대화형
      > set 3 person        # 3번 좌석 person (since=지금)
      > set 5 item -40m     # 5번 좌석 item, since=40분 전 (-90s, -2h 도 가능)
      > all empty
      > show
      > quit
    python tools/simulate.py --scenario demo    # 정해진 시나리오 자동 재생

백그라운드 스레드가 현재 상태 전체를 5초마다 /api/detections로 보낸다(heartbeat 흉내).
"""
import argparse
import http.cookiejar
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HEARTBEAT_SEC = 5
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def iso(epoch):
    return datetime.fromtimestamp(epoch, KST).isoformat(timespec="seconds")


def parse_offset(s):
    """'-40m' → -2400, '-90s' → -90, '-2h' → -7200"""
    m = re.fullmatch(r"-?(\d+)([smh]?)", s)
    if not m:
        raise ValueError(f"시간 형식 오류: {s} (예: -40m, -90s, -2h)")
    n = int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]
    return -n


class Simulator:
    def __init__(self, url, key, camera_id="cam1", seats=None, verbose=False):
        self.url = url.rstrip("/")
        self.key = key
        self.camera_id = camera_id
        self.verbose = verbose
        self.state = {}  # seat_no -> [occupancy, since]
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        for no in seats or []:
            self.state[no] = ["empty", int(time.time())]

    def set(self, no, occ, offset=0):
        with self.lock:
            prev = self.state.get(no)
            since = int(time.time()) + offset
            # 같은 상태를 다시 set하고 offset이 없으면 since 유지
            if prev and prev[0] == occ and offset == 0:
                since = prev[1]
            self.state[no] = [occ, since]
        self.send()

    def set_all(self, occ):
        now = int(time.time())
        with self.lock:
            for no in self.state:
                if self.state[no][0] != occ:
                    self.state[no] = [occ, now]
        self.send()

    def payload(self):
        with self.lock:
            return {
                "camera_id": self.camera_id,
                "ts": iso(time.time()),
                "seats": [{"seat_no": no, "occupancy": o, "since": iso(s), "confidence": 0.9}
                          for no, (o, s) in sorted(self.state.items())],
            }

    def send(self):
        body = json.dumps(self.payload()).encode()
        req = urllib.request.Request(self.url + "/api/detections", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Device-Key": self.key})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
                if self.verbose:
                    print(f"[전송] accepted={data['accepted']} ignored={data['ignored']}")
                return data
        except urllib.error.HTTPError as e:
            print(f"[전송 실패] HTTP {e.code}: {e.read().decode(errors='replace')}")
        except urllib.error.URLError as e:
            print(f"[전송 실패] {e.reason}")

    def heartbeat(self):
        while not self.stop_evt.wait(HEARTBEAT_SEC):
            self.send()

    def start(self):
        threading.Thread(target=self.heartbeat, daemon=True).start()
        self.send()

    def show(self):
        now = int(time.time())
        with self.lock:
            for no, (o, s) in sorted(self.state.items()):
                print(f"  {no:>3}번  {o:<6}  {(now - s) // 60}분 {(now - s) % 60}초 전부터")


class UserClient:
    """테스트 계정으로 로그인해 예약 API를 호출한다."""

    def __init__(self, url, student_no, password="1234"):
        self.url = url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        data = urllib.parse.urlencode({"student_no": student_no, "password": password}).encode()
        self.opener.open(self.url + "/login", data=data, timeout=5)

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.url + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=5) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            print(f"  [API {e.code}] {e.read().decode(errors='replace')}")
            return None

    def clear(self):
        mine = (self.call("GET", "/api/seats") or {}).get("my_reservation")
        if mine:
            self.call("POST", f"/api/reservations/{mine['id']}/return")


def load_tokens(db_path):
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT no, qr_token FROM seats").fetchall()
        conn.close()
        return dict(rows)
    except sqlite3.Error:
        return {}


def load_seat_nos():
    try:
        with open(os.path.join(ROOT, "config", "seats.json"), encoding="utf-8") as f:
            return [s["no"] for s in json.load(f)["seats"]]
    except (OSError, KeyError, ValueError):
        return list(range(1, 9))


def run_demo(sim, url, db_path, step_sec):
    tokens = load_tokens(db_path)
    if not tokens:
        print(f"DB({db_path})에서 좌석 QR 토큰을 읽지 못했습니다. --db 경로를 확인하세요.")
        return

    def say(msg):
        print(f"\n▶ {msg}")

    def pause():
        time.sleep(step_sec)

    users = [UserClient(url, f"2026000{i}") for i in range(1, 5)]
    for u in users:
        u.clear()
    sim.set_all("empty")
    say("모든 좌석 비움. (시연 전 관리자 설정에서 [시연 모드] 적용을 권장)")
    pause()

    say("1번: 테스트1이 착석 후 좌석 QR로 바로 예약 → 정상 이용")
    sim.set(1, "person")
    users[0].call("POST", "/api/reservations", {"seat_no": 1, "qr_token": tokens[1]})
    pause()

    say("2번: 테스트2 착석·QR 예약 후 자리를 비움 → (자리 비움 허용 시간 경과 후) 장시간 자리 비움")
    sim.set(2, "person")
    users[1].call("POST", "/api/reservations", {"seat_no": 2, "qr_token": tokens[2]})
    pause()
    sim.set(2, "empty")
    pause()

    say("3번: 예약 없이 착석 → 미예약 사용")
    sim.set(3, "person")
    pause()

    say("4번: 테스트4가 지도에서 예약만 하고 입실하지 않음 → (체크인 제한 경과 후) 미입실")
    users[3].call("POST", "/api/reservations", {"seat_no": 4})
    pause()

    say("시나리오 배치 완료. heartbeat를 계속 보냅니다. 종료: Ctrl+C")
    sim.show()
    while True:
        time.sleep(3600)


def interactive(sim):
    print("명령: set <좌석> <person|item|empty> [-40m]  |  all <상태>  |  show  |  quit")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()
        try:
            if cmd in ("quit", "exit", "q"):
                return
            elif cmd == "show":
                sim.show()
            elif cmd == "all" and len(parts) == 2:
                if parts[1] not in ("person", "item", "empty"):
                    raise ValueError("상태는 person|item|empty")
                sim.set_all(parts[1])
            elif cmd == "set" and len(parts) in (3, 4):
                if parts[2] not in ("person", "item", "empty"):
                    raise ValueError("상태는 person|item|empty")
                sim.set(int(parts[1]), parts[2], parse_offset(parts[3]) if len(parts) == 4 else 0)
            else:
                print("알 수 없는 명령입니다.")
        except ValueError as e:
            print(f"오류: {e}")


def main():
    ap = argparse.ArgumentParser(description="SeatSync 가짜 감지 전송기 (개발용)")
    ap.add_argument("--url", default="http://localhost:5000")
    ap.add_argument("--key", default=os.environ.get("SEATSYNC_DEVICE_KEY", "dev-key"))
    ap.add_argument("--camera", default="cam1")
    ap.add_argument("--scenario", choices=["demo"])
    ap.add_argument("--db", default=os.environ.get("SEATSYNC_DB", os.path.join(ROOT, "seatsync.db")),
                    help="시나리오에서 좌석 QR 토큰을 읽을 DB 경로")
    ap.add_argument("--step", type=float, default=3.0, help="시나리오 단계 간격(초)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    sim = Simulator(args.url, args.key, args.camera, load_seat_nos(), args.verbose)
    sim.start()
    try:
        if args.scenario == "demo":
            run_demo(sim, args.url, args.db, args.step)
        else:
            interactive(sim)
    except KeyboardInterrupt:
        pass
    finally:
        sim.stop_evt.set()


if __name__ == "__main__":
    sys.exit(main())
