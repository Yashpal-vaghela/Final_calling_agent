import os
import json
import math
import re
from typing import List, Optional, Tuple, Dict, Any

_COVERED_CITIES_CACHE: List[str] = []
_CITIES_LOADED: bool = False

COVERED_CITY_COORDINATES: Dict[str, Tuple[float, float]] = {
    "Ahmedabad": (23.0225, 72.5714),
    "Bangalore": (12.9716, 77.5946),
    "Bengaluru": (12.9716, 77.5946),
    "Bharuch": (21.7051, 72.9959),
    "Chennai": (13.0827, 80.2707),
    "Delhi": (28.6139, 77.2090),
    "Dhrangadhra": (22.9964, 71.4647),
    "Faridkot": (30.6769, 74.7583),
    "Guntur": (16.3067, 80.4365),
    "Gurgaon": (28.4595, 77.0266),
    "Gurugram": (28.4595, 77.0266),
    "Guwahati": (26.1445, 91.7362),
    "Gwalior": (26.2183, 78.1828),
    "Halvad": (23.0131, 71.1822),
    "Hyderabad": (17.3850, 78.4867),
    "Indore": (22.7196, 75.8577),
    "Jamnagar": (22.4707, 70.0577),
    "Malda": (25.0108, 88.1411),
    "Mumbai": (19.0760, 72.8777),
    "New Delhi": (28.6139, 77.2090),
    "Pune": (18.5204, 73.8567),
    "Rajkot": (22.3039, 70.8022),
    "Sangli": (16.8524, 74.5815),
    "Sri Ganganagar": (29.9038, 73.8772),
    "Surat": (21.1702, 72.8311),
    "Surendranagar": (22.7274, 71.6370),
    "Vadodara": (22.3072, 73.1812),
}

KNOWN_NEARBY_MAPPING: Dict[str, str] = {
    # Gujarat
    "navsari": "Surat", "vapi": "Surat", "valsad": "Surat", "bardoli": "Surat", "bilimora": "Surat",
    "vyara": "Surat", "ankleshwar": "Bharuch", "dahej": "Bharuch", "jambusar": "Bharuch",
    "anand": "Vadodara", "nadiad": "Vadodara", "khambhat": "Vadodara", "godhra": "Vadodara",
    "dabhoi": "Vadodara", "karjan": "Vadodara",
    "gandhinagar": "Ahmedabad", "sanand": "Ahmedabad", "kalol": "Ahmedabad", "kadi": "Ahmedabad",
    "dholka": "Ahmedabad", "bhavnagar": "Ahmedabad", "morbi": "Rajkot", "junagadh": "Rajkot",
    "amreli": "Rajkot", "gondal": "Rajkot", "jetpur": "Rajkot", "surendranagar": "Dhrangadhra",
    "wadhwan": "Dhrangadhra", "porbandar": "Jamnagar", "dwarka": "Jamnagar", "khambhalia": "Jamnagar",
    "bhuj": "Jamnagar", "gandhidham": "Jamnagar", "anjar": "Jamnagar", "mundra": "Jamnagar",
    "palanpur": "Ahmedabad", "mehsana": "Ahmedabad", "patan": "Ahmedabad", "himatnagar": "Ahmedabad",
    # Maharashtra
    "thane": "Mumbai", "navi mumbai": "Mumbai", "kalyan": "Mumbai", "dombivli": "Mumbai",
    "vasai": "Mumbai", "virar": "Mumbai", "panvel": "Mumbai", "nashik": "Mumbai", "alibag": "Mumbai",
    "pimpri": "Pune", "chinchwad": "Pune", "solapur": "Sangli", "kolhapur": "Sangli", "satara": "Pune",
    # North
    "delhi": "New Delhi", "noida": "New Delhi", "greater noida": "New Delhi", "ghaziabad": "New Delhi",
    "gurgaon": "Gurugram", "faridabad": "Gurugram", "manesar": "Gurugram", "jaipur": "Gurugram", "alwar": "Gurugram",
    "rohtak": "Gurugram", "panipat": "New Delhi", "sonipat": "New Delhi", "karnal": "New Delhi",
    "chandigarh": "Faridkot", "ludhiana": "Faridkot", "jalandhar": "Faridkot", "amritsar": "Faridkot",
    "bathinda": "Faridkot", "patiala": "Faridkot", "hissar": "Sri Ganganagar", "bikaner": "Sri Ganganagar",
    "agra": "Gwalior", "mathura": "New Delhi", "jhansi": "Gwalior", "morena": "Gwalior",
    "bhopal": "Indore", "ujjain": "Indore", "dewas": "Indore", "ratlam": "Indore",
    # South
    "bengaluru": "Bangalore", "mysore": "Bangalore", "mysuru": "Bangalore", "tumkur": "Bangalore",
    "hosur": "Bangalore", "secunderabad": "Hyderabad", "warangal": "Hyderabad", "karimnagar": "Hyderabad",
    "vijayawada": "Guntur", "amaravati": "Guntur", "visakhapatnam": "Guntur", "vizag": "Guntur",
    "kanchipuram": "Chennai", "vellore": "Chennai", "tirupati": "Chennai",
    # East
    "kolkata": "Malda", "siliguri": "Malda", "dispur": "Guwahati", "shillong": "Guwahati",
}

