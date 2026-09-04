# -*- coding: utf-8 -*-
"""
Эхлэлийн оношилгооны анхааруулгууд.

Хувьсагч хоосон биш гэдэг нь утга нь ЗӨВ гэсэн үг биш. Доорх хоёр алдаа
бодит ашиглалтад давтагдан гарсан тул тусад нь баригддаг байх ёстой:

  1. Зааврын `<...>` тэмдэглэгээг хаалттай нь хуулах.
  2. Gmail-д App Password биш, энгийн нууц үг оруулах.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402


def diagnostics_output(env):
    """Оношилгоог ажиллуулж, хэвлэсэн текстийг буцаана."""
    buffer = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(buffer):
        app_module._startup_diagnostics()
    return buffer.getvalue()


class PlaceholderWarningTests(unittest.TestCase):

    def test_value_wrapped_in_angle_brackets_is_flagged(self):
        out = diagnostics_output({"EDUTEST_SMTP_USER": "<gmail хаяг>"})
        self.assertIn("АНХААР", out)
        self.assertIn("EDUTEST_SMTP_USER", out)

    def test_admin_email_in_brackets_is_flagged(self):
        """Бодит ашиглалтад яг ийм алдаа гарч, админ бүртгэл олдохгүй байсан."""
        out = diagnostics_output({"EDUTEST_ADMIN_EMAIL": "<nomingerel@monos.mn>"})
        self.assertIn("АНХААР", out)
        self.assertIn("EDUTEST_ADMIN_EMAIL", out)

    def test_display_name_with_address_is_not_flagged(self):
        """`Нэр <хаяг>` бол имэйлийн стандарт бичиглэл — анхааруулах ёсгүй."""
        out = diagnostics_output({"EDUTEST_SMTP_FROM": "EduTest <a@gmail.com>"})
        self.assertNotIn("АНХААР", out)

    def test_ordinary_values_are_not_flagged(self):
        out = diagnostics_output({"EDUTEST_ENV": "production",
                                  "EDUTEST_DB": "/data/edutest-v3.db"})
        self.assertNotIn("АНХААР", out)


class GmailAppPasswordWarningTests(unittest.TestCase):

    BASE = {"EDUTEST_SMTP_HOST": "smtp.gmail.com", "EDUTEST_SMTP_USER": "a@gmail.com"}

    def test_short_password_with_gmail_is_flagged(self):
        out = diagnostics_output(dict(self.BASE, EDUTEST_SMTP_PASSWORD="arvanNegen"))
        self.assertIn("App Password 16 тэмдэгт", out)

    def test_sixteen_characters_is_accepted(self):
        out = diagnostics_output(dict(self.BASE,
                                      EDUTEST_SMTP_PASSWORD="abcdefghijklmnop"))
        self.assertNotIn("App Password", out)

    def test_other_providers_are_not_second_guessed(self):
        """Gmail биш серверийн нууц үг ямар ч урттай байж болно."""
        out = diagnostics_output({"EDUTEST_SMTP_HOST": "smtp.must.edu.mn",
                                  "EDUTEST_SMTP_PASSWORD": "богино"})
        self.assertNotIn("App Password", out)


if __name__ == "__main__":
    unittest.main()
