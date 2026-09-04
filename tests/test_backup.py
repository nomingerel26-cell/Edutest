# -*- coding: utf-8 -*-
"""
Нөөцлөлтийн скриптийн тест.

Нөөцлөлт нь СЭРГЭЭХ үед л хэрэгтэй болдог — тэр үед гэмтэлтэй байсныг
мэдэх нь оройтсон байна. Тиймээс энд:
  - бичилт явж байх үед авсан хуулбар бүрэн бүтэн эсэх,
  - rotation ХУУЧНЫГ нь устгаж байгаа эсэх,
  - алдааг exit code-оор мэдээлж байгаа эсэхийг шалгана.
"""

import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest import mock  # noqa: E402

import backup  # noqa: E402
import database as db  # noqa: E402
import domain  # noqa: E402


class BackupTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmpdir.name)
        self.db_path = self.root / "src.db"
        self.out = self.root / "backups"
        db.init_db(self.db_path, drop_existing=True)

        conn = db.connect(self.db_path)
        now = domain.now_iso()
        db.create_user(conn, "Багш", "t@b.mn", domain.hash_password("teach1234"),
                       "teacher", None, now)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def run_backup(self, keep=14):
        return backup.main(["--source", str(self.db_path),
                            "--out", str(self.out), "--keep", str(keep)])

    def files(self):
        return sorted(self.out.glob("edutest-*.db"),
                      key=lambda p: (p.stat().st_mtime_ns, p.name))

    # ---- үндсэн ----

    def test_backup_is_readable_and_has_the_data(self):
        self.assertEqual(self.run_backup(), 0)
        files = self.files()
        self.assertEqual(len(files), 1)
        conn = sqlite3.connect(str(files[0]))
        try:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        finally:
            conn.close()

    def test_missing_source_reports_failure(self):
        code = backup.main(["--source", str(self.root / "alga.db"),
                            "--out", str(self.out)])
        self.assertEqual(code, 1)
        self.assertFalse(self.out.exists())

    def test_no_partial_file_is_left_behind(self):
        self.run_backup()
        self.assertEqual(list(self.out.glob("*.partial")), [])

    def test_repeated_runs_do_not_overwrite_each_other(self):
        """Нэг секундэд хоёр удаа ажиллуулахад нэр давхцах ёсгүй."""
        self.run_backup()
        self.run_backup()
        self.assertEqual(len(self.files()), 2)

    # ---- бичилт явж байх үед ----

    def test_backup_while_writes_are_in_flight(self):
        """Ажиллаж байгаа санг зогсоолгүйгээр нөөцлөх нь гол зорилго."""
        stop = threading.Event()
        errors = []

        def writer():
            conn = db.connect(self.db_path)
            try:
                n = 0
                while not stop.is_set():
                    n += 1
                    code = f"W{n}"
                    db.create_user(conn, f"Х {code}", f"{code}@b.mn",
                                   domain.hash_password("pw12345678"),
                                   "teacher", None, domain.now_iso())
                    conn.commit()
            except Exception as exc:      # noqa: BLE001
                errors.append(repr(exc))
            finally:
                conn.close()

        t = threading.Thread(target=writer)
        t.start()
        time.sleep(0.05)
        try:
            self.assertEqual(self.run_backup(), 0)
        finally:
            stop.set()
            t.join(timeout=30)

        self.assertEqual(errors, [], "нөөцлөлт бичилтийг тасалдуулав")
        conn = sqlite3.connect(str(self.files()[-1]))
        try:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            conn.close()

    # ---- rotation ----

    def test_rotation_keeps_the_newest_and_deletes_the_oldest(self):
        """Нэрээр биш, ҮҮСГЭСЭН ЦАГААР эрэмбэлэх ёстой.

        `-2` дагавартай нэр нь ASCII-д дагаваргүйгээс ӨМНӨ ордог тул
        нэрээр эрэмбэлбэл хамгийн хуучныг шинэ мэт үзэж, буруу файл
        устгана.
        """
        self.out.mkdir(parents=True)
        names = ["edutest-20260101-000000.db",      # хамгийн ХУУЧИН
                 "edutest-20260101-000000-2.db",
                 "edutest-20260101-000000-3.db",
                 "edutest-20260101-000000-4.db"]    # хамгийн ШИНЭ
        for index, name in enumerate(names):
            path = self.out / name
            path.write_bytes(b"x")
            stamp = 1_000_000 + index * 60          # тодорхой цагийн дараалал
            os.utime(path, (stamp, stamp))

        removed = backup.rotate(self.out, keep=2)

        self.assertEqual([p.name for p in removed], names[:2])
        self.assertEqual(sorted(p.name for p in self.out.glob("*.db")),
                         sorted(names[2:]))

    def test_rotation_keeps_everything_when_under_the_limit(self):
        self.run_backup(keep=5)
        self.run_backup(keep=5)
        self.assertEqual(len(self.files()), 2)

    def test_rotation_trims_to_the_limit(self):
        for _ in range(5):
            self.run_backup(keep=3)
        self.assertEqual(len(self.files()), 3)


