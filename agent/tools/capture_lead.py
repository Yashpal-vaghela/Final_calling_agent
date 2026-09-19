"""
Tool: capture_lead
Saves a caller's lead details to persistent storage (JSON file for Phase 3,
promoted to a real database table in Phase 5).
"""

import json
import uuid
import os
from datetime import datetime, timezone
from typing import Optional

from backend.app.services.caller_context import validate_phone_number

# Phase 3: file-backed store. Phase 5 will swap this for a DB table.
LEADS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "leads.json")

VALID_LANGUAGES = {"en", "hi", "gu"}

def _load_leads() -> list:
    if not os.path.exists(LEADS_FILE):
        return []
    with open(LEADS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []

def _save_leads(leads: list) -> bool:
    # Ephemeral mode: disk saving disabled to avoid storing user data
    return False

def capture_lead(
    name: str,
    phone: str,
    city: str,
    intent: str,
    notes: Optional[str] = None,
    preferred_language: str = "en",
    call_id: Optional[str] = None,
) -> dict:
    """
    Persists caller lead details.
    When persistence is disabled, returns an honest 'not_persisted' status without claiming
    data was saved or promising a callback.
    """
    if preferred_language not in VALID_LANGUAGES:
        preferred_language = "en"

    phone_res = validate_phone_number(phone)
    if not phone_res["valid"]:
        return {
            "status": "invalid_phone",
            "valid": False,
            "code": phone_res["code"],
            "received_digits": phone_res["received_digits"],
            "expected_digits": 10,
            "message": f"Invalid phone number. {phone_res['message']} Please provide a valid 10-digit mobile number."
        }
    norm_phone = phone_res["phone"]

    lead = {
        "id": str(uuid.uuid4()),
        "call_id": call_id,
        "name": name.strip(),
        "phone": norm_phone,
        "city": city.strip(),
        "intent": intent.strip(),
        "notes": notes.strip() if notes else None,
        "preferred_language": preferred_language,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    leads = _load_leads()
    leads.append(lead)
    saved = _save_leads(leads)

    if not saved:
        return {
            "status": "not_persisted",
            "lead_id": None,
            "message": "Lead details received in memory, but persistent storage is currently disabled in this environment. No callback has been scheduled.",
            "lead": lead
        }

    return {
        "status": "success",
        "lead_id": lead["id"],
        "message": f"Details for {name} saved successfully.",
        "lead": lead,
    }
