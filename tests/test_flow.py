# -*- coding: utf-8 -*-
"""
Бүрэн урсгалын тест: нэвтрэлт -> хичээл/групп -> тестийн хос -> асуулт ->
оюутны бүртгэл -> тест бөглөх -> автомат бодолт -> үр дүн -> Pre/Post
харьцуулалт -> CSV экспорт.

Түр зуурын өгөгдлийн санд ажиллана — edutest.db-д хүрэхгүй.
"""

import csv
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import domain  # noqa: E402


class FlowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmpdir.name, "test_edutest.db")
        # app-ыг импортлохоос ӨМНӨ өгөгдлийн сангийн замыг солино.
        db.DB_PATH = __import__("pathlib").Path(cls.db_path)
        db.init_db(cls.db_path, drop_existing=True)

        import app as app_module
        cls.app_module = app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")

        conn = db.connect(cls.db_path)
        cls.admin_id = db.create_user(conn, "Админ", "admin@test.mn",
                                      domain.hash_password("admin1234"), "admin", "Алба",
                                      domain.now_iso())
        cls.teacher_id = db.create_user(conn, "Багш", "teacher@test.mn",
                                        domain.hash_password("teach1234"), "teacher", "Салбар",
                                        domain.now_iso())
        cls.other_teacher_id = db.create_user(conn, "Өөр багш", "other@test.mn",
                                              domain.hash_password("other1234"), "teacher", "Салбар",
                                              domain.now_iso())
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def setUp(self):
        self.client = self.app_module.app.test_client()

    # ---------------------------------------------------------------
    def login(self, email="teacher@test.mn", password="teach1234", client=None):
        return (client or self.client).post(
            "/login", data={"email": email, "password": password}, follow_redirects=True
        )

    def html(self, response):
        return response.get_data(as_text=True)


