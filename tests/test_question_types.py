# -*- coding: utf-8 -*-
"""
Бутархай кредит, асуултын шинэ төрлүүд (олон сонголттой, харгалзуулах)
болон схемийн миграцийн тест.

Онцгой анхаарах зүйл: ХУУЧИН нэг сонголттой асуултууд өөрчлөлтгүй
ажиллаж, хуучин өгөгдөл алдагдахгүй байх ёстой.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import domain  # noqa: E402


# =====================================================================
# 1. Бутархай кредит
# =====================================================================
class TestCredit(unittest.TestCase):
    def test_accepts_whole_and_decimal(self):
        self.assertEqual(domain.parse_credit("3"), 3.0)
        self.assertEqual(domain.parse_credit("3.5"), 3.5)
        self.assertEqual(domain.parse_credit(" 4.5 "), 4.5)

    def test_accepts_comma_decimal(self):
        """Монголд таслалаар бутархай бичих нь түгээмэл."""
        self.assertEqual(domain.parse_credit("3,5"), 3.5)

    def test_rejects_zero_negative_and_text(self):
        for bad in ("0", "-2", "", "abc", None, "3.5кр"):
            with self.subTest(value=bad):
                self.assertIsNone(domain.parse_credit(bad))

    def test_rejects_absurdly_large(self):
        self.assertIsNone(domain.parse_credit("500"))

    def test_format_drops_trailing_zero(self):
        self.assertEqual(domain.format_credit(3.0), "3")
        self.assertEqual(domain.format_credit(3.5), "3.5")
        self.assertEqual(domain.format_credit(3), "3")


# =====================================================================
# 2. Асуултын төрлийн логик
# =====================================================================
class TestQuestionTypes(unittest.TestCase):
    def test_missing_qtype_defaults_to_single(self):
        """Хуучин асуултад qtype багана байхгүй ч 'single' гэж ажиллана."""
        self.assertEqual(domain.question_type({}), "single")
        self.assertEqual(domain.question_type({"qtype": None}), "single")
        self.assertEqual(domain.question_type({"qtype": "ЯМАРНЭГЗҮЙЛ"}), "single")

    def test_option_set_is_normalised(self):
        self.assertEqual(domain.format_option_set("c,a"), "A,C")
        self.assertEqual(domain.format_option_set(["D", "b", "d"]), "B,D")
        self.assertEqual(domain.parse_option_set("A, Z, C"), ["A", "C"])

    # ---- single ----
    def test_single_grading_unchanged(self):
        q = {"id": 1, "score": 3, "correct_option": "B"}
        self.assertEqual(domain.grade_answer(q, "B"), (True, 3))
        self.assertEqual(domain.grade_answer(q, "A"), (False, 0))
        self.assertEqual(domain.grade_answer(q, None), (False, 0))
        self.assertEqual(domain.grade_answer(q, "Z"), (False, 0))

    # ---- multi ----
    def test_multi_requires_exact_set(self):
        q = {"id": 2, "score": 4, "qtype": "multi", "correct_option": "A,C"}
        self.assertEqual(domain.grade_answer(q, "A,C"), (True, 4))
        self.assertEqual(domain.grade_answer(q, "C,A"), (True, 4), "дараалал хамаарахгүй")
        self.assertEqual(domain.grade_answer(q, "A"), (False, 0), "дутуу бол 0")
        self.assertEqual(domain.grade_answer(q, "A,B,C"), (False, 0), "илүү бол 0")
        self.assertEqual(domain.grade_answer(q, ""), (False, 0))
        self.assertEqual(domain.grade_answer(q, None), (False, 0))

    def test_multi_validation(self):
        options = {k: "текст" for k in domain.OPTION_KEYS}
        too_few = domain.validate_question("Асуулт", options, "A", 1, qtype="multi")
        self.assertTrue(any("хамгийн багадаа 2" in e for e in too_few))

        all_of_them = domain.validate_question("Асуулт", options, "A,B,C,D,E", 1, qtype="multi")
        self.assertTrue(any("Бүх сонголт зөв" in e for e in all_of_them))

        good = domain.validate_question("Асуулт", options, "A,C", 1, qtype="multi")
        self.assertEqual(good, [])

    # ---- match ----
    def test_match_display_order_is_stable_and_shuffled(self):
        first = domain.match_display_order(42)
        self.assertEqual(first, domain.match_display_order(42), "дахин ачаалахад ижил байх ёстой")
        self.assertEqual(sorted(first), [0, 1, 2, 3], "бүх байрлал яг нэг удаа")

        # Олон асуултын дунд дор хаяж нэг нь холигдсон байх ёстой
        orders = [domain.match_display_order(i) for i in range(1, 15)]
        self.assertTrue(any(o != [0, 1, 2, 3] for o in orders),
                        "бүх асуулт A→1, B→2 гэж эгнэсэн бол холилт ажиллаагүй")

    def _correct_match_answer(self, question_id):
        order = domain.match_display_order(question_id)
        return domain.format_match_answer(
            {key: order.index(i) + 1 for i, key in enumerate(domain.MATCH_OPTION_KEYS)}
        )

    def test_match_all_pairs_correct_earns_full_score(self):
        q = {"id": 7, "score": 5, "qtype": "match"}
        self.assertEqual(domain.grade_answer(q, self._correct_match_answer(7)), (True, 5))

    def test_match_one_wrong_pair_earns_zero(self):
        q = {"id": 8, "score": 5, "qtype": "match"}
        correct = domain.parse_match_answer(self._correct_match_answer(8))
        # A ба B-г солиод буруу болгоно
        correct["A"], correct["B"] = correct["B"], correct["A"]
        self.assertEqual(domain.grade_answer(q, domain.format_match_answer(correct)), (False, 0))

    def test_match_incomplete_answer_earns_zero(self):
        q = {"id": 9, "score": 5, "qtype": "match"}
        self.assertEqual(domain.grade_answer(q, "A>1,B>2"), (False, 0))
        self.assertEqual(domain.grade_answer(q, ""), (False, 0))

    def test_match_answer_round_trip(self):
        self.assertEqual(domain.parse_match_answer("A>2,B>1"), {"A": 2, "B": 1})
        self.assertEqual(domain.format_match_answer({"B": 1, "A": 2}), "A>2,B>1")
        self.assertEqual(domain.parse_match_answer("A>9,Z>1,мусор"), {},
                         "хүрээнээс гарсан утга хаягдана")

    def test_match_validation_catches_duplicates_and_blanks(self):
        options = {k: f"зүүн {k}" for k in domain.OPTION_KEYS}

        blank = domain.validate_question("Асуулт", options, "", 1, qtype="match",
                                         matches={"A": "1", "B": "2", "C": "3", "D": ""})
        self.assertTrue(any("Баруун талын D" in e for e in blank))

        dupes = domain.validate_question("Асуулт", options, "", 1, qtype="match",
                                         matches={"A": "нэг", "B": "нэг", "C": "гурав", "D": "дөрөв"})
        self.assertTrue(any("давхардсан" in e for e in dupes))

        good = domain.validate_question("Асуулт", options, "", 1, qtype="match",
                                        matches={"A": "1", "B": "2", "C": "3", "D": "4"})
        self.assertEqual(good, [])


# =====================================================================
# 3. score_attempt — холимог төрөлтэй тест
# =====================================================================
class TestMixedScoring(unittest.TestCase):
    def test_mixed_question_types_in_one_attempt(self):
        order = domain.match_display_order(103)
        questions = [
            {"id": 101, "score": 2, "qtype": "single", "correct_option": "B"},
            {"id": 102, "score": 3, "qtype": "multi", "correct_option": "A,D"},
            {"id": 103, "score": 5, "qtype": "match"},
        ]
        answers = {
            101: "B",        # зөв  -> 2
            102: "A",        # дутуу -> 0
            103: domain.format_match_answer(
                {k: order.index(i) + 1 for i, k in enumerate(domain.MATCH_OPTION_KEYS)}),  # зөв -> 5
        }
        result = domain.score_attempt(questions, answers)
        self.assertEqual(result["total_score"], 7)
        self.assertEqual(result["max_score"], 10)
        self.assertEqual(result["correct_count"], 2)
        self.assertEqual(result["wrong_count"], 1)
        self.assertEqual(result["percent"], 70)

    def test_stored_answer_is_normalised(self):
        questions = [{"id": 1, "score": 1, "qtype": "multi", "correct_option": "A,B"}]
        graded = domain.score_attempt(questions, {1: "b,a"})["answers"][0]
        self.assertEqual(graded["selected_option"], "A,B")

    def test_unanswered_stays_none(self):
        questions = [{"id": 1, "score": 1, "qtype": "multi", "correct_option": "A,B"}]
        graded = domain.score_attempt(questions, {})["answers"][0]
        self.assertIsNone(graded["selected_option"])


# =====================================================================
# 4. Миграц — хуучин өгөгдөл алдагдахгүй
# =====================================================================
OLD_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','teacher')), department TEXT,
  created_at TEXT NOT NULL);
CREATE TABLE courses (id INTEGER PRIMARY KEY AUTOINCREMENT,
  teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL, code TEXT NOT NULL UNIQUE, credit INTEGER NOT NULL DEFAULT 3,
  semester TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE class_groups (id INTEGER PRIMARY KEY AUTOINCREMENT,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  name TEXT NOT NULL, student_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, UNIQUE (course_id, name));
CREATE TABLE test_pairs (id INTEGER PRIMARY KEY AUTOINCREMENT,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE tests (id INTEGER PRIMARY KEY AUTOINCREMENT,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  pair_id INTEGER REFERENCES test_pairs(id) ON DELETE SET NULL,
  class_group_id INTEGER REFERENCES class_groups(id) ON DELETE SET NULL,
  title TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('pre','post')),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','open','closed')),
  share_code TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE questions (id INTEGER PRIMARY KEY AUTOINCREMENT,
  test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
  order_no INTEGER NOT NULL, text TEXT NOT NULL,
  option_a TEXT NOT NULL, option_b TEXT NOT NULL,
  option_c TEXT NOT NULL, option_d TEXT NOT NULL,
  correct_option TEXT NOT NULL CHECK (correct_option IN ('A','B','C','D')),
  score INTEGER NOT NULL DEFAULT 1 CHECK (score > 0));
CREATE TABLE students (id INTEGER PRIMARY KEY AUTOINCREMENT,
  class_group_id INTEGER NOT NULL REFERENCES class_groups(id) ON DELETE CASCADE,
  full_name TEXT NOT NULL, student_code TEXT NOT NULL,
  normalized_student_code TEXT NOT NULL, email TEXT, created_at TEXT NOT NULL,
  UNIQUE (class_group_id, normalized_student_code));
CREATE TABLE attempts (id INTEGER PRIMARY KEY AUTOINCREMENT,
  test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
  test_pair_id INTEGER REFERENCES test_pairs(id) ON DELETE SET NULL,
  student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  match_key TEXT NOT NULL, entered_full_name TEXT NOT NULL, started_at TEXT NOT NULL,
  submitted_at TEXT, total_score INTEGER, max_score INTEGER, percent INTEGER,
  UNIQUE (test_id, student_id));
CREATE TABLE answers (id INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  selected_option TEXT CHECK (selected_option IN ('A','B','C','D')),
  is_correct INTEGER NOT NULL DEFAULT 0, earned_score INTEGER NOT NULL DEFAULT 0,
  UNIQUE (attempt_id, question_id));
"""