# Common coordinates for geographic fallback when city is not in KNOWN_NEARBY_MAPPING
CITY_COORDINATES_LOOKUP: Dict[str, Tuple[float, float]] = {
    # Gujarat feeder towns
    "amreli": (21.6032, 71.2223), "bhavnagar": (21.7645, 72.1519), "junagadh": (21.5222, 70.4579),
    "morbi": (22.8120, 70.8378), "gandhinagar": (23.2156, 72.6369), "anand": (22.5645, 72.9289),
    "nadiad": (22.6916, 72.8634), "navsari": (20.9467, 72.9520), "vapi": (20.3893, 72.9106),
    "valsad": (20.5992, 72.9342), "porbandar": (21.6417, 69.6293), "bhuj": (23.2420, 69.6669),
    "gandhidham": (23.0753, 70.1337), "mehsana": (23.5880, 72.3693), "palanpur": (24.1724, 72.4346),
    "himatnagar": (23.5979, 72.9698), "godhra": (22.7758, 73.6149), "ankleshwar": (21.6264, 73.0033),
    "botad": (22.1706, 71.6663), "somnath": (20.9015, 70.3996), "veraval": (20.9077, 70.3678),
    "wadhwan": (22.7000, 71.6833), "khambhat": (22.3131, 72.6192), "dabhoi": (22.1800, 73.4300),
    "karjan": (22.0500, 73.1200), "bardoli": (21.1200, 73.1100), "bilimora": (20.7600, 72.9600),
    "vyara": (21.1100, 73.4000), "dahej": (21.7100, 72.5800), "jambusar": (22.0500, 72.8000),
    "dholka": (22.7200, 72.4400), "sanand": (22.9800, 72.3800), "kalol": (23.2500, 72.5100),
    "kadi": (23.3000, 72.3300), "gondal": (21.9600, 70.8000), "jetpur": (21.7500, 70.7800),
    "dwarka": (22.2400, 68.9700), "khambhalia": (22.2000, 69.6500), "anjar": (23.1100, 70.0300),
    "mundra": (22.8400, 69.7200), "patan": (23.8500, 72.1300),
    # Maharashtra
    "thane": (19.2183, 72.9781), "navi mumbai": (19.0330, 73.0297), "kalyan": (19.2403, 73.1305),
    "dombivli": (19.2184, 73.0867), "vasai": (19.3919, 72.8397), "virar": (19.4700, 72.8000),
    "panvel": (18.9894, 73.1175), "nashik": (19.9975, 73.7898), "solapur": (17.6599, 75.9064),
    "kolhapur": (16.7050, 74.2433), "satara": (17.6805, 73.9975), "pimpri": (18.6279, 73.8009),
    "chinchwad": (18.6298, 73.7997), "alibag": (18.6584, 72.8773), "nagpur": (21.1458, 79.0882),
    "aurangabad": (19.8762, 75.3433), "chhatrapati sambhajinagar": (19.8762, 75.3433),
    # North
    "noida": (28.5355, 77.3910), "greater noida": (28.4744, 77.5040), "ghaziabad": (28.6692, 77.4538),
    "faridabad": (28.4089, 77.3178), "manesar": (28.3515, 76.9428), "panipat": (29.3909, 76.9635),
    "sonipat": (28.9931, 77.0151), "karnal": (29.6857, 76.9905), "rohtak": (28.8955, 76.6066),
    "chandigarh": (30.7333, 76.7794), "ludhiana": (30.9010, 75.8573), "jalandhar": (31.3260, 75.5762),
    "amritsar": (31.6340, 74.8723), "bathinda": (30.2110, 74.9455), "patiala": (30.3398, 76.3869),
    "jaipur": (26.9124, 75.7873), "jodhpur": (26.2389, 73.0243), "bikaner": (28.0229, 73.3119),
    "udaipur": (24.5854, 73.7125), "alwar": (27.5530, 76.6346), "hissar": (29.1492, 75.7217),
    "lucknow": (26.8467, 80.9462), "kanpur": (26.4499, 80.3319), "varanasi": (25.3176, 82.9739),
    "patna": (25.5941, 85.1376), "ranchi": (23.3441, 85.3096), "jamshedpur": (22.8046, 86.2029),
    "bhubaneswar": (20.2961, 85.8245), "cuttack": (20.4625, 85.8828), "raipur": (21.2514, 81.6296),
    "bilaspur": (22.0797, 82.1409), "kota": (25.2138, 75.8648), "ajmer": (26.4499, 74.6399),
    "bhopal": (23.2599, 77.4126), "ujjain": (23.1765, 75.7885), "dewas": (22.9676, 76.0534),
    "ratlam": (23.3315, 75.0367), "agra": (27.1767, 78.0081), "mathura": (27.4924, 77.6737),
    "jhansi": (25.4484, 78.5685), "morena": (26.4950, 77.9940),
    # South
    "mysore": (12.2958, 76.6394), "mysuru": (12.2958, 76.6394), "tumkur": (13.3379, 77.1010),
    "hosur": (12.7409, 77.8253), "secunderabad": (17.4399, 78.4983), "warangal": (17.9784, 79.5941),
    "karimnagar": (18.4386, 79.1288), "vijayawada": (16.5062, 80.6480), "amaravati": (16.5131, 80.5165),
    "visakhapatnam": (17.6868, 83.2185), "vizag": (17.6868, 83.2185), "kanchipuram": (12.8342, 79.7036),
    "vellore": (12.9165, 79.1325), "tirupati": (13.6288, 79.4192), "coimbatore": (11.0168, 76.9558),
    "madurai": (9.9252, 78.1198), "kochi": (9.9312, 76.2673), "thiruvananthapuram": (8.5241, 76.9366),
    "trivandrum": (8.5241, 76.9366), "kozhikode": (11.2588, 75.7804),
    # East
    "kolkata": (22.5726, 88.3639), "siliguri": (26.7271, 88.3953), "dispur": (26.1445, 91.7362),
    "shillong": (25.5788, 91.8933),
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def get_covered_cities() -> List[str]:
    """Returns a cached list of covered cities from data/covered_cities.json."""
    global _COVERED_CITIES_CACHE, _CITIES_LOADED
    
    if not _CITIES_LOADED:
        json_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "covered_cities.json")
        )
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                _COVERED_CITIES_CACHE = json.load(f)
        _CITIES_LOADED = True
        
    return _COVERED_CITIES_CACHE


