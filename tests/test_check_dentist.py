import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from agent.tools.check_dentist import check_dentist, find_best_doctor_match


class TestCheckDentist(unittest.TestCase):
    def test_empty_doctor_name(self):
        res = check_dentist("", "Surat")
        self.assertFalse(res["is_authorized"])

    def test_authorized_exact_name(self):
        res = check_dentist("Dr. Jay Patel", "Surat")
        self.assertTrue(res["is_authorized"])
        self.assertEqual(res["doctor_name"], "Dr. Jay Patel")
        self.assertEqual(res["city"], "Surat")

    def test_authorized_without_title(self):
        res = check_dentist("Jay Patel", "Surat")
        self.assertTrue(res["is_authorized"])
        self.assertEqual(res["doctor_name"], "Dr. Jay Patel")
        self.assertEqual(res["city"], "Surat")

    def test_authorized_partial_first_name(self):
        res = check_dentist("Purvi", "Surat")
        self.assertTrue(res["is_authorized"])
        self.assertEqual(res["doctor_name"], "Dr. Purvi Patel")

    def test_authorized_transliteration_virin_k_savani(self):
        # Caller says "Dr. Virin K. Savani" (vowel variation Virin vs Viren)
        res = check_dentist("Dr. Virin K. Savani", "Surat")
        self.assertTrue(res["is_authorized"])
        self.assertEqual(res["doctor_name"], "Dr. Viren K Savani")
        self.assertEqual(res["city"], "Surat")

    def test_authorized_transliteration_thawani_phonetic(self):
        # STT hears "Viren K Thawani" instead of "Viren K Savani"
        res = check_dentist("Viren K Thawani", "Surat")
        self.assertTrue(res["is_authorized"])
        self.assertEqual(res["doctor_name"], "Dr. Viren K Savani")

    def test_authorized_without_middle_initial(self):
        res = check_dentist("Viren Savani", "Surat")
        self.assertTrue(res["is_authorized"])
        self.assertEqual(res["doctor_name"], "Dr. Viren K Savani")

    def test_authorized_kathiria_spelling_variation(self):
        res = check_dentist("Dr. Priyanka Kathiria", "Surat")
        self.assertTrue(res["is_authorized"])
        self.assertEqual(res["doctor_name"], "Dr. Priyanka Kathiriya")

    def test_unauthorized_doctor_anywhere(self):
        res = check_dentist("Rajesh Patel", "Surat")
        self.assertFalse(res["is_authorized"])
        self.assertEqual(res["city"], "Surat")
        self.assertIn("not an authorized Ultimate Smile Design specialist", res["message"])

    def test_doctor_in_different_city(self):
        # Dr. Hetal Buch is authorized in Rajkot, not in Surat
        res = check_dentist("Hetal Buch", "Surat")
        self.assertFalse(res["is_authorized"])
        self.assertEqual(res.get("actual_city"), "Rajkot")
        self.assertIn("NOT in Surat", res["message"])

    def test_doctor_in_correct_city(self):
        res = check_dentist("Dr. Hetal Buch", "Rajkot")
        self.assertTrue(res["is_authorized"])
        self.assertEqual(res["city"], "Rajkot")


if __name__ == "__main__":
    unittest.main()
