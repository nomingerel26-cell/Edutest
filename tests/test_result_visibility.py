# -*- coding: utf-8 -*-
"""
Оюутны үр дүнгийн хуудасны нууцлал.

Оюутан хуурамч кодоор дахин орох боломжтой тул тест НЭЭЛТТЭЙ байхад
аль асуулт буруу байсныг хэлж өгвөл нэг дахин оролдлогоор бүх зөв
хариултыг олж болно. Тиймээс асуулт тус бүрийн ✓/✕ нь тест ХААГДСАНЫ
дараа л харагдана.

Зөв хариулт нь аль ч төлөвт ХЭЗЭЭ Ч харагдахгүй.
"""

import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import domain  # noqa: E402


class ResultVisibilityTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "vis.db")
        db.DB_PATH = pathlib.Path(self.db_path)
        db.init_db(self.db_path, drop_existing=True)

        import app as app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app_module.app.test_client()

        conn = db.connect(self.db_path)
        now = domain.now_iso()
        teacher_id = db.create_user(conn, "Багш", "t@v.mn",
                                    domain.hash_password("teach1234"),
                                    "teacher", "Салбар", now)
        course_id = db.create_course(conn, teacher_id, "Хичээл", "VIS101", 3,
                                     "2026 Намар", now)
        group_id = db.create_group(conn, course_id, "G1", 10, now)
        self.test_id = db.create_test(conn, course_id, None, group_id, "T", "pre",
                                      "open", domain.generate_share_code("VIS101", "pre"),
                                      now)
        # Зөв хариулт нь E — үр дүнгийн хуудсанд гарч ирэх ёсгүй.
        q1 = db.create_question(conn, self.test_id, 1, "Нэгдүгээр асуулт",
                                {"A": "a", "B": "b", "C": "c", "D": "d",
                                 "E": "НУУЦ ЗӨВ ХАРИУЛТ"}, "E", 2)
        q2 = db.create_question(conn, self.test_id, 2, "Хоёрдугаар асуулт",
                                {"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"},
                                "A", 3)
        student_id = db.create_student(conn, group_id, "Оюутан", "S1", "s1", None, now)
        self.attempt_id = db.create_attempt(conn, self.test_id, None, student_id,
                                            "s1", "Оюутан", now)
        # 1-рийг зөв, 2-рыг буруу хариулсан.
        db.save_answers(conn, self.attempt_id, [
            {"question_id": q1, "selected_option": "E", "is_correct": True, "earned_score": 2},
            {"question_id": q2, "selected_option": "C", "is_correct": False, "earned_score": 0},
        ])
        db.finish_attempt(conn, self.attempt_id, 2, 5, 40, now)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def set_status(self, status):
        conn = db.connect(self.db_path)
        db.set_test_status(conn, self.test_id, status)
        conn.commit()
        conn.close()

    def result_body(self):
        r = self.client.get(f"/r/{self.attempt_id}")
        self.assertEqual(r.status_code, 200)
        return r.data.decode()

    def test_open_test_hides_per_question_verdicts(self):
        self.set_status("open")
        body = self.result_body()
        self.assertIn("тест хаагдсаны дараа", body)
        self.assertNotIn("Зөв +2", body)
        self.assertNotIn("Буруу 0", body)
        self.assertNotIn("Хоёрдугаар асуулт", body)

    def test_open_test_still_shows_total_score(self):
        self.set_status("open")
        body = self.result_body()
        self.assertIn("40%", body)
        self.assertIn("2 / 5", body)

    def test_closed_test_reveals_per_question_verdicts(self):
        self.set_status("closed")
        body = self.result_body()
        self.assertIn("Зөв +2", body)
        self.assertIn("Буруу 0", body)
        self.assertIn("Хоёрдугаар асуулт", body)
        self.assertNotIn("тест хаагдсаны дараа", body)

    def test_correct_answer_is_never_revealed(self):
        """Аль ч төлөвт зөв сонголтын ТЕКСТ гарч ирэх ёсгүй."""
        for status in ("open", "closed"):
            self.set_status(status)
            self.assertNotIn("НУУЦ ЗӨВ ХАРИУЛТ", self.result_body(), status)


if __name__ == "__main__":
    unittest.main()