def is_city_covered(city_name: str) -> bool:
    """Case-insensitive check if a city is in the covered list."""
    if not city_name:
        return False
    
    cities = get_covered_cities()
    lower_city = city_name.strip().lower()
    return lower_city in (c.lower() for c in cities)


# Canonical 23 covered centers backed by dentists in the live USD API
CANONICAL_COVERED_CENTERS: List[str] = [
    "Ahmedabad", "Bangalore", "Bharuch", "Chennai", "Dhrangadhra", "Faridkot",
    "Guntur", "Gurugram", "Guwahati", "Gwalior", "Halvad", "Hyderabad",
    "Indore", "Jamnagar", "Malda", "Mumbai", "New Delhi", "Pune",
    "Rajkot", "Sangli", "Sri Ganganagar", "Surat", "Vadodara"
]

CANONICAL_CITY_ALIASES: Dict[str, str] = {
    "delhi": "New Delhi",
    "new delhi": "New Delhi",
    "gurgaon": "Gurugram",
    "gurugram": "Gurugram",
    "bengaluru": "Bangalore",
    "bangalore": "Bangalore",
    "surendranagar": "Dhrangadhra",
}


def _normalize_city_name(city_name: str) -> str:
    """Returns canonical city name for aliases."""
    if not city_name:
        return ""
    c = city_name.strip().lower()
    return CANONICAL_CITY_ALIASES.get(c, city_name.strip())


