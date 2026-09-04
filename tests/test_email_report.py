# -*- coding: utf-8 -*-
"""
Тайланг имэйлээр илгээх.

Бодит SMTP сервер рүү холбогдохгүй — `smtplib.SMTP`-г хуурамчаар
орлуулж, юу илгээхээр бэлдсэнийг шалгана.

Аюулгүй байдлын гол шаардлага: хүлээн авагчийг ФОРМООС АВАХГҮЙ. Зөвхөн
нэвтэрсэн багшийн бүртгэл дэх хаяг руу явна — эс бөгөөс энэ хаягийг
дурын хаяг руу оюутны өгөгдөл илгээх суваг болгон ашиглаж болно.
"""

import os
import pathlib
import smtplib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import domain  # noqa: E402
import mailer  # noqa: E402

SMTP_ENV = {
    "EDUTEST_SMTP_HOST": "smtp.example.mn",
    "EDUTEST_SMTP_USER": "robot@example.mn",
    "EDUTEST_SMTP_PASSWORD": "нууц-үг-хэзээ-ч-гарах-ёсгүй",
    "EDUTEST_SMTP_FROM": "EduTest <robot@example.mn>",
}


class FakeSMTP:
    """Илгээсэн мессежийг барьж авах хуурамч SMTP."""

    sent = []
    logins = []
    raise_auth_error = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        if FakeSMTP.raise_auth_error:
            raise smtplib.SMTPAuthenticationError(535, b"Bad credentials")
        FakeSMTP.logins.append(user)

    def send_message(self, message):
        FakeSMTP.sent.append(message)

    @classmethod
    def reset(cls):
        cls.sent, cls.logins, cls.raise_auth_error = [], [], False


