"""라즈베리파이 감지 프로토타입(seat_monitor, schema_version 1) 연동."""
import json

from conftest import DEVICE_KEY, admin_seat, qr_token, set_state

from seats.models import Camera, JudgmentFeedback, Seat
from seats.timeutil import to_iso


def snapshot(clock, seats, health="ok", ttl=5, observed=None, meaning="person_presence_only"):
    """seat_monitor.status.snapshot()과 같은 형식. seats: {"A01": ("OCCUPIED", 0.91, 12.3)}"""
    t = clock() if observed is None else observed
    from datetime import datetime, timezone
    iso = lambda e: datetime.fromtimestamp(e, timezone.utc).isoformat()  # noqa: E731  (Pi는 UTC로 보낸다)
    return {
        "schema_version": 1, "observed_at": iso(t), "valid_until": iso(t + ttl), "health": health, "meaning": meaning,
        "seats": [{"seat_id": sid, "state": st, "has_person": {"OCCUPIED": True, "EMPTY": False, "UNKNOWN": None}[st],
                   "current_person_confidence": conf, "pending_state": None, "state_duration_seconds": dur}
                  for sid, (st, conf, dur) in seats.items()],
    }


def post(device, body, camera_id="cam1", key=DEVICE_KEY, header=False):
    extra = {"HTTP_X_DEVICE_KEY": key}
    if header:
        extra["HTTP_X_CAMERA_ID"] = camera_id
    else:
        body = {**body, "camera_id": camera_id}
    return device.post("/api/detections", data=json.dumps(body), content_type="application/json", **extra)


def seat(no):
    return Seat.objects.get(no=no)


def test_snapshot_maps_camera_seat_ids(device, clock, admin):
    r = post(device, snapshot(clock, {"A01": ("OCCUPIED", 0.91, 12.3), "A02": ("EMPTY", 0.0, 40),
                                      "Z99": ("OCCUPIED", 0.8, 1)})).json()
    assert r["format"] == "snapshot_v1" and r["accepted"] == 2 and r["ignored"] == ["Z99"]
    assert "A03" in r["missing"]  # 대응표에 있는데 스냅샷에 빠진 좌석
    assert seat(1).state == "occupied" and seat(1).state_since == clock() - 12 and seat(1).cam_confidence == 0.91
    assert seat(2).state == "empty" and seat(2).state_source == "camera"
    assert admin_seat(admin, 1)["detail"] == "unauthorized"   # 예약 없이 사람 → 무단 점유
    cam2 = post(device, snapshot(clock, {"A01": ("OCCUPIED", 0.7, 3)}), camera_id="cam2").json()
    assert cam2["accepted"] == 1 and seat(10).state == "occupied"  # cam2의 A01 = C-1


def test_camera_id_via_header_and_required(device, clock):
    assert post(device, snapshot(clock, {"A01": ("EMPTY", 0, 1)}), header=True).status_code == 200
    body = snapshot(clock, {"A01": ("EMPTY", 0, 1)})
    r = device.post("/api/detections", data=json.dumps(body), content_type="application/json",
                    HTTP_X_DEVICE_KEY=DEVICE_KEY)
    assert r.status_code == 400
    assert post(device, {**body, "schema_version": 2}).json()["error"]["code"] == "UNSUPPORTED_SCHEMA"
    assert post(device, body, key="nope").status_code == 403


def test_unknown_keeps_last_state(device, clock):
    post(device, snapshot(clock, {"A01": ("OCCUPIED", 0.9, 5)}))
    clock.advance(3)
    r = post(device, snapshot(clock, {"A01": ("UNKNOWN", 0.0, 0)})).json()
    assert r["unknown"] == ["A01"]
    s = seat(1)
    assert s.state == "occupied" and s.cam_state == "UNKNOWN" and s.cam_unknown_since == clock()