class TestMigration(unittest.TestCase):
    """ХУУЧИН схемтэй өгөгдлийн санг барьж, миграц хийж шалгана."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "old.db")
        conn = sqlite3.connect(self.path)
        conn.executescript(OLD_SCHEMA)
        conn.execute("INSERT INTO users VALUES (1,'Багш','t@t.mn','h','teacher','С','2026-01-01')")
        conn.execute("INSERT INTO courses VALUES (1,1,'Хичээл','C1',3,'2026 Намар','2026-01-01')")
        conn.execute("INSERT INTO class_groups VALUES (1,1,'G1',10,'2026-01-01')")
        conn.execute("INSERT INTO tests VALUES (1,1,NULL,1,'Тест','pre','open','C1-PRE-AAAA','2026-01-01')")
        for i in range(1, 4):
            conn.execute("INSERT INTO questions VALUES (?,1,?,?,'a','b','c','d','A',2)",
                         (i, i, f"Асуулт {i}"))
        conn.execute("INSERT INTO students VALUES (1,1,'Оюутан','S1','S1',NULL,'2026-01-01')")
        conn.execute("INSERT INTO attempts VALUES (1,1,NULL,1,'grp:1|code:S1','Оюутан',"
                     "'2026-01-01','2026-01-01',4,6,67)")
        for i in range(1, 4):
            conn.execute("INSERT INTO answers VALUES (?,1,?,'A',1,2)", (i, i))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def _counts(self):
        conn = sqlite3.connect(self.path)
        out = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
               for t in ("users", "courses", "questions", "attempts", "answers")}
        conn.close()
        return out

    def test_migration_preserves_all_rows(self):
        before = self._counts()
        conn = db.connect(self.path)
        db.migrate(conn)
        conn.close()
        self.assertEqual(before, self._counts(), "миграц өгөгдөл алдсан")

    def test_migration_adds_columns_and_defaults_to_single(self):
        conn = db.connect(self.path)
        db.migrate(conn)
        cols = {r["name"] for r in db.fetch_all(conn, "PRAGMA table_info(questions)")}
        for expected in ("qtype", "match_a", "match_b", "match_c", "match_d"):
            self.assertIn(expected, cols)
        types = {r["qtype"] for r in db.fetch_all(conn, "SELECT qtype FROM questions")}
        self.assertEqual(types, {"single"}, "хуучин асуулт бүр single байх ёстой")
        conn.close()

    def test_migration_is_idempotent(self):
        conn = db.connect(self.path)
        first = db.migrate(conn)
        second = db.migrate(conn)
        conn.close()
        self.assertTrue(first, "эхний удаад ажил хийгдэх ёстой")
        self.assertEqual(second, [], "хоёр дахь удаад юу ч хийх ёсгүй")

    def test_multi_and_match_can_be_saved_after_migration(self):
        """Хуучин CHECK арилсан эсэх — 'A,C' хадгалагдах ёстой."""
        conn = db.connect(self.path)
        db.migrate(conn)
        qid = db.create_question(
            conn, 1, 4, "Олон сонголттой",
            {"A": "a", "B": "b", "C": "c", "D": "d"}, "A,C", 3, qtype="multi")
        mid = db.create_question(
            conn, 1, 5, "Харгалзуулах",
            {"A": "a", "B": "b", "C": "c", "D": "d"}, "", 4, qtype="match",
            matches={"A": "1", "B": "2", "C": "3", "D": "4"})
        conn.commit()

        multi = db.get_question(conn, qid)
        match = db.get_question(conn, mid)
        self.assertEqual(multi["correct_option"], "A,C")
        self.assertEqual(multi["qtype"], "multi")
        self.assertEqual(match["match_c"], "3")

        # answers дээр ч CHECK арилсан эсэх
        db.save_answers(conn, 1, [
            {"question_id": qid, "selected_option": "A,C", "is_correct": True, "earned_score": 3},
            {"question_id": mid, "selected_option": "A>2,B>1,C>4,D>3",
             "is_correct": False, "earned_score": 0},
        ])
        conn.commit()
        saved = {r["question_id"]: r["selected_option"]
                 for r in db.list_answers(conn, 1)}
        self.assertEqual(saved[qid], "A,C")
        self.assertEqual(saved[mid], "A>2,B>1,C>4,D>3")
        conn.close()

    def test_decimal_credit_stored_after_migration(self):
        """SQLite динамик төрөлтэй тул INTEGER багана ч 3.5-ыг хадгална."""
        conn = db.connect(self.path)
        db.migrate(conn)
        db.execute(conn, "UPDATE courses SET credit = ? WHERE id = 1", (3.5,))
        conn.commit()
        self.assertEqual(db.get_course(conn, 1)["credit"], 3.5)
        conn.close()

    def test_duplicate_test_copies_question_types(self):
        conn = db.connect(self.path)
        db.migrate(conn)
        db.create_question(conn, 1, 4, "Харгалзуулах",
                           {"A": "a", "B": "b", "C": "c", "D": "d"}, "", 4,
                           qtype="match", matches={"A": "1", "B": "2", "C": "3", "D": "4"})
        target = db.create_test(conn, 1, None, None, "Хуулбар", "post", "draft",
                                "C1-POST-BBBB", "2026-01-01")
        copied = db.copy_questions(conn, 1, target)
        conn.commit()
        self.assertEqual(copied, 4)
        types = [q["qtype"] for q in db.list_questions(conn, target)]
        self.assertEqual(types, ["single", "single", "single", "match"])
        last = db.list_questions(conn, target)[-1]
        self.assertEqual(last["match_b"], "2")
        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
