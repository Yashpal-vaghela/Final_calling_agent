import re
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from backend.app.services.id_service import generate_18_digit_lead_id

logger = logging.getLogger(__name__)

# In-memory fast cache for active caller contexts mapped by lead_id, raw phone, and 10-digit normalized phone
ACTIVE_CALLER_CONTEXTS: Dict[str, Dict[str, Any]] = {}


def normalize_phone(phone_str: Any) -> str:
    """Extracts numeric digits only for robust phone lookup matching."""
    if not phone_str:
        return ""
    return re.sub(r"\D", "", str(phone_str))


def validate_phone_number(phone_str: Any) -> Dict[str, Any]:
    """
    Validates and normalizes an Indian phone number to exactly 10 national digits.
    Accepts user-friendly formats:
      - 9876543210
      - 98765 43210
      - 98765-43210
      - +91 98765 43210
      - 919876543210
      - 09876543210
      - 1234567890 (accepts synthetic 10-digit test numbers; NO first-digit 6-9 restriction)

    Rejects:
      - Short numbers (e.g. 123, 12345, 123456789)
      - Long numbers (e.g. 12345678901)
      - Letters or invalid characters (e.g. phone123)

    Returns a structured dictionary:
      {
          "valid": bool,
          "phone": str,           # 10-digit normalized string if valid, else input digits
          "code": str,            # "VALID", "INVALID_PHONE_LENGTH", "INVALID_CHARACTERS", "MISSING_PHONE"
          "received_digits": int,
          "expected_digits": 10,
          "message": str
      }
    """
    if phone_str is None or not str(phone_str).strip():
        return {
            "valid": False,
            "phone": "",
            "code": "MISSING_PHONE",
            "received_digits": 0,
            "expected_digits": 10,
            "message": "Phone number is required."
        }

    raw = str(phone_str).strip()
    if re.search(r"[a-zA-Z]", raw):
        digits = re.sub(r"\D", "", raw)
        return {
            "valid": False,
            "phone": digits,
            "code": "INVALID_CHARACTERS",
            "received_digits": len(digits),
            "expected_digits": 10,
            "message": f"Phone number '{raw}' contains invalid characters."
        }

    digits = re.sub(r"\D", "", raw)

    # Strip leading country code +91 or 91 if exactly 12 digits
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    # Strip leading trunk 0 if exactly 11 digits
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == 10:
        return {
            "valid": True,
            "phone": digits,
            "code": "VALID",
            "received_digits": 10,
            "expected_digits": 10,
            "message": "Valid 10-digit phone number."
        }

    return {
        "valid": False,
        "phone": digits,
        "code": "INVALID_PHONE_LENGTH",
        "received_digits": len(digits),
        "expected_digits": 10,
        "message": f"Phone number has {len(digits)} digits. Exactly 10 digits are required."
    }


def register_caller_context(context_data: Dict[str, Any]) -> str:
    """
    Registers a caller context into in-memory lookup cache (ephemeral).
    Indexes deterministically by lead_id, normalized full digits, and last-10 digits.
    No global fallback or _latest_pending_lead is set to avoid cross-caller pollution.
    """
    lead_id = str(context_data.get("id") or generate_18_digit_lead_id())
    context_data["id"] = lead_id
    if "created_at" not in context_data:
        context_data["created_at"] = datetime.now(timezone.utc).isoformat()

    phone = context_data.get("phone", "")
    digits = normalize_phone(phone)
    
    # Store by lead_id
    ACTIVE_CALLER_CONTEXTS[lead_id] = context_data

    # Store by full digits (e.g. 918758657212)
    if digits:
        ACTIVE_CALLER_CONTEXTS[digits] = context_data
        # Store by last 10 digits (e.g. 8758657212)
        if len(digits) >= 10:
            ACTIVE_CALLER_CONTEXTS[digits[-10:]] = context_data

    # Persist to Redis L2 cache
    try:
        from agent.cache import cache_set_sync, build_call_context_key, TTL_CALL_CONTEXT
        cache_set_sync(build_call_context_key(lead_id), context_data, ttl_seconds=TTL_CALL_CONTEXT)
        if digits:
            cache_set_sync(build_call_context_key(digits), context_data, ttl_seconds=TTL_CALL_CONTEXT)
            if len(digits) >= 10:
                cache_set_sync(build_call_context_key(digits[-10:]), context_data, ttl_seconds=TTL_CALL_CONTEXT)
    except Exception:
        pass

    return lead_id