def get_ranked_covered_cities(
    city_name: str,
    exclude_cities: Optional[List[str]] = None,
    limit: int = 3
) -> List[str]:
    """
    Returns a list of the closest authorized covered cities for any given Indian city/town,
    strictly excluding any cities in exclude_cities.
    All returned cities are strictly from the authorized centers backed by dentists in the API.
    Never returns non-covered or intermediate towns.
    """
    clean_city = (city_name or "").strip()
    lower_city = clean_city.lower()

    # Normalize exclusion set
    excluded_canonical = set()
    if exclude_cities:
        for ec in exclude_cities:
            if ec and ec.strip():
                canon_ec = _normalize_city_name(ec).lower()
                excluded_canonical.add(canon_ec)
                excluded_canonical.add(ec.strip().lower())
                # Also handle aliases
                if canon_ec in ("new delhi", "delhi"):
                    excluded_canonical.update({"new delhi", "delhi"})
                elif canon_ec in ("gurgaon", "gurugram"):
                    excluded_canonical.update({"gurgaon", "gurugram"})
                elif canon_ec in ("bangalore", "bengaluru"):
                    excluded_canonical.update({"bangalore", "bengaluru"})

    # 1. Direct match with covered cities (if not excluded)
    target_canonical = _normalize_city_name(clean_city)
    is_direct_covered = is_city_covered(clean_city)

    # Resolve coordinates for target city
    target_coords: Optional[Tuple[float, float]] = None
    if lower_city in CITY_COORDINATES_LOOKUP:
        target_coords = CITY_COORDINATES_LOOKUP[lower_city]
    elif target_canonical in COVERED_CITY_COORDINATES:
        target_coords = COVERED_CITY_COORDINATES[target_canonical]
    elif lower_city in KNOWN_NEARBY_MAPPING:
        primary_city = KNOWN_NEARBY_MAPPING[lower_city]
        canon_prim = _normalize_city_name(primary_city)
        target_coords = COVERED_CITY_COORDINATES.get(canon_prim)

    if not target_coords:
        target_coords = COVERED_CITY_COORDINATES.get("Ahmedabad", (23.0225, 72.5714))

    # Calculate distances to all canonical covered centers
    scored_centers: List[Tuple[float, str]] = []
    for center in CANONICAL_COVERED_CENTERS:
        canon_center = _normalize_city_name(center).lower()
        if canon_center in excluded_canonical or center.lower() in excluded_canonical:
            continue
        coords = COVERED_CITY_COORDINATES.get(center)
        if coords:
            dist = _haversine_km(target_coords[0], target_coords[1], coords[0], coords[1])
            scored_centers.append((dist, center))

    scored_centers.sort(key=lambda x: x[0])

    # If the target city was directly covered and not excluded, prioritize it first
    results: List[str] = []
    if is_direct_covered and target_canonical.lower() not in excluded_canonical:
        if any(c == target_canonical for _, c in scored_centers):
            results.append(target_canonical)
    elif lower_city in KNOWN_NEARBY_MAPPING:
        known_target = _normalize_city_name(KNOWN_NEARBY_MAPPING[lower_city])
        if known_target.lower() not in excluded_canonical and any(c == known_target for _, c in scored_centers):
            results.append(known_target)

    for _, center in scored_centers:
        if center not in results:
            results.append(center)
        if len(results) >= max(1, limit):
            break

    return results or ["Ahmedabad"]


