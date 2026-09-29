"""SQLite 커넥션, 스키마 생성, seed."""
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager

import click
from flask import current_app, g
from werkzeug.security import generate_password_hash

from config import BASE_DIR
from status import DEFAULT_SETTINGS, Settings

SCHEMA_FILE = os.path.join(BASE_DIR, "schema.sql")

# (학번/아이디, 이름, 비밀번호, 역할)
SEED_ACCOUNTS = (
    [("admin", "관리자", "admin1234", "admin")]
    + [(f"user{c}", f"사용자{c}", "1234", "user") for c in "ABC"]
    + [(f"2026000{i}", f"테스트{i}", "1234", "user") for i in range(1, 6)]
)


def connect(path):
    conn = sqlite3.connect(path, isolation_level=None, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def tx(conn):
    """BEGIN IMMEDIATE 트랜잭션. 이미 트랜잭션 안이면 그대로 합류한다."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# ---------------------------------------------------------------- 좌석 배치

def load_layout(seats_file):
    with open(seats_file, encoding="utf-8") as f:
        data = json.load(f)
    return {
        "grid": data.get("grid", {"cols": 1, "rows": 1}),
        "seats": data.get("seats", []),
        "fixtures": data.get("fixtures", []),
    }


# ---------------------------------------------------------------- 스키마·seed

def init_db(conn):
    with open(SCHEMA_FILE, encoding="utf-8") as f:
        conn.executescript(f.read())


def seed(conn, seats_file, now=None, pw_method=None):
    now = int(now if now is not None else time.time())
    layout = load_layout(seats_file)
    with tx(conn):
        # 설정: 없을 때만 insert
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, str(v)))

        # 좌석: upsert. qr_token과 현장 상태는 새 좌석일 때만 seats.json 값으로 초기화(이후 유지)
        nos = []
        for st in layout["seats"]:
            nos.append(int(st["no"]))
            row = conn.execute("SELECT qr_token FROM seats WHERE no = ?", (st["no"],)).fetchone()
            if row:
                conn.execute(
                    "UPDATE seats SET label=?, x=?, y=?, zone=?, camera_id=?, active=1 WHERE no=?",
                    (st["label"], st["x"], st["y"], st.get("zone"), st.get("camera_id"), st["no"]),
                )
            else:
                state = st.get("state", "empty")
                if state not in ("empty", "occupied", "unavailable"):
                    raise ValueError(f"seats.json 좌석 {st['no']}: state는 empty|occupied|unavailable")
                conn.execute(
                    "INSERT INTO seats(no, label, x, y, zone, camera_id, qr_token, active, state, state_since, "
                    "state_source, state_note) VALUES (?,?,?,?,?,?,?,1,?,?,'seed',?)",
                    (st["no"], st["label"], st["x"], st["y"], st.get("zone"), st.get("camera_id"),
                     secrets.token_urlsafe(8), state, now, st.get("note")),
                )
        # seats.json에서 빠진 좌석은 비활성 (이력 보존을 위해 삭제하지 않음)
        if nos:
            marks = ",".join("?" * len(nos))
            conn.execute(f"UPDATE seats SET active=0 WHERE no NOT IN ({marks})", nos)
        else:
            conn.execute("UPDATE seats SET active=0")

        # 계정
        for student_no, name, pw, role in SEED_ACCOUNTS:
            conn.execute(
                "INSERT OR IGNORE INTO users(student_no, name, pw_hash, role, created_at) VALUES (?,?,?,?,?)",
                (student_no, name, generate_password_hash(pw, **({"method": pw_method} if pw_method else {})), role, now),
            )


def get_settings(conn):
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return Settings.from_dict({r["key"]: r["value"] for r in rows if r["key"] in DEFAULT_SETTINGS})


# ---------------------------------------------------------------- CLI

@click.command("init-db")
@click.option("--reset", is_flag=True, help="DB 파일을 삭제하고 새로 만든다.")
def init_db_command(reset):
    """스키마 생성 + seed."""
    path = current_app.config["DATABASE"]
    close_db()
    if reset and path != ":memory:":
        for suffix in ("", "-wal", "-shm", "-journal"):
            if os.path.exists(path + suffix):
                os.remove(path + suffix)
        click.echo(f"삭제: {path}")
    conn = connect(path)
    try:
        init_db(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(seats)")}
        ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "state" not in cols or "warnings" not in ucols:
            raise click.ClickException(
                "기존 DB가 이전 버전 구조입니다. `flask --app app init-db --reset` 으로 다시 만드세요.")
        seed(conn, current_app.config["SEATS_FILE"])
    finally:
        conn.close()
    click.echo(f"DB 초기화 완료: {path}")


@click.command("demo")
def demo_command():
    """시연 상황 배치: 사용자A/B/C 등으로 다양한 예약·현장 상태를 만든다."""
    from service import setup_demo  # 순환 import 방지

    conn = connect(current_app.config["DATABASE"])
    try:
        for line in setup_demo(conn, int(time.time())):
            click.echo(line)
    finally:
        conn.close()


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(demo_command)
