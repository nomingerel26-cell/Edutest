# -*- coding: utf-8 -*-
"""
EduTest — Оролт/Гаралтын тестийн систем (Python + Flask + SQLite MVP).

Ажиллуулах:
    python3 seed.py          # өгөгдлийн сан үүсгэж жишээ өгөгдөл нэмнэ
    python3 app.py           # http://127.0.0.1:5000

БҮТЭЦ:
    domain.py    — цэвэр бизнес логик (хэшлэлт, бодолт, тааруулалт, шалгалт)
    database.py  — БҮХ SQL (SQLite -> PostgreSQL шилжих цорын ганц хил)
    app.py       — зөвхөн HTTP маршрут, эрх шалгах, форм боловсруулалт

Энэ файлд SQL БАЙХГҮЙ — өгөгдлийн сангийн бүх хандалт database.py-ээр дамжина.
"""

from __future__ import annotations

import csv
import hmac
import io
import os
import pathlib
import secrets
import sqlite3
import tempfile
from datetime import date, datetime, timezone
from functools import wraps

from flask import (
    Flask, abort, flash, g, redirect, render_template, request, session, url_for, Response,
)

import backup
import database as db
import domain
import exports
import mailer

app = Flask(__name__)

# ---------------------------------------------------------------------
# Нууц түлхүүр
# ---------------------------------------------------------------------
# Байршуулахдаа EDUTEST_SECRET орчны хувьсагчийг ЗААВАЛ тохируулна.
# Тохируулаагүй үед санамсаргүй түлхүүр үүснэ: систем ажиллах боловч
# процесс дахин эхлэх бүрд бүх session хүчингүй болно (нэвтрэлт унана).
# Ингэснээр «түр зуурын түлхүүр» санамсаргүйгээр production-д гарахгүй.
_SECRET = os.environ.get("EDUTEST_SECRET")
if not _SECRET:
    _SECRET = secrets.token_hex(32)
    app.config["EDUTEST_EPHEMERAL_SECRET"] = True
app.secret_key = _SECRET

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # HTTPS-ээр байршуулсан бол EDUTEST_HTTPS=1 гэж тохируулна.
    SESSION_COOKIE_SECURE=os.environ.get("EDUTEST_HTTPS") == "1",
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

# Хөгжүүлэлтийн горим: демо нэвтрэлт зөвхөн энэ үед харагдана.
app.config["EDUTEST_DEV"] = os.environ.get("EDUTEST_ENV", "development") != "production"

app.jinja_env.globals["OPTION_KEYS"] = domain.OPTION_KEYS
app.jinja_env.globals["MATCH_OPTION_KEYS"] = domain.MATCH_OPTION_KEYS
app.jinja_env.globals["option_keys"] = domain.option_keys
app.jinja_env.globals["option_label"] = domain.option_label
app.jinja_env.globals["visible_option_keys"] = domain.visible_option_keys
app.jinja_env.globals["entry_mode"] = domain.entry_mode
app.jinja_env.globals["ENTRY_MODE_LABELS"] = domain.ENTRY_MODE_LABELS
app.jinja_env.globals["QUESTION_TYPES"] = domain.QUESTION_TYPES
app.jinja_env.globals["QUESTION_TYPE_LABELS"] = domain.QUESTION_TYPE_LABELS
app.jinja_env.globals["question_type"] = domain.question_type
app.jinja_env.globals["match_display_order"] = domain.match_display_order
app.jinja_env.filters["credit"] = domain.format_credit
app.jinja_env.filters["option_set"] = domain.parse_option_set
app.jinja_env.filters["match_answer"] = domain.parse_match_answer


# =====================================================================
# CSRF хамгаалалт
# ---------------------------------------------------------------------
# Гадаад сан ашиглаагүй: session дотор санамсаргүй token хадгалж, POST
# бүр дээр тулгана. `hmac.compare_digest` — цагийн зөрүүнд тэсвэртэй.
#
# `app.config['TESTING']` үед шалгалт алгасагдана (unittest-ийн client
# нь template render хийхгүйгээр POST илгээдэг). Production-д TESTING
# хэзээ ч асаалттай байхгүй.
# =====================================================================
CSRF_FIELD = "csrf_token"


def csrf_token() -> str:
    if CSRF_FIELD not in session:
        session[CSRF_FIELD] = secrets.token_urlsafe(32)
    return session[CSRF_FIELD]


@app.before_request
def _verify_csrf():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if app.config.get("TESTING"):
        return None
    submitted = request.form.get(CSRF_FIELD) or request.headers.get("X-CSRF-Token") or ""
    expected = session.get(CSRF_FIELD) or ""
    if not expected or not hmac.compare_digest(str(submitted), str(expected)):
        abort(400)
    return None


@app.errorhandler(400)
def _bad_request(_e):
    return render_template(
        "error.html", code=400,
        message="Хүсэлт хүчингүй байна. Хуудсыг дахин ачаалж, дахин оролдоно уу.",
    ), 400


# =====================================================================
# Схемийн миграц — эхлэхэд НЭГ УДАА
# ---------------------------------------------------------------------
# Хуучин хувилбарын `edutest.db`-тэй шууд ажиллуулахад асуултын шинэ
# багана (qtype, match_*) байхгүй тул алдаа гарна. Тиймээс програм
# эхлэхэд миграцийг автоматаар ажиллуулна.
#
# `database.migrate` нь idempotent — хийгдсэн алхмыг давтахгүй тул
# сервер дахин эхлэх бүрд ажиллуулахад аюулгүй.
# =====================================================================
def _bootstrap_admin(conn) -> None:
    """Хэрэглэгч огт байхгүй үед орчны хувьсагчаас админ үүсгэнэ.

    Байршуулсан сервер дээр `seed.py` ажиллуулах боломжгүй тул эхний
    админыг ингэж үүсгэнэ. EDUTEST_ADMIN_EMAIL / EDUTEST_ADMIN_PASSWORD
    хоёрын аль нэг нь тохируулаагүй бол ЮУ Ч ХИЙХГҮЙ — анхдагч нууц үгтэй
    админ автоматаар үүсгэвэл хэн ч нэвтэрч чадах эрсдэлтэй.
    """
    existing = db.list_users(conn)
    if existing:
        _log(f"Админ үүсгэхийг алгаслаа: DB-д {len(existing)} хэрэглэгч "
             f"аль хэдийн байна ({existing[0]['email']}...).")
        return
    email = os.environ.get("EDUTEST_ADMIN_EMAIL", "").strip()
    password = os.environ.get("EDUTEST_ADMIN_PASSWORD", "")
    if not email or not password:
        _log("АНХААРУУЛГА: Өгөгдлийн санд хэрэглэгч алга. Админ үүсгэхийн "
             "тулд EDUTEST_ADMIN_EMAIL, EDUTEST_ADMIN_PASSWORD хувьсагчийг "
             "тохируулаад серверээ дахин эхлүүлнэ үү.")
        return
    name = os.environ.get("EDUTEST_ADMIN_NAME", "Админ")
    department = os.environ.get("EDUTEST_ADMIN_DEPARTMENT", "Сургалтын алба")
    db.create_user(conn, name, email, domain.hash_password(password),
                   "admin", department, domain.now_iso())
    conn.commit()
    _log(f"✓ Админ хэрэглэгч үүслээ: {email}")


def _log(msg: str) -> None:
    """Контейнерын log руу ШУУД бичнэ.

    Python нь stdout нь TTY биш үед блокоор буферлэдэг тул контейнер
    богино хугацаанд унтрахад эхлэлийн мессежүүд алдагддаг. Оношилгооны
    мөрүүд ЗААВАЛ харагдах ёстой тул flush=True.
    """
    print(msg, flush=True)


