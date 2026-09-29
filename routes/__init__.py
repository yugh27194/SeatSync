"""라우트 공통 헬퍼: API 에러, 현재 시각, JSON 본문."""
from flask import current_app, jsonify, request


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def error_response(status, code, message):
    return jsonify({"error": {"code": code, "message": message}}), status


def now_ts():
    """현재 시각(epoch 초). time.time() 직접 호출은 라우트 계층에서만 — 테스트는 CLOCK을 교체한다."""
    return int(current_app.config["CLOCK"]())


def json_body():
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ApiError(400, "BAD_REQUEST", "요청 본문은 JSON 객체여야 합니다.")
    return data


def int_field(data, key, required=True):
    v = data.get(key)
    if v is None:
        if required:
            raise ApiError(400, "BAD_REQUEST", f"'{key}' 필드가 필요합니다.")
        return None
    try:
        if isinstance(v, bool):
            raise ValueError
        return int(v)
    except (TypeError, ValueError):
        raise ApiError(400, "BAD_REQUEST", f"'{key}' 필드는 정수여야 합니다.")


def register_error_handlers(app):
    @app.errorhandler(ApiError)
    def _api_error(e):
        return error_response(e.status, e.code, e.message)