def test_health_not_ok_means_unknown(device, clock):
    post(device, snapshot(clock, {"A01": ("EMPTY", 0, 5)}))
    r = post(device, snapshot(clock, {"A01": ("OCCUPIED", 0.9, 5)}, health="inference_too_slow")).json()
    assert r["fresh"] is False and seat(1).state == "empty" and seat(1).cam_state == "UNKNOWN"
    assert Camera.objects.get(camera_id="cam1").health == "inference_too_slow"


def test_stale_camera_freezes_away_escalation(device, clock, user, admin):
    """카메라가 끊긴 동안에는 이탈로 넘기지 않는다(감지 끊김 ≠ 자리 비움)."""
    user.jpost("/api/reservations", {"seat_no": 4, "qr_token": qr_token(4)})
    clock.advance(5)
    post(device, snapshot(clock, {"A04": ("EMPTY", 0, 0)}))   # 자리 비움 시작
    clock.advance(10)                                           # valid_until(5초) 경과 → 감지 끊김
    s = admin_seat(admin, 4)
    assert s["detail"] == "away_short" and s["stale"] is True and s["deadline"] is None
    clock.advance(40 * 60)
    admin.get("/api/admin/seats")
    clock.advance(10)
    s = admin_seat(admin, 4)
    assert s["detail"] == "away_short" and not s["needs_action"]   # 40분이 지나도 이탈 아님
    post(device, snapshot(clock, {"A04": ("EMPTY", 0, 45 * 60)}))  # 다시 연결: 45분째 비어 있음
    s = admin_seat(admin, 4)
    assert s["stale"] is False and s["detail"] == "away"


def test_escalated_before_outage_stays(device, clock, user, admin):
    user.jpost("/api/reservations", {"seat_no": 4, "qr_token": qr_token(4)})
    clock.advance(5)
    post(device, snapshot(clock, {"A04": ("EMPTY", 0, 0)}))
    for _ in range(7):  # 5분마다 heartbeat → 35분 동안 계속 비어 있음
        clock.advance(300)
        post(device, snapshot(clock, {"A04": ("EMPTY", 0, 0)}))
    assert admin_seat(admin, 4)["detail"] == "away"
    clock.advance(60)  # 이후 카메라 끊김
    s = admin_seat(admin, 4)
    assert s["stale"] is True and s["detail"] == "away"


def test_person_only_empty_keeps_item(device, clock, user, admin):
    """사람만 보는 카메라의 EMPTY는 짐이 있을 수 있으므로 관리자가 지정한 '짐만 있음'을 유지."""
    user.jpost("/api/reservations", {"seat_no": 2, "qr_token": qr_token(2)})
    set_state(admin, 2, "item")
    post(device, snapshot(clock, {"A02": ("EMPTY", 0, 60)}))
    assert seat(2).state == "item"
    post(device, snapshot(clock, {"A02": ("OCCUPIED", 0.9, 1)}))  # 사람이 돌아오면 사용중
    assert seat(2).state == "occupied"
    post(device, snapshot(clock, {"A02": ("EMPTY", 0, 1)}, meaning="person_and_item"))  # 짐까지 보는 카메라라면 비움
    assert seat(2).state == "empty"


def test_unavailable_not_overwritten(device, clock):
    post(device, snapshot(clock, {"A11": ("OCCUPIED", 0.9, 5)}), camera_id="cam2")  # cam2 A11 = E-3 (고장)
    assert seat(20).state == "unavailable" and seat(20).cam_state == "OCCUPIED"


def _iso_utc(e):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(e, timezone.utc).isoformat()


def test_clock_skew_corrected_with_sent_at(device, clock):
    pi = clock() - 100  # Pi 시계가 100초 느림: 브리지의 sent_at으로 보정
    body = {**snapshot(clock, {"A01": ("OCCUPIED", 0.9, 10)}, observed=pi), "sent_at": _iso_utc(pi)}
    r = post(device, body).json()
    assert r["clock_offset"] == 100 and r["fresh"] is True and seat(1).state_since == clock() - 10
    assert seat(1).cam_valid_until == clock() + 5


