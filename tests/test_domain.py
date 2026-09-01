# -*- coding: utf-8 -*-
"""Цэвэр логикийн тест — өгөгдлийн сан шаардахгүй (unittest, стандарт сан)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import domain  # noqa: E402


class TestPasswordHashing(unittest.TestCase):
    def test_hash_is_not_plain_text(self):
        h = domain.hash_password("demo1234")
        self.assertNotIn("demo1234", h)
        self.assertTrue(h.startswith("pbkdf2_sha256$"))

    def test_salt_makes_hashes_unique(self):
        self.assertNotEqual(domain.hash_password("demo1234"), domain.hash_password("demo1234"))

    def test_verify_roundtrip(self):
        h = domain.hash_password("Нууц үг 123")
        self.assertTrue(domain.verify_password("Нууц үг 123", h))
        self.assertFalse(domain.verify_password("буруу", h))

    def test_verify_rejects_broken_hash(self):
        self.assertFalse(domain.verify_password("x", "garbage"))
        self.assertFalse(domain.verify_password("x", ""))
        self.assertFalse(domain.verify_password("", domain.hash_password("x")))

    def test_empty_password_rejected(self):
        with self.assertRaises(ValueError):
            domain.hash_password("")


class TestEmailIsDisplayOnly(unittest.TestCase):
    """Имэйл нь ТААРУУЛАЛТАД оролцохгүй — зөвхөн хадгалахын өмнөх хэлбэрийн шалгалт."""

    def test_identity_matching_helpers_do_not_use_email(self):
        self.assertFalse(hasattr(domain, "normalize_email"))

    def test_basic_format_validation(self):
        self.assertTrue(domain.is_valid_email("bold@must.edu.mn"))
        self.assertTrue(domain.is_valid_email("  Bold@MUST.edu.mn "))
        for bad in (None, "", "   ", "no-at-sign", "a@b", "a b@c.mn"):
            self.assertFalse(domain.is_valid_email(bad), bad)


class TestNormalizeStudentCode(unittest.TestCase):
    def test_trims_outer_whitespace(self):
        self.assertEqual(domain.normalize_student_code("  B230101  "), "B230101")

    def test_uppercases(self):
        self.assertEqual(domain.normalize_student_code("b230101"), "B230101")

    def test_removes_accidental_internal_whitespace(self):
        self.assertEqual(domain.normalize_student_code("b23 0101"), "B230101")
        self.assertEqual(domain.normalize_student_code("B23\t0101"), "B230101")
        self.assertEqual(domain.normalize_student_code("  b23  01 01 "), "B230101")

    def test_all_variants_collapse_to_one_key(self):
        variants = ["B230101", "b230101", " b230101 ", "b23 0101", "  B23 01 01  "]
        self.assertEqual(len({domain.normalize_student_code(v) for v in variants}), 1)

    def test_empty_returns_none(self):
        for bad in (None, "", "   ", "\t"):
            self.assertIsNone(domain.normalize_student_code(bad), repr(bad))


class TestMatchKey(unittest.TestCase):
    def test_key_is_group_plus_normalized_code(self):
        self.assertEqual(domain.build_match_key(3, "B230101"), "grp:3|code:B230101")

    def test_same_code_different_group_is_different_student(self):
        self.assertNotEqual(domain.build_match_key(1, "B230101"),
                            domain.build_match_key(2, "B230101"))

    def test_group_required(self):
        with self.assertRaises(ValueError):
            domain.build_match_key(None, "B230101")

    def test_code_required(self):
        with self.assertRaises(ValueError):
            domain.build_match_key(3, None)

    def test_key_ignores_email_entirely(self):
        self.assertNotIn("@", domain.build_match_key(3, "B230101"))


class TestNormalizeName(unittest.TestCase):
    def test_case_and_spacing_ignored(self):
        self.assertEqual(domain.normalize_name("  Батын   Болд "),
                         domain.normalize_name("батын болд"))

    def test_different_names_differ(self):
        self.assertNotEqual(domain.normalize_name("Сүхийн Ануужин"),
                            domain.normalize_name("Сүхийн Ану"))

    def test_empty(self):
        self.assertEqual(domain.normalize_name(None), "")


def q(qid, correct, score=2):
    return {"id": qid, "correct_option": correct, "score": score,
            "text": f"Асуулт {qid}", "order_no": qid}


class TestScoring(unittest.TestCase):
    def setUp(self):
        self.questions = [q(1, "A", 2), q(2, "B", 2), q(3, "C", 1)]

    def test_all_correct(self):
        r = domain.score_attempt(self.questions, {1: "A", 2: "B", 3: "C"})
        self.assertEqual((r["total_score"], r["max_score"], r["percent"]), (5, 5, 100))
        self.assertEqual((r["correct_count"], r["wrong_count"]), (3, 0))

    def test_partial(self):
        r = domain.score_attempt(self.questions, {1: "A", 2: "D", 3: "C"})
        self.assertEqual(r["total_score"], 3)
        self.assertEqual(r["percent"], 60)

    def test_unanswered_is_zero_not_error(self):
        r = domain.score_attempt(self.questions, {1: "A"})
        self.assertEqual(r["total_score"], 2)
        self.assertEqual(r["wrong_count"], 2)
        self.assertIsNone(r["answers"][1]["selected_option"])

    def test_lowercase_answer_accepted(self):
        r = domain.score_attempt([q(1, "A", 2)], {1: "a"})
        self.assertEqual(r["percent"], 100)

    def test_invalid_option_scores_zero(self):
        r = domain.score_attempt([q(1, "A", 2)], {1: "Z"})
        self.assertEqual(r["total_score"], 0)

    def test_no_questions_no_division_by_zero(self):
        self.assertEqual(domain.score_attempt([], {})["percent"], 0)


PAIR = 1


def attempt(code, percent, name="Оюутан", group=1, pair=PAIR, email=""):
    """Тааруулалтад шаардагдах талбаруудтай оролдлого. Имэйл нь зөвхөн харуулах."""
    norm = domain.normalize_student_code(code)
    return {"match_key": domain.build_match_key(group, norm), "test_pair_id": pair,
            "percent": percent, "full_name": name, "entered_full_name": name,
            "student_code": code, "normalized_student_code": norm,
            "class_group_name": "PH23A", "email": email}


class TestMatchPrePost(unittest.TestCase):
    def test_matches_by_group_and_code(self):
        rows = domain.match_pre_post([attempt("B230101", 40)], [attempt("B230101", 75)], PAIR)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["delta_percent"], 35)
        self.assertEqual(rows[0]["status"], "matched")

    def test_matches_despite_different_emails(self):
        rows = domain.match_pre_post(
            [attempt("B230101", 40, email="school@must.edu.mn")],
            [attempt("B230101", 80, email="personal@gmail.com")], PAIR)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["delta_percent"], 40)

    def test_matches_with_empty_emails(self):
        rows = domain.match_pre_post([attempt("B230101", 30, email="")],
                                     [attempt("B230101", 70, email="")], PAIR)
        self.assertEqual(rows[0]["delta_percent"], 40)

    def test_matches_messy_code_spelling(self):
        rows = domain.match_pre_post([attempt("  b23 0101 ", 20)],
                                     [attempt("B230101", 60)], PAIR)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["delta_percent"], 40)

    def test_same_code_in_different_groups_not_merged(self):
        rows = domain.match_pre_post([attempt("B230101", 40, group=1)],
                                     [attempt("B230101", 90, group=2)], PAIR)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["delta_percent"] is None for r in rows))

    def test_other_pair_attempts_excluded(self):
        rows = domain.match_pre_post([attempt("B230101", 40)],
                                     [attempt("B230101", 90, pair=999)], PAIR)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pre_only")

    def test_pair_id_required(self):
        with self.assertRaises(ValueError):
            domain.match_pre_post([], [], None)

    def test_pre_only_and_post_only(self):
        rows = domain.match_pre_post([attempt("B230101", 40)], [attempt("B230102", 80)], PAIR)
        self.assertEqual(sorted(r["status"] for r in rows), ["post_only", "pre_only"])

    def test_name_conflict_flagged_not_merged(self):
        rows = domain.match_pre_post([attempt("B230202", 40, name="Сүхийн Ануужин")],
                                     [attempt("B230202", 70, name="Сүхийн Ану")], PAIR)
        self.assertEqual(len(rows), 1)                      # оноо нь тааруулагдана
        self.assertTrue(rows[0]["name_conflict"])           # гэхдээ тэмдэглэгдсэн
        self.assertEqual(len(rows[0]["conflicting_names"]), 2)
        self.assertIn("Сүхийн Ануужин", rows[0]["conflicting_names"])
        self.assertIn("Сүхийн Ану", rows[0]["conflicting_names"])

    def test_same_name_different_spacing_is_not_a_conflict(self):
        rows = domain.match_pre_post([attempt("B230101", 40, name="Батын Болд")],
                                     [attempt("B230101", 70, name="  батын   болд ")], PAIR)
        self.assertFalse(rows[0]["name_conflict"])

    def test_find_name_conflicts_directly(self):
        conflicts = domain.find_name_conflicts([
            attempt("B230202", 40, name="Сүхийн Ануужин"),
            attempt("B230202", 70, name="Сүхийн Ану"),
            attempt("B230101", 50, name="Батын Болд"),
        ])
        self.assertEqual(len(conflicts), 1)
        self.assertIn(domain.build_match_key(1, "B230202"), conflicts)

    def test_summary(self):
        rows = domain.match_pre_post(
            [attempt("B230101", 40), attempt("B230102", 50), attempt("B230103", 30)],
            [attempt("B230101", 60), attempt("B230102", 50)], PAIR)
        s = domain.comparison_summary(rows)
        self.assertEqual(s["matched_count"], 2)
        self.assertEqual(s["pre_only_count"], 1)
        self.assertEqual(s["improved_count"], 1)
        self.assertEqual(s["same_count"], 1)
        self.assertEqual(s["avg_delta"], 10.0)
        self.assertEqual(s["conflict_count"], 0)

    def test_empty_input(self):
        self.assertEqual(domain.match_pre_post([], [], PAIR), [])
        self.assertEqual(domain.comparison_summary([])["matched_count"], 0)


class TestValidation(unittest.TestCase):
    OK_OPTIONS = {"A": "нэг", "B": "хоёр", "C": "гурав", "D": "дөрөв"}

    def test_valid_question(self):
        self.assertEqual(domain.validate_question("Асуулт?", self.OK_OPTIONS, "B", 2), [])

    def test_missing_pieces_reported_in_mongolian(self):
        errors = domain.validate_question("", {"A": "", "B": "х", "C": "х", "D": "х"}, "Z", 0)
        self.assertTrue(any("текст хоосон" in e for e in errors))
        self.assertTrue(any("A сонголтын" in e for e in errors))
        self.assertTrue(any("A/B/C/D" in e for e in errors))
        self.assertTrue(any("Балл" in e for e in errors))

    def test_student_info_validation(self):
        self.assertEqual(domain.validate_student_info("Батын Болд", "b@must.edu.mn", "B1", "3"), [])

    def test_email_is_optional(self):
        self.assertEqual(domain.validate_student_info("Батын Болд", "", "B1", "3"), [])
        self.assertEqual(domain.validate_student_info("Батын Болд", None, "B1", "3"), [])

    def test_invalid_email_rejected_only_when_provided(self):
        errors = domain.validate_student_info("Батын Болд", "буруу", "B1", "3")
        self.assertEqual(len(errors), 1)
        self.assertIn("Имэйл", errors[0])

    def test_code_and_group_are_required(self):
        errors = domain.validate_student_info("", "", "   ", "")
        self.assertEqual(len(errors), 3)
        self.assertTrue(any("Оюутны код" in e for e in errors))
        self.assertTrue(any("групп" in e for e in errors))


class TestShareCode(unittest.TestCase):
    def test_format(self):
        code = domain.generate_share_code("PHR201", "pre")
        self.assertTrue(code.startswith("PHR201-PRE-"))
        self.assertEqual(len(code.split("-")[2]), 4)

    def test_codes_differ(self):
        codes = {domain.generate_share_code("PHR201", "pre") for _ in range(20)}
        self.assertGreater(len(codes), 15)


if __name__ == "__main__":
    unittest.main(verbosity=2)
