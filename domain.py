# -*- coding: utf-8 -*-
"""
EduTest — цэвэр бизнес логик (өгөгдлийн сангаас БҮРЭН хараат бус).

Энэ модуль зөвхөн Python стандарт сангаас хамаарна:
  hashlib, hmac, secrets, re, unicodedata, datetime.

Ингэснээр:
  * логикийг өгөгдлийн сангүйгээр туршиж болно (tests/ хавтас),
  * SQLite -> PostgreSQL шилжилт энэ файлд ЯМАР Ч өөрчлөлт шаардахгүй.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from datetime import datetime, timezone

# Хадгалалтын түлхүүр ҮРГЭЛЖ үсэг (A, B, ...). Дэлгэц дээрх шошго нь
# асуултын төрлөөс хамаарна — `option_label` харна уу. Ингэснээр
# дугаарлалт өөрчлөгдөхөд өгөгдлийн сан хөндөгдөхгүй.
OPTION_KEYS = ("A", "B", "C", "D", "E")

# Харгалзуулах асуулт 4 мөр хэвээр — зүүн/баруун талын хос тэнцүү байх
# ёстой тул сонголтын тоог өсгөх нь энэ төрөлд утгагүй.
MATCH_OPTION_KEYS = ("A", "B", "C", "D")


def option_keys(qtype) -> tuple:
    """Тухайн төрөлд хэдэн сонголт байхыг буцаана."""
    return MATCH_OPTION_KEYS if qtype == "match" else OPTION_KEYS


def option_label(qtype, key) -> str:
    """Дэлгэц дээр харагдах шошго.

    olon songolttoi (multi) -> '1'..'5'
    бусад                   -> 'A'..'E'

    Зөвхөн ХАРАГДАХ БАЙДАЛ. Хадгалалт, оноолт, экспорт бүгд үсгэн
    түлхүүрээр ажиллана.
    """
    key = str(key).strip().upper()
    if qtype == "multi" and key in OPTION_KEYS:
        return str(OPTION_KEYS.index(key) + 1)
    return key


def visible_option_keys(question) -> list:
    """Тухайн асуултад ХАРУУЛАХ сонголтын түлхүүрүүд.

    Хуучин, 4 сонголттой үед үүссэн асуултын `option_e` хоосон байдаг.
    Хоосон сонголтыг алгасахгүй бол оюутанд утгагүй хоосон мөр харагдана.
    Зөвхөн ХАРУУЛАХАД зориулагдсан — форм болон оноолт хөндөгдөхгүй.
    """
    qtype = question_type(question)
    get = question.get if hasattr(question, "get") else (lambda k, d=None: None)
    return [k for k in option_keys(qtype)
            if (get(f"option_{k.lower()}") or "").strip()]


def option_label_list(qtype) -> str:
    """Алдааны мессежид зориулсан 'A/B/C/D/E' эсвэл '1/2/3/4/5'."""
    return "/".join(option_label(qtype, k) for k in option_keys(qtype))

# =====================================================================
# АСУУЛТЫН ТӨРӨЛ
# ---------------------------------------------------------------------
#   single — нэг зөв хариулт (A/B/C/D/E).     Хадгалалт: "A"
#   multi  — олон зөв хариулт.                Хадгалалт: "A,C"
#   match  — харгалзуулах (A↔1, B↔2 гэх мэт). Хадгалалт: "A>2,B>1,C>4,D>3"
#
# `single` нь ХУУЧИН зан төлөв — qtype багана байхгүй эсвэл хоосон бол
# автоматаар single гэж үзнэ (буцаж нийцтэй).
# =====================================================================
QUESTION_TYPES = ("single", "multi", "match")

QUESTION_TYPE_LABELS = {
    "single": "Нэг сонголттой",
    "multi": "Олон сонголттой",
    "match": "Харгалзуулах",
}


def question_type(question) -> str:
    """Асуултын төрлийг найдвартай уншина. Танихгүй утга бол 'single'."""
    value = (question.get("qtype") if hasattr(question, "get") else None) or "single"
    value = str(value).strip().lower()
    return value if value in QUESTION_TYPES else "single"


def parse_option_set(raw) -> list:
    """
    'A,C' эсвэл ['A','C'] -> ['A', 'C'] (эрэмбэлэгдсэн, давхардалгүй).
    Танихгүй тэмдэгт хаягдана.
    """
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple, set)) else str(raw).split(",")
    picked = {str(x).strip().upper() for x in items}
    return [k for k in OPTION_KEYS if k in picked]


def format_option_set(keys) -> str:
    """['C','A'] -> 'A,C' — үргэлж нэг ижил дараалалтай хадгална."""
    return ",".join(parse_option_set(keys))


def match_display_order(question_id) -> list:
    """
    Харгалзуулах асуултын БАРУУН талын хариултуудыг харуулах дараалал.

    Оюутан бүрт ижил, дахин ачаалахад өөрчлөгддөггүй, гэхдээ зөв хариулт
    нь A→1, B→2 гэж эгнэчихгүй байхын тулд асуултын id-аас гаргасан
    тогтмол сэлгэлт ашиглана.

    Буцаах утга: [2, 0, 3, 1] гэх мэт. Жагсаалтын i-р байрлалд (дэлгэц
    дээрх i+1 дугаартай мөр) зүүн талын аль үсгийн хос байгааг заана.
    Жишээ: [2, 0, 3, 1] бол 1-р мөрөнд C-гийн хос, 2-т A-гийнх ...
    """
    digest = hashlib.sha256(str(question_id).encode("utf-8")).digest()
    order = list(range(len(MATCH_OPTION_KEYS)))
    # Fisher-Yates, digest-ийн байтуудыг санамсаргүй эх болгон ашиглана.
    for i in range(len(order) - 1, 0, -1):
        j = digest[i] % (i + 1)
        order[i], order[j] = order[j], order[i]
    return order


def parse_match_answer(raw) -> dict:
    """
    'A>2,B>1' -> {'A': 2, 'B': 1}. Буруу хэсэг чимээгүй хаягдана.
    """
    result = {}
    if not raw:
        return result
    for chunk in str(raw).split(","):
        if ">" not in chunk:
            continue
        left, _, right = chunk.partition(">")
        left = left.strip().upper()
        if left not in MATCH_OPTION_KEYS:
            continue
        try:
            slot = int(right.strip())
        except (TypeError, ValueError):
            continue
        if 1 <= slot <= len(MATCH_OPTION_KEYS):
            result[left] = slot
    return result


def format_match_answer(mapping: dict) -> str:
    """{'B': 1, 'A': 2} -> 'A>2,B>1'"""
    return ",".join(f"{k}>{mapping[k]}" for k in MATCH_OPTION_KEYS if k in mapping)

# =====================================================================
# 1. Нууц үгийн хэшлэлт — ил задгай нууц үг ХЭЗЭЭ Ч хадгалагдахгүй
# =====================================================================
# PBKDF2-HMAC-SHA256, санамсаргүй давс (salt), хадгалах формат:
#   pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
_PBKDF2_ITERATIONS = 240_000


def hash_password(password: str, *, iterations: int = _PBKDF2_ITERATIONS) -> str:
    """Нууц үгийг давс нэмж хэшлэнэ. Ижил нууц үг ч тутамдаа өөр хэш өгнө."""
    if not password:
        raise ValueError("Нууц үг хоосон байж болохгүй.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Хадгалсан хэштэй тулгана. Цагийн зөрүүнд тэсвэртэй харьцуулалт хэрэглэнэ."""
    if not password or not stored:
        return False
    try:
        algorithm, iterations, salt_hex, hash_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


