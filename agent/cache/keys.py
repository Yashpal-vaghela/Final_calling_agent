"""
Phase 2: Redis Data Structure & Key Specifications
Defines key schemas and TTL constants according to the Option 3 Hybrid Architecture Plan.

Data Map:
- call:{call_id}:context           -> JSON dict of caller context  (TTL: 30 minutes)
- faq:{topic}:{language}           -> JSON dict of get_faq result  (TTL: 1 hour)
- dentists:{pincode}:{city}:{page} -> JSON dict of dentists result (TTL: 6 hours)
- city:{city_lower}                -> JSON dict of city coverage   (TTL: 6 hours)
- state:{state_lower}              -> JSON dict of state coverage  (TTL: 6 hours)
- dentist:{doctor_norm}:{city}     -> JSON dict of check_dentist   (TTL: 6 hours)
"""
from typing import Optional

# TTL Constants (in seconds) according to Phase 2 TTL Rationale
TTL_CALL_CONTEXT: int = 1800      # 30 minutes: provides headroom for reconnects
TTL_FAQ: int = 3600               # 1 hour: FAQ answers are stable within a business day
TTL_DENTISTS: int = 21600         # 6 hours: static dentist directory
TTL_CITY: int = 21600             # 6 hours: static city coverage data
TTL_STATE: int = 21600            # 6 hours: static state coverage data
TTL_CHECK_DENTIST: int = 21600    # 6 hours: static authorized partner doctor list


def build_call_context_key(call_id: str) -> str:
    """Build key for caller context: call:{call_id}:context"""
    clean_id = (call_id or "").strip()
    return f"call:{clean_id}:context"


def build_faq_key(topic: str, language: str = "en") -> str:
    """Build key for get_faq result: faq:{topic}:{language}"""
    t = (topic or "").strip().lower()
    l = (language or "en").strip().lower()
    return f"faq:{t}:{l}"


def build_dentists_key(pincode: str, city: str = "", page: int = 1) -> str:
    """Build key for get_nearest_dentists result: dentists:{pincode}:{city}:{page}"""
    pin = (pincode or "").strip()
    c = (city or "").strip().lower()
    p = max(1, page or 1)
    return f"dentists:{pin}:{c}:{p}"


def build_city_key(city: str) -> str:
    """Build key for check_city_coverage result: city:{city_lower}"""
    c = (city or "").strip().lower()
    return f"city:{c}"


def build_state_key(state: str) -> str:
    """Build key for check_city_coverage state result: state:{state_lower}"""
    s = (state or "").strip().lower()
    return f"state:{s}"


def build_dentist_key(doctor_name: str, city: str = "") -> str:
    """Build key for check_dentist result: dentist:{doctor_norm}:{city}"""
    doc = (doctor_name or "").strip().lower()
    c = (city or "").strip().lower()
    return f"dentist:{doc}:{c}"
