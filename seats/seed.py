"""좌석(seats.json)·설정 기본값·테스트 계정 seed."""
import json
import secrets

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import transaction

from . import clock
from .models import ACTIVE, Alert, Reservation, Seat, Setting, User, WaitEntry
from .status import ASSIGNABLE, DEFAULT_SETTINGS

# (학번/아이디, 이름, 비밀번호). 시나리오용 이용자 userA·B·C와 관리자용 계정 manager.
# manager도 관리자 탭에 들어갈 때 관리자 코드를 한 번 입력한다(계정만으로 관리자 권한이 생기지 않는다).
SEED_ACCOUNTS = (
    [(f"user{c}", f"사용자{c}", "1234") for c in "ABC"]
    + [("manager", "관리자", "1234")]
    + [(f"2026000{i}", f"테스트{i}", "1234") for i in range(1, 6)]
)


def load_layout(path=None):
    with open(path or settings.SEATSYNC["SEATS_FILE"], encoding="utf-8") as f:
        data = json.load(f)
    return {"grid": data.get("grid", {"cols": 1, "rows": 1}), "seats": data.get("seats", []),
            "fixtures": data.get("fixtures", []), "zones": data.get("zones", {}), "cameras": data.get("cameras", {}),
            "demo": data.get("demo", [])}


def seed(now=None, seats_file=None):
    now = clock.now() if now is None else now
    layout = load_layout(seats_file)
    with transaction.atomic():
        for k, v in DEFAULT_SETTINGS.items():
            Setting.objects.get_or_create(key=k, defaults={"value": str(v)})

        # 좌석: upsert. qr_token과 현장 상태는 새 좌석일 때만 seats.json 값으로 초기화(이후 유지)
        nos = []
        for st in layout["seats"]:
            nos.append(int(st["no"]))
            common = {"label": st["label"], "x": st["x"], "y": st["y"], "zone": st.get("zone", ""),
                      "camera_id": st.get("camera_id", ""), "camera_seat": st.get("camera_seat", ""), "active": True}
            seat = Seat.objects.filter(no=st["no"]).first()
            if seat:
                for k, v in common.items():
                    setattr(seat, k, v)
                seat.save()
                continue
            detail = st.get("state", "empty")
            if detail not in ASSIGNABLE or ASSIGNABLE[detail][3]:
                raise ValueError(f"seats.json 좌석 {st['no']}: state는 {', '.join(k for k, v in ASSIGNABLE.items() if not v[3])} 중 하나")
            state, mark, reason, _ = ASSIGNABLE[detail]
            Seat.objects.create(no=st["no"], qr_token=secrets.token_urlsafe(8), state=state, mark=mark, reason=reason,
                                note=st.get("note"), state_since=now, state_source="seed", **common)
        # seats.json에서 빠진 좌석은 비활성 (이력 보존을 위해 삭제하지 않음).
        # 그 좌석의 진행 중 예약·빈자리 안내·미해결 알림은 정리한다 — 지도에서 사라진 좌석에 묶여 있지 않게.
        gone = list(Seat.objects.filter(active=True).exclude(no__in=nos).values_list("no", flat=True))
        if gone:
            Reservation.objects.filter(seat_id__in=gone, status__in=ACTIVE).update(status="cancelled", ended_at=now)
            WaitEntry.objects.filter(offered_seat_id__in=gone, status="offered").update(
                status="waiting", offered_seat=None, offered_at=None, expires_at=None)
            Alert.objects.filter(seat_id__in=gone, resolved_at__isnull=True).update(resolved_at=now, resolution="seat_removed")
        Seat.objects.exclude(no__in=nos).update(active=False)
        # 없어진 구역으로 대기 중인 빈자리 알림은 취소 (다시 신청하도록)
        zones = {st.get("zone", "") for st in layout["seats"]}
        WaitEntry.objects.filter(status__in=("waiting", "offered")).exclude(zone="").exclude(zone__in=zones).update(
            status="cancelled", ended_at=now)

        for student_no, name, pw in SEED_ACCOUNTS:
            if not User.objects.filter(student_no=student_no).exists():
                User.objects.create(student_no=student_no, name=name, password=make_password(pw), created_at=now)