class MailerConfigTests(unittest.TestCase):

    def test_missing_settings_are_listed(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(sorted(mailer.missing_settings()),
                             ["EDUTEST_SMTP_HOST", "EDUTEST_SMTP_PASSWORD",
                              "EDUTEST_SMTP_USER"])
            self.assertFalse(mailer.is_configured())

    def test_configured_when_all_present(self):
        with mock.patch.dict(os.environ, SMTP_ENV, clear=True):
            self.assertTrue(mailer.is_configured())
            self.assertEqual(mailer.sender_address(), SMTP_ENV["EDUTEST_SMTP_FROM"])

    def test_send_without_settings_raises_clear_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(mailer.MailError) as ctx:
                mailer.send("a@b.mn", "x", "y")
        self.assertIn("тохиргоо дутуу", str(ctx.exception))

    def test_auth_error_message_does_not_leak_the_password(self):
        FakeSMTP.reset()
        FakeSMTP.raise_auth_error = True
        with mock.patch.dict(os.environ, SMTP_ENV, clear=True), \
             mock.patch.object(mailer.smtplib, "SMTP", FakeSMTP):
            with self.assertRaises(mailer.MailError) as ctx:
                mailer.send("a@b.mn", "x", "y")
        message = str(ctx.exception)
        self.assertNotIn(SMTP_ENV["EDUTEST_SMTP_PASSWORD"], message)
        self.assertIn("App Password", message)


class EmailReportRouteTests(unittest.TestCase):

    def setUp(self):
        FakeSMTP.reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "mail.db")
        db.DB_PATH = pathlib.Path(self.db_path)
        db.init_db(self.db_path, drop_existing=True)

        import app as app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app_module.app.test_client()

        conn = db.connect(self.db_path)
        now = domain.now_iso()
        teacher_id = db.create_user(conn, "Багш", "bagsh@must.edu.mn",
                                    domain.hash_password("teach1234"),
                                    "teacher", "Салбар", now)
        db.create_user(conn, "Өөр багш", "oor@must.edu.mn",
                       domain.hash_password("other1234"), "teacher", "Салбар", now)
        course_id = db.create_course(conn, teacher_id, "Эмийн хими", "MAIL101", 3,
                                     "2026 Намар", now)
        group_id = db.create_group(conn, course_id, "G1", 20, now)
        self.pair_id = db.create_pair(conn, course_id, "хос", now)
        tests = {}
        for kind in ("pre", "post"):
            tests[kind] = db.create_test(conn, course_id, self.pair_id, group_id,
                                         f"T {kind}", kind, "closed",
                                         f"ML{kind.upper()}1", now)
            db.create_question(conn, tests[kind], 1, "Асуулт",
                               {"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"},
                               "A", 2)
        student_id = db.create_student(conn, group_id, "Болд", "B230101",
                                       "B230101", None, now)
        for kind, pct in (("pre", 40), ("post", 80)):
            aid = db.create_attempt(conn, tests[kind], self.pair_id, student_id,
                                    domain.build_match_key(group_id, "B230101"),
                                    "Болд", now)
            db.finish_attempt(conn, aid, pct, 100, pct, now)
        conn.commit()
        conn.close()
        self.login("bagsh@must.edu.mn", "teach1234")

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

    def post_email(self, extra=None):
        data = {"csrf_token": self.token()}
        data.update(extra or {})
        return self.client.post(f"/pairs/{self.pair_id}/report.email",
                                data=data, follow_redirects=True)

    # ---- тохиргоогүй үед ----

    def test_refuses_when_smtp_is_not_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            r = self.post_email()
        self.assertIn("тохиргоо хийгдээгүй", r.data.decode())
        self.assertEqual(FakeSMTP.sent, [])

    def test_button_is_hidden_when_not_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            body = self.client.get(f"/pairs/{self.pair_id}/comparison").data.decode()
        # Товчны текст анхааруулгад ч ордог тул ФОРМЫН байгаа эсэхээр шалгана.
        self.assertNotIn(f"/pairs/{self.pair_id}/report.email", body)
        self.assertIn("тохируулаагүй", body)

    def test_button_is_shown_when_configured(self):
        with mock.patch.dict(os.environ, SMTP_ENV, clear=True):
            body = self.client.get(f"/pairs/{self.pair_id}/comparison").data.decode()
        self.assertIn(f"/pairs/{self.pair_id}/report.email", body)
        self.assertIn("bagsh@must.edu.mn", body)   # хаяг урьдчилан харагдана

    # ---- илгээх ----

    def test_sends_to_the_logged_in_teacher(self):
        with mock.patch.dict(os.environ, SMTP_ENV, clear=True), \
             mock.patch.object(mailer.smtplib, "SMTP", FakeSMTP):
            r = self.post_email()
        self.assertIn("илгээгдлээ", r.data.decode())
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.assertEqual(FakeSMTP.sent[0]["To"], "bagsh@must.edu.mn")
        self.assertEqual(FakeSMTP.sent[0]["From"], SMTP_ENV["EDUTEST_SMTP_FROM"])

    def test_recipient_from_the_form_is_ignored(self):
        """Хамгийн чухал шалгалт: формоор хаяг оруулж болохгүй."""
        with mock.patch.dict(os.environ, SMTP_ENV, clear=True), \
             mock.patch.object(mailer.smtplib, "SMTP", FakeSMTP):
            self.post_email({"to": "halagch@evil.example",
                             "email": "halagch@evil.example"})
        self.assertEqual(FakeSMTP.sent[0]["To"], "bagsh@must.edu.mn")

    def test_both_attachments_are_included(self):
        with mock.patch.dict(os.environ, SMTP_ENV, clear=True), \
             mock.patch.object(mailer.smtplib, "SMTP", FakeSMTP):
            self.post_email()
        names = [p.get_filename() for p in FakeSMTP.sent[0].iter_attachments()]
        self.assertEqual(len(names), 2)
        self.assertTrue(any(n.endswith(".docx") for n in names), names)
        self.assertTrue(any(n.endswith(".xlsx") for n in names), names)

    def test_body_carries_the_summary_numbers(self):
        with mock.patch.dict(os.environ, SMTP_ENV, clear=True), \
             mock.patch.object(mailer.smtplib, "SMTP", FakeSMTP):
            self.post_email()
        body = FakeSMTP.sent[0].get_body(preferencelist=("plain",)).get_content()
        self.assertIn("MAIL101", body)
        self.assertIn("40%", body)      # оролтын дундаж
        self.assertIn("80%", body)      # гаралтын дундаж

    def test_smtp_failure_is_reported_not_crashed(self):
        FakeSMTP.raise_auth_error = True
        with mock.patch.dict(os.environ, SMTP_ENV, clear=True), \
             mock.patch.object(mailer.smtplib, "SMTP", FakeSMTP):
            r = self.post_email()
        self.assertEqual(r.status_code, 200)
        self.assertIn("илгээгдсэнгүй", r.data.decode())
        self.assertNotIn(SMTP_ENV["EDUTEST_SMTP_PASSWORD"], r.data.decode())

    # ---- эрх ----

    def test_other_teacher_is_refused(self):
        self.login("oor@must.edu.mn", "other1234")
        with mock.patch.dict(os.environ, SMTP_ENV, clear=True), \
             mock.patch.object(mailer.smtplib, "SMTP", FakeSMTP):
            r = self.client.post(f"/pairs/{self.pair_id}/report.email",
                                 data={"csrf_token": self.token()})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(FakeSMTP.sent, [])


if __name__ == "__main__":
    unittest.main()