class BackupListingTests(unittest.TestCase):
    """Хуваарьт нөөцлөлт ажилласан эсэхийг UI-аас харах боломж."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self.tmpdir.name) / "backups"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_missing_directory_is_empty_not_an_error(self):
        self.assertEqual(backup.list_backups(self.out), [])

    def test_newest_first(self):
        self.out.mkdir(parents=True)
        for index, name in enumerate(["edutest-a.db", "edutest-b.db", "edutest-c.db"]):
            path = self.out / name
            path.write_bytes(b"x" * (index + 1) * 1024)
            os.utime(path, (2_000_000 + index * 60,) * 2)
        rows = backup.list_backups(self.out)
        self.assertEqual([r["name"] for r in rows],
                         ["edutest-c.db", "edutest-b.db", "edutest-a.db"])
        self.assertEqual(rows[0]["size_kb"], 3.0)

    def test_unrelated_files_are_ignored(self):
        self.out.mkdir(parents=True)
        (self.out / "edutest-ok.db").write_bytes(b"x")
        (self.out / "readme.txt").write_bytes(b"x")
        (self.out / "edutest-partial.db.partial").write_bytes(b"x")
        self.assertEqual([r["name"] for r in backup.list_backups(self.out)],
                         ["edutest-ok.db"])


class BackupRouteTests(unittest.TestCase):
    """/admin/backup — зөвхөн админ, бүрэн бүтэн файл буцаана."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "r.db")
        db.DB_PATH = pathlib.Path(self.db_path)
        db.init_db(self.db_path, drop_existing=True)

        import app as app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app_module.app.test_client()

        conn = db.connect(self.db_path)
        now = domain.now_iso()
        db.create_user(conn, "Админ", "a@b.mn", domain.hash_password("admin1234"),
                       "admin", None, now)
        db.create_user(conn, "Багш", "t@b.mn", domain.hash_password("teach1234"),
                       "teacher", None, now)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def login(self, email, password):
        body = self.client.get("/login").data.decode()
        token = body.split('name="csrf_token"')[1].split('value="')[1].split('"')[0]
        self.client.post("/login", data={"csrf_token": token, "email": email,
                                         "password": password}, follow_redirects=True)

    def test_admin_downloads_a_valid_database(self):
        self.login("a@b.mn", "admin1234")
        r = self.client.get("/admin/backup")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers.get("Content-Disposition", ""))

        out = pathlib.Path(self.tmpdir.name) / "downloaded.db"
        out.write_bytes(r.data)
        conn = sqlite3.connect(str(out))
        try:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)
        finally:
            conn.close()

    def test_admin_page_says_when_no_backup_has_run(self):
        self.login("a@b.mn", "admin1234")
        with mock.patch.object(backup, "list_backups", return_value=[]):
            body = self.client.get("/admin/users").data.decode()
        self.assertIn("хараахан ажиллаагүй", body)

    def test_admin_page_lists_existing_backups(self):
        self.login("a@b.mn", "admin1234")
        fake = [{"name": "edutest-20260904-020000.db", "size_kb": 118.0,
                 "mtime": 1_800_000_000.0}]
        with mock.patch.object(backup, "list_backups", return_value=fake):
            body = self.client.get("/admin/users").data.decode()
        self.assertIn("edutest-20260904-020000.db", body)
        self.assertIn("118.0 KB", body)
        self.assertNotIn("хараахан ажиллаагүй", body)

    def test_teacher_is_refused(self):
        self.login("t@b.mn", "teach1234")
        self.assertEqual(self.client.get("/admin/backup").status_code, 403)

    def test_anonymous_is_refused(self):
        self.assertIn(self.client.get("/admin/backup").status_code, (302, 401, 403))


if __name__ == "__main__":
    unittest.main()