# =====================================================================
# 2. Хувь хүнийг таних — Pre/Post тааруулалтын түлхүүр
# =====================================================================
# ТААРУУЛАЛТЫН ТҮЛХҮҮР (гурвуулаа заавал):
#     class_group_id + normalized_student_code + test_pair_id
#
# ИМЭЙЛ ТААРУУЛАЛТАД ОРОЛЦОХГҮЙ. Оюутан Оролт/Гаралтын тест дээр өөр өөр
# имэйл бичсэн ч, огт бичээгүй ч тааруулалт ажиллана. Имэйл нь зөвхөн
# ХАРУУЛАХ, ЗААВАЛ БИШ талбар — доорх `is_valid_email` нь зөвхөн хадгалахын
# өмнөх энгийн хэлбэрийн шалгалт (оюутан имэйл бичсэн тохиолдолд).

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email: str | None) -> bool:
    """
    Имэйлийн ЭНГИЙН хэлбэрийн шалгалт — зөвхөн хадгалахад.
    Хоосон имэйл ЗӨВШӨӨРӨГДӨНӨ (имэйл заавал биш) тул хоосныг энд шалгахгүй.
    """
    if not email:
        return False
    return bool(_EMAIL_RE.match(unicodedata.normalize("NFKC", str(email)).strip()))


