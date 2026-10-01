"""SeatSync 데이터 모델. 시각은 모두 UTC epoch 초(int)로 저장한다."""
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models import Q

from . import clock

ACTIVE = ("reserved", "in_use")


class UserManager(BaseUserManager):
    def create_user(self, student_no, name, password=None, **extra):
        user = self.model(student_no=student_no, name=name, created_at=clock.now(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    """이용자. 관리자 계정은 따로 없다 — 관리자 권한은 관리자 코드로 켠다."""
    student_no = models.CharField("학번(아이디)", max_length=32, unique=True)
    name = models.CharField("이름", max_length=50)
    warnings = models.PositiveIntegerField("누적 경고", default=0)
    suspended_until = models.BigIntegerField("이용 정지 종료", null=True, blank=True)
    created_at = models.BigIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    USERNAME_FIELD = "student_no"
    REQUIRED_FIELDS = ["name"]
    objects = UserManager()

    class Meta:
        ordering = ["student_no"]

    def __str__(self):
        return f"{self.name}({self.student_no})"

    def suspended(self, now):
        """정지 중이면 종료 시각, 아니면 None."""
        return self.suspended_until if self.suspended_until and self.suspended_until > now else None


class Seat(models.Model):
    STATE_CHOICES = [("empty", "비어 있음"), ("occupied", "사람 있음"), ("item", "짐만 있음"), ("unavailable", "사용불가")]

    no = models.IntegerField(primary_key=True)  # Pi 매핑과 공유하는 유일한 키
    label = models.CharField(max_length=20)
    x = models.IntegerField()
    y = models.IntegerField()
    zone = models.CharField(max_length=50, blank=True, default="")
    camera_id = models.CharField(max_length=50, blank=True, default="")
    qr_token = models.CharField(max_length=32)  # 좌석 QR에 들어가는 난수 (원격 체크인 방지)
    active = models.BooleanField(default=True)
    # 현장 상태: 카메라 연동 전까지 관리자가 임시로 부여한다
    state = models.CharField(max_length=12, choices=STATE_CHOICES, default="empty")
    mark = models.CharField(max_length=5, null=True, blank=True)     # None | ok | issue (관리자 지정 의도)
    reason = models.CharField(max_length=12, null=True, blank=True)  # 사용불가 사유: broken | maintenance | blocked
    note = models.CharField(max_length=200, null=True, blank=True)
    state_since = models.BigIntegerField(default=0)
    state_source = models.CharField(max_length=10, default="manual")  # manual | camera | checkin | return | seed
    # 카메라(라즈베리파이 감지 프로토타입) 연동: 이 좌석이 카메라에서 불리는 이름과 마지막 감지 정보
    camera_seat = models.CharField(max_length=20, blank=True, default="")  # 예: "A01" (calibrate 순서로 붙는 ID)
    cam_state = models.CharField(max_length=10, null=True, blank=True)       # OCCUPIED | EMPTY | UNKNOWN
    cam_confidence = models.FloatField(null=True, blank=True)                # 현재 프레임 사람 탐지 점수
    cam_seen_at = models.BigIntegerField(null=True, blank=True)              # 서버가 마지막으로 받은 시각
    cam_valid_until = models.BigIntegerField(null=True, blank=True)          # 이 시각이 지나면 감지 확인 불가
    cam_unknown_since = models.BigIntegerField(null=True, blank=True)        # UNKNOWN(확인 불가) 시작 시각

    class Meta:
        ordering = ["no"]


class Camera(models.Model):
    """라즈베리파이 카메라 1대의 연결 상태 (감지 프로토타입의 status.json 스냅샷 기준)."""
    camera_id = models.CharField(max_length=50, primary_key=True)
    health = models.CharField(max_length=30, default="")        # ok | starting | inference_too_slow | stopped | error ...
    meaning = models.CharField(max_length=40, default="")       # person_presence_only 등 (감지 범위)
    schema_version = models.IntegerField(null=True)
    observed_at = models.BigIntegerField(null=True)              # 촬영 시각(서버 시계로 보정)
    valid_until = models.BigIntegerField(null=True)
    last_seen_at = models.BigIntegerField(null=True)             # 서버 수신 시각
    clock_offset = models.IntegerField(default=0)                # 서버 - Pi 시계 차이(초)
    seats_reported = models.IntegerField(default=0)


class Reservation(models.Model):
    STATUS = ["reserved", "in_use", "returned", "cancelled", "expired", "no_show", "force_returned"]

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="reservations")
    seat = models.ForeignKey(Seat, on_delete=models.PROTECT, related_name="reservations")
    status = models.CharField(max_length=16, choices=[(s, s) for s in STATUS])
    start_at = models.BigIntegerField()
    end_at = models.BigIntegerField()
    checked_in_at = models.BigIntegerField(null=True, blank=True)
    ended_at = models.BigIntegerField(null=True, blank=True)
    extend_count = models.IntegerField(default=0)
    source = models.CharField(max_length=10, default="map")  # map | seat_page | admin

    class Meta:
        constraints = [
            # 활성 예약은 좌석당 1개, 사용자당 1개
            models.UniqueConstraint(fields=["seat"], condition=Q(status__in=ACTIVE), name="ux_res_active_seat"),
            models.UniqueConstraint(fields=["user"], condition=Q(status__in=ACTIVE), name="ux_res_active_user"),
        ]


class SeatState(models.Model):
    """직전 판정 캐시 (전이 감지용)."""
    seat = models.OneToOneField(Seat, on_delete=models.CASCADE, primary_key=True)
    seat_state = models.CharField(max_length=12)
    detail = models.CharField(max_length=20)
    since = models.BigIntegerField()


class StatusLog(models.Model):
    """세부 상태 전이 이력 (통계)."""
    seat_no = models.IntegerField()
    seat_state = models.CharField(max_length=12)
    detail = models.CharField(max_length=20)
    prev_detail = models.CharField(max_length=20, null=True)
    reservation_id = models.BigIntegerField(null=True)
    actual = models.CharField(max_length=12, null=True)
    at = models.BigIntegerField()

    class Meta:
        indexes = [models.Index(fields=["seat_no", "at"])]


class Alert(models.Model):
    TYPES = ["unauthorized", "no_checkin", "away", "hoarding", "unknown", "seat_unavailable", "no_show", "call"]

    seat = models.ForeignKey(Seat, on_delete=models.PROTECT, related_name="alerts")
    type = models.CharField(max_length=20, choices=[(t, t) for t in TYPES])
    reservation = models.ForeignKey(Reservation, null=True, blank=True, on_delete=models.SET_NULL)
    memo = models.CharField(max_length=200, null=True, blank=True)
    created_at = models.BigIntegerField()
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")  # 호출한 이용자
    resolved_at = models.BigIntegerField(null=True, blank=True)
    resolved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    resolution = models.CharField(max_length=20, null=True, blank=True)  # handled | force_returned | auto | warned | reset

    class Meta:
        constraints = [
            # 같은 좌석·같은 유형의 미해결 알림은 1개만 (call 제외)
            models.UniqueConstraint(fields=["seat", "type"], condition=Q(resolved_at__isnull=True) & ~Q(type="call"),
                                    name="ux_alert_open"),
        ]


class AdminLog(models.Model):
    """관리자 처리 이력 (경고·정지·관리자 모드 켜고 끄기 포함)."""
    admin = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")  # 관리자 모드를 켠 사용자
    action = models.CharField(max_length=20)
    seat = models.ForeignKey(Seat, null=True, on_delete=models.SET_NULL, related_name="+")
    reservation_id = models.BigIntegerField(null=True)
    target_user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    alert_id = models.BigIntegerField(null=True)
    memo = models.CharField(max_length=300, null=True)
    at = models.BigIntegerField(db_index=True)


class Setting(models.Model):
    key = models.CharField(max_length=40, primary_key=True)
    value = models.CharField(max_length=40)


class ReservationEvent(models.Model):
    """예약별 이력: 예약·체크인·연장·반납·취소·미입실·만료·강제 반납·이동 (내 이용 기록용)."""
    KINDS = {
        "reserve": "예약", "checkin": "체크인", "extend": "연장", "return": "반납", "cancel": "예약 취소",
        "no_show": "미입실(자동 취소)", "expire": "이용 종료(시간 만료)", "force_return": "강제 반납(관리자)",
        "move": "좌석 이동(관리자)", "admin_extend": "연장(관리자)", "admin_checkin": "체크인(관리자)",
        "admin_assign": "배정(관리자)",
    }
    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name="events")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=16)
    at = models.BigIntegerField()
    memo = models.CharField(max_length=200, null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "at"])]


