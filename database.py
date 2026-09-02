# -*- coding: utf-8 -*-
"""
EduTest — өгөгдлийн сангийн ЦОРЫН ГАНЦ хил (data access boundary).

=======================================================================
POSTGRESQL РУУ ШИЛЖИХ ХИЛ
=======================================================================
app.py дотор ЯМАР Ч SQL байхгүй. Бүх SQL энэ файлд төвлөрсөн тул
серверт байрлуулахдаа зөвхөн энэ файлын ДЭЭД ХЭСГИЙГ (connect/placeholder)
солиход хангалттай:

  1) `connect()` — sqlite3.connect(...) -> psycopg.connect(DATABASE_URL)
  2) `_q()`      — SQLite `?` тэмдэглэгээг Postgres `%s` болгон хөрвүүлнэ
                   (энэ функц аль хэдийн бэлэн, DB_BACKEND=postgres үед идэвхжинэ)
  3) `insert_returning_id()` — SQLite `cursor.lastrowid` ->
                   Postgres `INSERT ... RETURNING id`
  4) schema.sql  -> schema_postgres.sql (тэнд төрлийн хөрвүүлэлт тайлбартай)

Доорх `fetch_all / fetch_one / execute` API нь өөрчлөгдөхгүй тул
дээрх 4 цэгээс гадна ямар ч код засварлах шаардлагагүй.
=======================================================================
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("EDUTEST_DB", BASE_DIR / "edutest.db"))
SCHEMA_PATH = BASE_DIR / "schema.sql"

# Ирээдүйд: EDUTEST_DB_BACKEND=postgres гэж тохируулаад connect()-ийг солино.
DB_BACKEND = os.environ.get("EDUTEST_DB_BACKEND", "sqlite")


# ---------------------------------------------------------------------
# Холболт
# ---------------------------------------------------------------------
def connect(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """
    Шинэ холболт нээнэ. Мөрүүд нь dict шиг индекслэгддэг (sqlite3.Row).

    POSTGRES: энд `psycopg.connect(os.environ['DATABASE_URL'],
              row_factory=psycopg.rows.dict_row)` болж солигдоно.
    """
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    # POSTGRES: гадаад түлхүүр үргэлж идэвхтэй тул энэ мөр хэрэггүй болно.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _q(sql: str) -> str:
    """
    Параметрийн тэмдэглэгээг backend-д тохируулна.
    SQLite: `?`   |   PostgreSQL: `%s`
    """
    return sql if DB_BACKEND == "sqlite" else sql.replace("?", "%s")


def fetch_all(conn, sql: str, params: tuple = ()) -> list:
    return [dict(r) for r in conn.execute(_q(sql), params).fetchall()]


def fetch_one(conn, sql: str, params: tuple = ()):
    row = conn.execute(_q(sql), params).fetchone()
    return dict(row) if row else None


def execute(conn, sql: str, params: tuple = ()):
    return conn.execute(_q(sql), params)


def insert_returning_id(conn, sql: str, params: tuple = ()) -> int:
    """
    INSERT хийгээд шинэ мөрийн id-г буцаана.
    POSTGRES: `sql + ' RETURNING id'` бичээд `cur.fetchone()['id']` авна.
    """
    cur = conn.execute(_q(sql), params)
    return int(cur.lastrowid)


def _columns(conn, table: str) -> set:
    return {row["name"] for row in fetch_all(conn, f"PRAGMA table_info({table})")}


def _table_sql(conn, table: str) -> str:
    row = fetch_one(conn, "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,))
    return (row or {}).get("sql") or ""


def _has_option_check(conn, table: str, column: str) -> bool:
    """
    Тухайн баганад A/B/C/D-ийн хуучин CHECK үлдсэн эсэх.

    `CREATE TABLE`-ийн текст нь зай, шинэ мөрөөр өөр өөр бичигдсэн байж
    болох тул бүх зайг арилгаад харьцуулна — эс бөгөөс миграц чимээгүй
    алгасагдаж, дараа нь INSERT дээр л алдаа гарна.
    """
    sql = re.sub(r"\s+", "", _table_sql(conn, table)).upper()
    return f"{column.upper()}IN('A','B','C','D')" in sql


def migrate(conn) -> list:
    """
    Хуучин өгөгдлийн санг шинэ схемд нийцүүлнэ. ДАХИН АЖИЛЛУУЛЖ БОЛНО
    (idempotent) — аль хэдийн хийгдсэн алхмыг алгасна.

    Ямар ч мөр УСТГАХГҮЙ. Одоо байгаа асуулт бүр `qtype='single'` болж,
    урьдын зан төлөв яг хэвээр үлдэнэ.

    Хийсэн алхмуудын жагсаалтыг буцаана (лог, тест, тайланд).
    """
    done = []

    # --- 1. questions: qtype болон match_* багана ---
    cols = _columns(conn, "questions")
    if cols:
        if "qtype" not in cols:
            execute(conn, "ALTER TABLE questions ADD COLUMN qtype TEXT NOT NULL DEFAULT 'single'")
            done.append("questions.qtype нэмэв")
        for key in ("a", "b", "c", "d"):
            if f"match_{key}" not in cols:
                execute(conn, f"ALTER TABLE questions ADD COLUMN match_{key} TEXT")
                done.append(f"questions.match_{key} нэмэв")

    # --- 2. questions.correct_option-ийн CHECK-ийг арилгах ---
    # SQLite-д CHECK-ийг ALTER-ээр өөрчлөх боломжгүй тул хүснэгтийг
    # дахин барина. Өгөгдлийг бүтнээр нь хуулна.
    if _has_option_check(conn, "questions", "correct_option"):
        execute(conn, "PRAGMA foreign_keys = OFF")
        execute(conn, """
            CREATE TABLE questions__new (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id        INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
                order_no       INTEGER NOT NULL,
                text           TEXT NOT NULL,
                qtype          TEXT NOT NULL DEFAULT 'single'
                                    CHECK (qtype IN ('single', 'multi', 'match')),
                option_a       TEXT NOT NULL,
                option_b       TEXT NOT NULL,
                option_c       TEXT NOT NULL,
                option_d       TEXT NOT NULL,
                match_a        TEXT,
                match_b        TEXT,
                match_c        TEXT,
                match_d        TEXT,
                correct_option TEXT NOT NULL DEFAULT '',
                score          INTEGER NOT NULL DEFAULT 1 CHECK (score > 0)
            )""")
        execute(conn, """
            INSERT INTO questions__new
                (id, test_id, order_no, text, qtype, option_a, option_b, option_c,
                 option_d, match_a, match_b, match_c, match_d, correct_option, score)
            SELECT id, test_id, order_no, text,
                   COALESCE(qtype, 'single'),
                   option_a, option_b, option_c, option_d,
                   match_a, match_b, match_c, match_d,
                   correct_option, score
              FROM questions""")
        execute(conn, "DROP TABLE questions")
        execute(conn, "ALTER TABLE questions__new RENAME TO questions")
        execute(conn, "CREATE INDEX IF NOT EXISTS idx_questions_test ON questions(test_id, order_no)")
        execute(conn, "PRAGMA foreign_keys = ON")
        done.append("questions.correct_option-ийн CHECK-ийг арилгав")

    # --- 3. answers.selected_option-ийн CHECK-ийг арилгах ---
    if _has_option_check(conn, "answers", "selected_option"):
        execute(conn, "PRAGMA foreign_keys = OFF")
        execute(conn, """
            CREATE TABLE answers__new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id      INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
                question_id     INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                selected_option TEXT,
                is_correct      INTEGER NOT NULL DEFAULT 0,
                earned_score    INTEGER NOT NULL DEFAULT 0,
                UNIQUE (attempt_id, question_id)
            )""")
        execute(conn, """
            INSERT INTO answers__new
                (id, attempt_id, question_id, selected_option, is_correct, earned_score)
            SELECT id, attempt_id, question_id, selected_option, is_correct, earned_score
              FROM answers""")
        execute(conn, "DROP TABLE answers")
        execute(conn, "ALTER TABLE answers__new RENAME TO answers")
        execute(conn, "CREATE INDEX IF NOT EXISTS idx_answers_attempt ON answers(attempt_id)")
        execute(conn, "PRAGMA foreign_keys = ON")
        done.append("answers.selected_option-ийн CHECK-ийг арилгав")

    # --- 4. questions.option_e — 5 дахь сонголт ---
    # Дээрх хүснэгт дахин барих алхмууд option_e-гүй хуулбар үүсгэдэг тул
    # энэ алхам ЗААВАЛ тэднээс хойш байх ёстой, эс бөгөөс багана алга болно.
    # Харгалзуулах төрөл 4 мөр хэвээр учир match_e НЭМЭХГҮЙ.
    cols = _columns(conn, "questions")
    if cols and "option_e" not in cols:
        execute(conn, "ALTER TABLE questions ADD COLUMN option_e TEXT NOT NULL DEFAULT ''")
        done.append("questions.option_e нэмэв")

    conn.commit()
    return done


# ---------------------------------------------------------------------
# Схем үүсгэх
# ---------------------------------------------------------------------
def init_db(db_path: str | os.PathLike | None = None, *, drop_existing: bool = False) -> None:
    """schema.sql-ийг ажиллуулж хүснэгтүүдийг үүсгэнэ (байхгүй бол),
    дараа нь хуучин сангийн миграцийг ажиллуулна."""
    target = Path(db_path or DB_PATH)
    if drop_existing and target.exists():
        target.unlink()
    conn = connect(target)
    try:
        # POSTGRES: executescript -> conn.execute(schema_sql) (psycopg олон
        # мэдэгдлийг нэг дор дэмждэг).
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        migrate(conn)
    finally:
        conn.close()


# =====================================================================
# ХЭРЭГЛЭГЧ (багш / админ)
# =====================================================================
def get_user_by_email(conn, email: str):
    return fetch_one(conn, "SELECT * FROM users WHERE lower(email) = lower(?)", (email,))


def get_user(conn, user_id: int):
    return fetch_one(conn, "SELECT * FROM users WHERE id = ?", (user_id,))


def list_users(conn) -> list:
    return fetch_all(conn, "SELECT * FROM users ORDER BY role, full_name")


def create_user(conn, full_name, email, password_hash, role, department, created_at) -> int:
    return insert_returning_id(
        conn,
        """INSERT INTO users (full_name, email, password_hash, role, department, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (full_name, email, password_hash, role, department, created_at),
    )