def normalize_student_code(code: str | None) -> str | None:
    """
    Оюутны кодыг ТААРУУЛАХАД зориулж хэвийн болгоно:
      1. эхэн ба төгсгөлийн зайг таслах,
      2. дотор нь санамсаргүй орсон зайг (таб, давхар зай) БҮГДИЙГ арилгах,
      3. ТОМ үсэг болгох.

    Жишээ: '  b23 0101 ' -> 'B230101',  'b230101' -> 'B230101'
    Хоосон эсвэл зөвхөн зайнаас бүрдсэн бол None.

    Эх хэлбэрийг (student_code) ТУСДАА хадгална — энэ нь зөвхөн тааруулалтын түлхүүр.
    """
    if code is None:
        return None
    cleaned = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(code)))
    return cleaned.upper() or None


def normalize_name(name: str | None) -> str:
    """
    Нэрийг ЗӨВХӨН зөрчил тулгахад харьцуулах хэлбэр (том/жижиг үсэг, давхар зай үл хамаарна).
    Харуулахдаа эх хэлбэрийг нь хэрэглэнэ — энэ нь түлхүүр БИШ.
    """
    if not name:
        return ""
    return " ".join(unicodedata.normalize("NFKC", str(name)).split()).casefold()


def build_match_key(class_group_id, normalized_student_code: str | None) -> str:
    """
    Pre/Post тааруулалтын түлхүүр: 'grp:<class_group_id>|code:<NORMALIZED>'
    Хоёр хэсгийн аль нэг нь дутвал таних боломжгүй тул алдаа гаргана.
    """
    if not class_group_id:
        raise ValueError("Ангийн групп заавал шаардлагатай (тааруулалтын түлхүүрийн хэсэг).")
    if not normalized_student_code:
        raise ValueError("Оюутны код заавал шаардлагатай (тааруулалтын түлхүүрийн хэсэг).")
    return f"grp:{class_group_id}|code:{normalized_student_code}"


# =====================================================================
# 3. Автомат бодолт
# =====================================================================

def grade_answer(question: dict, selected_option: str | None) -> tuple[bool, int]:
    """
    Нэг хариултыг шалгаад (зөв_эсэх, авсан_балл) буцаана.

    БҮХ ТӨРӨЛД «бүгд зөв бол бүтэн балл, эс бөгөөс 0» дүрэм үйлчилнэ
    (хэсэгчилсэн оноо байхгүй). Ингэснээр Оролт/Гаралтын хувь нь
    ойлгомжтой, харьцуулахад шударга байна.
    """
    qtype = question_type(question)
    score = int(question["score"])

    if qtype == "multi":
        picked = parse_option_set(selected_option)
        correct = parse_option_set(question.get("correct_option"))
        is_correct = bool(picked) and picked == correct
        return is_correct, score if is_correct else 0

    if qtype == "match":
        answer = parse_match_answer(selected_option)
        if len(answer) != len(MATCH_OPTION_KEYS):
            return False, 0
        order = match_display_order(question["id"])
        # order[slot-1] нь тухайн мөрөнд байгаа зүйл зүүн талын хэддүгээр
        # үсгийнх болохыг заана. Сонгосон мөр зөв үсэгтэй таарах ёстой.
        for index, key in enumerate(MATCH_OPTION_KEYS):
            slot = answer.get(key)
            if slot is None or order[slot - 1] != index:
                return False, 0
        return True, score

    # single — хуучин зан төлөв, өөрчлөгдөөгүй.
    if not selected_option:
        return False, 0
    picked = str(selected_option).strip().upper()
    if picked not in OPTION_KEYS:
        return False, 0
    is_correct = picked == str(question["correct_option"]).strip().upper()
    return is_correct, score if is_correct else 0


def normalize_submitted_answer(question: dict, raw) -> str | None:
    """
    Формоос ирсэн түүхий утгыг хадгалах хэлбэрт хөрвүүлнэ.
    Хоосон бол None (хариулаагүй).
    """
    qtype = question_type(question)
    if qtype == "multi":
        return format_option_set(raw) or None
    if qtype == "match":
        return format_match_answer(raw if isinstance(raw, dict) else parse_match_answer(raw)) or None
    value = str(raw or "").strip().upper()
    return value if value in OPTION_KEYS else None


