# -*- coding: utf-8 -*-
"""
Хичээл устгах боломжийн тест.

Дүрэм:
  - ЗӨВХӨН админ. Багш өөрийн хичээлээ ч устгаж чадахгүй.
  - Хичээлийн кодыг гараар бичиж баталгаажуулна.
  - Устгахад ангийн бүлэг, оюутан, хос, тест, асуулт, оролдлого,
    хариулт бүгд cascade-аар дагаж устана.

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


class CourseDeleteTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "del.db")
        db.DB_PATH = pathlib.Path(self.db_path)
        db.init_db(self.db_path, drop_existing=True)

        import app as app_module
        self.app_module = app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app_module.app.test_client()

        conn = db.connect(self.db_path)
        now = domain.now_iso()
        db.create_user(conn, "Админ", "a@d.mn", domain.hash_password("admin1234"),
                       "admin", "Алба", now)
        self.teacher_id = db.create_user(conn, "Багш", "t@d.mn",
                                         domain.hash_password("teach1234"),
                                         "teacher", "Салбар", now)
        self.course_id = db.create_course(conn, self.teacher_id, "Устгах хичээл",
                                          "DEL101", 3, "2026 Намар", now)
        self.other_id = db.create_course(conn, self.teacher_id, "Үлдэх хичээл",
                                         "KEEP99", 3, "2026 Намар", now)
        group_id = db.create_group(conn, self.course_id, "G1", 10, now)
        pair_id = db.create_pair(conn, self.course_id, "хос", now)
        test_id = db.create_test(conn, self.course_id, pair_id, group_id, "T", "pre",
                                 "open", domain.generate_share_code("DEL101", "pre"), now)
        db.create_question(conn, test_id, 1, "Асуулт",
                           {"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"}, "A", 2)
        student_id = db.create_student(conn, group_id, "Оюутан", "S1", "s1", None, now)
        db.create_attempt(conn, test_id, pair_id, student_id, "s1", "Оюутан", now)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def login(self, email, password):
        body = self.client.get("/login").data.decode()
        token = body.split('name="csrf_token"')[1].split('value="')[1].split('"')[0]
        self.client.post("/login", data={"csrf_token": token, "email": email,
                                         "password": password}, follow_redirects=True)

    def token(self):
        body = self.client.get("/courses", follow_redirects=True).data.decode()
        return body.split('name="csrf_token"')[1].split('value="')[1].split('"')[0]

    def course_exists(self, course_id):
        conn = db.connect(self.db_path)
        try:
            return db.get_course(conn, course_id) is not None
        finally:
            conn.close()

    # ---- эрх ----

    def test_teacher_cannot_open_or_post_delete(self):
        self.login("t@d.mn", "teach1234")
        self.assertEqual(self.client.get(f"/courses/{self.course_id}/delete").status_code, 403)
        r = self.client.post(f"/courses/{self.course_id}/delete",
                             data={"csrf_token": self.token(), "confirm_code": "DEL101"})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(self.course_exists(self.course_id))

    def test_anonymous_is_redirected_to_login(self):
        r = self.client.get(f"/courses/{self.course_id}/delete")
        self.assertIn(r.status_code, (302, 401, 403))
        self.assertTrue(self.course_exists(self.course_id))

    # ---- баталгаажуулалт ----

    def test_admin_sees_what_will_be_deleted(self):
        self.login("a@d.mn", "admin1234")
        body = self.client.get(f"/courses/{self.course_id}/delete").data.decode()
        self.assertIn("DEL101", body)
        self.assertIn("Ангийн бүлэг", body)
        self.assertIn("Оюутны оролдлого", body)

    def test_wrong_code_does_not_delete(self):
        self.login("a@d.mn", "admin1234")
        r = self.client.post(f"/courses/{self.course_id}/delete",
                             data={"csrf_token": self.token(), "confirm_code": "БУРУУ"})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(self.course_exists(self.course_id))

    def test_empty_code_does_not_delete(self):
        self.login("a@d.mn", "admin1234")
        r = self.client.post(f"/courses/{self.course_id}/delete",
                             data={"csrf_token": self.token(), "confirm_code": ""})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(self.course_exists(self.course_id))

    def test_code_match_is_case_insensitive(self):
        self.login("a@d.mn", "admin1234")
        self.client.post(f"/courses/{self.course_id}/delete",
                         data={"csrf_token": self.token(), "confirm_code": " del101 "},
                         follow_redirects=True)
        self.assertFalse(self.course_exists(self.course_id))

    # ---- бодит устгалт ----

    def test_correct_code_deletes_course_and_all_related_rows(self):
        self.login("a@d.mn", "admin1234")
        r = self.client.post(f"/courses/{self.course_id}/delete",
                             data={"csrf_token": self.token(), "confirm_code": "DEL101"},
                             follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.course_exists(self.course_id))

        conn = db.connect(self.db_path)
        try:
            for table in ("class_groups", "test_pairs", "tests"):
                rows = db.fetch_all(conn, f"SELECT * FROM {table} WHERE course_id = ?",
                                    (self.course_id,))
                self.assertEqual(rows, [], table)
            # Cascade нь холбоотой асуулт, оюутан, оролдлого, хариултыг ч авна.
            for table in ("questions", "students", "attempts", "answers"):
                self.assertEqual(db.fetch_all(conn, f"SELECT * FROM {table}"), [], table)
            # Бусад хичээл ХӨНДӨГДӨХГҮЙ.
            self.assertIsNotNone(db.get_course(conn, self.other_id))
        finally:
            conn.close()

    def test_summary_counts_match_reality(self):
        conn = db.connect(self.db_path)
        try:
            s = db.course_delete_summary(conn, self.course_id)
        finally:
            conn.close()
        self.assertEqual(s, {"group_count": 1, "pair_count": 1, "test_count": 1,
                             "student_count": 1, "question_count": 1, "attempt_count": 1})


if __name__ == "__main__":
    unittest.main()