def update_user_password(conn, user_id: int, password_hash: str) -> None:
    execute(conn, "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))


def delete_user(conn, user_id: int) -> None:
    execute(conn, "DELETE FROM users WHERE id = ?", (user_id,))


# =====================================================================
# ХИЧЭЭЛ
# =====================================================================
def list_courses(conn, teacher_id: int | None = None) -> list:
    """teacher_id=None бол бүх хичээл (админ эрх)."""
    sql = """SELECT c.*, u.full_name AS teacher_name,
                    (SELECT COUNT(*) FROM tests t WHERE t.course_id = c.id) AS test_count,
                    (SELECT COUNT(*) FROM class_groups g WHERE g.course_id = c.id) AS group_count
             FROM courses c JOIN users u ON u.id = c.teacher_id"""
    if teacher_id is None:
        return fetch_all(conn, sql + " ORDER BY c.code")
    return fetch_all(conn, sql + " WHERE c.teacher_id = ? ORDER BY c.code", (teacher_id,))


def get_course(conn, course_id: int):
    return fetch_one(
        conn,
        """SELECT c.*, u.full_name AS teacher_name
           FROM courses c JOIN users u ON u.id = c.teacher_id WHERE c.id = ?""",
        (course_id,),
    )


def get_course_by_code(conn, code: str):
    return fetch_one(conn, "SELECT * FROM courses WHERE lower(code) = lower(?)", (code,))


def create_course(conn, teacher_id, name, code, credit, semester, created_at) -> int:
    return insert_returning_id(
        conn,
        """INSERT INTO courses (teacher_id, name, code, credit, semester, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (teacher_id, name, code, credit, semester, created_at),
    )


def course_delete_summary(conn, course_id: int) -> dict:
    """Хичээл устгахад ХАМТ устах мөрүүдийн тоо.

    Баталгаажуулах хуудсанд харуулна — админ юу алдахаа урьдчилан
    харах ёстой. Cascade нь courses -> class_groups -> students ->
    attempts -> answers, мөн courses -> tests -> questions гэж явдаг.
    """
    row = fetch_one(conn, """
        SELECT
          (SELECT COUNT(*) FROM class_groups WHERE course_id = ?) AS group_count,
          (SELECT COUNT(*) FROM test_pairs   WHERE course_id = ?) AS pair_count,
          (SELECT COUNT(*) FROM tests        WHERE course_id = ?) AS test_count,
          (SELECT COUNT(*) FROM students s
             JOIN class_groups cg ON cg.id = s.class_group_id
            WHERE cg.course_id = ?)                              AS student_count,
          (SELECT COUNT(*) FROM questions q
             JOIN tests t ON t.id = q.test_id
            WHERE t.course_id = ?)                               AS question_count,
          (SELECT COUNT(*) FROM attempts a
             JOIN tests t ON t.id = a.test_id
            WHERE t.course_id = ?)                               AS attempt_count
    """, (course_id,) * 6)
    return {k: int(v or 0) for k, v in (row or {}).items()}


def delete_course(conn, course_id: int) -> None:
    execute(conn, "DELETE FROM courses WHERE id = ?", (course_id,))


# =====================================================================
# АНГИЙН ГРУПП
# =====================================================================
def list_groups(conn, course_id: int) -> list:
    return fetch_all(
        conn, "SELECT * FROM class_groups WHERE course_id = ? ORDER BY name", (course_id,)
    )


def list_groups_for_test(conn, test_id: int) -> list:
    """Оюутны бүртгэлийн формд харагдах группүүд — тестийн хичээлээр."""
    return fetch_all(
        conn,
        """SELECT g.* FROM class_groups g
           JOIN tests t ON t.course_id = g.course_id
           WHERE t.id = ? ORDER BY g.name""",
        (test_id,),
    )


def get_group(conn, group_id: int):
    return fetch_one(conn, "SELECT * FROM class_groups WHERE id = ?", (group_id,))


def create_group(conn, course_id, name, student_count, created_at) -> int:
    return insert_returning_id(
        conn,
        "INSERT INTO class_groups (course_id, name, student_count, created_at) VALUES (?, ?, ?, ?)",
        (course_id, name, student_count, created_at),
    )


def delete_group(conn, group_id: int) -> None:
    execute(conn, "DELETE FROM class_groups WHERE id = ?", (group_id,))


# =====================================================================
# ТЕСТ БА ХОС
# =====================================================================
def create_pair(conn, course_id, name, created_at) -> int:
    return insert_returning_id(
        conn,
        "INSERT INTO test_pairs (course_id, name, created_at) VALUES (?, ?, ?)",
        (course_id, name, created_at),
    )


def get_pair(conn, pair_id: int):
    return fetch_one(
        conn,
        """SELECT p.*, c.name AS course_name, c.code AS course_code, c.teacher_id
           FROM test_pairs p JOIN courses c ON c.id = p.course_id WHERE p.id = ?""",
        (pair_id,),
    )


def list_pairs(conn, course_id: int) -> list:
    return fetch_all(
        conn,
        """SELECT p.*,
                  (SELECT id    FROM tests t WHERE t.pair_id = p.id AND t.kind = 'pre')  AS pre_test_id,
                  (SELECT title FROM tests t WHERE t.pair_id = p.id AND t.kind = 'pre')  AS pre_title,
                  (SELECT id    FROM tests t WHERE t.pair_id = p.id AND t.kind = 'post') AS post_test_id,
                  (SELECT title FROM tests t WHERE t.pair_id = p.id AND t.kind = 'post') AS post_title
           FROM test_pairs p WHERE p.course_id = ? ORDER BY p.created_at DESC""",
        (course_id,),
    )


def get_pair_tests(conn, pair_id: int) -> dict:
    rows = fetch_all(conn, "SELECT * FROM tests WHERE pair_id = ?", (pair_id,))
    return {r["kind"]: r for r in rows}


def create_test(conn, course_id, pair_id, class_group_id, title, kind, status, share_code, created_at) -> int:
    return insert_returning_id(
        conn,
        """INSERT INTO tests (course_id, pair_id, class_group_id, title, kind, status, share_code, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (course_id, pair_id, class_group_id, title, kind, status, share_code, created_at),
    )


def list_tests(conn, course_id: int) -> list:
    return fetch_all(
        conn,
        """SELECT t.*, g.name AS class_group_name, p.name AS pair_name,
                  (SELECT COUNT(*) FROM questions q WHERE q.test_id = t.id) AS question_count,
                  (SELECT COUNT(*) FROM attempts a WHERE a.test_id = t.id
                                     AND a.submitted_at IS NOT NULL) AS attempt_count
           FROM tests t
           LEFT JOIN class_groups g ON g.id = t.class_group_id
           LEFT JOIN test_pairs  p ON p.id = t.pair_id
           WHERE t.course_id = ? ORDER BY t.kind, t.created_at DESC""",
        (course_id,),
    )


def get_test(conn, test_id: int):
    return fetch_one(
        conn,
        """SELECT t.*, c.name AS course_name, c.code AS course_code, c.teacher_id,
                  g.name AS class_group_name, p.name AS pair_name
           FROM tests t
           JOIN courses c ON c.id = t.course_id
           LEFT JOIN class_groups g ON g.id = t.class_group_id
           LEFT JOIN test_pairs  p ON p.id = t.pair_id
           WHERE t.id = ?""",
        (test_id,),
    )


def get_test_by_share_code(conn, share_code: str):
    return fetch_one(
        conn,
        """SELECT t.*, c.name AS course_name, c.code AS course_code
           FROM tests t JOIN courses c ON c.id = t.course_id
           WHERE upper(t.share_code) = upper(?)""",
        (share_code,),
    )


def set_test_status(conn, test_id: int, status: str) -> None:
    execute(conn, "UPDATE tests SET status = ? WHERE id = ?", (status, test_id))


def delete_test(conn, test_id: int) -> None:
    execute(conn, "DELETE FROM tests WHERE id = ?", (test_id,))


# =====================================================================
# АСУУЛТ
# =====================================================================
def list_questions(conn, test_id: int) -> list:
    return fetch_all(
        conn, "SELECT * FROM questions WHERE test_id = ? ORDER BY order_no, id", (test_id,)
    )


def next_question_order(conn, test_id: int) -> int:
    row = fetch_one(conn, "SELECT COALESCE(MAX(order_no), 0) AS mx FROM questions WHERE test_id = ?", (test_id,))
    return int(row["mx"]) + 1


def create_question(conn, test_id, order_no, text, options, correct_option, score,
                    qtype: str = "single", matches: dict | None = None) -> int:
    """
    Асуулт үүсгэнэ.

    `qtype` ба `matches` анхдагч утгатай тул ХУУЧИН дуудлагууд
    (seed.py, тестүүд) өөрчлөлтгүй ажиллана — тэдгээр нь 'single' болно.
    """
    matches = matches or {}
    return insert_returning_id(
        conn,
        """INSERT INTO questions (test_id, order_no, text, qtype,
                                  option_a, option_b, option_c, option_d, option_e,
                                  match_a, match_b, match_c, match_d,
                                  correct_option, score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            test_id, order_no, text, qtype,
            # .get(...) — харгалзуулах асуулт E-гүй ирдэг, мөн хуучин
            # дуудлагууд (seed.py, тестүүд) зөвхөн A-D дамжуулдаг.
            options.get("A") or "", options.get("B") or "", options.get("C") or "",
            options.get("D") or "", options.get("E") or "",
            matches.get("A"), matches.get("B"), matches.get("C"), matches.get("D"),
            correct_option, score,
        ),
    )


def delete_question(conn, question_id: int) -> None:
    execute(conn, "DELETE FROM questions WHERE id = ?", (question_id,))


def get_question(conn, question_id: int):
    return fetch_one(conn, "SELECT * FROM questions WHERE id = ?", (question_id,))


# =====================================================================
# ОЮУТАН
# =====================================================================
# Таних түлхүүр: (class_group_id, normalized_student_code) — схемд UNIQUE.
# Имэйл нь түлхүүр БИШ, заавал ч биш.
def get_student_by_code(conn, class_group_id: int, normalized_student_code: str):
    return fetch_one(
        conn,
        """SELECT * FROM students
           WHERE class_group_id = ? AND normalized_student_code = ?""",
        (class_group_id, normalized_student_code),
    )


def get_student(conn, student_id: int):
    return fetch_one(conn, "SELECT * FROM students WHERE id = ?", (student_id,))


def create_student(conn, class_group_id, full_name, student_code,
                   normalized_student_code, email, created_at) -> int:
    return insert_returning_id(
        conn,
        """INSERT INTO students (class_group_id, full_name, student_code,
                                 normalized_student_code, email, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (class_group_id, full_name, student_code, normalized_student_code, email, created_at),
    )


def update_student_email(conn, student_id: int, email: str | None) -> None:
    """
    Имэйл нь зөвхөн ХАРУУЛАХ талбар тул хамгийн сүүлд бичсэнээр шинэчилж болно.
    Нэрийг энд ХЭЗЭЭ Ч дарж бичихгүй — зөрчилтэй нэрийг чимээгүй нэгтгэхгүй
    (attempts.entered_full_name дээр тэмдэглэгдэж, багшид зөрчил болж харагдана).
    """
    if email:
        execute(conn, "UPDATE students SET email = ? WHERE id = ?", (email, student_id))


# =====================================================================
# ОРОЛДЛОГО БА ХАРИУЛТ
# =====================================================================
def get_attempt(conn, attempt_id: int):
    return fetch_one(
        conn,
        """SELECT a.*, s.full_name, s.email, s.student_code, s.normalized_student_code,
                  t.title AS test_title, t.kind AS test_kind, t.share_code,
                  t.status AS test_status
           FROM attempts a
           JOIN students s ON s.id = a.student_id
           JOIN tests    t ON t.id = a.test_id
           WHERE a.id = ?""",
        (attempt_id,),
    )


def find_attempt(conn, test_id: int, student_id: int):
    return fetch_one(
        conn, "SELECT * FROM attempts WHERE test_id = ? AND student_id = ?", (test_id, student_id)
    )


def create_attempt(conn, test_id, test_pair_id, student_id, match_key,
                   entered_full_name, started_at) -> int:
    return insert_returning_id(
        conn,
        """INSERT INTO attempts (test_id, test_pair_id, student_id, match_key,
                                 entered_full_name, started_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (test_id, test_pair_id, student_id, match_key, entered_full_name, started_at),
    )


def finish_attempt(conn, attempt_id, total_score, max_score, percent, submitted_at) -> None:
    execute(
        conn,
        """UPDATE attempts SET total_score = ?, max_score = ?, percent = ?, submitted_at = ?
           WHERE id = ?""",
        (total_score, max_score, percent, submitted_at, attempt_id),
    )


def save_answers(conn, attempt_id: int, graded_answers: list) -> None:
    execute(conn, "DELETE FROM answers WHERE attempt_id = ?", (attempt_id,))
    for a in graded_answers:
        execute(
            conn,
            """INSERT INTO answers (attempt_id, question_id, selected_option, is_correct, earned_score)
               VALUES (?, ?, ?, ?, ?)""",
            (attempt_id, a["question_id"], a["selected_option"], 1 if a["is_correct"] else 0, a["earned_score"]),
        )


def list_answers(conn, attempt_id: int) -> list:
    return fetch_all(
        conn,
        """SELECT ans.*, q.text, q.correct_option, q.order_no, q.score AS question_score
           FROM answers ans JOIN questions q ON q.id = ans.question_id
           WHERE ans.attempt_id = ? ORDER BY q.order_no""",
        (attempt_id,),
    )


def list_results(conn, test_id: int) -> list:
    """
    Нэг тестийн бүх дуусгасан оролдлого — үр дүнгийн хүснэгт ба Pre/Post тааруулалтад.
    match_key, test_pair_id, entered_full_name нь тааруулалт ба нэрийн зөрчил
    илрүүлэхэд шаардлагатай (domain.match_pre_post / find_name_conflicts).
    """
    return fetch_all(
        conn,
        """SELECT a.id AS attempt_id, a.match_key, a.test_pair_id, a.entered_full_name,
                  a.percent, a.total_score, a.max_score, a.submitted_at,
                  s.id AS student_id, s.full_name, s.email,
                  s.student_code, s.normalized_student_code,
                  g.id AS class_group_id, g.name AS class_group_name
           FROM attempts a
           JOIN students s ON s.id = a.student_id
           JOIN class_groups g ON g.id = s.class_group_id
           WHERE a.test_id = ? AND a.submitted_at IS NOT NULL
           ORDER BY a.percent DESC, s.student_code""",
        (test_id,),
    )


# =====================================================================
# НЭГТГЭСЭН ЖАГСААЛТ ба ХЯНАЛТЫН САМБАРЫН ҮЗҮҮЛЭЛТ
# ---------------------------------------------------------------------
# Бүгд `teacher_id` параметртэй: None бол бүх багшийн өгөгдөл (админ),
# өөр тохиолдолд зөвхөн тухайн багшийн хичээлүүд.
# Эрхийн шалгалт app.py дээр хийгдэнэ — энд зөвхөн шүүлт.
# =====================================================================
def _own(teacher_id, alias="c"):
    """(SQL хэлтэрхий, параметр) — багшийн шүүлтийг нэг мөрөөр нэмнэ."""
    if teacher_id is None:
        return "", ()
    return f" AND {alias}.teacher_id = ?", (teacher_id,)


def list_all_tests(conn, teacher_id: int | None = None) -> list:
    """Бүх тест — хичээл, групп, хос, асуулт/оролдлогын тоотой."""
    where, params = _own(teacher_id)
    return fetch_all(
        conn,
        f"""SELECT t.*, c.name AS course_name, c.code AS course_code, c.teacher_id,
                   u.full_name AS teacher_name,
                   g.name AS class_group_name, p.name AS pair_name,
                   (SELECT COUNT(*) FROM questions q WHERE q.test_id = t.id) AS question_count,
                   (SELECT COUNT(*) FROM attempts a WHERE a.test_id = t.id
                                     AND a.submitted_at IS NOT NULL) AS attempt_count
            FROM tests t
            JOIN courses c ON c.id = t.course_id
            JOIN users   u ON u.id = c.teacher_id
            LEFT JOIN class_groups g ON g.id = t.class_group_id
            LEFT JOIN test_pairs  p ON p.id = t.pair_id
            WHERE 1 = 1{where}
            ORDER BY t.created_at DESC, t.id DESC""",
        params,
    )


def list_all_pairs(conn, teacher_id: int | None = None) -> list:
    """Бүх Pre/Post хос — хичээл ба хоёр тестийн товч мэдээлэлтэй."""
    where, params = _own(teacher_id)
    return fetch_all(
        conn,
        f"""SELECT p.*, c.name AS course_name, c.code AS course_code, c.teacher_id,
                   (SELECT id     FROM tests t WHERE t.pair_id = p.id AND t.kind = 'pre')  AS pre_test_id,
                   (SELECT title  FROM tests t WHERE t.pair_id = p.id AND t.kind = 'pre')  AS pre_title,
                   (SELECT status FROM tests t WHERE t.pair_id = p.id AND t.kind = 'pre')  AS pre_status,
                   (SELECT id     FROM tests t WHERE t.pair_id = p.id AND t.kind = 'post') AS post_test_id,
                   (SELECT title  FROM tests t WHERE t.pair_id = p.id AND t.kind = 'post') AS post_title,
                   (SELECT status FROM tests t WHERE t.pair_id = p.id AND t.kind = 'post') AS post_status,
                   (SELECT COUNT(*) FROM attempts a
                     WHERE a.test_pair_id = p.id AND a.submitted_at IS NOT NULL) AS attempt_count
            FROM test_pairs p
            JOIN courses c ON c.id = p.course_id
            WHERE 1 = 1{where}
            ORDER BY p.created_at DESC, p.id DESC""",
        params,
    )


def dashboard_stats(conn, teacher_id: int | None = None, *, today: str = "") -> dict:
    """
    Хяналтын самбарын нэгтгэсэн тоонууд.
    `today` нь 'YYYY-MM-DD' хэлбэрийн мөр — өнөөдрийн оролдлогыг тоолоход.
    """
    where, params = _own(teacher_id)

    def scalar(sql, extra=()):
        row = fetch_one(conn, sql, params + extra)
        return (row or {}).get("v") or 0

    stats = {
        "courses": scalar(f"SELECT COUNT(*) AS v FROM courses c WHERE 1 = 1{where}"),
        "groups": scalar(
            f"""SELECT COUNT(*) AS v FROM class_groups g
                JOIN courses c ON c.id = g.course_id WHERE 1 = 1{where}"""),
        "tests": scalar(
            f"""SELECT COUNT(*) AS v FROM tests t
                JOIN courses c ON c.id = t.course_id WHERE 1 = 1{where}"""),
        "open_tests": scalar(
            f"""SELECT COUNT(*) AS v FROM tests t
                JOIN courses c ON c.id = t.course_id
                WHERE t.status = 'open'{where}"""),
        "pairs": scalar(
            f"""SELECT COUNT(*) AS v FROM test_pairs p
                JOIN courses c ON c.id = p.course_id WHERE 1 = 1{where}"""),
        "attempts": scalar(
            f"""SELECT COUNT(*) AS v FROM attempts a
                JOIN tests t ON t.id = a.test_id
                JOIN courses c ON c.id = t.course_id
                WHERE a.submitted_at IS NOT NULL{where}"""),
        "participants": scalar(
            f"""SELECT COUNT(DISTINCT a.match_key) AS v FROM attempts a
                JOIN tests t ON t.id = a.test_id
                JOIN courses c ON c.id = t.course_id
                WHERE a.submitted_at IS NOT NULL{where}"""),
        "today": 0,
    }

    if today:
        row = fetch_one(
            conn,
            f"""SELECT COUNT(*) AS v FROM attempts a
                JOIN tests t ON t.id = a.test_id
                JOIN courses c ON c.id = t.course_id
                WHERE a.submitted_at IS NOT NULL
                  AND substr(a.submitted_at, 1, 10) = ?{where}""",
            (today,) + params,
        )
        stats["today"] = (row or {}).get("v") or 0

    # Оролт/Гаралтын дундаж хувь (зөвхөн илгээсэн оролдлого).
    for kind in ("pre", "post"):
        row = fetch_one(
            conn,
            f"""SELECT AVG(a.percent) AS v FROM attempts a
                JOIN tests t ON t.id = a.test_id
                JOIN courses c ON c.id = t.course_id
                WHERE a.submitted_at IS NOT NULL AND t.kind = ?{where}""",
            (kind,) + params,
        )
        value = (row or {}).get("v")
        stats[f"avg_{kind}"] = round(value, 1) if value is not None else None

    if stats["avg_pre"] is not None and stats["avg_post"] is not None:
        stats["avg_delta"] = round(stats["avg_post"] - stats["avg_pre"], 1)
    else:
        stats["avg_delta"] = None
    return stats


def recent_attempts(conn, teacher_id: int | None = None, limit: int = 8) -> list:
    """Сүүлд илгээгдсэн оролдлогууд."""
    where, params = _own(teacher_id)
    return fetch_all(
        conn,
        f"""SELECT a.id, a.percent, a.total_score, a.max_score, a.submitted_at,
                   a.entered_full_name, s.student_code, g.name AS class_group_name,
                   t.id AS test_id, t.title AS test_title, t.kind AS test_kind
            FROM attempts a
            JOIN students s ON s.id = a.student_id
            JOIN class_groups g ON g.id = s.class_group_id
            JOIN tests t ON t.id = a.test_id
            JOIN courses c ON c.id = t.course_id
            WHERE a.submitted_at IS NOT NULL{where}
            ORDER BY a.submitted_at DESC LIMIT ?""",
        params + (limit,),
    )


def list_tests_with_attempts(conn, teacher_id: int | None = None) -> list:
    """Хариулт ирсэн тестүүд — «Үр дүн» хуудсанд."""
    return [t for t in list_all_tests(conn, teacher_id) if t["attempt_count"] > 0]


# =====================================================================
# АСУУЛТ ЗАСВАРЛАХ ба ЭРЭМБЭЛЭХ
# =====================================================================
def update_question(conn, question_id, text, options, correct_option, score,
                    qtype: str = "single", matches: dict | None = None) -> None:
    matches = matches or {}
    execute(
        conn,
        """UPDATE questions
              SET text = ?, qtype = ?,
                  option_a = ?, option_b = ?, option_c = ?, option_d = ?,
                  option_e = ?,
                  match_a = ?, match_b = ?, match_c = ?, match_d = ?,
                  correct_option = ?, score = ?
            WHERE id = ?""",
        (text, qtype,
         options.get("A") or "", options.get("B") or "", options.get("C") or "",
         options.get("D") or "", options.get("E") or "",
         matches.get("A"), matches.get("B"), matches.get("C"), matches.get("D"),
         correct_option, score, question_id),
    )


def swap_question_order(conn, test_id: int, question_id: int, direction: str) -> bool:
    """
    Асуултыг нэг байрлалаар дээш/доош зөөнө.
    Хөрш асуулттай order_no-г солино. Хөрш байхгүй бол False буцаана.
    """
    current = get_question(conn, question_id)
    if not current or current["test_id"] != test_id:
        return False
    if direction == "up":
        neighbour = fetch_one(
            conn,
            """SELECT * FROM questions WHERE test_id = ? AND order_no < ?
               ORDER BY order_no DESC, id DESC LIMIT 1""",
            (test_id, current["order_no"]),
        )
    else:
        neighbour = fetch_one(
            conn,
            """SELECT * FROM questions WHERE test_id = ? AND order_no > ?
               ORDER BY order_no ASC, id ASC LIMIT 1""",
            (test_id, current["order_no"]),
        )
    if not neighbour:
        return False
    execute(conn, "UPDATE questions SET order_no = ? WHERE id = ?",
            (neighbour["order_no"], current["id"]))
    execute(conn, "UPDATE questions SET order_no = ? WHERE id = ?",
            (current["order_no"], neighbour["id"]))
    return True


def renumber_questions(conn, test_id: int) -> None:
    """order_no-г 1..N болгон цэгцэлнэ (устгасны дараа завсар үлдэхээс сэргийлнэ)."""
    for index, q in enumerate(list_questions(conn, test_id), start=1):
        if q["order_no"] != index:
            execute(conn, "UPDATE questions SET order_no = ? WHERE id = ?", (index, q["id"]))


def delete_test_questions(conn, test_id: int) -> int:
    """Тестийн БҮХ асуултыг устгана. Устгасан тоог буцаана.

    answers.question_id нь ON DELETE CASCADE тул энэ дуудлага тухайн
    асуултуудад өгсөн хариултыг ХАМТ устгана. Оролдлого бүртгэгдсэн
    тест дээр дуудахаас өмнө `count_test_attempts`-ээр шалгана уу.
    """
    rows = list_questions(conn, test_id)
    execute(conn, "DELETE FROM questions WHERE test_id = ?", (test_id,))
    return len(rows)


def copy_questions(conn, source_test_id: int, target_test_id: int) -> int:
    """Нэг тестийн асуултуудыг нөгөө тест рүү хуулна. Хуулсан тоог буцаана."""
    rows = list_questions(conn, source_test_id)
    for index, q in enumerate(rows, start=1):
        create_question(
            conn, target_test_id, index, q["text"],
            {"A": q["option_a"], "B": q["option_b"], "C": q["option_c"],
             "D": q["option_d"], "E": q.get("option_e") or ""},
            q["correct_option"], q["score"],
            qtype=q.get("qtype") or "single",
            matches={"A": q.get("match_a"), "B": q.get("match_b"),
                     "C": q.get("match_c"), "D": q.get("match_d")},
        )
    return len(rows)


def update_test(conn, test_id: int, title: str, class_group_id) -> None:
    execute(conn, "UPDATE tests SET title = ?, class_group_id = ? WHERE id = ?",
            (title, class_group_id, test_id))


def count_test_attempts(conn, test_id: int) -> int:
    row = fetch_one(conn, "SELECT COUNT(*) AS v FROM attempts WHERE test_id = ?", (test_id,))
    return int((row or {}).get("v") or 0)