def lookup_caller_context(identifier: Optional[Any], allow_latest_fallback: bool = False) -> Optional[Dict[str, Any]]:
    """
    Deterministically looks up caller context by lead_id, call_id, or normalized phone number.
    Failed lookups return None.
    Never returns an unrelated caller's context unless allow_latest_fallback is explicitly True.
    """
    if identifier is None:
        if allow_latest_fallback:
            return ACTIVE_CALLER_CONTEXTS.get("_latest_pending_lead")
        return None

    if isinstance(identifier, str):
        clean_id = identifier.strip()
    else:
        clean_id = str(identifier).strip()

    if not clean_id or clean_id.lower() == "none":
        if allow_latest_fallback:
            return ACTIVE_CALLER_CONTEXTS.get("_latest_pending_lead")
        return None

    # 1. Exact match in memory cache (lead_id or call_sid)
    if clean_id in ACTIVE_CALLER_CONTEXTS:
        res = ACTIVE_CALLER_CONTEXTS[clean_id]
        logger.info(f"[CALLER CONTEXT] [DATA SOURCE: RAM_L1] Exact match for '{clean_id}' (name='{res.get('name')}', phone='{res.get('phone')}')")
        return res

    # 2. Normalized phone match in memory cache
    digits = normalize_phone(clean_id)
    if digits and digits in ACTIVE_CALLER_CONTEXTS:
        res = ACTIVE_CALLER_CONTEXTS[digits]
        logger.info(f"[CALLER CONTEXT] [DATA SOURCE: RAM_L1] Normalized phone match for '{clean_id}' (phone='{res.get('phone')}')")
        return res
    if digits and len(digits) >= 10 and digits[-10:] in ACTIVE_CALLER_CONTEXTS:
        res = ACTIVE_CALLER_CONTEXTS[digits[-10:]]
        logger.info(f"[CALLER CONTEXT] [DATA SOURCE: RAM_L1] 10-digit phone match for '{clean_id}' (phone='{res.get('phone')}')")
        return res

    # 3. Explicit fallback only if enabled (disabled by default)
    if allow_latest_fallback:
        res = ACTIVE_CALLER_CONTEXTS.get("_latest_pending_lead")
        if res:
            logger.info(f"[CALLER CONTEXT] [DATA SOURCE: RAM_L1] Latest pending fallback matched for '{clean_id}'")
            return res

    # 4. Redis L2 fallback (after RAM miss)
    try:
        from agent.cache import cache_get_sync, build_call_context_key
        redis_val = cache_get_sync(build_call_context_key(clean_id))
        if not redis_val and digits:
            redis_val = cache_get_sync(build_call_context_key(digits))
            if not redis_val and len(digits) >= 10:
                redis_val = cache_get_sync(build_call_context_key(digits[-10:]))
        if redis_val:
            ACTIVE_CALLER_CONTEXTS[clean_id] = redis_val  # Re-warm L1
            logger.info(f"[CALLER CONTEXT] [DATA SOURCE: REDIS_L2] Context resolved from Redis L2 for '{clean_id}' (re-warmed RAM L1)")
            return redis_val
    except Exception as e:
        logger.warning(f"[CALLER CONTEXT] Redis L2 lookup error for '{clean_id}': {e}")

    logger.info(f"[CALLER CONTEXT] [MISS] No caller context found for '{clean_id}'")
    return None


def reindex_caller_phone(old_phone: Optional[str], new_phone: str, context_data: Dict[str, Any]) -> None:
    """
    Reindexes ACTIVE_CALLER_CONTEXTS when a caller updates their phone number.
    Removes old phone indices and registers new phone indices for the caller context.
    """
    if old_phone:
        old_digits = normalize_phone(old_phone)
        if old_digits:
            ACTIVE_CALLER_CONTEXTS.pop(old_digits, None)
            if len(old_digits) >= 10:
                ACTIVE_CALLER_CONTEXTS.pop(old_digits[-10:], None)
            try:
                from agent.cache import cache_delete_sync, build_call_context_key
                cache_delete_sync(build_call_context_key(old_digits))
                if len(old_digits) >= 10:
                    cache_delete_sync(build_call_context_key(old_digits[-10:]))
            except Exception:
                pass

    new_digits = normalize_phone(new_phone)
    if new_digits:
        ACTIVE_CALLER_CONTEXTS[new_digits] = context_data
        if len(new_digits) >= 10:
            ACTIVE_CALLER_CONTEXTS[new_digits[-10:]] = context_data
        try:
            from agent.cache import cache_set_sync, build_call_context_key, TTL_CALL_CONTEXT
            cache_set_sync(build_call_context_key(new_digits), context_data, ttl_seconds=TTL_CALL_CONTEXT)
            if len(new_digits) >= 10:
                cache_set_sync(build_call_context_key(new_digits[-10:]), context_data, ttl_seconds=TTL_CALL_CONTEXT)
        except Exception:
            pass


def remove_caller_context(lead_id: Optional[str] = None, phone: Optional[str] = None, call_id: Optional[str] = None) -> None:
    """
    Removes caller context from the active memory cache to free resources after a call ends.
    """
    keys_to_remove = []
    if lead_id:
        keys_to_remove.append(lead_id)
    if call_id:
        keys_to_remove.append(call_id)
    if phone:
        digits = normalize_phone(phone)
        if digits:
            keys_to_remove.append(digits)
            if len(digits) >= 10:
                keys_to_remove.append(digits[-10:])
                
    for k in keys_to_remove:
        ACTIVE_CALLER_CONTEXTS.pop(k, None)
        try:
            from agent.cache import cache_delete_sync, build_call_context_key
            cache_delete_sync(build_call_context_key(k))
        except Exception:
            pass

    # Check _latest_pending_lead if lingering
    latest = ACTIVE_CALLER_CONTEXTS.get("_latest_pending_lead")
    if latest:
        if (lead_id and latest.get("id") == lead_id) or \
           (phone and normalize_phone(latest.get("phone", "")) == normalize_phone(phone)):
            ACTIVE_CALLER_CONTEXTS.pop("_latest_pending_lead", None)