def _startup_diagnostics() -> None:
    """Эхлэхэд орчны төлөвийг log-д бичнэ. НУУЦ УТГА ХЭВЛЭХГҮЙ —
    зөвхөн хувьсагч тохируулагдсан эсэхийг л харуулна."""
    _log("--- EduTest эхлэлийн оношилгоо ---")
    _log(f"  DB зам          : {db.DB_PATH}")
    _log(f"  DB файл байгаа  : {db.DB_PATH.exists()}")
    secrets_keys = ("EDUTEST_SECRET", "EDUTEST_ADMIN_PASSWORD", "EDUTEST_SMTP_PASSWORD")
    for key in ("EDUTEST_DB", "EDUTEST_SECRET", "EDUTEST_ENV", "EDUTEST_HTTPS",
                "EDUTEST_ADMIN_EMAIL", "EDUTEST_ADMIN_PASSWORD", "EDUTEST_ADMIN_NAME",
                "EDUTEST_INSTITUTION",
                "EDUTEST_BACKUP_DIR", "EDUTEST_BACKUP_KEEP",
                "EDUTEST_SMTP_HOST", "EDUTEST_SMTP_PORT", "EDUTEST_SMTP_USER",
                "EDUTEST_SMTP_PASSWORD", "EDUTEST_SMTP_FROM", "EDUTEST_SMTP_TLS"):
        raw = os.environ.get(key)
        if raw is None:
            state = "ТОХИРУУЛААГҮЙ"
        elif key in secrets_keys:
            state = f"тохируулсан ({len(raw)} тэмдэгт)"
        else:
            state = repr(raw)
        _log(f"  {key:<24}: {state}")
    _log(f"  {'нөөцлөлтийн хавтас':<24}: {backup.backup_dir()} "
         f"({len(backup.list_backups())} файл)")
    _log(f"  {'имэйл илгээх':<24}: "
         f"{'БЭЛЭН' if mailer.is_configured() else 'тохируулаагүй'}")

    # --- Түгээмэл тохиргооны алдааг барих ---
    # Хувьсагч хоосон биш гэдэг нь утга нь ЗӨВ гэсэн үг биш. Доорх хоёр
    # алдаа бодит ашиглалтад давтагдан гарсан тул тусад нь анхааруулна.
    for key, raw in sorted(os.environ.items()):
        if not key.startswith("EDUTEST_") or not raw:
            continue
        # `<...>` бол зааврын орлуулах тэмдэглэгээ. Утга нь БҮХЭЛДЭЭ
        # хаалтанд байвал хуулахдаа хаалтыг нь хасаагүй гэсэн үг.
        # EDUTEST_SMTP_FROM дээрх `Нэр <хаяг>` нь ЗӨВ — өмнө нь текст
        # байгаа тул энд баригдахгүй.
        if raw.startswith("<") and raw.endswith(">"):
            _log(f"  АНХААР: {key} нь «{raw}» — зааврын тэмдэглэгээг хуулсан "
                 f"бололтой. < > хаалтыг хасаж, бодит утгаа бичнэ үү.")

    smtp_host = (os.environ.get("EDUTEST_SMTP_HOST") or "").lower()
    smtp_password = os.environ.get("EDUTEST_SMTP_PASSWORD") or ""
    if "gmail" in smtp_host and smtp_password and len(smtp_password) != 16:
        _log(f"  АНХААР: Gmail-ийн App Password 16 тэмдэгт байдаг, харин "
             f"EDUTEST_SMTP_PASSWORD {len(smtp_password)} тэмдэгт байна. "
             f"Энгийн нууц үг оруулсан бол Gmail хүлээж авахгүй.")
    _log("----------------------------------")


def _migrate_on_startup() -> None:
    # Сан байхгүй бол схемийг ЭНД үүсгэнэ. gunicorn зэрэг WSGI сервер нь
    # `__main__` блокийг ажиллуулдаггүй тул тэнд байгаа шалгалт хүрэлцэхгүй —
    # энэ функц import үед дуудагддаг учир байршуулсан орчинд ч ажиллана.
    _startup_diagnostics()
    fresh = not db.DB_PATH.exists()
    if fresh:
        _log(f"{db.DB_PATH} олдсонгүй — schema.sql-аас шинээр үүсгэж байна…")
        db.init_db()
    conn = db.connect()
    try:
        steps = db.migrate(conn)
        if steps:
            _log("Өгөгдлийн сангийн шинэчлэл хийгдлээ:")
            for step in steps:
                _log(f"  • {step}")
        # Зөвхөн `fresh` үед биш, хэрэглэгч огт байхгүй бүрд оролдоно.
        # Volume залгасан үед эхний boot-д админы хувьсагч тохируулаагүй
        # бол хоосон DB үлдэж, дахин хэзээ ч админ үүсгэх боломжгүй
        # болох эрсдэлтэй. `_bootstrap_admin` өөрөө idempotent —
        # хэрэглэгч байвал юу ч хийхгүй буцдаг.
        _bootstrap_admin(conn)
    finally:
        conn.close()


_migrate_on_startup()


# =====================================================================
# Холболтын амьдралын мөчлөг
# =====================================================================
@app.before_request
def _open_connection():
    g.conn = db.connect()


@app.teardown_request
def _close_connection(exc):
    conn = g.pop("conn", None)
    if conn is not None:
        if exc is None:
            conn.commit()
        else:
            conn.rollback()
        conn.close()


# =====================================================================
# Нэвтрэлт ба эрх
# =====================================================================
def current_user():
    uid = session.get("user_id")
    conn = g.get("conn")
    # Холболт нээгдэхээс өмнө (жишээ нь CSRF алдааны хуудас) g.conn байхгүй.
    return db.get_user(conn, uid) if uid and conn is not None else None


# Хажуугийн цэсэнд аль зүйл идэвхтэй болохыг route-аас тодорхойлно.
_NAV_MAP = {
    "dashboard": "dashboard",
    "courses": "courses", "course_detail": "courses", "create_group": "courses",
    "delete_group": "courses", "create_pair": "courses",
    "tests_index": "tests_index", "test_detail": "tests_index",
    "test_share": "tests_index", "edit_question": "tests_index",
    "pairs_index": "pairs_index",
    "results_index": "results_index", "test_results": "results_index",
    "analytics": "analytics", "pair_comparison": "analytics",
    "exports": "exports",
    "admin_users": "admin_users",
}


