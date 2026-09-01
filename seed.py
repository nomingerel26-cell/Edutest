# -*- coding: utf-8 -*-
"""
EduTest — өгөгдлийн санг үүсгэж, жишээ өгөгдлөөр дүүргэх скрипт.

Ажиллуулах:
    python3 seed.py            # edutest.db байхгүй бол үүсгэнэ (байвал зөвхөн схемээ шинэчилнэ)
    python3 seed.py --reset    # edutest.db-г УСТГААД дахин цэвэр үүсгэнэ

Үүсгэх жишээ өгөгдөл:
    * 1 админ + 2 багш (нууц үг нь PBKDF2-SHA256-аар хэшлэгдэнэ)
    * 2 хичээл, 3 ангийн групп
    * 1 Оролт/Гаралтын тестийн хос (нээлттэй), тус бүр 5 асуулт (A/B/C/D)
    * 6 оюутан, 11 бөглөсөн оролдлого (5 нь Pre+Post хоёулаа => харьцуулалт бэлэн)

Тааруулалтын жишээнүүд (имэйл ТААРУУЛАЛТАД ОРОЛЦОХГҮЙ):
    * Оролт/Гаралт дээр ӨӨР имэйл бичсэн оюутан    -> кодоор зөв тааруулна
    * Имэйл огт бичээгүй оюутан                    -> кодоор зөв тааруулна
    * Код нь жижиг үсэг, санамсаргүй зайтай бичсэн  -> хэвийн болгож тааруулна
    * Ижил код, ижил группд ӨӨР нэр бичигдсэн      -> «нэрийн зөрчил» болж багшид харагдана
"""

from __future__ import annotations

import sys

import database as db
import domain

# ---------------------------------------------------------------------
# Жишээ нэвтрэлтүүд (README-д мөн бичигдсэн)
# ---------------------------------------------------------------------
ADMIN = ("Д.Алтанцэцэг", "admin@must.edu.mn", "admin1234", "admin", "Сургалтын алба")
TEACHERS = [
    ("Б.Сарантуяа", "saraa@must.edu.mn", "demo1234", "teacher", "Эм зүйн салбар"),
    ("Г.Мөнхбат", "munkhbat@must.edu.mn", "demo1234", "teacher", "Хими технологийн салбар"),
]

PRE_QUESTIONS = [
    ("Ацетилсалицилын хүчлийн үндсэн үйлдэл юу вэ?",
     {"A": "Циклооксигеназыг дарангуйлах", "B": "Бета рецепторыг блоклох",
      "C": "Ходоодны хүчлийг саармагжуулах", "D": "Мөгөөрсний хучилтыг нэмэгдүүлэх"}, "A", 2),
    ("Парацетамолын химийн нэр аль нь вэ?",
     {"A": "Ацетилсалицилын хүчил", "B": "N-ацетил-п-аминофенол",
      "C": "Ибупрофен", "D": "Метамизол натри"}, "B", 2),
    ("Бензодиазепины эмийн үндсэн бүтцийн цөм аль нь вэ?",
     {"A": "Индол", "B": "Бензодиазепин", "C": "Пиримидин", "D": "Стероид"}, "B", 2),
    ("Эмийн бодисын тогтвортой байдалд хамгийн их нөлөөлөх хүчин зүйл аль нь вэ?",
     {"A": "Гэрэл, температур, чийг", "B": "Савлагааны өнгө",
      "C": "Үйлдвэрлэгчийн лого", "D": "Худалдааны нэр"}, "A", 2),
    ("Эмийн бодисын хүчиллэг/шүлтлэг чанарыг илэрхийлэх үзүүлэлт аль нь вэ?",
     {"A": "pKa", "B": "Молекул жин", "C": "Хайлах температур", "D": "Нягт"}, "A", 2),
]

POST_QUESTIONS = [
    ("Морфины үндсэн фармакологийн ангилал юу вэ?",
     {"A": "Опиоид анальгетик", "B": "Антибиотик",
      "C": "Диуретик", "D": "Антигистамин"}, "A", 2),
    ("Сульфаниламидын бүлгийн эмийн үйлдлийн механизм юу вэ?",
     {"A": "Фолийн хүчлийн нийлэгжилтийг саатуулах", "B": "Эсийн ханыг задлах",
      "C": "ДНХ-г шууд тасалдуулах", "D": "Рибосомыг бүрэн устгах"}, "A", 2),
    ("Титрлэх шинжилгээнд ямар үзүүлэлтийг хэмждэг вэ?",
     {"A": "Урвалжийн эзлэхүүн", "B": "Гэрлийн өнгө",
      "C": "Савны жин", "D": "Агаарын даралт"}, "A", 2),
    ("Эмийн бодисын биологийн ашиглагдах чанар (bioavailability) гэж юу вэ?",
     {"A": "Системийн эргэлтэд орсон идэвхтэй бодисын хувь",
      "B": "Эмийн савлагааны хэмжээ", "C": "Хадгалах хугацаа",
      "D": "Үйлдвэрлэлийн өртөг"}, "A", 2),
    ("Хроматографийн аргыг юунд хэрэглэдэг вэ?",
     {"A": "Холимог бодисыг салгах, тодорхойлох", "B": "Эмийн үнийг тогтоох",
      "C": "Савлагааг ариутгах", "D": "Тунг нэмэгдүүлэх"}, "A", 2),
]