class WaitEntry(models.Model):
    """빈자리 알림 대기. 빈자리가 나면 먼저 등록한 순서대로 일정 시간 우선 예약 기회를 준다."""
    STATUS = ["waiting", "offered", "fulfilled", "expired", "declined", "cancelled"]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="wait_entries")
    zone = models.CharField(max_length=50, blank=True, default="")  # "" = 아무 자리
    status = models.CharField(max_length=10, default="waiting")
    created_at = models.BigIntegerField()
    offered_seat = models.ForeignKey(Seat, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    offered_at = models.BigIntegerField(null=True, blank=True)
    expires_at = models.BigIntegerField(null=True, blank=True)
    ended_at = models.BigIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user"], condition=Q(status__in=("waiting", "offered")), name="ux_wait_active_user"),
            models.UniqueConstraint(fields=["offered_seat"], condition=Q(status="offered"), name="ux_wait_offer_seat"),
        ]


class Notification(models.Model):
    """본인 계정 알림: 사전 경고, 처리 필요 전환, 관리자 경고·정지, 빈자리 안내."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=20)       # prewarn | issue | warning | suspend | notice | offer | info
    level = models.CharField(max_length=8, default="info")  # info | warn | danger | ok
    title = models.CharField(max_length=100)
    body = models.CharField(max_length=300, blank=True, default="")
    seat = models.ForeignKey(Seat, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reservation = models.ForeignKey(Reservation, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    dedup_key = models.CharField(max_length=80, null=True, blank=True, unique=True)  # 같은 사안 중복 알림 방지
    created_at = models.BigIntegerField()
    read_at = models.BigIntegerField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"])]


class JudgmentFeedback(models.Model):
    """판정 피드백: 관리자가 좌석을 직접 확인해 화면의 판정이 맞는지 기록한다 (카메라 판정 정확도 측정)."""
    seat = models.ForeignKey(Seat, on_delete=models.CASCADE, related_name="feedback")
    admin = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    at = models.BigIntegerField(db_index=True)
    shown_state = models.CharField(max_length=12)    # 판정된 좌석 상태 (available|in_use|unavailable)
    shown_detail = models.CharField(max_length=20)   # 판정된 세부 상태
    source = models.CharField(max_length=10)         # 판정 근거 출처: camera | manual | checkin | return | seed
    verdict = models.CharField(max_length=8)         # correct | wrong
    correct_detail = models.CharField(max_length=20, null=True, blank=True)
    memo = models.CharField(max_length=200, null=True, blank=True)
    applied = models.BooleanField(default=False)     # 올바른 상태로 바로 수정했는지
    cam_state = models.CharField(max_length=10, null=True, blank=True)   # 피드백 시점의 카메라 판정
    cam_confidence = models.FloatField(null=True, blank=True)            # 피드백 시점의 사람 탐지 점수 (임계값 조정용)
