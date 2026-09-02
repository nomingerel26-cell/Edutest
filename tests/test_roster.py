# -*- coding: utf-8 -*-
"""
Ангийн жагсаалт ба «Зөвхөн жагсаалтаас» нэвтрэх горим.

Асуудал: оюутан хуурамч оюутны код бичээд тестийг дахин өгч болдог.
Шийдэл: тест бүрт горим сонгоно.
  any    — хэн ч ямар ч код бичээд орно (гадны сургалт, кодгүй хүмүүс).
  roster — багшийн урьдчилан бүртгэсэн кодоор л орно.

Хуучин бүх тест `any` хэвээр үлдэх ёстой — зан төлөв өөрчлөгдөхгүй.
"""

import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import domain  # noqa: E402


class RosterParsingTests(unittest.TestCase):

    def test_accepts_comma_semicolon_and_tab(self):
        rows, errors = domain.parse_student_roster(
            "B230101, Батын Болд\nB230102;Доржийн Сараа\nB230103\tЦэрэн")
        self.assertEqual([r["normalized"] for r in rows],
                         ["B230101", "B230102", "B230103"])
        self.assertEqual([r["full_name"] for r in rows],
                         ["Батын Болд", "Доржийн Сараа", "Цэрэн"])
        self.assertEqual(errors, [])

    def test_code_is_normalized_but_original_kept(self):
        rows, _ = domain.parse_student_roster("  b23 0101 , Болд")
        self.assertEqual(rows[0]["normalized"], "B230101")
        self.assertEqual(rows[0]["student_code"], "b23 0101")

    def test_name_defaults_to_code(self):
        rows, _ = domain.parse_student_roster("B230104")
        self.assertEqual(rows[0]["full_name"], "B230104")

    def test_header_row_and_blank_lines_are_skipped(self):
        rows, errors = domain.parse_student_roster("код,нэр\n\nB230101,Болд\n   \n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(errors, [])

    def test_duplicate_code_is_reported_not_silently_dropped(self):
        rows, errors = domain.parse_student_roster("B230101,Болд\nb230101,Өөр нэр")
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("давхардсан" in e for e in errors))

    def test_empty_input_is_an_error(self):
        rows, errors = domain.parse_student_roster("   \n\n")
        self.assertEqual(rows, [])
        self.assertTrue(errors)


class RosterFlowTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "roster.db")
        db.DB_PATH = pathlib.Path(self.db_path)
        db.init_db(self.db_path, drop_existing=True)

        import app as app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app_module.app.test_client()

        conn = db.connect(self.db_path)
        now = domain.now_iso()
        teacher_id = db.create_user(conn, "Багш", "t@r.mn",
                                    domain.hash_password("teach1234"),
                                    "teacher", "Салбар", now)
        db.create_user(conn, "Өөр багш", "o@r.mn",
                       domain.hash_password("other1234"), "teacher", "Салбар", now)
        self.course_id = db.create_course(conn, teacher_id, "Хичээл", "RST101", 3,
                                          "2026 Намар", now)
        self.group_id = db.create_group(conn, self.course_id, "G1", 30, now)
        self.test_id = db.create_test(conn, self.course_id, None, None, "T", "pre",
                                      "open", "RSTPRE1", now)
        db.create_question(conn, self.test_id, 1, "Асуулт",
                           {"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"}, "A", 2)
        conn.commit()
        conn.close()
        self.login("t@r.mn", "teach1234")

    def tearDown(self):
        self.tmpdir.cleanup()

    def login(self, email, password):
        self.client.get("/logout")
        body = self.client.get("/login").data.decode()
        token = body.split('name="csrf_token"')[1].split('value="')[1].split('"')[0]
        self.client.post("/login", data={"csrf_token": token, "email": email,
                                         "password": password}, follow_redirects=True)

    def token(self):
        body = self.client.get("/courses", follow_redirects=True).data.decode()
        return body.split('name="csrf_token"')[1].split('value="')[1].split('"')[0]

    def roster(self):
        conn = db.connect(self.db_path)
        try:
            return db.list_students(conn, self.group_id)
        finally:
            conn.close()

    def set_mode(self, mode):
        return self.client.post(f"/tests/{self.test_id}/entry-mode",
                                data={"csrf_token": self.token(), "entry_mode": mode},
                                follow_redirects=True)

    def take_test(self, code, name="Шинэ Оюутан"):
        return self.client.post("/t/RSTPRE1", data={
            "full_name": name, "email": "", "student_code": code,
            "class_group_id": str(self.group_id),
        }, follow_redirects=False)

    # ---- жагсаалт удирдах ----

    def test_add_students_in_bulk(self):
        self.client.post(f"/groups/{self.group_id}/students", data={
            "csrf_token": self.token(),
            "roster": "B230101, Болд\nB230102, Сараа\nB230103",
        }, follow_redirects=True)
        # list_students нь НЭРЭЭР эрэмбэлдэг тул оруулсан дараалал хадгалагдахгүй.
        self.assertEqual(sorted(s["normalized_student_code"] for s in self.roster()),
                         ["B230101", "B230102", "B230103"])

    def test_re_adding_does_not_overwrite_existing_name(self):
        t = self.token()
        self.client.post(f"/groups/{self.group_id}/students",
                         data={"csrf_token": t, "roster": "B230101, Анхны нэр"},
                         follow_redirects=True)
        self.client.post(f"/groups/{self.group_id}/students",
                         data={"csrf_token": self.token(), "roster": "B230101, Өөр нэр"},
                         follow_redirects=True)
        rows = self.roster()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["full_name"], "Анхны нэр")

    def test_delete_student(self):
        self.client.post(f"/groups/{self.group_id}/students",
                         data={"csrf_token": self.token(), "roster": "B230101, Болд"},
                         follow_redirects=True)
        sid = self.roster()[0]["id"]
        self.client.post(f"/students/{sid}/delete",
                         data={"csrf_token": self.token()}, follow_redirects=True)
        self.assertEqual(self.roster(), [])

    def test_other_teacher_cannot_view_or_edit_roster(self):
        self.login("o@r.mn", "other1234")
        self.assertEqual(self.client.get(f"/groups/{self.group_id}").status_code, 403)
        r = self.client.post(f"/groups/{self.group_id}/students",
                             data={"csrf_token": self.token(), "roster": "X1"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.roster(), [])

    # ---- нэвтрэх горим ----

    def test_default_mode_is_any_so_old_tests_keep_working(self):
        conn = db.connect(self.db_path)
        test = db.get_test(conn, self.test_id)
        conn.close()
        self.assertEqual(domain.entry_mode(test), "any")
        self.assertEqual(self.take_test("HURAMTS999").status_code, 302)

    def test_roster_mode_rejects_unlisted_code(self):
        self.set_mode("roster")
        r = self.take_test("HURAMTS999")
        self.assertEqual(r.status_code, 403)
        self.assertIn("жагсаалтад бүртгэлгүй", r.data.decode())
        self.assertEqual(self.roster(), [], "бүртгэлгүй оюутан үүсэх ёсгүй")

    def test_roster_mode_allows_listed_code(self):
        self.client.post(f"/groups/{self.group_id}/students",
                         data={"csrf_token": self.token(), "roster": "B230101, Болд"},
                         follow_redirects=True)
        self.set_mode("roster")
        # Кодыг өөр бичиглэлээр оруулсан ч хэвийн болгосон хэлбэр таарна.
        self.assertEqual(self.take_test("b23 0101", "Болд").status_code, 302)

    def test_test_page_shows_both_modes_and_marks_current(self):
        """Сонголт нь харагдаж, аль нь идэвхтэйг ялгаж харуулах ёстой —
        эс бөгөөс тохиргоо байхгүй мэт харагдана."""
        body = self.client.get(f"/tests/{self.test_id}").data.decode()
        self.assertIn('name="entry_mode" value="any"', body)
        self.assertIn('name="entry_mode" value="roster"', body)
        head = body[body.find("Хэн тест өгөх вэ"):]
        self.assertIn("Нээлттэй", head[:400])

        self.set_mode("roster")
        body = self.client.get(f"/tests/{self.test_id}").data.decode()
        head = body[body.find("Хэн тест өгөх вэ"):]
        self.assertIn("Зөвхөн жагсаалтаас", head[:400])

    def test_invalid_mode_is_rejected(self):
        self.set_mode("zzz")
        conn = db.connect(self.db_path)
        test = db.get_test(conn, self.test_id)
        conn.close()
        self.assertEqual(domain.entry_mode(test), "any")


if __name__ == "__main__":
    unittest.main()