# Оюутны жишээ бүртгэл.
# ТААРУУЛАХ ТҮЛХҮҮР: групп + хэвийн болгосон оюутны код. Имэйл ОРОЛЦОХГҮЙ.
# Тухайн бүртгэлд Оролт ба Гаралтын үед бичсэн нэр/имэйл/кодыг ТУСАД нь өгнө —
# ингэснээр «өөр имэйл», «хоосон имэйл», «эмх замбараагүй код», «нэрийн зөрчил»
# бүх тохиолдол демо өгөгдөлд бодитоор үүснэ.
#
# (группын индекс,
#  оролт:  (нэр, имэйл, оюутны код, хариултууд),
#  гаралт: (нэр, имэйл, оюутны код, хариултууд) эсвэл None)
STUDENTS = [
    # 1) Ердийн тохиолдол — Оролт/Гаралт дээр бүх зүйл ижил.
    (0,
     ("Батын Болд", "bold@must.edu.mn", "B230101", ["A", "B", "B", "A", "A"]),
     ("Батын Болд", "bold@must.edu.mn", "B230101", ["A", "A", "A", "A", "A"])),

    # 2) ӨӨР ИМЭЙЛ — оролт дээр сургуулийн, гаралт дээр хувийн имэйл.
    #    Хуучин (имэйлээр таарах) логикоор ТААРАХГҮЙ байсан; одоо кодоор таарна.
    (0,
     ("Доржийн Сараа", "saraa@must.edu.mn", "B230102", ["A", "C", "B", "B", "A"]),
     ("Доржийн Сараа", "sara.dorj@gmail.com", "B230102", ["A", "A", "A", "A", "A"])),

    # 3) ИМЭЙЛГҮЙ — имэйл заавал биш тул хоосон орхисон.
    (0,
     ("Ганбатын Номин", None, "B230103", ["C", "D", "A", "A", "B"]),
     ("Ганбатын Номин", None, "B230103", ["A", "A", "B", "A", "A"])),

    # 4) ЭМХ ЗАМБАРААГҮЙ КОД — жижиг үсэг, урд хойно ба дунд нь зай.
    #    '  b23 0201 ' ба 'B230201' хоёр ижил оюутан гэж танигдана.
    (1,
     ("Лхагвын Тэмүүлэн", "temuulen@must.edu.mn", "  b23 0201 ", ["A", "B", "B", "A", "A"]),
     ("Лхагвын Тэмүүлэн", "temuulen@must.edu.mn", "B230201", ["A", "A", "A", "B", "A"])),

    # 5) НЭРИЙН ЗӨРЧИЛ — ижил групп, ижил код, гэвч өөр нэр бичигдсэн.
    #    Систем ЧИМЭЭГҮЙ НЭГТГЭХГҮЙ: үр дүнг хадгална, багшид анхааруулга харуулна.
    (1,
     ("Сүхийн Ануужин", "anuujin@must.edu.mn", "B230202", ["B", "B", "C", "A", "D"]),
     ("Сүхийн Ану", "anuujin@must.edu.mn", "B230202", ["A", "B", "A", "A", "A"])),

    # 6) Зөвхөн Оролтын тест өгсөн — харьцуулалтад «Зөвхөн оролт» болж харагдана.
    (1,
     ("Чойжилын Эрдэнэ", "erdene@must.edu.mn", "B230203", ["A", "B", "A", "A", "A"]),
     None),
]