class TestAuth(FlowTestCase):
    def test_login_required_redirects(self):
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_wrong_password_rejected(self):
        r = self.client.post("/login", data={"email": "teacher@test.mn", "password": "буруу"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("Имэйл эсвэл нууц үг буруу байна.", self.html(r))

    def test_unknown_user_same_message(self):
        r = self.client.post("/login", data={"email": "nobody@test.mn", "password": "x"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("Имэйл эсвэл нууц үг буруу байна.", self.html(r))

    def test_login_and_logout(self):
        r = self.login()
        self.assertEqual(r.status_code, 200)
        self.assertIn("Хяналтын самбар", self.html(r))
        r = self.client.get("/logout", follow_redirects=True)
        self.assertIn("Системээс гарлаа.", self.html(r))

    def test_teacher_cannot_open_admin_page(self):
        self.login()
        self.assertEqual(self.client.get("/admin/users").status_code, 403)

    def test_admin_can_open_admin_page(self):
        self.login("admin@test.mn", "admin1234")
        r = self.client.get("/admin/users")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Хэрэглэгчид", self.html(r))

    def test_password_never_stored_in_plain_text(self):
        conn = db.connect(self.db_path)
        user = db.get_user_by_email(conn, "teacher@test.mn")
        conn.close()
        self.assertNotIn("teach1234", user["password_hash"])
        self.assertTrue(user["password_hash"].startswith("pbkdf2_sha256$"))


class TestFullFlow(FlowTestCase):
    """Нэг дараалсан урсгал — тестүүд цагаан толгойн дарааллаар ажиллана."""

    def test_01_create_course_group_pair_questions(self):
        self.login()
        r = self.client.post("/courses", data={
            "name": "Эмийн хими 1", "code": "FLOW101", "credit": "3", "semester": "2026 Намар",
        }, follow_redirects=True)
        self.assertIn("хичээл нэмэгдлээ", self.html(r))

        conn = db.connect(self.db_path)
        course = db.get_course_by_code(conn, "FLOW101")
        conn.close()
        FlowTestCase.course_id = course["id"]

        r = self.client.post(f"/courses/{course['id']}/groups",
                             data={"name": "PH23A", "student_count": "25"}, follow_redirects=True)
        self.assertIn("групп нэмэгдлээ", self.html(r))

        # Давхардсан группын нэр — тодорхой мессежтэй татгалзана
        r = self.client.post(f"/courses/{course['id']}/groups",
                             data={"name": "PH23A", "student_count": "25"}, follow_redirects=True)
        self.assertIn("аль хэдийн байна", self.html(r))

        r = self.client.post(f"/courses/{course['id']}/pairs",
                             data={"name": "Хос 2026", "class_group_id": ""}, follow_redirects=True)
        self.assertIn("хос үүсч", self.html(r))

        conn = db.connect(self.db_path)
        group = db.list_groups(conn, course["id"])[0]
        pairs = db.list_pairs(conn, course["id"])
        conn.close()
        FlowTestCase.group_id = group["id"]
        FlowTestCase.pair_id = pairs[0]["id"]
        FlowTestCase.pre_test_id = pairs[0]["pre_test_id"]
        FlowTestCase.post_test_id = pairs[0]["post_test_id"]
        self.assertIsNotNone(FlowTestCase.pre_test_id)
        self.assertIsNotNone(FlowTestCase.post_test_id)

    def test_02_question_validation_and_creation(self):
        self.login()
        # Буруу форм — алдаа монголоор, асуулт нэмэгдэхгүй
        r = self.client.post(f"/tests/{FlowTestCase.pre_test_id}", data={
            "text": "", "option_a": "", "option_b": "б", "option_c": "в", "option_d": "г",
            "correct_option": "A", "score": "0",
        }, follow_redirects=True)
        body = self.html(r)
        self.assertIn("Асуултын текст хоосон байна.", body)
        self.assertIn("Балл 0-ээс их бүхэл тоо байх ёстой.", body)

        for test_id in (FlowTestCase.pre_test_id, FlowTestCase.post_test_id):
            for i in range(1, 4):
                r = self.client.post(f"/tests/{test_id}", data={
                    "text": f"Асуулт {i} — үнэн үү?",
                    "option_a": "Зөв хариулт", "option_b": "Буруу 1",
                    "option_c": "Буруу 2", "option_d": "Буруу 3",
                    "correct_option": "A", "score": "2",
                }, follow_redirects=True)
                self.assertIn("Асуулт нэмэгдлээ.", self.html(r))

        conn = db.connect(self.db_path)
        self.assertEqual(len(db.list_questions(conn, FlowTestCase.pre_test_id)), 3)
        conn.close()

    def test_03_closed_test_blocks_students(self):
        conn = db.connect(self.db_path)
        test = db.get_test(conn, FlowTestCase.pre_test_id)
        conn.close()
        FlowTestCase.pre_code = test["share_code"]
        r = self.client.get(f"/t/{test['share_code']}")   # төлөв = draft
        self.assertEqual(r.status_code, 403)
        self.assertIn("хараахан нээгдээгүй", self.html(r))

    def test_04_open_tests(self):
        self.login()
        for test_id in (FlowTestCase.pre_test_id, FlowTestCase.post_test_id):
            r = self.client.post(f"/tests/{test_id}/status",
                                 data={"status": "open"}, follow_redirects=True)
            self.assertIn("Нээлттэй", self.html(r))
        conn = db.connect(self.db_path)
        FlowTestCase.post_code = db.get_test(conn, FlowTestCase.post_test_id)["share_code"]
        conn.close()

    def test_05_student_validation_errors(self):
        client = self.app_module.app.test_client()
        r = client.post(f"/t/{FlowTestCase.pre_code}", data={
            "full_name": "", "email": "буруу-имэйл", "student_code": "", "class_group_id": "",
        })
        self.assertEqual(r.status_code, 400)
        body = self.html(r)
        self.assertIn("Овог нэрээ бүтнээр нь бичнэ үү.", body)
        self.assertIn("Имэйл хаяг буруу байна", body)
        self.assertIn("Оюутны код хоосон байна", body)
        self.assertIn("Ангийн группээ сонгоно уу", body)

    def _take_test(self, share_code, name, code, answers, email="", group_id=None):
        """
        Оюутны бүрэн урсгал: бүртгэл -> тест -> илгээх.
        Таних түлхүүр нь ГРУПП + ОЮУТНЫ КОД. Имэйл нь заавал биш, зөвхөн харуулах.
        """
        client = self.app_module.app.test_client()
        r = client.post(f"/t/{share_code}", data={
            "full_name": name, "email": email, "student_code": code,
            "class_group_id": str(group_id or FlowTestCase.group_id),
        }, follow_redirects=False)
        self.assertEqual(r.status_code, 302, self.html(r))
        take_url = r.headers["Location"]

        r = client.get(take_url)
        self.assertEqual(r.status_code, 200)
        # Бүртгэлийн үеийн анхааруулга (жишээ нь нэрийн зөрчил) ЭНЭ хуудсанд гарна.
        FlowTestCase.last_take_html = self.html(r)
        self.assertIn("Хариултаа илгээх", FlowTestCase.last_take_html)

        conn = db.connect(self.db_path)
        test = db.get_test_by_share_code(conn, share_code)
        questions = db.list_questions(conn, test["id"])
        conn.close()

        form = {f"q{q['id']}": pick for q, pick in zip(questions, answers)}
        r = client.post(take_url, data=form, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        return client, self.html(r)

    def test_06_student_takes_pre_test(self):
        # 3 асуултын 2 нь зөв (A, A) -> 4/6 = 67%. Имэйл огт бичээгүй.
        _client, body = self._take_test(FlowTestCase.pre_code, "Батын Болд", "B230101",
                                        ["A", "A", "B"])
        self.assertIn("67%", body)
        self.assertIn("4 / 6", body)

    def test_07_incomplete_submission_rejected(self):
        client = self.app_module.app.test_client()
        r = client.post(f"/t/{FlowTestCase.pre_code}", data={
            "full_name": "Дутуу Хариулт", "email": "", "student_code": "B230999",
            "class_group_id": str(FlowTestCase.group_id),
        })
        take_url = r.headers["Location"]
        r = client.post(take_url, data={}, follow_redirects=True)
        self.assertEqual(r.status_code, 400)
        self.assertIn("хариулагдаагүй байна", self.html(r))

    def test_08_students_take_both_tests(self):
        # ШААРДЛАГА 11a: Оролт/Гаралт дээр ӨӨР имэйл — кодоор таарах ёстой.
        self._take_test(FlowTestCase.pre_code, "Доржийн Сараа", "B230102",
                        ["A", "B", "C"], email="saraa@must.edu.mn")     # 2/6 = 33%
        _c, body = self._take_test(FlowTestCase.post_code, "Доржийн Сараа", "B230102",
                                   ["A", "A", "B"], email="sara.dorj@gmail.com")  # 67%
        self.assertIn("67%", body)

        # ШААРДЛАГА 11b: имэйл хоосон (Оролт дээр ч хоосон байсан).
        _c, body = self._take_test(FlowTestCase.post_code, "Батын Болд", "B230101",
                                   ["A", "A", "A"])                     # 6/6 = 100%
        self.assertIn("100%", body)

        # ШААРДЛАГА 11c: код нь жижиг үсэг, санамсаргүй зайтай.
        self._take_test(FlowTestCase.pre_code, "Лхагвын Тэмүүлэн", "  b23 0104 ",
                        ["A", "B", "B"])                                # 2/6 = 33%
        self._take_test(FlowTestCase.post_code, "Лхагвын Тэмүүлэн", "B230104",
                        ["A", "A", "B"])                                # 4/6 = 67%

        # ШААРДЛАГА 5/6: ижил групп + ижил код, ӨӨР нэр -> зөрчил, чимээгүй нэгтгэхгүй.
        self._take_test(FlowTestCase.pre_code, "Сүхийн Ануужин", "B230105",
                        ["A", "B", "C"])                                # 33%
        _c, body = self._take_test(FlowTestCase.post_code, "Сүхийн Ану", "B230105",
                                   ["A", "A", "A"])                     # 100%
        self.assertIn("100%", body)                                     # оноо хэвийн хадгалагдана
        # Гэхдээ нэрийг чимээгүй нэгтгээгүй — бүртгэлийн үед анхааруулга харагдсан.
        self.assertIn("өөр нэр бүртгэлтэй байна", FlowTestCase.last_take_html)

    def test_09_repeat_attempt_blocked_by_code(self):
        # Имэйл огт өөр, код нь зайтай/жижиг үсэгтэй — ижил оюутан гэж танина.
        client = self.app_module.app.test_client()
        r = client.post(f"/t/{FlowTestCase.pre_code}", data={
            "full_name": "Батын Болд", "email": "totally-different@gmail.com",
            "student_code": " b230101 ", "class_group_id": str(FlowTestCase.group_id),
        }, follow_redirects=True)
        self.assertIn("аль хэдийн өгсөн байна", self.html(r))

    def test_09b_unique_rule_one_student_row_per_group_and_code(self):
        conn = db.connect(self.db_path)
        rows = db.fetch_all(
            conn,
            """SELECT class_group_id, normalized_student_code, COUNT(*) AS n
               FROM students GROUP BY class_group_id, normalized_student_code
               HAVING COUNT(*) > 1""")
        student = db.get_student_by_code(conn, FlowTestCase.group_id, "B230101")
        conn.close()
        self.assertEqual(rows, [])                       # UNIQUE дүрэм биелж байна
        self.assertIsNotNone(student)
        self.assertEqual(student["student_code"], "B230101")

    def test_09c_same_code_other_group_is_separate_student(self):
        self.login()
        self.client.post(f"/courses/{FlowTestCase.course_id}/groups",
                         data={"name": "PH23Z", "student_count": "5"}, follow_redirects=True)
        conn = db.connect(self.db_path)
        other = [g for g in db.list_groups(conn, FlowTestCase.course_id) if g["name"] == "PH23Z"][0]
        conn.close()
        self._take_test(FlowTestCase.pre_code, "Өөр Группын Оюутан", "B230101",
                        ["A", "A", "A"], group_id=other["id"])
        conn = db.connect(self.db_path)
        a = db.get_student_by_code(conn, FlowTestCase.group_id, "B230101")
        b = db.get_student_by_code(conn, other["id"], "B230101")
        conn.close()
        self.assertNotEqual(a["id"], b["id"])            # ижил код, өөр групп = өөр хүн

    def test_10_results_page_and_csv(self):
        self.login()
        r = self.client.get(f"/tests/{FlowTestCase.pre_test_id}/results")
        body = self.html(r)
        self.assertIn("Батын Болд", body)
        self.assertIn("Доржийн Сараа", body)

        r = self.client.get(f"/tests/{FlowTestCase.pre_test_id}/results.csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["Content-Type"])
        rows = list(csv.reader(io.StringIO(r.get_data().decode("utf-8-sig"))))
        self.assertEqual(rows[0][0], "Овог нэр")
        self.assertIn("Хэвийн код", rows[0])
        self.assertIn("Имэйл (заавал бус)", rows[0])
        self.assertIn("Нэрийн зөрчил", rows[0])
        # Эмх замбараагүй бичсэн код нь хэвийн баганад цэвэр хэлбэрээр гарна.
        messy = [row for row in rows[1:] if row[0] == "Лхагвын Тэмүүлэн"][0]
        self.assertEqual(messy[1], "b23 0104")     # эх хэлбэр — харуулах
        self.assertEqual(messy[2], "B230104")      # хэвийн хэлбэр — тааруулах

    def test_11_comparison_matches_by_code_not_email(self):
        self.login()
        r = self.client.get(f"/pairs/{FlowTestCase.pair_id}/comparison")
        body = self.html(r)
        self.assertEqual(r.status_code, 200)
        self.assertIn("+33%", body)   # Болд:   67 -> 100 (имэйлгүй)
        self.assertIn("+34%", body)   # Сараа:  33 -> 67  (ӨӨР имэйл)
        self.assertIn("+67%", body)   # Ануужин: 33 -> 100 (нэрийн зөрчилтэй)
        self.assertIn("Нэр зөрчилтэй", body)

        conn = db.connect(self.db_path)
        pre = db.list_results(conn, FlowTestCase.pre_test_id)
        post = db.list_results(conn, FlowTestCase.post_test_id)
        conn.close()
        rows = domain.match_pre_post(pre, post, FlowTestCase.pair_id)
        matched = [r for r in rows if r["status"] == "matched"]
        self.assertEqual(len(matched), 4)   # Болд, Сараа, Тэмүүлэн, Ануужин

        temuulen = [r for r in matched if "Тэмүүлэн" in r["full_name"]][0]
        self.assertEqual(temuulen["delta_percent"], 34)   # 33 -> 67, кодыг хэвийн болгосон

        conflicted = [r for r in matched if r["name_conflict"]]
        self.assertEqual(len(conflicted), 1)
        self.assertEqual(sorted(conflicted[0]["conflicting_names"]),
                         sorted(["Сүхийн Ануужин", "Сүхийн Ану"]))

    def test_12_comparison_csv(self):
        self.login()
        r = self.client.get(f"/pairs/{FlowTestCase.pair_id}/comparison.csv")
        self.assertEqual(r.status_code, 200)
        rows = list(csv.reader(io.StringIO(r.get_data().decode("utf-8-sig"))))
        self.assertEqual(rows[0],
                         ["Овог нэр", "Оюутны код", "Тааруулах түлхүүр", "Групп",
                          "Имэйл (заавал бус)", "Оролт %", "Гаралт %", "Ахиц %",
                          "Төлөв", "Нэрийн зөрчил"])
        matched = [row for row in rows[1:] if row[8] == "Тааруулсан"]
        self.assertEqual(len(matched), 4)
        self.assertTrue(all(row[2].startswith("grp:") and "code:" in row[2] for row in rows[1:]))
        conflict_cells = [row[9] for row in rows[1:] if row[9]]
        self.assertEqual(len(conflict_cells), 1)
        self.assertIn("Сүхийн Ану", conflict_cells[0])

    def test_13_other_teacher_cannot_see_course(self):
        client = self.app_module.app.test_client()
        self.login("other@test.mn", "other1234", client=client)
        self.assertEqual(client.get(f"/courses/{FlowTestCase.course_id}").status_code, 403)
        self.assertEqual(client.get(f"/tests/{FlowTestCase.pre_test_id}").status_code, 403)

    def test_14_admin_sees_every_course(self):
        client = self.app_module.app.test_client()
        self.login("admin@test.mn", "admin1234", client=client)
        self.assertEqual(client.get(f"/courses/{FlowTestCase.course_id}").status_code, 200)
        self.assertIn("FLOW101", self.html(client.get("/")))

    def test_15_bad_share_code_404(self):
        r = self.app_module.app.test_client().get("/t/ БАЙХГҮЙ-КОД ")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