def test_stale_snapshot_not_mistaken_for_clock_skew(device, clock):
    """감지기가 멈춘 뒤 브리지가 오래된 스냅샷을 계속 보내도 새것으로 보지 않는다."""
    post(device, snapshot(clock, {"A01": ("OCCUPIED", 0.9, 10)}))
    old = snapshot(clock, {"A01": ("OCCUPIED", 0.9, 10)})
    clock.advance(30)
    r = post(device, {**old, "sent_at": _iso_utc(clock())}).json()   # 시계는 정상, 스냅샷만 30초 묵음
    assert r["clock_offset"] == 0 and r["fresh"] is False and seat(1).cam_state == "UNKNOWN"
    r = post(device, old).json()                                     # sent_at 없이 보내도 마찬가지
    assert r["fresh"] is False


def test_device_config_mapping(device):
    r = device.get("/api/device/config?camera_id=cam2", HTTP_X_DEVICE_KEY=DEVICE_KEY).json()
    assert [s["camera_seat"] for s in r["seats"]][:3] == ["A01", "A02", "A03"]
    assert r["seats"][0]["label"] == "C-1" and len(r["seats"]) == 11 and r["post_interval_sec"] == 2
    assert device.get("/api/device/config").status_code == 403


def test_admin_camera_panel_and_feedback_confidence(device, clock, admin):
    post(device, snapshot(clock, {"A01": ("OCCUPIED", 0.42, 5)}))
    cams = {c["camera_id"]: c for c in admin.jget("/api/admin/cameras")["cameras"]}
    assert cams["cam1"]["fresh"] is True and cams["cam1"]["meaning"] == "person_presence_only"
    assert cams["cam2"]["connected"] is False and len(cams["cam2"]["seats"]) == 11
    assert admin_seat(admin, 1)["camera"]["camera_seat"] == "A01"
    admin.jpost("/api/admin/seats/1/feedback", {"verdict": "wrong", "correct_detail": "empty"})
    f = JudgmentFeedback.objects.get()
    assert f.source == "camera" and f.cam_state == "OCCUPIED" and f.cam_confidence == 0.42
    assert admin.jget("/api/admin/feedback")["camera_confidence"]["wrong"] == 0.42


def test_bridge_payload_roundtrip(device, clock, tmp_path, monkeypatch):
    """tools/pi_bridge.py가 만든 요청을 실제 API가 받아들이는지 (네트워크 대신 테스트 클라이언트로 연결)."""
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "pi_bridge", pathlib.Path(__file__).resolve().parent.parent / "tools" / "pi_bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    sent = {}

    def fake_req(self, method, path, body=None):
        r = device.generic(method, path, data=json.dumps(body) if body is not None else "",
                           content_type="application/json", HTTP_X_DEVICE_KEY=self.key, HTTP_X_CAMERA_ID=self.camera_id)
        sent[path] = r.status_code
        return r.json()
    monkeypatch.setattr(bridge.Web, "_req", fake_req)
    from datetime import datetime as real_dt

    class FakeDT:  # 브리지의 sent_at도 테스트 시계를 쓰게 한다
        @staticmethod
        def now(tz=None):
            return real_dt.fromtimestamp(clock(), tz)
    monkeypatch.setattr(bridge, "datetime", FakeDT)
    web = bridge.Web("http://seatsync", DEVICE_KEY, "cam1")
    cfg_file = tmp_path / "seats.json"
    cfg_file.write_text(json.dumps({"seats": [{"id": "A01"}, {"id": "A02"}, {"id": "X9"}]}))
    cfg = bridge.check(web, str(cfg_file))
    assert len(cfg["seats"]) == 9
    res = web.send(snapshot(clock, {"A01": ("OCCUPIED", 0.8, 2)}))
    assert res["accepted"] == 1 and seat(1).state == "occupied"
    assert "상태" not in bridge.summarize({"health": "ok", "seats": []}, res)
