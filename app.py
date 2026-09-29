"""Flask 앱 생성, 블루프린트 등록, 실행 진입점."""
import time
from datetime import timedelta

from flask import Flask, g, request

import auth
import db
from config import Config
from routes import error_response, register_error_handlers


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["CLOCK"] = time.time
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)
    app.json.ensure_ascii = False
    app.json.sort_keys = False  # 설정·상태 요약을 정의한 순서대로
    if overrides:
        app.config.update(overrides)

    db.init_app(app)
    register_error_handlers(app)

    checked = {}

    @app.before_request
    def _check_schema():
        """이전 버전 DB로 실행하면 알 수 없는 500 대신 재생성 안내를 보여 준다."""
        if request.endpoint == "static":
            return None
        path = app.config["DATABASE"]
        if not checked.get(path):
            if not db.schema_ok(db.get_db()):
                if request.path.startswith("/api/"):
                    return error_response(500, "DB_RESET_REQUIRED", db.RESET_HINT)
                return f"<h1>SeatSync</h1><p>{db.RESET_HINT}</p>", 500
            checked[path] = True
        return None

    app.before_request(auth.load_user)

    from routes import api_admin, api_device, api_user, pages

    app.register_blueprint(auth.bp)
    app.register_blueprint(pages.bp)
    app.register_blueprint(api_user.bp)
    app.register_blueprint(api_admin.bp)
    app.register_blueprint(api_device.bp)

    @app.context_processor
    def _inject_user():
        return {"current_user": g.get("user"), "admin_mode": g.get("admin", False)}

    return app


app = create_app()

if __name__ == "__main__":
    if Config.ADMIN_CODE == "0000":
        print("[SeatSync] 관리자 코드가 기본값(0000)입니다. 실제 운영 시 SEATSYNC_ADMIN_CODE 환경변수로 바꾸세요.")
    app.run(host="0.0.0.0", port=5000, threaded=True)