def get_nearest_covered_city(
    city_name: str,
    exclude_cities: Optional[List[str]] = None
) -> str:
    """
    Finds the closest covered city for any given Indian city/town,
    optionally excluding rejected cities.
    Returns only the city name (e.g. 'Surat', 'Ahmedabad').
    Never returns distance or km.
    """
    if not city_name:
        return "Ahmedabad"

    ranked = get_ranked_covered_cities(city_name=city_name, exclude_cities=exclude_cities, limit=1)
    return ranked[0] if ranked else "Ahmedabad"


CITY_DEFAULT_PINCODES: Dict[str, str] = {
    # Covered cities
    "ahmedabad": "380001",
    "bangalore": "560001",
    "bengaluru": "560001",
    "bharuch": "392001",
    "chennai": "600001",
    "dhrangadhra": "363310",
    "faridkot": "151203",
    "guntur": "522001",
    "gurgaon": "122001",
    "gurugram": "122001",
    "guwahati": "781001",
    "gwalior": "474001",
    "halvad": "363330",
    "hyderabad": "500001",
    "indore": "452001",
    "jamnagar": "361001",
    "junagadh": "362001",
    "malda": "732101",
    "mumbai": "400001",
    "new delhi": "110001",
    "delhi": "110001",
    "pune": "411001",
    "rajkot": "360001",
    "sangli": "416416",
    "sri ganganagar": "335001",
    "surat": "395001",
    "surendranagar": "363001",
    "vadodara": "390001",
    # Key surrounding towns
    "navsari": "396445",
    "vapi": "396191",
    "valsad": "396001",
    "bhavnagar": "364001",
    "morbi": "363641",
    "gandhinagar": "382010",
    "anand": "388001",
    "nadiad": "387001",
    "porbandar": "360575",
    "bhuj": "370001",
    "gandhidham": "370201",
}


def get_default_pincode_for_city(city_name: str) -> str:
    """Returns a valid 6-digit central pincode for the given city or fallback."""
    if not city_name:
        return "362001"
    c_clean = city_name.strip().lower()
    if c_clean in CITY_DEFAULT_PINCODES:
        return CITY_DEFAULT_PINCODES[c_clean]
    nearest = get_nearest_covered_city(city_name).lower()
    return CITY_DEFAULT_PINCODES.get(nearest, "362001")


def validate_pincode(pincode: Any, city: Optional[str] = None) -> Dict[str, Any]:
    """
    Validates Indian 6-digit area PIN code.
    If valid, returns clean 6-digit string.
    If invalid or missing, provides the default fallback PIN for the city.
    """
    if not pincode:
        fallback = get_default_pincode_for_city(city or "")
        return {
            "valid": False,
            "pincode": fallback,
            "is_fallback": True,
            "message": "PIN code is empty. Fallback central city PIN used."
        }
    clean = re.sub(r"\D", "", str(pincode).strip())
    if len(clean) == 6 and not clean.startswith("0"):
        return {
            "valid": True,
            "pincode": clean,
            "is_fallback": False,
            "message": "Valid 6-digit PIN code."
        }
    fallback = get_default_pincode_for_city(city or "")
    return {
        "valid": False,
        "pincode": fallback,
        "is_fallback": True,
        "message": f"PIN code '{pincode}' is invalid. Fallback central city PIN used."
    }


