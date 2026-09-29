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

    class Meta:
        ordering = ["no"]


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
    TYPES = ["unauthorized", "no_checkin", "away", "hoarding", "seat_unavailable", "no_show", "call"]

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
