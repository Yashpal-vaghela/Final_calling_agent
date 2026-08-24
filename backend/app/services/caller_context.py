import re
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# In-memory fast cache for active caller contexts mapped by lead_id, raw phone, and 10-digit normalized phone
ACTIVE_CALLER_CONTEXTS: Dict[str, Dict[str, Any]] = {}


def normalize_phone(phone_str: str) -> str:
    """Extracts numeric digits only for robust phone lookup matching."""
    if not phone_str:
        return ""
    digits = re.sub(r"\D", "", phone_str)
    return digits


def register_caller_context(context_data: Dict[str, Any]) -> str:
    """
    Registers a caller context into in-memory lookup cache (ephemeral).
    Indexes by lead_id, normalized full digits, and last-10 digits.
    No data is saved to disk.
    """
    lead_id = context_data.get("id") or str(uuid.uuid4())
    context_data["id"] = lead_id
    if "created_at" not in context_data:
        context_data["created_at"] = datetime.now(timezone.utc).isoformat()

    phone = context_data.get("phone", "")
    digits = normalize_phone(phone)
    
    # Store by lead_id
    ACTIVE_CALLER_CONTEXTS[lead_id] = context_data
    # Store as latest pending lead for fallback session bridging
    ACTIVE_CALLER_CONTEXTS["_latest_pending_lead"] = context_data

    # Store by full digits (e.g. 918758657212)
    if digits:
        ACTIVE_CALLER_CONTEXTS[digits] = context_data
        # Store by last 10 digits (e.g. 8758657212)
        if len(digits) >= 10:
            ACTIVE_CALLER_CONTEXTS[digits[-10:]] = context_data

    return lead_id


def lookup_caller_context(identifier: Optional[Any]) -> Optional[Dict[str, Any]]:
    """
    Looks up caller context by lead_id, phone number, or session fallback from ephemeral in-memory cache.
    """
    if isinstance(identifier, str):
        clean_id = identifier.strip()
    elif identifier is not None:
        clean_id = str(identifier).strip()
    else:
        clean_id = "_latest_pending_lead"

    if clean_id == "" or clean_id == "latest":
        clean_id = "_latest_pending_lead"

    # 1. Exact match in memory cache (including _latest_pending_lead)
    if clean_id in ACTIVE_CALLER_CONTEXTS:
        return ACTIVE_CALLER_CONTEXTS[clean_id]

    # 2. Normalized phone match in memory cache
    digits = normalize_phone(clean_id)
    if digits and digits in ACTIVE_CALLER_CONTEXTS:
        return ACTIVE_CALLER_CONTEXTS[digits]
    if digits and len(digits) >= 10 and digits[-10:] in ACTIVE_CALLER_CONTEXTS:
        return ACTIVE_CALLER_CONTEXTS[digits[-10:]]

    # 3. Fallback to latest pending lead in active memory
    return ACTIVE_CALLER_CONTEXTS.get("_latest_pending_lead")


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

    # Check _latest_pending_lead
    latest = ACTIVE_CALLER_CONTEXTS.get("_latest_pending_lead")
    if latest:
        if (lead_id and latest.get("id") == lead_id) or \
           (phone and normalize_phone(latest.get("phone", "")) == normalize_phone(phone)):
            ACTIVE_CALLER_CONTEXTS.pop("_latest_pending_lead", None)
