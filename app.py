"""Flask 앱 생성, 블루프린트 등록, 실행 진입점."""
import time
from datetime import timedelta

from flask import Flask, g

import auth
import db
from config import Config
from routes import register_error_handlers


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["CLOCK"] = time.time
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)
    app.json.ensure_ascii = False
    if overrides:
        app.config.update(overrides)

    db.init_app(app)
    register_error_handlers(app)
    app.before_request(auth.load_user)

    from routes import api_admin, api_device, api_user, pages

    app.register_blueprint(auth.bp)
    app.register_blueprint(pages.bp)
    app.register_blueprint(api_user.bp)
    app.register_blueprint(api_admin.bp)
    app.register_blueprint(api_device.bp)

    @app.context_processor
    def _inject_user():
        return {"current_user": g.get("user")}

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
