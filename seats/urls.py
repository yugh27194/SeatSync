from django.urls import path

from .views import accounts, api_admin, api_device, api_user, pages

urlpatterns = [
    # 화면
    path("", pages.index),
    path("login", accounts.login_view),
    path("signup", accounts.signup_view),
    path("logout", accounts.logout_view),
    path("map", pages.map_page),
    path("history", pages.history_page),
    path("congestion", pages.congestion_page),
    path("seat/<int:no>", pages.seat_page),
    path("my", pages.my_page),
    path("admin", pages.admin_page),
    path("admin/settings", pages.admin_settings_page),
    path("admin/qr", pages.admin_qr_page),
    path("admin/qr/files/<str:name>", pages.admin_qr_file),
    path("admin/unlock", accounts.admin_unlock_page),
    path("admin/lock", accounts.admin_lock_page),

    # 관리자 모드 켜기/끄기
    path("api/admin-mode", accounts.admin_mode_status),
    path("api/admin-mode/unlock", accounts.admin_mode_unlock),
    path("api/admin-mode/lock", accounts.admin_mode_lock),

    # 사용자 API
    path("api/seats", api_user.seats),
    path("api/seats/<int:no>", api_user.seat_detail),
    path("api/reservations", api_user.create_reservation),
    path("api/reservations/<int:res_id>/checkin", api_user.checkin),
    path("api/reservations/<int:res_id>/extend", api_user.extend),
    path("api/reservations/<int:res_id>/return", api_user.return_reservation),
    path("api/calls", api_user.create_call),
    path("api/waitlist", api_user.waitlist_join),
    path("api/waitlist/cancel", api_user.waitlist_cancel),
    path("api/waitlist/decline", api_user.waitlist_decline),
    path("api/me/history", api_user.my_history),
    path("api/me/reservations", api_user.my_reservations),
    path("api/me/notifications", api_user.my_notifications),
    path("api/me/notifications/read", api_user.my_notifications_read),
    path("api/congestion", api_user.congestion),

    # 관리자 API
    path("api/admin/seats", api_admin.seats),
    path("api/admin/seats/<int:no>/state", api_admin.change_seat_state),
    path("api/admin/seats/<int:no>/feedback", api_admin.feedback),
    path("api/admin/feedback", api_admin.feedback_list),
    path("api/admin/cameras", api_admin.cameras),
    path("api/admin/alerts", api_admin.alerts),
    path("api/admin/alerts/<int:alert_id>/resolve", api_admin.resolve_alert),
    path("api/admin/reservations", api_admin.assign),
    path("api/admin/reservations/<int:res_id>/checkin", api_admin.admin_checkin),
    path("api/admin/reservations/<int:res_id>/move", api_admin.move),
    path("api/admin/reservations/<int:res_id>/extend", api_admin.admin_extend),
    path("api/admin/reservations/<int:res_id>/force-return", api_admin.force_return),
    path("api/admin/users", api_admin.users),
    path("api/admin/users/<int:user_id>/warn", api_admin.warn),
    path("api/admin/users/<int:user_id>/unwarn", api_admin.unwarn),
    path("api/admin/users/<int:user_id>/suspend", api_admin.suspend),
    path("api/admin/users/<int:user_id>/unsuspend", api_admin.unsuspend),
    path("api/admin/users/<int:user_id>/notice", api_admin.notice),
    path("api/admin/log", api_admin.admin_log),
    path("api/admin/demo", api_admin.demo),
    path("api/admin/demo-history", api_admin.demo_history),
    path("api/admin/settings", api_admin.settings_api),
    path("api/admin/stats", api_admin.stats),

    # 디바이스(Pi)
    path("api/detections", api_device.detections),
    path("api/device/config", api_device.device_config),
]