def score_attempt(questions: list, answers_by_question_id: dict) -> dict:
    """
    Бүх асуултыг автоматаар бодно.
    Хариулаагүй асуулт = 0 балл (алдаа биш).
    """
    max_score = sum(int(q["score"]) for q in questions)
    total_score = 0
    correct_count = 0
    graded = []
    for q in questions:
        picked = answers_by_question_id.get(q["id"])
        is_correct, earned = grade_answer(q, picked)
        total_score += earned
        correct_count += 1 if is_correct else 0
        graded.append(
            {
                "question_id": q["id"],
                "selected_option": normalize_submitted_answer(q, picked),
                "is_correct": is_correct,
                "earned_score": earned,
            }
        )
    return {
        "total_score": total_score,
        "max_score": max_score,
        "correct_count": correct_count,
        "wrong_count": len(questions) - correct_count,
        "percent": round(total_score * 100 / max_score) if max_score else 0,
        "answers": graded,
    }


# =====================================================================
# 4. Pre/Post тааруулалт ба харьцуулалт
# =====================================================================

def _distinct_names(attempts: list) -> list:
    """Нэг оюутны түлхүүр дээр бичигдсэн ЯЛГААТАЙ нэрсийг эх хэлбэрээр нь буцаана."""
    seen, names = set(), []
    for a in attempts:
        raw = (a.get("entered_full_name") or a.get("full_name") or "").strip()
        key = normalize_name(raw)
        if key and key not in seen:
            seen.add(key)
            names.append(raw)
    return names


def find_name_conflicts(attempts: list) -> dict:
    """
    Нэг (групп + хэвийн болгосон оюутны код) дээр ЗӨРӨХ нэр бичигдсэн эсэхийг илрүүлнэ.

    Ижил код, ижил группд өөр нэр орж ирвэл ЧИМЭЭГҮЙ НЭГТГЭХГҮЙ — багш өөрөө
    шалгаж залруулах ёстой тул тэмдэглээд буцаана.

    @returns {match_key: [нэр1, нэр2, ...]} — зөвхөн зөрчилтэй түлхүүрүүд.
    """
    by_key: dict[str, list] = {}
    for a in attempts:
        by_key.setdefault(a["match_key"], []).append(a)
    return {key: _distinct_names(group)
            for key, group in by_key.items() if len(_distinct_names(group)) > 1}


def match_pre_post(pre_attempts: list, post_attempts: list, test_pair_id) -> list:
    """
    Оролт (pre) ба Гаралтын (post) оролдлогуудыг нэг хүн = нэг мөр болгож нэгтгэнэ.

    ТААРУУЛАХ ТҮЛХҮҮР: attempt['match_key'] буюу
        'grp:<class_group_id>|code:<NORMALIZED_STUDENT_CODE>'
    ИМЭЙЛ ОГТ ОРОЛЦОХГҮЙ.

    ХАМРАХ ХҮРЭЭ: зөвхөн `test_pair_id`-тай ижил хосын оролдлогууд. Өөр хосын
    оролдлого санамсаргүй орж ирвэл тааруулалтад ОРОХГҮЙ (хос хооронд холилдохгүй).

    Оролдлого бүрт шаардагдах талбарууд:
        match_key, test_pair_id, percent, full_name, entered_full_name,
        student_code, class_group_name (email нь заавал биш, зөвхөн харуулах)
    """
    if not test_pair_id:
        raise ValueError("test_pair_id заавал шаардлагатай — тааруулалт хосын дотор явагдана.")

    in_pair = [a for a in list(pre_attempts) + list(post_attempts)
               if a.get("test_pair_id") == test_pair_id]
    conflicts = find_name_conflicts(in_pair)
    rows: dict[str, dict] = {}

    def put(attempt, slot):
        if attempt.get("test_pair_id") != test_pair_id:
            return  # өөр хосынх — тааруулахгүй
        key = attempt["match_key"]
        row = rows.setdefault(
            key,
            {
                "match_key": key,
                "full_name": attempt.get("full_name") or "—",
                "student_code": attempt.get("student_code") or "—",
                "email": attempt.get("email") or "",
                "class_group_name": attempt.get("class_group_name") or "—",
                "pre_percent": None,
                "post_percent": None,
                "delta_percent": None,
                "name_conflict": key in conflicts,
                "conflicting_names": conflicts.get(key, []),
            },
        )
        row[f"{slot}_percent"] = attempt["percent"]

    for a in pre_attempts:
        put(a, "pre")
    for a in post_attempts:
        put(a, "post")

    result = []
    for row in rows.values():
        if row["pre_percent"] is not None and row["post_percent"] is not None:
            row["delta_percent"] = row["post_percent"] - row["pre_percent"]
            row["status"] = "matched"
        elif row["pre_percent"] is not None:
            row["status"] = "pre_only"
        else:
            row["status"] = "post_only"
        result.append(row)
    result.sort(key=lambda r: (r["status"] != "matched", r["student_code"]))
    return result


