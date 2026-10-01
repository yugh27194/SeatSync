"""짐 감지(카메라 items_run의 has_item) + 관리자 [짐 감지] on/off + 지도 노란 점."""
from conftest import admin_seat, set_state
from test_camera import post, seat, snapshot

from seats.models import AdminLog


def items_snapshot(clock, seats, **kw):
    """items_run 형식: 사람 state에 has_item·item_state·presence_state가 붙는다. seats: {"A01": ("EMPTY", True)}"""
    body = snapshot(clock, {sid: (st, 0.9 if st == "OCCUPIED" else 0.0, 30) for sid, (st, _i) in seats.items()}, **kw)
    body["item_meaning"] = "selected_personal_items"
    body["item_classes"] = ["backpack", "book", "cup", "handbag", "laptop", "suitcase"]
    for row in body["seats"]:
        has_item = seats[row["seat_id"]][1]
        row.update(has_item=has_item, item_state={True: "OCCUPIED", False: "EMPTY", None: "UNKNOWN"}[has_item],
                   current_item_confidence=0.6 if has_item else 0.0, item_pending_state=None)
    return body


def toggle(admin, enabled):
    return admin.jput("/api/admin/item-detection", {"enabled": enabled})


def user_row(user, no):
    return next(s for s in user.jget("/api/seats")["seats"] if s["no"] == no)


def test_toggle_api_default_off_and_admin_only(admin, user, device, clock):
    d = admin.jget("/api/admin/item-detection")
    assert d == {"enabled": False, "receiving": False, "cameras": []}
    assert user.jput("/api/admin/item-detection", {"enabled": True}).status_code == 403
    assert toggle(admin, "yes").status_code == 400
    post(device, items_snapshot(clock, {"A01": ("EMPTY", False)}))
    r = toggle(admin, True).json()
    assert r == {"enabled": True, "receiving": True, "cameras": ["cam1"]}
    assert AdminLog.objects.filter(action="item_detection", memo="짐 감지 켬").exists()


def test_off_ignores_items(admin, user, device, clock):
    post(device, items_snapshot(clock, {"A01": ("EMPTY", True), "A02": ("OCCUPIED", True)}))
    assert seat(1).state == "empty" and seat(1).cam_item is True  # 받기는 하지만 판정에 쓰지 않음
    assert admin_seat(admin, 1)["has_item"] is False and "item" not in user_row(user, 1)


def test_on_items_only_and_person_with_items(admin, user, device, clock):
    toggle(admin, True)
    post(device, items_snapshot(clock, {"A01": ("EMPTY", True), "A02": ("OCCUPIED", True), "A03": ("EMPTY", False)}))
    assert seat(1).state == "item" and seat(1).state_since == clock()  # 짐을 놓은 때부터 센다
    assert seat(2).state == "occupied" and seat(3).state == "empty"
    a1, a2, a3 = (admin_seat(admin, n) for n in (1, 2, 3))
    assert a1["detail"] == "item" and a1["has_item"] and a1["camera"]["item"] is True
    assert a2["detail"] == "detected" and a2["has_item"]  # 사람 + 짐: 정상 분류 그대로, 노란 점만
    assert not a3["has_item"]
    # 짐 유무는 관리자 지도에서만 — 이용자 좌석 지도 API에는 싣지 않는다
    assert all("item" not in user_row(user, n) for n in (1, 2, 3))
    assert "item_detection" not in user.jget("/api/seats")


def test_checked_in_person_leaves_bag_then_hoarding(admin, user, device, clock):
    toggle(admin, True)
    r = user.jpost("/api/reservations", {"seat_no": 1})
    assert r.status_code == 201
    from conftest import qr_token
    from seats.models import Reservation
    rid = Reservation.objects.get(seat_id=1, status="reserved").id
    assert user.jpost(f"/api/reservations/{rid}/checkin", {"qr_token": qr_token(1)}).status_code == 200
    post(device, items_snapshot(clock, {"A01": ("OCCUPIED", True)}))
    clock.advance(60)
    post(device, items_snapshot(clock, {"A01": ("EMPTY", True)}))  # 사람만 나감, 노트북은 남음
    assert admin_seat(admin, 1)["detail"] == "item"  # 짐만 있음 → 일시 이석
    clock.advance(31 * 60)
    post(device, items_snapshot(clock, {"A01": ("EMPTY", True)}))
    assert admin_seat(admin, 1)["detail"] == "hoarding"  # 사석화 기준(30분) 넘김
    post(device, items_snapshot(clock, {"A01": ("EMPTY", False)}))  # 짐을 치움 → 이석 시간은 이어서
    assert admin_seat(admin, 1)["detail"] == "away"


def test_unknown_item_keeps_last(admin, device, clock):
    toggle(admin, True)
    post(device, items_snapshot(clock, {"A01": ("EMPTY", True)}))
    post(device, items_snapshot(clock, {"A01": ("EMPTY", None)}))  # 짐 확인 불가 → 짐만 있음 유지
    assert seat(1).state == "item"
    post(device, items_snapshot(clock, {"A01": ("OCCUPIED", True)}, health="error"))  # 카메라 오류
    assert seat(1).state == "item" and seat(1).cam_item is None


def test_turning_off_clears_camera_items_only(admin, device, clock):
    toggle(admin, True)
    post(device, items_snapshot(clock, {"A01": ("EMPTY", True)}))
    set_state(admin, 5, "item")  # 관리자가 직접 지정한 짐만 있음
    since = seat(1).state_since
    toggle(admin, False)
    assert seat(1).state == "empty" and seat(1).state_since == since
    assert seat(5).state == "item"
    assert admin_seat(admin, 5)["has_item"] is False  # 꺼져 있으면 점은 찍지 않는다


def test_dot_depends_only_on_item_not_person(admin, device, clock):
    """노란 점은 사람 유무와 상관없이 짐 유무만으로 정한다."""
    toggle(admin, True)
    post(device, items_snapshot(clock, {"A01": ("OCCUPIED", True), "A02": ("EMPTY", True),
                                        "A03": ("OCCUPIED", False), "A04": ("EMPTY", False),
                                        "A05": ("UNKNOWN", True)}))  # 사람 확정 전이어도 짐이 확인되면 점
    dots = {n: admin_seat(admin, n)["has_item"] for n in (1, 2, 3, 4, 5)}
    assert dots == {1: True, 2: True, 3: False, 4: False, 5: True}
    set_state(admin, 6, "item")  # 짐 정보가 없는 좌석: 관리자가 지정한 '짐만 있음'을 따름
    assert admin_seat(admin, 6)["has_item"] is True
    clock.advance(60)  # 카메라 끊김 → 짐 판정이 만료되면 마지막 상태만 따름
    assert admin_seat(admin, 1)["has_item"] is False and admin_seat(admin, 2)["has_item"] is True