@app.context_processor
def _inject_globals():
    return {
        "current_user": current_user(),
        "csrf_token": csrf_token(),
        "active_nav": _NAV_MAP.get(request.endpoint or "", ""),
        "IS_DEV": app.config.get("EDUTEST_DEV", False),
    }


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("Үргэлжлүүлэхийн тулд нэвтэрнэ үү.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def owned_course(course_id: int) -> dict:
    """Хичээлийг ачаалж, эрхийг шалгана. Админ бүх хичээлд ханддаг."""
    course = db.get_course(g.conn, course_id)
    if not course:
        abort(404)
    user = current_user()
    if user["role"] != "admin" and course["teacher_id"] != user["id"]:
        abort(403)
    return course


def owned_test(test_id: int) -> dict:
    test = db.get_test(g.conn, test_id)
    if not test:
        abort(404)
    user = current_user()
    if user["role"] != "admin" and test["teacher_id"] != user["id"]:
        abort(403)
    return test


@app.errorhandler(403)
def _forbidden(_e):
    return render_template("error.html", code=403,
                           message="Танд энэ хуудсыг үзэх эрх байхгүй."), 403


@app.errorhandler(404)
def _not_found(_e):
    return render_template("error.html", code=404,
                           message="Хуудас олдсонгүй."), 404


# =====================================================================
# Нэвтрэх / гарах
# =====================================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        user = db.get_user_by_email(g.conn, email) if email else None
        # Хэрэглэгч байхгүй ч, нууц үг буруу ч ИЖИЛ мессеж (мэдээлэл алдагдуулахгүй).
        if user and domain.verify_password(password, user["password_hash"]):
            session.clear()
            session["user_id"] = user["id"]
            flash(f"Тавтай морил, {user['full_name']}!", "success")
            nxt = request.args.get("next")
            return redirect(nxt if nxt and nxt.startswith("/") else url_for("dashboard"))
        flash("Имэйл эсвэл нууц үг буруу байна.", "error")
        return render_template("login.html", email=email), 401
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("login.html", email="")


@app.route("/logout")
def logout():
    session.clear()
    flash("Системээс гарлаа.", "success")
    return redirect(url_for("login"))


# =====================================================================
# Хяналтын самбар
# =====================================================================
def _scope():
    """Нэвтэрсэн хүний хамрах хүрээ: админ бол None (бүгд), багш бол өөрийн id."""
    user = current_user()
    return None if user["role"] == "admin" else user["id"]


@app.route("/")
@login_required
def dashboard():
    teacher_id = _scope()
    stats = db.dashboard_stats(g.conn, teacher_id, today=date.today().isoformat())
    tests = db.list_all_tests(g.conn, teacher_id)
    return render_template(
        "dashboard.html",
        stats=stats,
        courses=db.list_courses(g.conn, teacher_id),
        recent_tests=tests[:5],
        recent_attempts=db.recent_attempts(g.conn, teacher_id, limit=6),
    )


# =====================================================================
# Хичээл ба групп
# =====================================================================
@app.route("/courses", methods=["GET", "POST"])
@login_required
def courses():
    user = current_user()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        code = (request.form.get("code") or "").strip().upper()
        semester = (request.form.get("semester") or "").strip()
        credit_raw = (request.form.get("credit") or "3").strip()
        errors = []
        if not name:
            errors.append("Хичээлийн нэр хоосон байна.")
        if not code:
            errors.append("Хичээлийн код хоосон байна.")
        elif db.get_course_by_code(g.conn, code):
            errors.append(f"«{code}» код аль хэдийн бүртгэгдсэн байна.")
        if not semester:
            errors.append("Улирлыг бичнэ үү (жишээ: 2026 Намар).")
        credit = domain.parse_credit(credit_raw)
        if credit is None:
            credit = 0
            errors.append("Кредит 0-ээс их тоо байх ёстой (жишээ: 3 эсвэл 3.5).")
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            db.create_course(g.conn, user["id"], name, code, credit, semester, domain.now_iso())
            flash(f"«{name}» хичээл нэмэгдлээ.", "success")
        return redirect(url_for("courses"))

    teacher_id = None if user["role"] == "admin" else user["id"]
    return render_template("courses.html", courses=db.list_courses(g.conn, teacher_id))


@app.route("/courses/<int:course_id>")
@login_required
def course_detail(course_id):
    course = owned_course(course_id)
    return render_template(
        "course_detail.html",
        course=course,
        groups=db.list_groups(g.conn, course_id),
        tests=db.list_tests(g.conn, course_id),
        pairs=db.list_pairs(g.conn, course_id),
    )


@app.route("/courses/<int:course_id>/delete", methods=["GET", "POST"])
@admin_required
def delete_course(course_id):
    """Хичээлийг бүх өгөгдлийн хамт устгана. ЗӨВХӨН АДМИН.

    Cascade нь ангийн бүлэг, оюутан, хос, тест, асуулт, оролдлого,
    хариулт бүгдийг авч явна — эргэж сэргээх боломжгүй. Тиймээс энэ нь
    нэг товчны үйлдэл БИШ: тусдаа хуудсанд юу устахыг харуулж, админаар
    хичээлийн кодыг гараар бичүүлж баталгаажуулна.
    """
    course = owned_course(course_id)
    summary = db.course_delete_summary(g.conn, course_id)

    if request.method == "POST":
        typed = (request.form.get("confirm_code") or "").strip()
        if typed.upper() != (course["code"] or "").upper():
            flash(f"Код таарахгүй байна. Устгахын тулд «{course['code']}» "
                  f"гэж яг бичнэ үү.", "error")
            return render_template("course_delete.html",
                                   course=course, summary=summary), 400
        name = course["name"]
        db.delete_course(g.conn, course_id)
        flash(f"«{name}» хичээл бүх өгөгдлийн хамт устлаа.", "success")
        return redirect(url_for("courses"))

    return render_template("course_delete.html", course=course, summary=summary)


@app.route("/courses/<int:course_id>/groups", methods=["POST"])
@login_required
def create_group(course_id):
    owned_course(course_id)
    name = (request.form.get("name") or "").strip()
    count_raw = (request.form.get("student_count") or "0").strip()
    if not name:
        flash("Группын нэр хоосон байна.", "error")
        return redirect(url_for("course_detail", course_id=course_id))
    try:
        student_count = max(0, int(count_raw))
    except ValueError:
        flash("Оюутны тоо бүхэл тоо байх ёстой.", "error")
        return redirect(url_for("course_detail", course_id=course_id))
    try:
        db.create_group(g.conn, course_id, name, student_count, domain.now_iso())
        flash(f"«{name}» групп нэмэгдлээ.", "success")
    except sqlite3.IntegrityError:
        flash(f"«{name}» групп энэ хичээлд аль хэдийн байна.", "error")
    return redirect(url_for("course_detail", course_id=course_id))


@app.route("/groups/<int:group_id>")
@login_required
def group_detail(group_id):
    """Бүлгийн оюутны жагсаалт — «Зөвхөн жагсаалтаас» горимын үндэс."""
    group = db.get_group(g.conn, group_id)
    if not group:
        abort(404)
    course = owned_course(group["course_id"])
    return render_template(
        "group_detail.html", group=group, course=course,
        students=db.list_students(g.conn, group_id),
    )


@app.route("/groups/<int:group_id>/students", methods=["POST"])
@login_required
def add_students(group_id):
    """Оюутнуудыг жагсаалтад нэмнэ.

    Нэг талбар — нэг мөр нэг оюутан. Ингэснээр нэгийг нэмэх ба Excel-ээс
    олноор буулгах хоёр тусдаа форм шаардахгүй.
    """
    group = db.get_group(g.conn, group_id)
    if not group:
        abort(404)
    owned_course(group["course_id"])

    rows, errors = domain.parse_student_roster(request.form.get("roster") or "")
    for e in errors:
        flash(e, "warning")

    now = domain.now_iso()
    added = skipped = 0
    for row in rows:
        if db.get_student_by_code(g.conn, group_id, row["normalized"]):
            skipped += 1        # аль хэдийн бүртгэлтэй — нэрийг нь дарж бичихгүй
            continue
        db.create_student(g.conn, group_id, row["full_name"], row["student_code"],
                          row["normalized"], None, now)
        added += 1

    if added:
        flash(f"{added} оюутан жагсаалтад нэмэгдлээ."
              + (f" {skipped} нь аль хэдийн бүртгэлтэй байсан." if skipped else ""),
              "success")
    elif skipped:
        flash(f"{skipped} оюутан бүгд аль хэдийн бүртгэлтэй байна.", "info")
    return redirect(url_for("group_detail", group_id=group_id))


@app.route("/students/<int:student_id>/edit", methods=["GET", "POST"])
@login_required
def edit_student(student_id):
    """Жагсаалт дахь оюутны нэр, кодыг засна.

    Код нь Оролт/Гаралтын ТААРУУЛАЛТЫН ТҮЛХҮҮР тул солиход
    `attempts.match_key`-г хамт шинэчилнэ — эс бөгөөс тухайн оюутны
    хуучин ба шинэ оролдлого хоёр өөр хүн мэт сална.
    """
    student = db.get_student(g.conn, student_id)
    if not student:
        abort(404)
    group = db.get_group(g.conn, student["class_group_id"])
    owned_course(group["course_id"])

    attempt_count = db.count_student_attempts(g.conn, student_id)
    form = {"full_name": student["full_name"], "student_code": student["student_code"]}

    if request.method == "POST":
        form = {
            "full_name": (request.form.get("full_name") or "").strip(),
            "student_code": (request.form.get("student_code") or "").strip(),
        }
        norm = domain.normalize_student_code(form["student_code"])
        errors = []
        if len(form["full_name"]) < 2:
            errors.append("Овог нэрийг бүтнээр нь бичнэ үү.")
        if not norm:
            errors.append("Оюутны код хоосон байна.")
        else:
            clash = db.get_student_by_code(g.conn, group["id"], norm)
            if clash and clash["id"] != student_id:
                errors.append(f"«{clash['full_name']}» энэ кодтой аль хэдийн "
                              f"бүртгэлтэй байна.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("student_edit.html", student=student, group=group,
                                   form=form, attempt_count=attempt_count), 400

        code_changed = norm != student["normalized_student_code"]
        db.update_student(g.conn, student_id, form["full_name"],
                          form["student_code"], norm)
        if code_changed:
            moved = db.relabel_attempt_match_keys(
                g.conn, student_id, domain.build_match_key(group["id"], norm))
            if moved:
                flash(f"Код солигдсон тул {moved} оролдлогын тааруулалтын "
                      f"түлхүүрийг хамт шинэчиллээ.", "info")
        flash(f"«{form['full_name']}» шинэчлэгдлээ.", "success")
        return redirect(url_for("group_detail", group_id=group["id"]))

    return render_template("student_edit.html", student=student, group=group,
                           form=form, attempt_count=attempt_count)


@app.route("/students/<int:student_id>/delete", methods=["POST"])
@login_required
def delete_student(student_id):
    student = db.get_student(g.conn, student_id)
    if not student:
        abort(404)
    group = db.get_group(g.conn, student["class_group_id"])
    owned_course(group["course_id"])
    db.delete_student(g.conn, student_id)
    flash(f"«{student['full_name']}» жагсаалтаас хасагдлаа.", "success")
    return redirect(url_for("group_detail", group_id=group["id"]))


@app.route("/tests/<int:test_id>/entry-mode", methods=["POST"])
@login_required
def set_entry_mode(test_id):
    test = owned_test(test_id)
    mode = (request.form.get("entry_mode") or "").strip().lower()
    if mode not in domain.ENTRY_MODES:
        flash("Нэвтрэх горим буруу байна.", "error")
        return redirect(url_for("test_detail", test_id=test_id))
    db.set_test_entry_mode(g.conn, test_id, mode)
    flash(f"Нэвтрэх горим: {domain.ENTRY_MODE_LABELS[mode]}.", "success")
    return redirect(url_for("test_detail", test_id=test_id))


@app.route("/groups/<int:group_id>/delete", methods=["POST"])
@login_required
def delete_group(group_id):
    group = db.get_group(g.conn, group_id)
    if not group:
        abort(404)
    owned_course(group["course_id"])
    db.delete_group(g.conn, group_id)
    flash("Групп устлаа.", "success")
    return redirect(url_for("course_detail", course_id=group["course_id"]))


# =====================================================================
# Тестийн хос (Pre + Post) үүсгэх
# =====================================================================
@app.route("/courses/<int:course_id>/pairs", methods=["POST"])
@login_required
def create_pair(course_id):
    course = owned_course(course_id)
    name = (request.form.get("name") or "").strip()
    group_raw = (request.form.get("class_group_id") or "").strip()
    if not name:
        flash("Хосын нэр хоосон байна.", "error")
        return redirect(url_for("course_detail", course_id=course_id))
    class_group_id = int(group_raw) if group_raw.isdigit() else None

    now = domain.now_iso()
    pair_id = db.create_pair(g.conn, course_id, name, now)
    for kind, label in (("pre", "Оролтын тест"), ("post", "Гаралтын тест")):
        db.create_test(
            g.conn, course_id, pair_id, class_group_id,
            f"{course['name']} — {label}", kind, "draft",
            domain.generate_share_code(course["code"], kind), now,
        )
    flash(f"«{name}» хос үүсч, Оролт/Гаралтын тест хоёулаа бэлдлээ.", "success")
    return redirect(url_for("course_detail", course_id=course_id))


def _question_signature(rows) -> list:
    """Хоёр тестийн асуулт ижил эсэхийг харьцуулах түлхүүр.

    id, order_no зэрэг тестээс хамаарах багана ОРОХГҮЙ — зөвхөн агуулга.
    """
    return [(
        r["text"], r.get("qtype") or "single", r["correct_option"], r["score"],
        r["option_a"], r["option_b"], r["option_c"], r["option_d"],
        r.get("option_e") or "",
        r.get("match_a"), r.get("match_b"), r.get("match_c"), r.get("match_d"),
    ) for r in rows]


def _sync_pair_questions(test, *, force: bool = False) -> tuple[str, str]:
    """Оролтын тест нээгдэхэд асуултыг ижил хосын гаралтын тест рүү хуулна.

    Оролт/гаралт хоёр ЯГ ИЖИЛ асуулттай байх нь зорилготой — ахиц (Δ)
    хэмжихийн тулд хоёр хэмжилт ижил хэрэглүүрээр хийгдэх ёстой.

    ХЭЗЭЭ Ч чимээгүй алгасахгүй — алгассан бол ЯАГААД гэдгээ хэлнэ.
    Чимээгүй алгасалт нь юу болоод байгааг ойлгох боломжгүй болгодог.

    Буцаах: (мессеж, төрөл) — үргэлж утга буцаана.
    """
    def done(msg, kind="success"):
        _log(f"[хосын синк] тест={test['id']} ({test['kind']}): {msg}")
        return (msg, kind)

    if test["kind"] != "pre":
        return done("Зөвхөн Оролтын тестээс Гаралт руу хуулна. Энэ нь "
                    "Гаралтын тест байна.", "error")
    if not test["pair_id"]:
        return done("Энэ тест ямар ч Оролт/Гаралтын хост харьяалагдахгүй "
                    "тул хуулах Гаралтын тест алга. Хичээлийн хуудсанд хос "
                    "үүсгэнэ үү.", "error")

    post = db.get_pair_tests(g.conn, test["pair_id"]).get("post")
    if not post:
        return done("Энэ хосод Гаралтын тест олдсонгүй.", "error")

    source = db.list_questions(g.conn, test["id"])
    if not source:
        return done("Оролтын тестэд асуулт алга — хуулах зүйл байхгүй.", "error")

    existing = db.list_questions(g.conn, post["id"])
    if _question_signature(existing) == _question_signature(source):
        return done(f"«{post['title']}» аль хэдийн ижил {len(source)} "
                    f"асуулттай байна — өөрчлөлт хийсэнгүй.", "info")

    # Асуулт устахад answers мөрүүд cascade-аар дагаж устдаг тул
    # ДУУСГАСАН оролдлоготой байхад дарж бичихгүй. Дуусгаагүй оролдлого
    # (жишээ нь багш линкээ нээж үзсэн) нь хариултгүй тул саад болохгүй —
    # эс бөгөөс нэг удаагийн санамсаргүй нээлт синкийг бүр мөсөн хаана.
    submitted = db.count_submitted_attempts(g.conn, post["id"])
    if existing and submitted and not force:
        return done(f"«{post['title']}» дээр {submitted} оюутан тестээ өгсөн "
                    f"байна. Асуултыг солиход тэдний үр дүн устах тул "
                    f"хуулаагүй. Дарж бичихийн тулд баталгаажуулах "
                    f"шаардлагатай.", "error")

    replaced = db.delete_test_questions(g.conn, post["id"]) if existing else 0
    copied = db.copy_questions(g.conn, test["id"], post["id"])

    if replaced and submitted:
        return done(f"«{post['title']}» руу {copied} асуулт хуулж, хуучин "
                    f"{replaced} асуулт болон {submitted} оюутны үр дүнг "
                    f"устгалаа.", "warning")
    if replaced:
        return done(f"«{post['title']}» руу {copied} асуулт хуулж, хуучин "
                    f"{replaced} асуултыг сольлоо.")
    return done(f"«{post['title']}» руу {copied} асуулт хуулагдлаа. "
                f"Оролт/Гаралт одоо ижил асуулттай боллоо.")


@app.route("/tests/<int:test_id>/sync-pair", methods=["POST"])
@login_required
def sync_pair_questions(test_id):
    """Оролтын асуултыг Гаралт руу ГАРААР хуулна.

    Автомат хуулалт нь зөвхөн тест нээгдэх мөчид ажилладаг. Нээсний
    ДАРАА асуулт нэмсэн бол энэ товчоор дахин тэнцүүлнэ.
    """
    test = owned_test(test_id)
    force = (request.form.get("force") or "") == "1"
    message, kind = _sync_pair_questions(test, force=force)
    flash(message, kind)
    return redirect(url_for("test_detail", test_id=test_id))


@app.route("/tests/<int:test_id>/status", methods=["POST"])
@login_required
def change_test_status(test_id):
    test = owned_test(test_id)
    status = (request.form.get("status") or "").strip()
    if status not in ("draft", "open", "closed"):
        flash("Төлөв буруу байна.", "error")
        return redirect(url_for("test_detail", test_id=test_id))
    if status == "open" and not db.list_questions(g.conn, test_id):
        flash("Асуултгүй тестийг нээх боломжгүй. Эхлээд асуулт нэмнэ үү.", "error")
        return redirect(url_for("test_detail", test_id=test_id))
    db.set_test_status(g.conn, test_id, status)
    labels = {"draft": "Ноорог", "open": "Нээлттэй", "closed": "Хаагдсан"}
    flash(f"«{test['title']}» тестийн төлөв: {labels[status]}.", "success")
    if status == "open" and test["kind"] == "pre" and test["pair_id"]:
        message, kind = _sync_pair_questions(test)
        flash(message, kind)
    return redirect(url_for("test_detail", test_id=test_id))


# =====================================================================
# Асуулт удирдах
# =====================================================================

def _question_form(source) -> dict:
    """Асуулт үүсгэх/засах формыг нэг жигд уншина."""
    qtype = (source.get("qtype") or "single").strip().lower()
    if qtype not in domain.QUESTION_TYPES:
        qtype = "single"
    form = {
        "qtype": qtype,
        "text": (source.get("text") or "").strip(),
        "score": (source.get("score") or "").strip(),
        # multi үед олон checkbox ирнэ, single үед нэг radio.
        "correct_option": (
            domain.format_option_set(source.getlist("correct_options"))
            if qtype == "multi"
            else (source.get("correct_option") or "").strip().upper()
        ),
    }
    for key in domain.OPTION_KEYS:
        form[key] = (source.get(f"option_{key.lower()}") or "").strip()
    # Баруун талын хос зөвхөн харгалзуулах төрөлд, тэр нь 4 мөр хэвээр.
    for key in domain.MATCH_OPTION_KEYS:
        form[f"match_{key}"] = (
            (source.get(f"match_{key.lower()}") or "").strip()
            if qtype == "match" else ""
        )
    if qtype == "match":
        form["correct_option"] = ""
    return form


def _question_payload(form: dict) -> tuple:
    """Форм -> (options, matches) — database.py-д дамжуулах хэлбэр."""
    options = {k: form[k] for k in domain.OPTION_KEYS}
    matches = {k: (form[f"match_{k}"] or None) for k in domain.MATCH_OPTION_KEYS}
    return options, matches


def _blank_question_form() -> dict:
    form = {"qtype": "single", "text": "", "correct_option": "A", "score": "1"}
    for key in domain.OPTION_KEYS:
        form[key] = ""
    for key in domain.MATCH_OPTION_KEYS:
        form[f"match_{key}"] = ""
    return form


@app.route("/tests/<int:test_id>", methods=["GET", "POST"])
@login_required
def test_detail(test_id):
    test = owned_test(test_id)
    form = _blank_question_form()

    if request.method == "POST":
        form = _question_form(request.form)
        options, matches = _question_payload(form)
        errors = domain.validate_question(
            form["text"], options, form["correct_option"], form["score"],
            qtype=form["qtype"], matches=matches,
        )
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            db.create_question(
                g.conn, test_id, db.next_question_order(g.conn, test_id),
                form["text"], options, form["correct_option"], int(form["score"]),
                qtype=form["qtype"], matches=matches,
            )
            flash("Асуулт нэмэгдлээ.", "success")
            return redirect(url_for("test_detail", test_id=test_id))

    questions = db.list_questions(g.conn, test_id)
    share_url = url_for("student_start", share_code=test["share_code"], _external=True)

    # Гаралт руу хуулах товч дарж бичихээс өмнө анхааруулах эсэхийг
    # мэдэхийн тулд хосын гаралтад хэдэн дүн байгааг тоолно.
    pair_post_submitted = 0
    if test["kind"] == "pre" and test["pair_id"]:
        pair_post = db.get_pair_tests(g.conn, test["pair_id"]).get("post")
        if pair_post:
            pair_post_submitted = db.count_submitted_attempts(g.conn, pair_post["id"])

    # «Зөвхөн жагсаалтаас» горимд хэн ч орж чадахгүй байх эрсдэлийг
    # анхааруулахын тулд холбогдох бүлгүүдийн жагсаалтын нийт хэмжээ.
    groups = db.list_groups(g.conn, test["course_id"])
    relevant = ([gr for gr in groups if gr["id"] == test["class_group_id"]]
                if test["class_group_id"] else groups)
    roster_total = sum(len(db.list_students(g.conn, gr["id"])) for gr in relevant)

    return render_template(
        "test_detail.html", test=test, questions=questions, form=form,
        share_url=share_url, total_score=sum(q["score"] for q in questions),
        groups=groups,
        attempt_count=db.count_test_attempts(g.conn, test_id),
        pair_post_submitted=pair_post_submitted,
        roster_total=roster_total,
    )


@app.route("/questions/<int:question_id>/delete", methods=["POST"])
@login_required
def delete_question(question_id):
    question = db.get_question(g.conn, question_id)
    if not question:
        abort(404)
    owned_test(question["test_id"])
    db.delete_question(g.conn, question_id)
    db.renumber_questions(g.conn, question["test_id"])
    flash("Асуулт устлаа.", "success")
    return redirect(url_for("test_detail", test_id=question["test_id"]))


@app.route("/questions/<int:question_id>/edit", methods=["GET", "POST"])
@login_required
def edit_question(question_id):
    question = db.get_question(g.conn, question_id)
    if not question:
        abort(404)
    test = owned_test(question["test_id"])

    form = {
        "qtype": domain.question_type(question),
        "text": question["text"],
        "correct_option": question["correct_option"] or "",
        "score": str(question["score"]),
    }
    for key in domain.OPTION_KEYS:
        form[key] = question[f"option_{key.lower()}"]
        form[f"match_{key}"] = question.get(f"match_{key.lower()}") or ""

    if request.method == "POST":
        form = _question_form(request.form)
        options, matches = _question_payload(form)
        errors = domain.validate_question(
            form["text"], options, form["correct_option"], form["score"],
            qtype=form["qtype"], matches=matches,
        )
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            db.update_question(g.conn, question_id, form["text"], options,
                               form["correct_option"], int(form["score"]),
                               qtype=form["qtype"], matches=matches)
            flash("Асуулт шинэчлэгдлээ.", "success")
            return redirect(url_for("test_detail", test_id=test["id"]))

    return render_template("question_edit.html", test=test, question=question, form=form)


@app.route("/questions/<int:question_id>/move", methods=["POST"])
@login_required
def move_question(question_id):
    question = db.get_question(g.conn, question_id)
    if not question:
        abort(404)
    owned_test(question["test_id"])
    direction = (request.form.get("direction") or "").strip()
    if direction not in ("up", "down"):
        abort(400)
    if not db.swap_question_order(g.conn, question["test_id"], question_id, direction):
        flash("Асуулт аль хэдийн эхэнд/төгсгөлд байна.", "warning")
    return redirect(url_for("test_detail", test_id=question["test_id"]) + f"#q{question_id}")


# =====================================================================
# Үр дүн ба харьцуулалт
# =====================================================================
@app.route("/tests/<int:test_id>/results")
@login_required
def test_results(test_id):
    test = owned_test(test_id)
    results = db.list_results(g.conn, test_id)
    # Ижил групп + ижил кодоор өөр нэр бичигдсэн бол багшид анхааруулна.
    conflicts = domain.find_name_conflicts(results)
    for r in results:
        r["name_conflict"] = r["match_key"] in conflicts
        r["conflicting_names"] = conflicts.get(r["match_key"], [])
    percents = [r["percent"] for r in results]
    summary = {
        "count": len(results),
        "avg": round(sum(percents) / len(percents), 1) if percents else None,
        "max": max(percents) if percents else None,
        "min": min(percents) if percents else None,
    }
    return render_template("results.html", test=test, results=results, summary=summary,
                           conflicts=conflicts)


def _csv_response(header: list, rows: list, filename: str) -> Response:
    """
    Python-ий стандарт `csv` модулиар CSV үүсгэнэ.
    UTF-8 BOM (utf-8-sig) — Excel дээр монгол үсэг зөв нээгдэнэ.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    data = buffer.getvalue().encode("utf-8-sig")
    return Response(
        data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/tests/<int:test_id>/results.csv")
@login_required
def test_results_csv(test_id):
    test = owned_test(test_id)
    results = db.list_results(g.conn, test_id)
    conflicts = domain.find_name_conflicts(results)
    rows = [
        [r["full_name"], r["student_code"], r["normalized_student_code"], r["class_group_name"],
         r["email"] or "", r["total_score"], r["max_score"], r["percent"], r["submitted_at"],
         " / ".join(conflicts.get(r["match_key"], []))]
        for r in results
    ]
    return _csv_response(
        ["Овог нэр", "Оюутны код", "Хэвийн код", "Групп", "Имэйл (заавал бус)",
         "Авсан балл", "Нийт балл", "Хувь", "Илгээсэн", "Нэрийн зөрчил"],
        rows, f"{test['share_code']}-results.csv",
    )


def _pair_rows(pair_id: int):
    """
    Хосын Pre/Post мөрүүдийг тааруулж буцаана.
    Тааруулалт нь групп + хэвийн болгосон оюутны кодоор, ЗӨВХӨН энэ хосын дотор.
    """
    tests = db.get_pair_tests(g.conn, pair_id)
    pre = db.list_results(g.conn, tests["pre"]["id"]) if "pre" in tests else []
    post = db.list_results(g.conn, tests["post"]["id"]) if "post" in tests else []
    return domain.match_pre_post(pre, post, pair_id), tests


@app.route("/pairs/<int:pair_id>/comparison")
@login_required
def pair_comparison(pair_id):
    """
    Pre/Post шинжилгээ. Шүүлтүүр нь ЗӨВХӨН харагдах мөрийг багасгана —
    тааруулалтын логикт (domain.match_pre_post) ямар ч нөлөө үзүүлэхгүй.
    """
    pair = _owned_pair(pair_id)
    all_rows, tests = _pair_rows(pair_id)

    group = (request.args.get("group") or "").strip()
    status = (request.args.get("status") or "").strip()
    rows = all_rows
    if group:
        rows = [r for r in rows if r["class_group_name"] == group]
    if status in ("matched", "unmatched"):
        rows = [r for r in rows
                if (r["status"] == "matched") == (status == "matched")]

    groups = sorted({r["class_group_name"] for r in all_rows if r["class_group_name"]})
    summary = domain.comparison_summary(rows)

    # Chart.js-д зориулсан өгөгдөл. Хувийн мэдээлэл (имэйл) ОРУУЛАХГҮЙ.
    matched = [r for r in rows if r["status"] == "matched"]
    buckets = {"0-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}

    def bucket(value):
        if value <= 40:
            return "0-40"
        if value <= 60:
            return "41-60"
        if value <= 80:
            return "61-80"
        return "81-100"

    pre_dist = dict.fromkeys(buckets, 0)
    post_dist = dict.fromkeys(buckets, 0)
    for r in matched:
        pre_dist[bucket(r["pre_percent"])] += 1
        post_dist[bucket(r["post_percent"])] += 1

    chart = {
        "labels": [r["student_code"] for r in matched],
        "pre": [r["pre_percent"] for r in matched],
        "post": [r["post_percent"] for r in matched],
        "avg_pre": summary["avg_pre"] or 0,
        "avg_post": summary["avg_post"] or 0,
        "dist_labels": list(buckets.keys()),
        "dist_pre": [pre_dist[k] for k in buckets],
        "dist_post": [post_dist[k] for k in buckets],
        "improvement": [
            summary["improved_count"], summary["same_count"], summary["declined_count"],
        ],
    }

    return render_template(
        "comparison.html", pair=pair, tests=tests, rows=rows, summary=summary,
        groups=groups, filter_group=group, filter_status=status,
        total_rows=len(all_rows), chart=chart,
        # Имэйл товч харуулах эсэх, ямар хаяг руу явахыг УРЬДЧИЛАН
        # харуулахын тулд — багш дараад л мэдэх ёсгүй.
        mail_ready=mailer.is_configured(),
        mail_missing=mailer.missing_settings(),
        mail_to=(current_user() or {}).get("email"),
    )


@app.route("/pairs/<int:pair_id>/comparison.csv")
@login_required
def pair_comparison_csv(pair_id):
    pair = db.get_pair(g.conn, pair_id)
    if not pair:
        abort(404)
    owned_course(pair["course_id"])
    rows, _tests = _pair_rows(pair_id)
    status_mn = {"matched": "Тааруулсан", "pre_only": "Зөвхөн оролт", "post_only": "Зөвхөн гаралт"}
    csv_rows = [
        [r["full_name"], r["student_code"], r["match_key"], r["class_group_name"],
         r["email"] or "",
         r["pre_percent"] if r["pre_percent"] is not None else "",
         r["post_percent"] if r["post_percent"] is not None else "",
         r["delta_percent"] if r["delta_percent"] is not None else "",
         status_mn[r["status"]],
         " / ".join(r["conflicting_names"]) if r["name_conflict"] else ""]
        for r in rows
    ]
    return _csv_response(
        ["Овог нэр", "Оюутны код", "Тааруулах түлхүүр", "Групп", "Имэйл (заавал бус)",
         "Оролт %", "Гаралт %", "Ахиц %", "Төлөв", "Нэрийн зөрчил"],
        csv_rows, f"pair-{pair_id}-comparison.csv",
    )


# ---------------------------------------------------------------------
# Excel ба Word экспорт
# ---------------------------------------------------------------------
def _owned_pair(pair_id: int) -> dict:
    """Хосыг ачаалж эрхийг шалгана (эрхийн логик нэг газраас)."""
    pair = db.get_pair(g.conn, pair_id)
    if not pair:
        abort(404)
    owned_course(pair["course_id"])
    return pair


def _pair_group_label(tests: dict) -> str:
    """Хосын хоёр тестийн бүлгийн нэр — файлын нэр ба тайланд."""
    for kind in ("pre", "post"):
        test = tests.get(kind)
        if test:
            full = db.get_test(g.conn, test["id"])
            if full and full.get("class_group_name"):
                return full["class_group_name"]
    return ""


def _download(data: bytes, filename: str, mimetype: str) -> Response:
    return Response(
        data,
        mimetype=mimetype,
        headers={
            # RFC 6266: ASCII нэр + UTF-8 хувилбар (кирилл нэр зөв татагдана).
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{filename}",
            "Content-Length": str(len(data)),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.route("/pairs/<int:pair_id>/comparison.xlsx")
@login_required
def pair_comparison_xlsx(pair_id):
    pair = _owned_pair(pair_id)
    rows, tests = _pair_rows(pair_id)
    group_label = _pair_group_label(tests)
    data = exports.build_pair_workbook(pair, rows, domain.comparison_summary(rows), tests)
    filename = exports.build_filename(pair["course_code"], group_label, "PrePost", "xlsx")
    return _download(
        data, filename,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/pairs/<int:pair_id>/report.docx")
@login_required
def pair_report_docx(pair_id):
    pair = _owned_pair(pair_id)
    rows, tests = _pair_rows(pair_id)
    pair = dict(pair)
    pair["group_label"] = _pair_group_label(tests)
    data = exports.build_pair_report(
        pair, rows, domain.comparison_summary(rows),
        institution=os.environ.get("EDUTEST_INSTITUTION", "EduTest"),
    )
    filename = exports.build_filename(
        pair["course_code"], pair["group_label"], "PrePost_Report", "docx"
    )
    return _download(
        data, filename,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def _report_email_body(pair, summary) -> str:
    """Имэйлийн бие — хавсралт нээхээс өмнө гол тоог харуулна."""
    def pct(value):
        # format_credit нь 40.0 -> '40', 40.5 -> '40.5' болгоно.
        return "—" if value is None else f"{domain.format_credit(value)}%"

    lines = [
        f"{pair['course_name']} ({pair['course_code']})",
        f"Хос: {pair['name']}",
        f"Бүлэг: {pair['group_label']}",
        "",
        f"Тааруулсан оюутан : {summary['matched_count']}",
        f"Оролтын дундаж    : {pct(summary['avg_pre'])}",
        f"Гаралтын дундаж   : {pct(summary['avg_post'])}",
        f"Дундаж ахиц       : {pct(summary['avg_delta'])}",
        "",
        f"Ахисан  : {summary['improved_count']}",
        f"Буурсан : {summary['declined_count']}",
        f"Хэвээр  : {summary['same_count']}",
    ]
    if summary["pre_only_count"] or summary["post_only_count"]:
        lines += ["",
                  f"Зөвхөн Оролт өгсөн  : {summary['pre_only_count']}",
                  f"Зөвхөн Гаралт өгсөн : {summary['post_only_count']}"]
    if summary["conflict_count"]:
        lines += ["", f"АНХААР: {summary['conflict_count']} нэрийн зөрчил байна "
                      f"(ижил кодтой өөр нэр). Тайлангаас шалгана уу."]
    lines += ["", "Дэлгэрэнгүйг хавсаргасан Word тайлан ба Excel хүснэгтээс харна уу.",
              "", "— EduTest"]
    return "\n".join(lines)


@app.route("/pairs/<int:pair_id>/report.email", methods=["POST"])
@login_required
def email_pair_report(pair_id):
    """Word тайлан + Excel хүснэгтийг НЭВТЭРСЭН багшийн ӨӨРИЙН хаяг руу илгээнэ.

    Хүлээн авагчийг формоос АВАХГҮЙ — session дэх хэрэглэгчийн хаягийг
    хэрэглэнэ. Ингэснээр буруу хаяг руу оюутны өгөгдөл явах, мөн энэ
    хаягийг дурын хаяг руу файл илгээх суваг болгон ашиглах зам хаагдана.
    """
    pair = _owned_pair(pair_id)
    user = current_user()
    to = (user.get("email") or "").strip()
    if not to:
        flash("Таны бүртгэлд имэйл хаяг байхгүй байна.", "error")
        return redirect(url_for("pair_comparison", pair_id=pair_id))

    missing = mailer.missing_settings()
    if missing:
        flash("Имэйл илгээх тохиргоо хийгдээгүй байна: " + ", ".join(missing)
              + ". Серверийн орчны хувьсагчид нэмнэ үү.", "error")
        return redirect(url_for("pair_comparison", pair_id=pair_id))

    rows, tests = _pair_rows(pair_id)
    pair = dict(pair)
    pair["group_label"] = _pair_group_label(tests)
    summary = domain.comparison_summary(rows)

    docx = exports.build_pair_report(
        pair, rows, summary,
        institution=os.environ.get("EDUTEST_INSTITUTION", "EduTest"),
    )
    xlsx = exports.build_pair_workbook(pair, rows, summary, tests)
    base = exports.build_filename(pair["course_code"], pair["group_label"],
                                  "PrePost_Report", "docx")

    try:
        mailer.send(
            to,
            f"{pair['course_code']} — Оролт/Гаралтын тайлан ({pair['group_label']})",
            _report_email_body(pair, summary),
            attachments=[
                (base, docx,
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                (base[:-4] + "xlsx", xlsx,
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ],
        )
    except mailer.MailError as exc:
        _log(f"[имэйл] хос={pair_id} илгээлт амжилтгүй: {exc}")
        flash(f"Имэйл илгээгдсэнгүй. {exc}", "error")
        return redirect(url_for("pair_comparison", pair_id=pair_id))

    _log(f"[имэйл] хос={pair_id} -> {to} илгээгдлээ")
    flash(f"Тайлан {to} хаяг руу илгээгдлээ (Word + Excel хавсаргасан).", "success")
    return redirect(url_for("pair_comparison", pair_id=pair_id))


# =====================================================================
# НЭГТГЭСЭН ЖАГСААЛТУУД (хажуугийн цэсний үндсэн хуудсууд)
# =====================================================================
@app.route("/tests")
@login_required
def tests_index():
    """Бүх тест — хайлт/шүүлтүүр клиент талд ажиллана."""
    return render_template("tests_index.html", tests=db.list_all_tests(g.conn, _scope()))


@app.route("/pairs")
@login_required
def pairs_index():
    return render_template("pairs_index.html", pairs=db.list_all_pairs(g.conn, _scope()))


@app.route("/results")
@login_required
def results_index():
    return render_template("results_index.html",
                           tests=db.list_tests_with_attempts(g.conn, _scope()))


@app.route("/analytics")
@login_required
def analytics():
    """
    Шинжилгээний эхлэл — хосыг сонгоно. Дүн бүхий хос нэг л байвал
    шууд түүний харьцуулалт руу шилжинэ.
    """
    pairs = db.list_all_pairs(g.conn, _scope())
    with_data = [p for p in pairs if p["attempt_count"]]
    if len(with_data) == 1:
        return redirect(url_for("pair_comparison", pair_id=with_data[0]["id"]))
    return render_template("analytics_index.html", pairs=pairs)


# `exports` нэрийг модуль эзэлсэн тул функцийг өөрөөр нэрлэж,
# endpoint-ыг тодорхой зааж өгнө (url_for('exports') ажиллана).
@app.route("/exports", endpoint="exports")
@login_required
def exports_page():
    return render_template(
        "exports.html",
        pairs=db.list_all_pairs(g.conn, _scope()),
        tests=db.list_tests_with_attempts(g.conn, _scope()),
    )


# =====================================================================
# QR / хуваалцах хуудас
# =====================================================================
@app.route("/tests/<int:test_id>/share")
@login_required
def test_share(test_id):
    test = owned_test(test_id)
    share_url = url_for("student_start", share_code=test["share_code"], _external=True)
    return render_template(
        "share.html", test=test, share_url=share_url,
        question_count=len(db.list_questions(g.conn, test_id)),
    )


@app.route("/tests/<int:test_id>/qr.svg")
@login_required
def test_qr_svg(test_id):
    """
    QR кодыг SVG болгон буцаана — вектор тул хэдэн ч хэмжээгээр тод хэвлэгдэнэ.
    `qrcode` сан нь цэвэр Python, гадаад сүлжээ шаардахгүй.
    """
    test = owned_test(test_id)
    share_url = url_for("student_start", share_code=test["share_code"], _external=True)

    import qrcode
    import qrcode.image.svg as qrsvg

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(share_url)
    qr.make(fit=True)
    buffer = io.BytesIO()
    qr.make_image(image_factory=qrsvg.SvgPathImage).save(buffer)

    download = request.args.get("download") == "1"
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = (
            f'attachment; filename="{exports.safe_filename_part(test["share_code"])}-qr.svg"'
        )
    return Response(buffer.getvalue(), mimetype="image/svg+xml", headers=headers)


# =====================================================================
# Тестийн үйлдэл — засах, хувилах, устгах
# =====================================================================
@app.route("/tests/<int:test_id>/update", methods=["POST"])
@login_required
def update_test(test_id):
    test = owned_test(test_id)
    title = (request.form.get("title") or "").strip()
    group_raw = (request.form.get("class_group_id") or "").strip()
    if len(title) < 2:
        flash("Тестийн гарчгийг бүтнээр нь бичнэ үү.", "error")
        return redirect(url_for("test_detail", test_id=test_id))
    class_group_id = int(group_raw) if group_raw.isdigit() else None
    db.update_test(g.conn, test_id, title, class_group_id)
    flash("Тестийн мэдээлэл шинэчлэгдлээ.", "success")
    return redirect(url_for("test_detail", test_id=test_id))


@app.route("/tests/<int:test_id>/duplicate", methods=["POST"])
@login_required
def duplicate_test(test_id):
    """
    Тестийг асуултын хамт хуулна. Хуулбар нь ҮРГЭЛЖ «Ноорог» төлөвтэй,
    ШИНЭ share_code-той үүснэ — хуучин QR хуулбар руу заахгүй.
    Оролдлого, үр дүн ХУУЛАГДАХГҮЙ.
    """
    test = owned_test(test_id)
    new_id = db.create_test(
        g.conn, test["course_id"], None, test["class_group_id"],
        f"{test['title']} (хуулбар)", test["kind"], "draft",
        domain.generate_share_code(test["course_code"], test["kind"]), domain.now_iso(),
    )
    copied = db.copy_questions(g.conn, test_id, new_id)
    flash(f"Тест хуулагдлаа ({copied} асуулт). Хуулбар нь ноорог төлөвтэй байна.", "success")
    return redirect(url_for("test_detail", test_id=new_id))


@app.route("/tests/<int:test_id>/delete", methods=["POST"])
@login_required
def delete_test(test_id):
    """
    Тестийг устгана. Илгээгдсэн хариулт байвал ЗӨВШӨӨРӨХГҮЙ — оюутны
    өгөгдлийг санамсаргүй алдахаас сэргийлнэ (эхлээд «Хаах» хэрэглэнэ).
    """
    test = owned_test(test_id)
    if db.count_test_attempts(g.conn, test_id):
        flash("Хариулт ирсэн тестийг устгах боломжгүй. Оронд нь «Хаах» товчийг ашиглана уу.",
              "error")
        return redirect(url_for("test_detail", test_id=test_id))
    db.delete_test(g.conn, test_id)
    flash(f"«{test['title']}» тест устлаа.", "success")
    return redirect(url_for("tests_index"))


# =====================================================================
# Админ — хэрэглэгч удирдах
# =====================================================================
@app.route("/admin/backup")
@admin_required
def admin_backup():
    """Өгөгдлийн сангийн бүрэн бүтэн хуулбарыг татаж авна.

    Серверийн volume дээрх автомат нөөцлөлт нь санамсаргүй устгал,
    эвдрэлээс хамгаална — ГЭХДЭЭ volume өөрөө уствал нөөцлөлт нь хамт
    устана. Тиймээс гадагш хуулбар авах зам ЗААВАЛ хэрэгтэй.

    Ажиллаж байгаа файлыг шууд илгээхгүй: бичилтийн дундуур байвал
    хагас гүйлгээтэй, WAL горимд бүр дутуу хуулбар гарна. `backup.py`
    нь SQLite-ийн онлайн нөөцлөх API-аар бүрэн бүтэн хуулбар үүсгэдэг.
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            target = backup.make_backup(db.DB_PATH, pathlib.Path(tmp))
            backup.verify(target)
            data = target.read_bytes()
        except Exception as exc:            # noqa: BLE001
            _log(f"[нөөцлөлт] татаж авахад алдаа гарлаа: {exc!r}")
            flash("Нөөцлөлт үүсгэхэд алдаа гарлаа. Логоос шалтгааныг харна уу.",
                  "error")
            return redirect(url_for("admin_users"))

    stamp = domain.now_iso()[:19].replace(":", "").replace("-", "").replace("T", "-")
    return _download(data, f"edutest-{stamp}.db", "application/vnd.sqlite3")


@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        role = (request.form.get("role") or "teacher").strip()
        department = (request.form.get("department") or "").strip()
        errors = []
        if len(full_name) < 2:
            errors.append("Овог нэрийг бүтнээр нь бичнэ үү.")
        # Багш/админы имэйл нь НЭВТРЭХ нэр — оюутны тааруулалттай ямар ч холбоогүй.
        if not domain.is_valid_email(email):
            errors.append("Имэйл хаяг буруу байна.")
        elif db.get_user_by_email(g.conn, email):
            errors.append(f"«{email}» имэйл аль хэдийн бүртгэгдсэн байна.")
        if len(password) < 8:
            errors.append("Нууц үг хамгийн багадаа 8 тэмдэгт байх ёстой.")
        if role not in ("admin", "teacher"):
            errors.append("Эрхийн төрөл буруу байна.")
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            db.create_user(g.conn, full_name, email, domain.hash_password(password),
                           role, department, domain.now_iso())
            flash(f"«{full_name}» хэрэглэгч нэмэгдлээ.", "success")
        return redirect(url_for("admin_users"))
    # Хуваарьт нөөцлөлт ажилласан эсэхийг UI-аас харах боломж. Үүнгүйгээр
    # cron ажиллаж байгаа эсэхийг зөвхөн серверийн лог уншиж мэдэх байсан.
    backups = backup.list_backups()
    now = datetime.now(timezone.utc).timestamp()
    for row in backups:
        row["when"] = datetime.fromtimestamp(row["mtime"], timezone.utc) \
                              .strftime("%Y-%m-%d %H:%M UTC")
        row["age_hours"] = round((now - row["mtime"]) / 3600, 1)
    return render_template("admin_users.html", users=db.list_users(g.conn),
                           backups=backups, backup_dir=backup.backup_dir())


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    if user_id == session.get("user_id"):
        flash("Өөрийн бүртгэлээ устгах боломжгүй.", "error")
        return redirect(url_for("admin_users"))
    db.delete_user(g.conn, user_id)
    flash("Хэрэглэгч устлаа.", "success")
    return redirect(url_for("admin_users"))


# =====================================================================
# ОЮУТНЫ НИЙТИЙН ХЭСЭГ (нэвтрэлт шаардахгүй)
# =====================================================================
@app.route("/t/<share_code>", methods=["GET", "POST"])
def student_start(share_code):
    test = db.get_test_by_share_code(g.conn, share_code)
    if not test:
        return render_template("student_closed.html", reason="Ийм холбоостой тест олдсонгүй."), 404
    if test["status"] != "open":
        reason = ("Энэ тест хараахан нээгдээгүй байна."
                  if test["status"] == "draft" else "Энэ тест хаагдсан байна.")
        return render_template("student_closed.html", test=test, reason=reason), 403

    groups = db.list_groups_for_test(g.conn, test["id"])
    form = {"full_name": "", "email": "", "student_code": "", "class_group_id": ""}

    if request.method == "POST":
        form = {
            "full_name": (request.form.get("full_name") or "").strip(),
            "email": (request.form.get("email") or "").strip(),
            "student_code": (request.form.get("student_code") or "").strip(),
            "class_group_id": (request.form.get("class_group_id") or "").strip(),
        }
        errors = domain.validate_student_info(
            form["full_name"], form["email"], form["student_code"], form["class_group_id"]
        )
        if not db.list_questions(g.conn, test["id"]):
            errors.append("Энэ тестэд асуулт байхгүй байна. Багштайгаа холбогдоно уу.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("student_start.html", test=test, groups=groups, form=form), 400

        # ТААРУУЛАЛТЫН ТҮЛХҮҮР: групп + хэвийн болгосон оюутны код (имэйл ОРОЛЦОХГҮЙ).
        group_id = int(form["class_group_id"])
        norm_code = domain.normalize_student_code(form["student_code"])
        email = form["email"].strip() or None   # заавал биш, зөвхөн харуулах

        student = db.get_student_by_code(g.conn, group_id, norm_code)

        # «Зөвхөн жагсаалтаас» горимд бүртгэлгүй код нэвтрэхгүй. Ингэснээр
        # хуурамч кодоор дахин оролдох зам хаагдана.
        if not student and domain.entry_mode(test) == "roster":
            flash("Таны оюутны код энэ бүлгийн жагсаалтад бүртгэлгүй байна. "
                  "Кодоо шалгах эсвэл багштайгаа холбогдоно уу.", "error")
            return render_template("student_start.html", test=test, groups=groups,
                                   form=form), 403

        if student:
            # Нэр зөрвөл students.full_name-ийг ДАРЖ БИЧИХГҮЙ — чимээгүй нэгтгэхгүй.
            # Бичсэн нэрийг оролдлого дээр хадгалж, багшид зөрчил болгон харуулна.
            if domain.normalize_name(form["full_name"]) != domain.normalize_name(student["full_name"]):
                flash("Энэ группд ижил оюутны кодтой өөр нэр бүртгэлтэй байна. "
                      "Үр дүн хадгалагдана, гэхдээ багш нэрийг шалгах шаардлагатай.", "warning")
            db.update_student_email(g.conn, student["id"], email)
            student_id = student["id"]
        else:
            student_id = db.create_student(
                g.conn, group_id, form["full_name"], form["student_code"].strip(),
                norm_code, email, domain.now_iso(),
            )

        existing = db.find_attempt(g.conn, test["id"], student_id)
        if existing and existing["submitted_at"]:
            flash("Та энэ тестийг аль хэдийн өгсөн байна.", "warning")
            return redirect(url_for("student_result", attempt_id=existing["id"]))

        if existing:
            attempt_id = existing["id"]
        else:
            attempt_id = db.create_attempt(
                g.conn, test["id"], test["pair_id"], student_id,
                domain.build_match_key(group_id, norm_code),
                form["full_name"].strip(), domain.now_iso(),
            )
        session[f"attempt_{test['id']}"] = attempt_id
        return redirect(url_for("student_take", share_code=test["share_code"], attempt_id=attempt_id))

    return render_template("student_start.html", test=test, groups=groups, form=form)


@app.route("/t/<share_code>/take/<int:attempt_id>", methods=["GET", "POST"])
def student_take(share_code, attempt_id):
    test = db.get_test_by_share_code(g.conn, share_code)
    attempt = db.get_attempt(g.conn, attempt_id)
    if not test or not attempt or attempt["test_id"] != test["id"]:
        abort(404)
    if attempt["submitted_at"]:
        return redirect(url_for("student_result", attempt_id=attempt_id))
    if test["status"] != "open":
        return render_template("student_closed.html", test=test,
                               reason="Энэ тест хаагдсан байна."), 403

    questions = db.list_questions(g.conn, test["id"])

    if request.method == "POST":
        answers = {}
        missing = []
        for q in questions:
            qtype = domain.question_type(q)
            if qtype == "multi":
                picked = domain.format_option_set(request.form.getlist(f"q{q['id']}"))
            elif qtype == "match":
                # Зүүн үсэг бүрт нэг select: q<id>_A, q<id>_B ...
                mapping = {}
                for key in domain.OPTION_KEYS:
                    raw = (request.form.get(f"q{q['id']}_{key}") or "").strip()
                    if raw.isdigit():
                        mapping[key] = int(raw)
                picked = (domain.format_match_answer(mapping)
                          if len(mapping) == len(domain.OPTION_KEYS) else "")
            else:
                value = (request.form.get(f"q{q['id']}") or "").strip().upper()
                picked = value if value in domain.OPTION_KEYS else ""

            if picked:
                answers[q["id"]] = picked
            else:
                missing.append(q["order_no"])
        if missing:
            flash("Дараах асуултууд хариулагдаагүй байна: "
                  + ", ".join(str(n) for n in missing), "error")
            return render_template("student_take.html", test=test, attempt=attempt,
                                   questions=questions, picked=answers), 400

        # Автомат бодолт — бүхэлдээ domain.py дотор (өгөгдлийн сангаас хараат бус).
        result = domain.score_attempt(questions, answers)
        db.save_answers(g.conn, attempt_id, result["answers"])
        db.finish_attempt(g.conn, attempt_id, result["total_score"], result["max_score"],
                          result["percent"], domain.now_iso())
        return redirect(url_for("student_result", attempt_id=attempt_id))

    return render_template("student_take.html", test=test, attempt=attempt,
                           questions=questions, picked={})


@app.route("/r/<int:attempt_id>")
def student_result(attempt_id):
    attempt = db.get_attempt(g.conn, attempt_id)
    if not attempt or not attempt["submitted_at"]:
        abort(404)
    answers = db.list_answers(g.conn, attempt_id)
    correct_count = sum(1 for a in answers if a["is_correct"])
    # Асуулт тус бүрийн ✓/✕ нь ТЕСТ ХААГДСАНЫ ДАРАА л харагдана.
    #
    # Шалтгаан: оюутан хуурамч кодоор дахин орж болдог. Хэрэв аль асуулт
    # буруу байсныг шууд хэлж өгвөл нэг дахин оролдлогоор бүх зөв
    # хариултыг олох боломжтой болно. Хаагдсаны дараа тэр эрсдэл алга
    # болох тул сурах ач холбогдол нь үлдэнэ.
    #
    # `answers` дотор `correct_option` багана байгаа боловч загвар
    # түүнийг ХЭЗЭЭ Ч харуулахгүй.
    reveal = attempt["test_status"] == "closed"
    return render_template("student_result.html", attempt=attempt, answers=answers,
                           correct_count=correct_count,
                           wrong_count=len(answers) - correct_count,
                           reveal=reveal)


# =====================================================================
if __name__ == "__main__":
    # Debug горим ЗӨВХӨН EDUTEST_DEBUG=1 үед асна.
    # Production-д анхдагчаар унтраалттай (Werkzeug debugger нь кодыг
    # алсаас ажиллуулах боломж нээдэг тул нээлттэй сүлжээнд аюултай).
    debug = os.environ.get("EDUTEST_DEBUG") == "1"
    if app.config.get("EDUTEST_EPHEMERAL_SECRET"):
        print("АНХААРУУЛГА: EDUTEST_SECRET тохируулаагүй тул түр зуурын түлхүүр "
              "үүсгэлээ. Сервер дахин эхлэх бүрд нэвтрэлт унана. "
              "Байршуулахдаа EDUTEST_SECRET=<урт санамсаргүй мөр> гэж тохируулна уу.")
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", 5000)), debug=debug)
