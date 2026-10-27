import re
import uuid
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from backend.app.services.id_service import generate_18_digit_lead_id

logger = logging.getLogger(__name__)

# In-memory fast cache for active caller contexts mapped by lead_id, raw phone, and 10-digit normalized phone
ACTIVE_CALLER_CONTEXTS: Dict[str, Dict[str, Any]] = {}
# Pending FIFO queue per normalized phone number to disambiguate consecutive calls with the same phone number
PENDING_CALLER_CONTEXTS_BY_PHONE: Dict[str, list] = {}


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
    Maintains a FIFO queue of pending calls per phone number to correctly correlate
    consecutive outbound calls when Smartflo's dynamic webhook does not include lead_id.
    Persists to Redis asynchronously in the background without blocking the caller.
    """
    lead_id = str(context_data.get("id") or generate_18_digit_lead_id())
    context_data["id"] = lead_id
    if "created_at" not in context_data:
        context_data["created_at"] = datetime.now(timezone.utc).isoformat()

    now_ts = datetime.now(timezone.utc).timestamp()
    context_data["_registered_ts"] = now_ts

    phone = context_data.get("phone", "")
    digits = normalize_phone(phone)
    
    # Store by lead_id in RAM L1
    ACTIVE_CALLER_CONTEXTS[lead_id] = context_data

    # Store by full digits (e.g. 918758657212) in RAM L1
    if digits:
        ACTIVE_CALLER_CONTEXTS[digits] = context_data
        
        # Append to pending FIFO queue for this phone number (cleaning expired > 600s entries)
        q = PENDING_CALLER_CONTEXTS_BY_PHONE.setdefault(digits, [])
        PENDING_CALLER_CONTEXTS_BY_PHONE[digits] = [
            item for item in q if now_ts - item.get("_registered_ts", now_ts) < 600
        ] + [context_data]

        # Store by last 10 digits (e.g. 8758657212)
        if len(digits) >= 10:
            last10 = digits[-10:]
            ACTIVE_CALLER_CONTEXTS[last10] = context_data
            if last10 != digits:
                q10 = PENDING_CALLER_CONTEXTS_BY_PHONE.setdefault(last10, [])
                PENDING_CALLER_CONTEXTS_BY_PHONE[last10] = [
                    item for item in q10 if now_ts - item.get("_registered_ts", now_ts) < 600
                ] + [context_data]

    # Non-blocking async persistence to Redis L2 cache
    try:
        from agent.cache import cache_set, build_call_context_key, TTL_CALL_CONTEXT
        keys_to_set = [build_call_context_key(lead_id)]
        if digits:
            keys_to_set.append(build_call_context_key(digits))
            if len(digits) >= 10:
                keys_to_set.append(build_call_context_key(digits[-10:]))

        async def _do_persist():
            for k in keys_to_set:
                try:
                    await cache_set(k, context_data, ttl_seconds=TTL_CALL_CONTEXT)
                except Exception:
                    pass

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_persist())
        except RuntimeError:
            from agent.cache import cache_set_sync
            for k in keys_to_set:
                cache_set_sync(k, context_data, ttl_seconds=TTL_CALL_CONTEXT)
    except Exception:
        pass

    return lead_id


def pop_pending_caller_context_by_phone(phone_str: Optional[Any]) -> Optional[Dict[str, Any]]:
    """
    Pops the oldest pending caller context for a phone number in FIFO order.
    Used by Smartflo dynamic endpoint to match multiple outbound calls queued for the same phone number.
    """
    if not phone_str:
        return None
    digits = normalize_phone(phone_str)
    if not digits:
        return None

    keys_to_check = [digits]
    if len(digits) >= 10 and digits[-10:] not in keys_to_check:
        keys_to_check.append(digits[-10:])

    for k in keys_to_check:
        queue = PENDING_CALLER_CONTEXTS_BY_PHONE.get(k)
        if queue:
            matched = queue.pop(0)
            logger.info(
                f"[CALLER CONTEXT] [FIFO MATCH] Popped pending context for phone '{k}' "
                f"(Lead ID: {matched.get('id')}, Name: '{matched.get('name')}', City: '{matched.get('city')}')"
            )
            if not queue:
                PENDING_CALLER_CONTEXTS_BY_PHONE.pop(k, None)
            # Synchronize the other representation
            for other_k in keys_to_check:
                if other_k != k and other_k in PENDING_CALLER_CONTEXTS_BY_PHONE:
                    PENDING_CALLER_CONTEXTS_BY_PHONE[other_k] = [
                        item for item in PENDING_CALLER_CONTEXTS_BY_PHONE[other_k]
                        if str(item.get("id")) != str(matched.get("id"))
                    ]
                    if not PENDING_CALLER_CONTEXTS_BY_PHONE[other_k]:
                        PENDING_CALLER_CONTEXTS_BY_PHONE.pop(other_k, None)
            return matched

    return None


def lookup_caller_context(identifier: Optional[Any], allow_latest_fallback: bool = False) -> Optional[Dict[str, Any]]:
    """
    Ultra-fast L1 RAM lookup (0.001 ms) - NEVER blocks the event loop with synchronous network calls.
    Deterministically looks up caller context by lead_id, call_id, or normalized phone number in RAM.
    Failed lookups return None immediately without freezing the main thread.
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

    # Return None immediately - zero blocking network calls on the main thread!
    logger.info(f"[CALLER CONTEXT] [MISS: RAM_L1] No caller context found in RAM for '{clean_id}'")
    return None


