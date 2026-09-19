"""
Tool: update_caller_profile (and alias update_contact_details)
Allows explicit caller-detail updates (name, phone) with deterministic validation,
confirmation gating, and backend persistence verification.
"""

import json
import httpx
from typing import Optional, Dict, Any

from backend.app.services.caller_context import validate_phone_number

API_URL = "http://192.168.0.161:5050/api/consult-with-dentist/"


def update_caller_profile(
    name: Optional[str] = None,
    phone: Optional[str] = None,
    confirm_phone: Optional[bool] = False,
    lead_id: Optional[str] = None,
    email: Optional[str] = None,
    city: Optional[str] = None,
    doctor_name: Optional[str] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update or correct the caller's profile details (name or phone number).
    ONLY call this tool when the caller explicitly provides or corrects their own identity.

    CRITICAL RULES FOR CALLER NAME:
    - Never invent a caller name.
    - Never infer a caller name from unrelated words.
    - Never autocomplete names.
    - Never translate names.
    - Never change a trusted form name unless the caller explicitly corrects it.
    - If a spoken name is uncertain, ask caller to repeat it rather than guessing.
    - It is better to omit the caller's name than use an uncertain or wrong name.

    CRITICAL RULES FOR PHONE UPDATES:
    - Pass complete 10-digit mobile number.
    - If caller provided fewer or more digits, do NOT call this tool; ask for complete 10 digits.
    - If confirm_phone is False, this tool validates and returns status="confirmation_required".
    - Once confirmed by caller, pass confirm_phone=True to commit the change to the backend.
    """
    result: Dict[str, Any] = {
        "status": "no_change",
        "message": "No contact details were provided to update."
    }

    clean_lead_id = int(lead_id.strip()) if (lead_id and lead_id.strip().isdigit()) else ""

    # 1. Handle Name Update
    if name is not None and name.strip():
        clean_name = name.strip()
        result["status"] = "success"
        result["name"] = clean_name
        result["message"] = f'Caller name updated to "{clean_name}".'

    # 2. Handle Phone Update
    if phone is not None and phone.strip():
        validation = validate_phone_number(phone)
        if not validation["valid"]:
            return {
                "status": "invalid_phone",
                "valid": False,
                "code": validation["code"],
                "received_digits": validation["received_digits"],
                "expected_digits": 10,
                "message": f"Invalid phone number. {validation['message']} Please ask the caller for their complete 10-digit mobile number."
            }

        norm_phone = validation["phone"]

        # Step 2A: Confirmation required before committing mutation
        if not confirm_phone:
            return {
                "status": "confirmation_required",
                "valid": True,
                "phone": norm_phone,
                "last_4": norm_phone[-4:],
                "message": f'Please ask the caller to confirm: "Just to confirm, the number ending in {norm_phone[-4:]}—is that correct?"'
            }

        # Step 2B: Confirmed by caller -> check backend persistence
        if not clean_lead_id:
            # No registered CRM booking record exists to update
            return {
                "status": "persistence_unavailable",
                "phone": norm_phone,
                "message": "No registered backend booking record exists to update contact details. Phone update cannot be persisted."
            }

        # Perform backend CRM update
        payload = {
            "lead_id": clean_lead_id,
            "phone": norm_phone,
            "source": "calling_agent"
        }
        if name and name.strip():
            parts = name.strip().split(maxsplit=1)
            payload["first_name"] = parts[0]
            payload["last_name"] = parts[1] if len(parts) > 1 else "."
        if email and email.strip():
            payload["email"] = email.strip()
        if city and city.strip():
            payload["city"] = city.strip()
        if doctor_name and doctor_name.strip():
            payload["doctor_name"] = doctor_name.strip()
        if message and message.strip():
            payload["message"] = message.strip()

        print("\n" + "="*70)
        print(">>> [PROFILE UPDATE] Submitting Contact Details Update to CRM API:")
        print(f"Target URL: {API_URL}")
        print(f"Payload sent:\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
        print("="*70)

        try:
            response = httpx.post(API_URL, json=payload, timeout=10.0)
            response.raise_for_status()
            print(f"[+] [PROFILE UPDATE SUCCESS] HTTP {response.status_code}")
            return {
                "status": "success",
                "phone": norm_phone,
                "name": result.get("name"),
                "lead_id": str(clean_lead_id),
                "message": f"Phone number successfully updated to {norm_phone}."
            }
        except Exception as e:
            print(f"[-] [PROFILE UPDATE FAILED] Error: {e}")
            return {
                "status": "error",
                "phone": norm_phone,
                "message": f"Failed to update phone number in backend system: {str(e)}"
            }

    return result

# Alias for backwards compatibility and schema flexibility
update_contact_details = update_caller_profile