def seed(reset: bool = False) -> None:
    if reset:
        print("edutest.db устгаж, шинээр үүсгэж байна…")
    db.init_db(drop_existing=reset)

    conn = db.connect()
    try:
        if db.get_user_by_email(conn, ADMIN[1]):
            print("Жишээ өгөгдөл аль хэдийн ачаалагдсан байна. Дахин ачаалах бол:")
            print("    python3 seed.py --reset")
            return

        now = domain.now_iso()

        # --- Хэрэглэгчид: нууц үгийг хэзээ ч ил хадгалахгүй ---------------
        admin_id = db.create_user(conn, ADMIN[0], ADMIN[1],
                                  domain.hash_password(ADMIN[2]), ADMIN[3], ADMIN[4], now)
        teacher_ids = [
            db.create_user(conn, t[0], t[1], domain.hash_password(t[2]), t[3], t[4], now)
            for t in TEACHERS
        ]
        print(f"✓ Хэрэглэгч: 1 админ (id={admin_id}), {len(teacher_ids)} багш")

        # --- Хичээл --------------------------------------------------------
        course_id = db.create_course(conn, teacher_ids[0], "Эмийн хими 1", "PHR201", 3, "2026 Намар", now)
        course2_id = db.create_course(conn, teacher_ids[1], "Фармакологи 1", "PHR210", 3, "2026 Намар", now)
        print("✓ Хичээл: PHR201, PHR210")

        # --- Ангийн групп --------------------------------------------------
        group_ids = [
            db.create_group(conn, course_id, "PH23A", 26, now),
            db.create_group(conn, course_id, "PH23B", 24, now),
        ]
        db.create_group(conn, course2_id, "PH23A", 26, now)
        print("✓ Групп: PH23A, PH23B (PHR201) · PH23A (PHR210)")

        # --- Тестийн хос ба тестүүд -----------------------------------------
        pair_id = db.create_pair(conn, course_id, "Эмийн хими 1 · 2026 Намар", now)
        pre_test_id = db.create_test(conn, course_id, pair_id, group_ids[0],
                                     "Эмийн хими 1 — Оролтын тест", "pre", "open",
                                     "PHR201-PRE-7F2K", now)
        post_test_id = db.create_test(conn, course_id, pair_id, group_ids[0],
                                      "Эмийн хими 1 — Гаралтын тест", "post", "open",
                                      "PHR201-POST-B4M9", now)
        print("✓ Тестийн хос: PHR201-PRE-7F2K + PHR201-POST-B4M9 (хоёулаа нээлттэй)")

        # --- Асуултууд -------------------------------------------------------
        for test_id, bank in ((pre_test_id, PRE_QUESTIONS), (post_test_id, POST_QUESTIONS)):
            for order_no, (text, options, correct, score) in enumerate(bank, start=1):
                db.create_question(conn, test_id, order_no, text, options, correct, score)
        print(f"✓ Асуулт: {len(PRE_QUESTIONS)} оролт + {len(POST_QUESTIONS)} гаралт (A/B/C/D)")

        # --- Оюутан ба оролдлого ---------------------------------------------
        pre_questions = db.list_questions(conn, pre_test_id)
        post_questions = db.list_questions(conn, post_test_id)
        attempts = 0

        for group_idx, pre_reg, post_reg in STUDENTS:
            group_id = group_ids[group_idx]
            student_id = None

            for test_id, questions, registration in (
                (pre_test_id, pre_questions, pre_reg),
                (post_test_id, post_questions, post_reg),
            ):
                if registration is None:
                    continue
                name, email, raw_code, picks = registration
                norm_code = domain.normalize_student_code(raw_code)

                # Таних: групп + хэвийн болгосон код (имэйл ОРОЛЦОХГҮЙ).
                student = db.get_student_by_code(conn, group_id, norm_code)
                if student:
                    student_id = student["id"]
                    # Нэр зөрсөн ч students.full_name-ийг ДАРЖ БИЧИХГҮЙ.
                    db.update_student_email(conn, student_id, email)
                else:
                    student_id = db.create_student(conn, group_id, name, raw_code.strip(),
                                                   norm_code, email, now)

                attempt_id = db.create_attempt(
                    conn, test_id, pair_id, student_id,
                    domain.build_match_key(group_id, norm_code), name, now,
                )
                answers = {q["id"]: pick for q, pick in zip(questions, picks)}
                result = domain.score_attempt(questions, answers)
                db.save_answers(conn, attempt_id, result["answers"])
                db.finish_attempt(conn, attempt_id, result["total_score"],
                                  result["max_score"], result["percent"], now)
                attempts += 1

        conn.commit()
        print(f"✓ Оюутан: {len(STUDENTS)} · Дуусгасан оролдлого: {attempts}")
        print(f"\nӨгөгдлийн сан бэлэн: {db.DB_PATH}")
        print("Нэвтрэх мэдээлэл:")
        print(f"    Админ — {ADMIN[1]} / {ADMIN[2]}")
        print(f"    Багш  — {TEACHERS[0][1]} / {TEACHERS[0][2]}")
        print("\nАжиллуулах:  python3 app.py   ->  http://127.0.0.1:5000")
    finally:
        conn.close()


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
