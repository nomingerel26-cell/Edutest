# -*- coding: utf-8 -*-
"""
Хоёр шинэ боломжийн тест:

1. Сонголт 5 болсон. Нэг сонголттой нь A-E үсгээр, олон сонголттой нь
   1-5 дугаараар ХАРАГДАНА. Хадгалалт бүх төрөлд үсгэн түлхүүр хэвээр.
   Харгалзуулах нь 4 мөр хэвээр.
2. Оролтын тестийг «Нээлттэй» болгоход асуулт нь ижил хосын гаралтын
   тест рүү автоматаар хуулагдана.

Түр зуурын өгөгдлийн санд ажиллана — edutest.db-д хүрэхгүй.
"""

import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import domain  # noqa: E402


class OptionLabelTests(unittest.TestCase):
    """Шошго нь ЗӨВХӨН харагдах байдал — түлхүүр өөрчлөгдөхгүй."""

    def test_multi_shows_digits(self):
        self.assertEqual([domain.option_label("multi", k) for k in domain.OPTION_KEYS],
                         ["1", "2", "3", "4", "5"])

    def test_single_shows_letters(self):
        self.assertEqual([domain.option_label("single", k) for k in domain.OPTION_KEYS],
                         ["A", "B", "C", "D", "E"])

    def test_storage_keys_stay_letters(self):
        # Дугаарлалт нэвтрүүлсэн ч хадгалалт үсгээрээ хэвээр — хуучин
        # өгөгдөл, оноолт, экспорт бүгд хөндөгдөхгүй байх ёстой.
        self.assertEqual(domain.OPTION_KEYS, ("A", "B", "C", "D", "E"))
        self.assertEqual(domain.parse_option_set("A,C,E"), ["A", "C", "E"])
        self.assertEqual(domain.format_option_set(["E", "A"]), "A,E")

    def test_match_keeps_four_rows(self):
        self.assertEqual(domain.MATCH_OPTION_KEYS, ("A", "B", "C", "D"))
        self.assertEqual(domain.option_keys("match"), ("A", "B", "C", "D"))
        self.assertEqual(len(domain.match_display_order(11)), 4)
        # E-тэй хос бол харгалзуулахад хүчингүй.
        self.assertNotIn("E", domain.parse_match_answer("A>1,B>2,C>3,D>4,E>5"))

    def test_legacy_four_option_question_hides_empty_fifth(self):
        legacy = {"qtype": "single", "option_a": "a", "option_b": "b",
                  "option_c": "c", "option_d": "d", "option_e": ""}
        self.assertEqual(domain.visible_option_keys(legacy), ["A", "B", "C", "D"])

    def test_validation_requires_all_five_for_single_and_multi(self):
        four = {"A": "a", "B": "b", "C": "c", "D": "d", "E": ""}
        self.assertTrue(domain.validate_question("Асуулт?", four, "A", 1))
        five = dict(four, E="e")
        self.assertEqual(domain.validate_question("Асуулт?", five, "E", 1), [])
        # Харгалзуулахад E шаардахгүй.
        self.assertEqual(
            domain.validate_question("Асуулт?", four, "", 1, qtype="match",
                                     matches={"A": "1", "B": "2", "C": "3", "D": "4"}),
            [])

    def test_grading_accepts_fifth_option(self):
        single = {"id": 1, "score": 2, "qtype": "single", "correct_option": "E"}
        self.assertEqual(domain.grade_answer(single, "E"), (True, 2))
        self.assertEqual(domain.grade_answer(single, "D"), (False, 0))
        multi = {"id": 2, "score": 3, "qtype": "multi", "correct_option": "A,E"}
        self.assertEqual(domain.grade_answer(multi, "A,E"), (True, 3))
        self.assertEqual(domain.grade_answer(multi, "A"), (False, 0))


