"""API 공통: 에러 형식, JSON 본문 파싱, 응답."""
import json

from django.http import JsonResponse


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def jres(data, status=200):
    return JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False}, safe=False)


def error_response(status, code, message):
    return jres({"error": {"code": code, "message": message}}, status)


def json_body(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "BAD_REQUEST", "요청 본문이 올바른 JSON이 아닙니다.")
    if not isinstance(data, dict):
        raise ApiError(400, "BAD_REQUEST", "요청 본문은 JSON 객체여야 합니다.")
    return data


def int_field(data, key, required=True):
    v = data.get(key)
    if v is None or v == "":
        if required:
            raise ApiError(400, "BAD_REQUEST", f"'{key}' 필드가 필요합니다.")
        return None
    if isinstance(v, bool):
        raise ApiError(400, "BAD_REQUEST", f"'{key}' 필드는 정수여야 합니다.")
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ApiError(400, "BAD_REQUEST", f"'{key}' 필드는 정수여야 합니다.")


def str_field(data, key, max_len=200):
    v = data.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise ApiError(400, "BAD_REQUEST", f"'{key}'는 문자열이어야 합니다.")
    return v.strip()[:max_len] or None