def comparison_summary(rows: list) -> dict:
    """Харьцуулалтын хүснэгтийн нэгтгэсэн үзүүлэлт."""
    matched = [r for r in rows if r["status"] == "matched"]
    improved = [r for r in matched if r["delta_percent"] > 0]
    declined = [r for r in matched if r["delta_percent"] < 0]
    same = [r for r in matched if r["delta_percent"] == 0]
    avg = lambda vals: round(sum(vals) / len(vals), 1) if vals else None  # noqa: E731
    return {
        "total_rows": len(rows),
        "matched_count": len(matched),
        "pre_only_count": len([r for r in rows if r["status"] == "pre_only"]),
        "post_only_count": len([r for r in rows if r["status"] == "post_only"]),
        "avg_pre": avg([r["pre_percent"] for r in matched]),
        "avg_post": avg([r["post_percent"] for r in matched]),
        "avg_delta": avg([r["delta_percent"] for r in matched]),
        "improved_count": len(improved),
        "declined_count": len(declined),
        "same_count": len(same),
        "conflict_count": len([r for r in rows if r.get("name_conflict")]),
    }


# =====================================================================
# 5. Шалгалт (validation) — бүх мессеж монголоор
# =====================================================================

def parse_credit(raw) -> float | None:
    """
    Кредитийг задлан шинжилнэ. 3, 3.5, «3,5» бүгд зөвшөөрөгдөнө
    (Монголд таслалаар бутархай бичих нь түгээмэл).

    Буруу эсвэл 0-ээс бага бол None. Хэт нарийн бутархайг 0.5 хүртэл
    нарийвчлалтай хадгална — 3.333 кредит гэж байдаггүй.
    """
    text = str(raw or "").strip().replace(",", ".")
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value > 30:
        return None
    return round(value, 1)