class PairSyncTests(unittest.TestCase):
    """Оролт нээгдэхэд гаралт руу асуулт хуулагдах."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "sync.db")
        db.DB_PATH = pathlib.Path(self.db_path)
        db.init_db(self.db_path, drop_existing=True)

        import app as app_module
        self.app_module = app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app_module.app.test_client()

        conn = db.connect(self.db_path)
        now = domain.now_iso()
        self.teacher_id = db.create_user(conn, "Багш", "t@s.mn",
                                         domain.hash_password("teach1234"),
                                         "teacher", "Салбар", now)
        self.course_id = db.create_course(conn, self.teacher_id, "Хичээл",
                                          "SYN101", 3, "2026 Намар", now)
        self.pair_id = db.create_pair(conn, self.course_id, "хос", now)
        self.tests = {}
        for kind in ("pre", "post"):
            self.tests[kind] = db.create_test(
                conn, self.course_id, self.pair_id, None, f"SYN101 {kind}",
                kind, "draft", domain.generate_share_code("SYN101", kind), now)
        conn.commit()
        conn.close()
        self.login()

    def tearDown(self):
        self.tmpdir.cleanup()

    def login(self):
        body = self.client.get("/login").data.decode()
        token = body.split('name="csrf_token"')[1].split('value="')[1].split('"')[0]
        self.client.post("/login", data={"csrf_token": token, "email": "t@s.mn",
                                         "password": "teach1234"},
                         follow_redirects=True)

    def token(self):
        body = self.client.get("/courses", follow_redirects=True).data.decode()
        return body.split('name="csrf_token"')[1].split('value="')[1].split('"')[0]

    def add_question(self, test_id, text, correct="E"):
        return self.client.post(f"/tests/{test_id}", data={
            "csrf_token": self.token(), "qtype": "single", "text": text,
            "option_a": "a", "option_b": "b", "option_c": "c",
            "option_d": "d", "option_e": "e",
            "correct_option": correct, "score": "2",
        }, follow_redirects=True)

    def open_pre(self):
        return self.client.post(f"/tests/{self.tests['pre']}/status",
                                data={"csrf_token": self.token(), "status": "open"},
                                follow_redirects=True)

    def questions(self, kind):
        conn = db.connect(self.db_path)
        try:
            return db.list_questions(conn, self.tests[kind])
        finally:
            conn.close()

    def test_opening_pre_copies_questions_to_post(self):
        self.add_question(self.tests["pre"], "Оролтын асуулт 1")
        self.add_question(self.tests["pre"], "Оролтын асуулт 2")
        self.assertEqual(len(self.questions("post")), 0)

        self.open_pre()

        post_rows = self.questions("post")
        self.assertEqual(len(post_rows), 2)
        self.assertEqual(self.app_module._question_signature(post_rows),
                         self.app_module._question_signature(self.questions("pre")))

    def test_reopening_does_not_rewrite_identical_questions(self):
        self.add_question(self.tests["pre"], "Асуулт")
        self.open_pre()
        ids = [q["id"] for q in self.questions("post")]

        self.client.post(f"/tests/{self.tests['pre']}/status",
                         data={"csrf_token": self.token(), "status": "draft"},
                         follow_redirects=True)
        self.open_pre()

        self.assertEqual([q["id"] for q in self.questions("post")], ids)

    def test_post_with_attempts_is_left_alone(self):
        """Оролдлого өгсөн гаралтыг дарж бичихгүй — үр дүн устах эрсдэлтэй."""
        self.add_question(self.tests["pre"], "Оролтын асуулт")
        conn = db.connect(self.db_path)
        db.create_question(conn, self.tests["post"], 1, "Гаралтын өөр асуулт",
                           {"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"}, "A", 2)
        group_id = db.create_group(conn, self.course_id, "G1", 10, domain.now_iso())
        student_id = db.create_student(conn, group_id, "Оюутан", "S1", "s1",
                                       None, domain.now_iso())
        db.create_attempt(conn, self.tests["post"], self.pair_id, student_id,
                          "s1", "Оюутан", domain.now_iso())
        conn.commit()
        conn.close()

        self.open_pre()

        rows = self.questions("post")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "Гаралтын өөр асуулт")

    def test_opening_post_does_not_touch_pre(self):
        self.add_question(self.tests["post"], "Зөвхөн гаралтад")
        self.client.post(f"/tests/{self.tests['post']}/status",
                         data={"csrf_token": self.token(), "status": "open"},
                         follow_redirects=True)
        self.assertEqual(len(self.questions("pre")), 0)


if __name__ == "__main__":
    unittest.main()