async def async_lookup_caller_context(identifier: Optional[Any], allow_latest_fallback: bool = False, timeout_seconds: float = 0.15) -> Optional[Dict[str, Any]]:
    """
    Asynchronous caller context lookup:
    1. Checks ultra-fast RAM L1 cache (0.001ms).
    2. If missed and Redis is enabled, checks Redis L2 asynchronously with a strict timeout (default 150ms)
       to ensure the asyncio event loop NEVER freezes.
    3. Re-warms RAM L1 cache on Redis hit.
    """
    # 1. Ultra-fast path: RAM L1
    ram_hit = lookup_caller_context(identifier, allow_latest_fallback=allow_latest_fallback)
    if ram_hit:
        return ram_hit

    if not identifier:
        return None
    clean_id = str(identifier).strip()
    if not clean_id or clean_id.lower() == "none":
        return None

    # 2. Non-blocking Redis L2 async query
    try:
        from agent.cache import cache_get, build_call_context_key
        digits = normalize_phone(clean_id)

        async def _query_redis():
            val = await cache_get(build_call_context_key(clean_id))
            if not val and digits:
                val = await cache_get(build_call_context_key(digits))
                if not val and len(digits) >= 10:
                    val = await cache_get(build_call_context_key(digits[-10:]))
            return val

        redis_val = await asyncio.wait_for(_query_redis(), timeout=timeout_seconds)
        if redis_val and isinstance(redis_val, dict):
            ACTIVE_CALLER_CONTEXTS[clean_id] = redis_val
            if digits:
                ACTIVE_CALLER_CONTEXTS[digits] = redis_val
            logger.info(f"[CALLER CONTEXT] [DATA SOURCE: REDIS_L2] Context resolved asynchronously for '{clean_id}' (re-warmed RAM L1)")
            return redis_val
    except asyncio.TimeoutError:
        logger.warning(f"[CALLER CONTEXT] Async Redis lookup timed out (> {timeout_seconds}s) for '{clean_id}'. Proceeding snappily.")
    except Exception as e:
        logger.warning(f"[CALLER CONTEXT] Async Redis lookup error for '{clean_id}': {e}")

    return None


def reindex_caller_phone(old_phone: Optional[str], new_phone: str, context_data: Dict[str, Any]) -> None:
    """
    Reindexes ACTIVE_CALLER_CONTEXTS when a caller updates their phone number.
    Removes old phone indices and registers new phone indices for the caller context.
    """
    old_keys = []
    if old_phone:
        old_digits = normalize_phone(old_phone)
        if old_digits:
            ACTIVE_CALLER_CONTEXTS.pop(old_digits, None)
            old_keys.append(old_digits)
            if len(old_digits) >= 10:
                ACTIVE_CALLER_CONTEXTS.pop(old_digits[-10:], None)
                old_keys.append(old_digits[-10:])

    new_digits = normalize_phone(new_phone)
    new_keys = []
    if new_digits:
        ACTIVE_CALLER_CONTEXTS[new_digits] = context_data
        new_keys.append(new_digits)
        if len(new_digits) >= 10:
            ACTIVE_CALLER_CONTEXTS[new_digits[-10:]] = context_data
            new_keys.append(new_digits[-10:])

    try:
        from agent.cache import cache_delete, cache_set, build_call_context_key, TTL_CALL_CONTEXT
        async def _do_reindex():
            for ok in old_keys:
                try: await cache_delete(build_call_context_key(ok))
                except Exception: pass
            for nk in new_keys:
                try: await cache_set(build_call_context_key(nk), context_data, ttl_seconds=TTL_CALL_CONTEXT)
                except Exception: pass

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_reindex())
        except RuntimeError:
            from agent.cache import cache_delete_sync, cache_set_sync
            for ok in old_keys: cache_delete_sync(build_call_context_key(ok))
            for nk in new_keys: cache_set_sync(build_call_context_key(nk), context_data, ttl_seconds=TTL_CALL_CONTEXT)
    except Exception:
        pass


