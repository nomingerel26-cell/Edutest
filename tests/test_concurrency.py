# -*- coding: utf-8 -*-
"""
Зэрэгцээ бичилт.

gunicorn нь 2 worker x 4 thread ажиллуулдаг. Анги бүтнээрээ нэг зэрэг
хариултаа илгээхэд SQLite-ийн анхдагч журналын горим бүх санг түгжиж,
зарим хүсэлт `database is locked` алдаа авдаг. WAL + busy_timeout үүнийг
шийднэ.

Түр зуурын өгөгдлийн санд ажиллана — edutest.db-д хүрэхгүй.
"""

import os
import pathlib
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import domain  # noqa: E402


class ConnectionPragmaTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "c.db")
        db.init_db(self.db_path, drop_existing=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_wal_and_busy_timeout_are_enabled(self):
        conn = db.connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            # Гадаад түлхүүр урьдын адил идэвхтэй хэвээр байх ёстой —
            # cascade устгалт үүн дээр тулгуурладаг.
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        finally:
            conn.close()

    def test_drop_existing_clears_wal_sidecar_files(self):
        conn = db.connect(self.db_path)
        db.create_user(conn, "A", "a@a.mn", domain.hash_password("pw12345678"),
                       "admin", None, domain.now_iso())
        conn.commit()
        conn.close()

        db.init_db(self.db_path, drop_existing=True)

        conn = db.connect(self.db_path)
        try:
            self.assertEqual(db.list_users(conn), [],
                             "-wal файлаас хуучин мөр буцаж уншигдлаа")
        finally:
            conn.close()


class ConcurrentWriteTests(unittest.TestCase):
    """Олон thread нэг зэрэг бичихэд алдаа гарах ёсгүй."""

    THREADS = 8
    ROWS_PER_THREAD = 10

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "cc.db")
        db.init_db(self.db_path, drop_existing=True)

        conn = db.connect(self.db_path)
        now = domain.now_iso()
        teacher_id = db.create_user(conn, "Багш", "t@c.mn",
                                    domain.hash_password("teach1234"),
                                    "teacher", None, now)
        course_id = db.create_course(conn, teacher_id, "Х", "CNC101", 3, "2026", now)
        self.group_id = db.create_group(conn, course_id, "G", 50, now)
        self.test_id = db.create_test(conn, course_id, None, self.group_id, "T",
                                      "pre", "open", "CNCPRE1", now)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_parallel_submissions_do_not_hit_database_is_locked(self):
        errors = []
        barrier = threading.Barrier(self.THREADS)

        def worker(index):
            try:
                barrier.wait(timeout=10)      # бүгд ЯГ нэг зэрэг эхэлнэ
                conn = db.connect(self.db_path)
                try:
                    now = domain.now_iso()
                    for n in range(self.ROWS_PER_THREAD):
                        code = f"S{index}-{n}"
                        student_id = db.create_student(
                            conn, self.group_id, f"Оюутан {code}", code,
                            domain.normalize_student_code(code), None, now)
                        db.create_attempt(conn, self.test_id, None, student_id,
                                          domain.build_match_key(self.group_id, code),
                                          f"Оюутан {code}", now)
                        conn.commit()
                finally:
                    conn.close()
            except Exception as exc:          # noqa: BLE001 — тестэд бүгдийг барина
                errors.append(f"thread {index}: {exc!r}")

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(self.THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [])

        conn = db.connect(self.db_path)
        try:
            rows = db.fetch_all(conn, "SELECT COUNT(*) AS v FROM attempts")
            self.assertEqual(rows[0]["v"], self.THREADS * self.ROWS_PER_THREAD)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
