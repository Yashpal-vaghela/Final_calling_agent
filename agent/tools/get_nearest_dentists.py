import os
import re
import json
import asyncio
import httpx
from typing import Dict, Any, List, Optional, Tuple

from backend.app.services.city_service import (
    get_nearest_covered_city,
    get_default_pincode_for_city,
    get_city_from_pincode,
    is_city_covered
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")

REMOTE_API_URL = "https://ultimatesmiledesign.com/api/dentists-list/pincode/"
LOCAL_API_URL = "http://192.168.0.161:1020/api/dentists-list/pincode/"
POSTAL_PINCODE_API_URL = "https://api.postalpincode.in/pincode/"

def _clean_pincode(pincode: Any) -> str:
    """Extracts numeric digits from pincode string."""
    if not pincode:
        return ""
    digits = re.sub(r"\D", "", str(pincode).strip())
    return digits if len(digits) == 6 else ""

async def _fetch_postal_pincode_async(pincode: str) -> Optional[Dict[str, Any]]:
    """
    Asynchronously queries https://api.postalpincode.in/pincode/{pincode}
    to retrieve verified Indian postal details: District, State, Division, and Localities.
    """
    if not pincode or len(pincode) != 6:
        return None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{POSTAL_PINCODE_API_URL}{pincode}")
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data and data[0].get("Status") == "Success":
                    post_offices = data[0].get("PostOffice") or []
                    if post_offices:
                        first_po = post_offices[0]
                        district = (first_po.get("District") or "").strip()
                        state = (first_po.get("State") or "").strip()
                        division = (first_po.get("Division") or "").strip()
                        region = (first_po.get("Region") or "").strip()
                        localities = [po.get("Name").strip() for po in post_offices if po.get("Name")]
                        return {
                            "status": "success",
                            "district": district,
                            "state": state,
                            "division": division,
                            "region": region,
                            "localities": localities,
                            "pincode": pincode,
                        }
    except Exception:
        pass
    return None

def _fetch_postal_pincode(pincode: str) -> Optional[Dict[str, Any]]:
    """
    Synchronously queries https://api.postalpincode.in/pincode/{pincode}
    for offline / synchronous test callers.
    """
    if not pincode or len(pincode) != 6:
        return None
    try:
        resp = httpx.get(f"{POSTAL_PINCODE_API_URL}{pincode}", timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data and data[0].get("Status") == "Success":
                post_offices = data[0].get("PostOffice") or []
                if post_offices:
                    first_po = post_offices[0]
                    district = (first_po.get("District") or "").strip()
                    state = (first_po.get("State") or "").strip()
                    localities = [po.get("Name").strip() for po in post_offices if po.get("Name")]
                    return {
                        "status": "success",
                        "district": district,
                        "state": state,
                        "localities": localities,
                        "pincode": pincode,
                    }
    except Exception:
        pass
    return None

async def _fetch_from_api_async(pincode: str) -> List[Dict[str, Any]]:
    """
    Asynchronously fetches dentists sorted by distance directly from the live PIN code API.
    Tries remote production endpoint first with 5.0s timeout, then local backend.
    Does NOT store or cache to local files.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        for base_url in [REMOTE_API_URL, LOCAL_API_URL]:
            try:
                resp = await client.get(base_url, params={"pincode": pincode})
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("status") == "success":
                        dentist_list = data.get("data", [])
                        if dentist_list:
                            return dentist_list
            except Exception:
                continue
    return []

def _fetch_from_api(pincode: str) -> List[Dict[str, Any]]:
    """
    Synchronously fetches dentists sorted by distance directly from the live PIN code API.
    Tries remote production endpoint first with 5.0s timeout, then local backend.
    Does NOT store or cache to local files.
    """
    for base_url in [REMOTE_API_URL, LOCAL_API_URL]:
        try:
            resp = httpx.get(base_url, params={"pincode": pincode}, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("status") == "success":
                    dentist_list = data.get("data", [])
                    if dentist_list:
                        return dentist_list
        except Exception:
            continue
    return []



def _extract_locality_tokens(localities: List[str]) -> Tuple[List[str], List[str]]:
    """
    Extracts full phrases and significant root keywords from post office names.
    E.g. ['Adajan Dn', 'Anand Mahal Road'] ->
      phrases: ['adajan dn', 'anand mahal road']
      keywords: ['adajan', 'anand', 'mahal']
    """
    phrases: List[str] = []
    keywords: set = set()
    stop_words = {
        "so", "bo", "ho", "dn", "nd", "rd", "road", "street", "nagar",
        "college", "market", "house", "city", "center", "centre", "post",
        "office", "branch", "sub", "head", "campus", "near", "opp"
    }

    for loc in localities:
        if not loc:
            continue
        cleaned = re.sub(r"\(.*?\)", "", loc).strip().lower()
        if len(cleaned) >= 3:
            phrases.append(cleaned)
        
        words = re.findall(r"[a-z]+", cleaned)
        for w in words:
            if len(w) >= 4 and w not in stop_words:
                keywords.add(w)

    return phrases, list(keywords)

def _rank_dentists_by_postal_proximity(
    dentists: List[Dict[str, Any]],
    user_pincode: str,
    postal_info: Optional[Dict[str, Any]] = None,
    target_city: str = ""
) -> List[Dict[str, Any]]:
    """
    Ranks dentists based on postal proximity to the user's PIN code and verified post office details.
    Combines:
      1. Exact locality phrase match from Postal PIN Code API (+1200 pts)
      2. Root locality keyword match (+1000 pts)
      3. Exact 6-digit PIN code match (+600 pts)
      4. 4-digit / 3-digit postal sorting zone match (+400 / +300 pts)
      5. Numerical PIN distance bonus (up to +100 pts)
      6. District match from Postal PIN Code API (+500 pts)
      7. Target covered city match (+400 pts)
      8. Preserves geodesic distance tie-breaker from original API ordering
    """
    if not dentists:
        return []

    localities = postal_info.get("localities", []) if postal_info else []
    district = (postal_info.get("district") or "").strip() if postal_info else ""
    user_pin = user_pincode.strip()

    loc_phrases, loc_keywords = _extract_locality_tokens(localities)

    scored = []
    for idx, d in enumerate(dentists):
        score = 0
        raw_doc_pin = re.sub(r"\D", "", str(d.get("pincode", "")))
        doc_addr = (d.get("address", "") + " " + d.get("clinic_name", "")).lower()

        # 1. Locality match from Postal PIN Code API
        matched_locality = False
        for phrase in loc_phrases:
            if phrase and len(phrase) >= 4 and phrase in doc_addr:
                score += 1200
                matched_locality = True
                break
        
        if not matched_locality:
            for kw in loc_keywords:
                if kw in doc_addr:
                    score += 1000
                    matched_locality = True
                    break

        # 2. PIN code hierarchical proximity
        if len(raw_doc_pin) == 6 and len(user_pin) == 6:
            if raw_doc_pin == user_pin:
                score += 600
            elif raw_doc_pin[:4] == user_pin[:4]:
                score += 400
            elif raw_doc_pin[:3] == user_pin[:3]:
                score += 300
            
            diff = abs(int(raw_doc_pin) - int(user_pin))
            score += max(0, 100 - min(100, diff // 50))

        # 3. District match from Postal PIN Code API
        if district and district.lower() in doc_addr:
            score += 500

        # 4. Target covered city match
        if target_city and target_city.lower() in doc_addr:
            score += 400

        # Tuple: (score DESC, original API distance index ASC, dentist)
        scored.append((score, -idx, d))

    scored.sort(reverse=True)
    return [item[2] for item in scored]


KNOWN_CITIES = [
    "Ahmedabad", "Bangalore", "Bharuch", "Chennai", "Dhrangadhra", "Faridkot",
    "Guntur", "Gurugram", "Guwahati", "Gwalior", "Halvad", "Hyderabad",
    "Indore", "Jamnagar", "Malda", "Mumbai", "New Delhi", "Pune",
    "Rajkot", "Sangli", "Sri Ganganagar", "Surat", "Vadodara"
]

CITY_ALIASES = {
    "delhi": "New Delhi",
    "new delhi": "New Delhi",
    "gurgaon": "Gurugram",
    "gurugram": "Gurugram",
    "bengaluru": "Bangalore",
    "bangalore": "Bangalore",
    "surendranagar": "Dhrangadhra",
}

def _canonical_city(city_name: str) -> str:
    """Returns canonical city name for aliases."""
    if not city_name:
        return ""
    c = city_name.strip().lower()
    return CITY_ALIASES.get(c, city_name.strip())

def _extract_city_from_address(address: str) -> Optional[str]:
    """Matches a known city from the doctor's address string using aliases and known cities."""
    if not address:
        return None
    for alias, canonical in CITY_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", address, re.IGNORECASE):
            return canonical

    for city in KNOWN_CITIES:
        if re.search(r"\b" + re.escape(city) + r"\b", address, re.IGNORECASE):
            return city

    return None

def _filter_dentists_by_city(
    dentists: List[Dict[str, Any]],
    target_city: str
) -> List[Dict[str, Any]]:
    """
    Filters doctors to only those located in target_city.
    If only 1 doctor is in the city (e.g. Bharuch, Pune, Bangalore, Chennai), returns only that 1 doctor.
    If 2 doctors are in the city (e.g. Vadodara, New Delhi), returns those 2 doctors.
    If 3 or more are in the city (e.g. Surat, Ahmedabad, Mumbai), returns all doctors in that city.
    """
    if not dentists or not target_city:
        return []
    
    canon_target = _canonical_city(target_city).lower()
    matching = []
    for d in dentists:
        addr = (d.get("address", "") + " " + d.get("clinic_name", "")).strip()
        doc_city = _extract_city_from_address(addr)
        if doc_city and _canonical_city(doc_city).lower() == canon_target:
            matching.append(d)

    return matching

def _build_response(
    raw_dentists: List[Dict[str, Any]],
    caller_city: str,
    page: int = 1,
    limit: int = 3,
    postal_info: Optional[Dict[str, Any]] = None,
    target_covered_city: Optional[str] = None
) -> Dict[str, Any]:
    """Formats the standardized doctor recommendation response dictionary."""
    doctor_names: List[str] = []
    if raw_dentists:
        seen = set()
        for d in raw_dentists:
            name = (d.get("name") or "").strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                doctor_names.append(name)


    total_count = len(doctor_names)
    
    # Slice for requested page
    safe_page = max(1, page)
    safe_limit = max(1, limit)
    start_idx = (safe_page - 1) * safe_limit
    end_idx = start_idx + safe_limit
    
    batch = doctor_names[start_idx:end_idx]
    has_more = end_idx < total_count
    
    # The #1 overall nearest doctor from the search (used when caller is hesitant)
    top_recommended = doctor_names[0] if doctor_names else ""

    # Check city coverage
    is_covered = is_city_covered(caller_city) if caller_city else True
    nearest_city = target_covered_city or (caller_city if is_covered else get_nearest_covered_city(caller_city))

    # Build clear prompt instruction for Gemini Live
    city_note = f"in {nearest_city}" if nearest_city else ""

    if len(batch) == 1:
        presentation_rule = f"2. Present the doctor: 'We have {batch[0]} {city_note}. Should I confirm your appointment with them as your nearest dentist?'"
        hesitation_rule = f"3. If the caller asks if this dentist is good, more experienced, is hesitant, or confirms: DO NOT say coordinator assigns. Reassure them: '{top_recommended} is highly experienced and great because they are an authorized smile designer. Shall I confirm your appointment with {top_recommended} {city_note}?'"
    elif len(batch) == 2:
        presentation_rule = f"2. Present the names: 'We have {batch[0]} and {batch[1]} {city_note}. Should I confirm your appointment with your nearest dentist, {batch[0]}?'"
        hesitation_rule = f"3. If the caller asks which dentist is best, great, more experienced, or cannot decide: DO NOT say coordinator assigns. NEVER say one doctor is better than another. Reassure them: 'All of our dentists are equally experienced and great because all of them are our authorized smile designers. Since {top_recommended} is your nearest dentist {city_note}, should I confirm your appointment with {top_recommended}?'"
    elif len(batch) >= 3:
        presentation_rule = f"2. Present the names: 'We have {', '.join(batch[:-1])} and {batch[-1]} {city_note}. Should I confirm your appointment with your nearest dentist, {batch[0]}?'"
        hesitation_rule = f"3. If the caller asks which dentist is best, great, more experienced, or cannot decide: DO NOT say coordinator assigns. NEVER say one doctor is better than another. Reassure them: 'All of our dentists are equally experienced and great because all of them are our authorized smile designers. Since {top_recommended} is your nearest dentist {city_note}, should I confirm your appointment with {top_recommended}?'"
    else:
        presentation_rule = "2. Inform the caller that no authorized dentists were found in this area."
        hesitation_rule = "3. Offer to check another city or nearby area."

    instruction = (
        "Respond entirely in the language of the caller's CURRENT spoken turn. "
        "Strict rules:\n"
        "1. DO NOT say 'in your closest area' or 'in your nearest area'. DO NOT say 'in our nearest center'.\n"
        f"{presentation_rule}\n"
        f"{hesitation_rule}\n"
        "4. Speak ONLY doctor names. NEVER speak clinic addresses, clinic names, or PIN codes over the phone."
    )

    result: Dict[str, Any] = {
        "status": "success" if batch else "no_dentists_found",
        "dentists": batch,
        "count": len(batch),
        "total_available": total_count,
        "top_recommended_doctor": top_recommended,
        "page": safe_page,
        "has_more": has_more,
        "city": caller_city or nearest_city,
        "nearest_covered_city": nearest_city,
        "is_caller_city": is_covered,
        "instruction": instruction
    }

    if postal_info:
        result["postal_district"] = postal_info.get("district")
        result["postal_state"] = postal_info.get("state")
        result["postal_localities"] = postal_info.get("localities", [])[:5]

    return result

async def async_get_nearest_dentists(
    pincode: str = "",
    city: str = "",
    page: int = 1,
    limit: int = 3,
    exclude_cities: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Asynchronously fetches and ranks the nearest authorized smile designers (dentists)
    for the caller's PIN code using both the India Post Postal PIN Code API and the USD Dentists API.
    Filters strictly to the caller's city (giving only 1 if 1 in the city, 2 if 2, or top 3 nearest).
    Supports exclude_cities to bypass rejected cities and find the next nearest authorized dentists.
    100% non-blocking HTTP execution on the asyncio event loop via httpx.AsyncClient.
    """
    clean_pin = _clean_pincode(pincode)
    caller_city = city.strip() if city else ""

    # If PIN code is missing or malformed, resolve central PIN code for caller's city
    if not clean_pin and caller_city:
        clean_pin = get_default_pincode_for_city(caller_city)

    # 1. Asynchronously fetch Postal details and USD Dentists concurrently!
    t_postal = _fetch_postal_pincode_async(clean_pin) if clean_pin else None
    t_dentists = _fetch_from_api_async(clean_pin) if clean_pin else None

    postal_info: Optional[Dict[str, Any]] = None
    raw_dentists: List[Dict[str, Any]] = []

    if t_postal and t_dentists:
        res_postal, res_dentists = await asyncio.gather(t_postal, t_dentists, return_exceptions=True)
        if isinstance(res_postal, dict):
            postal_info = res_postal
        if isinstance(res_dentists, list):
            raw_dentists = res_dentists
    elif t_dentists:
        try:
            res_d = await t_dentists
            if isinstance(res_d, list):
                raw_dentists = res_d
        except Exception:
            pass
    elif t_postal:
        try:
            res_p = await t_postal
            if isinstance(res_p, dict):
                postal_info = res_p
        except Exception:
            pass

    # 2. Enrich/infer caller_city from Postal PIN Code API if missing
    if postal_info and not caller_city:
        postal_district = postal_info.get("district")
        if postal_district:
            caller_city = postal_district

    if not caller_city and clean_pin:
        inferred = get_city_from_pincode(clean_pin)
        if inferred:
            caller_city = inferred

    # Cross-city safety check: If clean_pin belongs to a different covered city than caller_city, discard mismatched PIN
    if clean_pin and caller_city:
        pin_city = (postal_info.get("district") if postal_info else None) or get_city_from_pincode(clean_pin)
        if pin_city and get_nearest_covered_city(caller_city).lower() != get_nearest_covered_city(pin_city).lower():
            clean_pin = get_default_pincode_for_city(caller_city)

    # 3. Filter strictly to target covered city & rank by postal proximity
    target_covered = (
        caller_city
        if (is_city_covered(caller_city) and (not exclude_cities or caller_city.lower() not in [ec.lower() for ec in exclude_cities]))
        else get_nearest_covered_city(caller_city, exclude_cities=exclude_cities)
    )

    city_dentists = _filter_dentists_by_city(raw_dentists, target_covered) if raw_dentists else []

    if city_dentists:
        candidate_dentists = _rank_dentists_by_postal_proximity(
            dentists=city_dentists,
            user_pincode=clean_pin,
            postal_info=postal_info,
            target_city=target_covered
        )
    elif raw_dentists:
        # If city filter matched 0 from API, rank all raw_dentists as fallback
        candidate_dentists = _rank_dentists_by_postal_proximity(
            dentists=raw_dentists,
            user_pincode=clean_pin,
            postal_info=postal_info,
            target_city=target_covered
        )
    else:
        from backend.app.services.dentist_service import get_dentist_service
        live_dir = await get_dentist_service().get_directory_async()
        city_docs = live_dir.get(target_covered, [])
        candidate_dentists = [{"name": n} for n in city_docs]

    return _build_response(candidate_dentists, caller_city, page, limit, postal_info=postal_info, target_covered_city=target_covered)

def get_nearest_dentists(
    pincode: str = "",
    city: str = "",
    page: int = 1,
    limit: int = 3,
    exclude_cities: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Synchronously fetches the nearest authorized smile designers (dentists) for the caller's PIN code.
    Filters strictly to the caller's city (giving only 1 if 1 in the city, 2 if 2, or top 3 nearest).
    Extracts ONLY doctor names. Omits clinic addresses, clinic names, and PIN codes.
    Supports exclude_cities to bypass rejected cities.
    """
    clean_pin = _clean_pincode(pincode)
    caller_city = city.strip() if city else ""

    # If PIN code is missing or malformed, resolve central PIN code for caller's city
    if not clean_pin and caller_city:
        clean_pin = get_default_pincode_for_city(caller_city)

    postal_info = _fetch_postal_pincode(clean_pin) if clean_pin else None
    raw_dentists = _fetch_from_api(clean_pin) if clean_pin else []

    if postal_info and not caller_city:
        postal_district = postal_info.get("district")
        if postal_district:
            caller_city = postal_district

    if not caller_city and clean_pin:
        inferred = get_city_from_pincode(clean_pin)
        if inferred:
            caller_city = inferred

    # Cross-city safety check: If clean_pin belongs to a different covered city than caller_city, discard mismatched PIN
    if clean_pin and caller_city:
        pin_city = (postal_info.get("district") if postal_info else None) or get_city_from_pincode(clean_pin)
        if pin_city and get_nearest_covered_city(caller_city).lower() != get_nearest_covered_city(pin_city).lower():
            clean_pin = get_default_pincode_for_city(caller_city)

    target_covered = (
        caller_city
        if (is_city_covered(caller_city) and (not exclude_cities or caller_city.lower() not in [ec.lower() for ec in exclude_cities]))
        else get_nearest_covered_city(caller_city, exclude_cities=exclude_cities)
    )

    city_dentists = _filter_dentists_by_city(raw_dentists, target_covered) if raw_dentists else []

    if city_dentists:
        candidate_dentists = _rank_dentists_by_postal_proximity(
            dentists=city_dentists,
            user_pincode=clean_pin,
            postal_info=postal_info,
            target_city=target_covered
        )
    elif raw_dentists:
        candidate_dentists = _rank_dentists_by_postal_proximity(
            dentists=raw_dentists,
            user_pincode=clean_pin,
            postal_info=postal_info,
            target_city=target_covered
        )
    else:
        from backend.app.services.dentist_service import get_dentist_service
        live_dir = get_dentist_service().get_directory_sync()
        city_docs = live_dir.get(target_covered, [])
        candidate_dentists = [{"name": n} for n in city_docs]

    return _build_response(candidate_dentists, caller_city, page, limit, postal_info=postal_info, target_covered_city=target_covered)

# Alias for async callers
get_nearest_dentists_async = async_get_nearest_dentists

