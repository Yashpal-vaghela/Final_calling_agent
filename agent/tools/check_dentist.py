import difflib
import json
import os
import re
from typing import Dict, Any, Optional, List

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
AUTH_DENTISTS_FILE = os.path.join(DATA_DIR, "authorized_dentists.json")

def _norm_doc(name: str) -> str:
    cleaned = re.sub(r"^(dr\.?|doctor|mr\.?|mrs\.?|ms\.?)\s+", "", name.strip(), flags=re.IGNORECASE)
    return re.sub(r"[^\w\s]", " ", cleaned).strip().lower()

def norm_phonetic_word(w: str) -> str:
    w = w.lower()
    w = re.sub(r"th", "s", w)
    w = re.sub(r"sh", "s", w)
    w = re.sub(r"ee", "i", w)
    w = re.sub(r"oo", "u", w)
    w = re.sub(r"aa", "a", w)
    w = re.sub(r"ph", "f", w)
    w = re.sub(r"b|v|w", "v", w)
    return w

def consonant_skeleton(word: str) -> str:
    word = norm_phonetic_word(word)
    if not word:
        return ""
    first = word[0]
    rest = re.sub(r"[aeiouy]", "", word[1:])
    return first + rest

def is_word_match(w1: str, w2: str) -> bool:
    if w1 == w2:
        return True
    sk1 = consonant_skeleton(w1)
    sk2 = consonant_skeleton(w2)
    if sk1 and sk1 == sk2:
        return True
    return False

def find_best_doctor_match(input_name: str, doctor_list: List[str]) -> Optional[str]:
    """
    Finds the best matching doctor from doctor_list for an input name.
    Supports exact match, whole-string equality, and phonetic consonant skeleton matching.
    Correctly matches transliteration variations (e.g. 'Virin K. Savani' -> 'Dr. Viren K Savani',
    'Viren K Thawani' -> 'Dr. Viren K Savani') while strictly distinguishing different names
    (e.g. 'Rajesh' vs 'Rakesh') and rejecting unlisted names.
    """
    norm_in = " ".join(_norm_doc(input_name).split())
    in_words = [w for w in norm_in.split() if w]
    if not in_words:
        return None

    # Step 1: Exact normalized string match
    for d in doctor_list:
        norm_d = " ".join(_norm_doc(d).split())
        if norm_in == norm_d:
            return d

    # Step 2: Phonetic consonant token matching
    candidates = []
    for d in doctor_list:
        norm_d = " ".join(_norm_doc(d).split())
        d_words = [w for w in norm_d.split() if w]
        
        all_matched = True
        for iw in in_words:
            if len(iw) == 1:
                if not any(dw == iw for dw in d_words):
                    all_matched = False
                    break
            else:
                found = False
                for dw in d_words:
                    if is_word_match(iw, dw):
                        found = True
                        break
                if not found:
                    all_matched = False
                    break

        if all_matched:
            # Avoid matching generic sole surnames if multiple doctors share it
            if len(in_words) == 1 and in_words[0] in ["patel", "shah", "singh", "sharma", "agrawal"]:
                continue
            candidates.append(d)

    if len(candidates) == 1:
        return candidates[0]
    elif len(candidates) > 1:
        return max(candidates, key=lambda d: difflib.SequenceMatcher(None, norm_in, " ".join(_norm_doc(d).split())).ratio())

    return None

def check_dentist(doctor_name: str, city: str = "") -> Dict[str, Any]:
    """
    Check if a specific doctor is an authorized Ultimate Smile Design specialist in the specified city.
    """
    doc_clean = doctor_name.strip()
    if not doc_clean:
        return {"is_authorized": False, "message": "No doctor name provided."}

    auth_dentists = {}
    if os.path.exists(AUTH_DENTISTS_FILE):
        try:
            with open(AUTH_DENTISTS_FILE, "r", encoding="utf-8") as f:
                auth_dentists = json.load(f)
        except Exception:
            pass

    city_clean = city.strip()
    city_dentists = []
    matched_city = None
    for c in auth_dentists:
        if c.lower() == city_clean.lower():
            city_dentists = auth_dentists[c]
            matched_city = c
            break

    # 1. Check in the requested city
    matched_doctor = None
    if city_dentists:
        matched_doctor = find_best_doctor_match(doc_clean, city_dentists)

    if matched_doctor:
        return {
            "is_authorized": True,
            "doctor_name": matched_doctor,
            "city": matched_city,
            "message": f"{matched_doctor} is an authorized Ultimate Smile Design specialist in {matched_city}."
        }

    # 2. Check if authorized in another city
    for other_city, dentists in auth_dentists.items():
        other_match = find_best_doctor_match(doc_clean, dentists)
        if other_match:
            return {
                "is_authorized": False,
                "doctor_name": other_match,
                "city": matched_city or city_clean,
                "actual_city": other_city,
                "message": f"{other_match} is an authorized specialist in {other_city}, but NOT in {matched_city or city_clean}."
            }

    # 3. Not authorized in any city
    return {
        "is_authorized": False,
        "doctor_name": doc_clean,
        "city": matched_city or city_clean,
        "message": f"{doc_clean} is not an authorized Ultimate Smile Design specialist."
    }