def remove_caller_context(lead_id: Optional[str] = None, phone: Optional[str] = None, call_id: Optional[str] = None, attempt_id: Optional[str] = None) -> None:
    """
    Removes caller context from the active memory cache to free resources after a call ends.
    Guards against deleting newer contexts belonging to other leads with the same phone number.
    Non-blockingly purges keys from Redis L2.
    """
    keys_to_remove = []
    for key in (lead_id, call_id, attempt_id):
        existing = ACTIVE_CALLER_CONTEXTS.get(key) if key else None
        if key and (not attempt_id or existing is None or existing.get("attempt_id") == attempt_id):
            ACTIVE_CALLER_CONTEXTS.pop(key, None)
            keys_to_remove.append(key)
    if attempt_id:
        # Remove only this attempt's RAM aliases; durable provider mappings remain.
        for key, context in list(ACTIVE_CALLER_CONTEXTS.items()):
            if context.get("attempt_id") == attempt_id:
                ACTIVE_CALLER_CONTEXTS.pop(key, None)
                keys_to_remove.append(key)
    if phone:
        digits = normalize_phone(phone)
        phone_keys = []
        if digits:
            phone_keys.append(digits)
            if len(digits) >= 10 and digits[-10:] not in phone_keys:
                phone_keys.append(digits[-10:])

        for pk in phone_keys:
            existing = ACTIVE_CALLER_CONTEXTS.get(pk)
            # CRITICAL OWNERSHIP GUARD:
            # Only remove if no lead_id is specified, or if the stored context's id matches the lead_id being cleaned up!
            if (existing is None or not lead_id or str(existing.get("id")) == lead_id) and (
                not attempt_id or existing is None or existing.get("attempt_id") == attempt_id
            ):
                ACTIVE_CALLER_CONTEXTS.pop(pk, None)
                keys_to_remove.append(pk)
            else:
                logger.info(
                    f"[CALLER CONTEXT] Preserved phone key '{pk}' in RAM L1 "
                    f"(currently belongs to newer lead '{existing.get('id')}', cleanup requested for '{lead_id}')"
                )

            # Prune this lead from pending FIFO queue
            if pk in PENDING_CALLER_CONTEXTS_BY_PHONE and lead_id:
                PENDING_CALLER_CONTEXTS_BY_PHONE[pk] = [
                    item for item in PENDING_CALLER_CONTEXTS_BY_PHONE[pk]
                    if (item.get("attempt_id") != attempt_id if attempt_id else str(item.get("id")) != lead_id)
                ]
                if not PENDING_CALLER_CONTEXTS_BY_PHONE[pk]:
                    PENDING_CALLER_CONTEXTS_BY_PHONE.pop(pk, None)

    try:
        from agent.cache import cache_delete, build_call_context_key
        del_keys = [build_call_context_key(k) for k in keys_to_remove]
        async def _do_delete():
            for dk in del_keys:
                try:
                    await cache_delete(dk)
                except Exception:
                    pass
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_delete())
        except RuntimeError:
            from agent.cache import cache_delete_sync
            for dk in del_keys:
                cache_delete_sync(dk)
    except Exception:
        pass

    # Check _latest_pending_lead if lingering
    latest = ACTIVE_CALLER_CONTEXTS.get("_latest_pending_lead")
    if latest and (not attempt_id or latest.get("attempt_id") == attempt_id):
        if (lead_id and latest.get("id") == lead_id) or \
           (phone and normalize_phone(latest.get("phone", "")) == normalize_phone(phone)):
            ACTIVE_CALLER_CONTEXTS.pop("_latest_pending_lead", None)