def fetch_city_from_postal_api(pincode: Any) -> Optional[str]:
    """
    Fetches the city or district directly from the Postal PIN Code API (api.postalpincode.in).
    """
    if not pincode:
        return None
    clean = re.sub(r"\D", "", str(pincode).strip())
    if len(clean) != 6:
        return None
    try:
        import httpx
        resp = httpx.get(f"https://api.postalpincode.in/pincode/{clean}", timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data and data[0].get("Status") == "Success":
                pos = data[0].get("PostOffice") or []
                if pos:
                    return (pos[0].get("District") or pos[0].get("Division") or pos[0].get("Region") or "").strip()
    except Exception:
        pass
    return None


def get_city_from_pincode(pincode: Any) -> Optional[str]:
    """
    Identifies the city from a 6-digit Indian PIN code using the Postal API.
    """
    return fetch_city_from_postal_api(pincode)


def is_city_supported(city_name: str) -> bool:
    """
    Case-insensitive check if a city is supported either directly as a covered center
    or as a recognized nearby town with assigned service.
    """
    if not city_name:
        return False
    if is_city_covered(city_name):
        return True
    lower_city = city_name.strip().lower()
    return lower_city in KNOWN_NEARBY_MAPPING


def validate_city_pincode_match(city: str, pincode: Any) -> Tuple[bool, Optional[str]]:
    """
    Validates that the provided PIN code matches the selected city using the Postal API.
    """
    clean_pin = re.sub(r"\D", "", str(pincode).strip()) if pincode else ""
    if len(clean_pin) != 6:
        return False, "Please enter a valid 6-digit area PIN code."
        
    if not city or not city.strip():
        return False, "City name is required."

    clean_city = city.strip().lower()
    postal_city = fetch_city_from_postal_api(clean_pin)
    if not postal_city:
        return True, None

    postal_city_lower = postal_city.lower()
    if clean_city == postal_city_lower or postal_city_lower in clean_city or clean_city in postal_city_lower:
        return True, None

    aliases = [
        {"delhi", "new delhi", "central delhi", "north delhi", "south delhi", "east delhi", "west delhi"},
        {"bangalore", "bengaluru"},
        {"gurgaon", "gurugram"},
    ]
    for alias_set in aliases:
        if clean_city in alias_set and any(a in postal_city_lower for a in alias_set):
            return True, None

    return False, f"PIN code '{clean_pin}' belongs to {postal_city}, which does not match selected city '{city.strip()}'."


async def fetch_city_from_postal_api_async(pincode: Any) -> Optional[str]:
    """
    Asynchronously queries https://api.postalpincode.in/pincode/{pincode} to retrieve the city/district.
    """
    if not pincode:
        return None
    clean = re.sub(r"\D", "", str(pincode).strip())
    if len(clean) != 6:
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"https://api.postalpincode.in/pincode/{clean}")
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data and data[0].get("Status") == "Success":
                    pos = data[0].get("PostOffice") or []
                    if pos:
                        return (pos[0].get("District") or pos[0].get("Division") or pos[0].get("Region") or "").strip()
    except Exception:
        pass
    return None


async def validate_city_pincode_match_async(city: str, pincode: Any) -> Tuple[bool, Optional[str]]:
    """
    Asynchronously validates that the provided PIN code matches the selected city using Postal API.
    """
    clean_pin = re.sub(r"\D", "", str(pincode).strip()) if pincode else ""
    if len(clean_pin) != 6:
        return False, "Please enter a valid 6-digit area PIN code."
        
    if not city or not city.strip():
        return False, "City name is required."

    clean_city = city.strip().lower()
    postal_city = await fetch_city_from_postal_api_async(clean_pin)
    if not postal_city:
        return True, None

    postal_city_lower = postal_city.lower()
    if clean_city == postal_city_lower or postal_city_lower in clean_city or clean_city in postal_city_lower:
        return True, None

    aliases = [
        {"delhi", "new delhi", "central delhi", "north delhi", "south delhi", "east delhi", "west delhi"},
        {"bangalore", "bengaluru"},
        {"gurgaon", "gurugram"},
    ]
    for alias_set in aliases:
        if clean_city in alias_set and any(a in postal_city_lower for a in alias_set):
            return True, None

    return False, f"PIN code '{clean_pin}' belongs to {postal_city}, which does not match selected city '{city.strip()}'."






