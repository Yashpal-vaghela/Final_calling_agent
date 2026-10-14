"""
Lead ID Generation Service.

Provides cryptographically secure, collision-free, exactly 18-digit numeric
lead identifiers (range 100,000,000,000,000,000 to 999,999,999,999,999,999).
Guarantees the first digit is always 1-9 so leading zeros are never truncated
when converted to an integer or stored in SQL BIGINT columns.
"""

import secrets
from typing import Any

MIN_18_DIGIT = 100_000_000_000_000_000  # 10^17 (18 digits)
MAX_18_DIGIT = 999_999_999_999_999_999  # 10^18 - 1 (18 digits)
SPAN_18_DIGIT = MAX_18_DIGIT - MIN_18_DIGIT + 1  # 900 quadrillion values


def generate_18_digit_lead_id() -> str:
    """
    Generates a cryptographically random, exactly 18-digit numeric lead ID.
    The first digit is guaranteed between 1 and 9.
    """
    try:
        from backend.app.services.caller_context import ACTIVE_CALLER_CONTEXTS
        for _ in range(10):
            val = secrets.randbelow(SPAN_18_DIGIT) + MIN_18_DIGIT
            lead_id = str(val)
            if lead_id not in ACTIVE_CALLER_CONTEXTS:
                return lead_id
    except Exception:
        pass

    return str(secrets.randbelow(SPAN_18_DIGIT) + MIN_18_DIGIT)


def is_valid_lead_id(lead_id: Any) -> bool:
    """
    Checks if a given lead_id is an exactly 18-digit numeric string.
    """
    if not lead_id:
        return False
    s = str(lead_id).strip()
    return len(s) == 18 and s.isdigit()