def format_credit(value) -> str:
    """3.0 -> '3',  3.5 -> '3.5' — илүү тэг харуулахгүй."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else str(number)


def validate_question(text: str, options: dict, correct_option: str, score,
                      qtype: str = "single", matches: dict | None = None) -> list:
    """
    Асуултын формын алдаануудыг жагсаана. Хоосон жагсаалт = зөв.

    `qtype` анхдагчаар 'single' тул хуучин дуудлагууд өөрчлөлтгүй ажиллана.
    """
    errors = []
    qtype = qtype if qtype in QUESTION_TYPES else "single"

    if not (text or "").strip():
        errors.append("Асуултын текст хоосон байна.")

    keys = option_keys(qtype)
    label = "Зүүн талын" if qtype == "match" else ""
    for key in keys:
        if not (options.get(key) or "").strip():
            shown = option_label(qtype, key)
            errors.append(f"{label} {shown} сонголтын текст хоосон байна.".strip())

    if qtype == "single":
        if (correct_option or "").strip().upper() not in keys:
            errors.append(f"Зөв хариултыг {option_label_list(qtype)}-ээс сонгоно уу.")
    elif qtype == "multi":
        picked = parse_option_set(correct_option)
        if len(picked) < 2:
            errors.append("Олон сонголттой асуултад хамгийн багадаа 2 зөв "
                          "хариулт сонгоно уу.")
        elif len(picked) == len(keys):
            errors.append("Бүх сонголт зөв бол асуулт утгагүй болно. "
                          "Дор хаяж нэг буруу хариулт үлдээнэ үү.")
    else:  # match
        matches = matches or {}
        for key in keys:
            if not (matches.get(key) or "").strip():
                errors.append(f"Баруун талын {key} хосын текст хоосон байна.")
        values = [(matches.get(k) or "").strip().casefold() for k in keys]
        filled = [v for v in values if v]
        if len(filled) == len(keys) and len(set(filled)) != len(keys):
            errors.append("Баруун талын хосууд давхардсан байна. "
                          "Хос бүр өвөрмөц байх ёстой.")

    try:
        if int(score) <= 0:
            errors.append("Балл 0-ээс их бүхэл тоо байх ёстой.")
    except (TypeError, ValueError):
        errors.append("Балл 0-ээс их бүхэл тоо байх ёстой.")
    return errors


# =====================================================================
# ТЕСТЭД НЭВТРЭХ ГОРИМ
# ---------------------------------------------------------------------
#   any    — хэн ч ямар ч оюутны код бичээд орно. Гадны сургалт,
#            семинар, кодгүй оролцогчдод тохиромжтой (ХУУЧИН зан төлөв).
#   roster — багшийн урьдчилан бүртгэсэн кодоор л орно. Жинхэнэ ангийн
#            шалгалтад хуурамч кодоор дахин оролдохыг хаана.
# =====================================================================
ENTRY_MODES = ("any", "roster")

ENTRY_MODE_LABELS = {
    "any": "Нээлттэй",
    "roster": "Зөвхөн жагсаалтаас",
}


def entry_mode(test) -> str:
    """Тестийн нэвтрэх горимыг найдвартай уншина. Танихгүй утга бол 'any'
    — хуучин, багана байхгүй сангуудад урьдын зан төлөв хэвээр үлдэнэ."""
    value = (test.get("entry_mode") if hasattr(test, "get") else None) or "any"
    value = str(value).strip().lower()
    return value if value in ENTRY_MODES else "any"


_ROSTER_HEADERS = {"код", "code", "student_code", "оюутны код", "нэр", "name",
                   "full_name", "овог нэр"}


def parse_student_roster(raw: str) -> tuple[list, list]:
    """Олон мөр текстээс оюутны жагсаалт уншина.

    Мөр бүр `код` эсвэл `код,нэр` хэлбэртэй. Таслал, цэг таслал, табыг
    бүгдийг нь салгагч болгон хүлээн авна — багш Excel-ээс шууд хуулж
    буулгахад ямар салгагч орж ирэхийг урьдчилан мэдэх боломжгүй.

    Хоосон мөр, толгой мөрийг алгасна. Ижил код давтагдвал эхнийхийг нь
    авч, үлдсэнийг алдаа болгон буцаана.

    Буцаах: (мөрүүд, алдаанууд). Мөр бүр:
        {"student_code": ..., "normalized": ..., "full_name": ...}
    """
    rows, errors, seen = [], [], set()
    for lineno, line in enumerate((raw or "").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in re.split(r"[,;\t]", line) if p.strip()]
        if not parts:
            continue
        if parts[0].strip().lower() in _ROSTER_HEADERS:
            continue          # Excel-ийн толгой мөр
        code = parts[0]
        name = parts[1] if len(parts) > 1 else ""
        norm = normalize_student_code(code)
        if not norm:
            errors.append(f"{lineno}-р мөр: оюутны код уншигдсангүй.")
            continue
        if norm in seen:
            errors.append(f"{lineno}-р мөр: «{code}» код давхардсан тул алгаслаа.")
            continue
        seen.add(norm)
        rows.append({"student_code": code, "normalized": norm,
                     "full_name": name or code})
    if not rows and not errors:
        errors.append("Жагсаалт хоосон байна.")
    return rows, errors


def validate_student_info(full_name: str, email: str, student_code: str, class_group_id) -> list:
    """
    Оюутны бүртгэлийн формын шалгалт.

    Оюутны код ба ангийн групп нь ТААРУУЛАЛТЫН ТҮЛХҮҮР тул ЗААВАЛ шаардлагатай.
    Имэйл ЗААВАЛ БИШ — бичсэн тохиолдолд л хэлбэрийг нь шалгана.
    """
    errors = []
    if len((full_name or "").strip()) < 2:
        errors.append("Овог нэрээ бүтнээр нь бичнэ үү.")
    if not normalize_student_code(student_code):
        errors.append("Оюутны код хоосон байна (Оролт/Гаралтыг тааруулах түлхүүр).")
    if not class_group_id:
        errors.append("Ангийн группээ сонгоно уу (Оролт/Гаралтыг тааруулах түлхүүр).")
    if (email or "").strip() and not is_valid_email(email):
        errors.append("Имэйл хаяг буруу байна (жишээ: bat@must.edu.mn). Хоосон орхиж болно.")
    return errors


# =====================================================================
# 6. Туслах функцууд
# =====================================================================

def now_iso() -> str:
    """UTC цагийн ISO-8601 тэмдэгт мөр (SQLite-д TEXT, Postgres-д TIMESTAMPTZ)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_share_code(course_code: str, kind: str) -> str:
    """Оюутанд өгөх нийтийн холбоосын код. Жишээ: PHR201-PRE-7F2K."""
    prefix = re.sub(r"[^A-Za-z0-9]", "", (course_code or "TEST")).upper()[:8] or "TEST"
    suffix = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(4))
    return f"{prefix}-{kind.upper()}-{suffix}"
