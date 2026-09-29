from django.conf import settings
from django.urls import include, re_path
from django.views.static import serve

urlpatterns = [
    # 로컬 네트워크 시연용: DEBUG와 상관없이 runserver가 정적 파일을 직접 제공한다.
    re_path(r"^static/(?P<path>.*)$", serve, {"document_root": settings.STATIC_DIR}),
    re_path(r"", include("seats.urls")),
]
