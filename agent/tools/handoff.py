"""
Tool: human_handoff
Escalates the call to a human patient care team.
"""

import json
import uuid
import os
from datetime import datetime, timezone
from typing import Optional

LEADS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "leads.json")

def _load_leads() -> list:
    if not os.path.exists(LEADS_FILE):
        return []
    with open(LEADS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []

def _save_leads(leads: list) -> None:
    # Ephemeral mode: disk saving disabled to avoid storing user data
    pass

def human_handoff(reason: str, phone_number: Optional[str] = None, call_id: Optional[str] = None) -> dict:
    """
    Escalate to human patient care team.
    Saves status 'human_handoff_requested' to data/leads.json with call_id linkage.
    
    Args:
        reason: The reason for the handoff.
        phone_number: The caller's phone number (optional).
        call_id: The ID of the call (injected at runtime).
        
    Returns:
        dict: A response dictionary indicating the action Kiara should take next.
    """
    # Save the handoff request to leads.json
    lead = {
        "id": str(uuid.uuid4()),
        "call_id": call_id,
        "name": "Unknown", # Can be updated if we had capture_lead before
        "phone": phone_number.strip() if phone_number else "Unknown",
        "city": "Unknown",
        "intent": "human_handoff_requested",
        "notes": f"Handoff Reason: {reason}",
        "preferred_language": "en", # Fallback, could be injected
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "human_handoff_requested"
    }

    leads = _load_leads()
    
    # Check if we already have a lead for this call_id
    existing_lead = next((l for l in leads if l.get("call_id") == call_id), None)
    if existing_lead:
        existing_lead["status"] = "human_handoff_requested"
        existing_lead["intent"] = "human_handoff_requested"
        if existing_lead.get("notes"):
            existing_lead["notes"] += f" | Handoff Reason: {reason}"
        else:
            existing_lead["notes"] = f"Handoff Reason: {reason}"
        if phone_number and (not existing_lead.get("phone") or existing_lead.get("phone") == "Unknown"):
            existing_lead["phone"] = phone_number.strip()
    else:
        leads.append(lead)
        
    _save_leads(leads)
    
    # Return a message instructing the LLM what to say before ending the call
    return {
        "status": "success",
        "message": "Handoff details recorded successfully. Tell the caller exactly: 'You can reach our patient care team directly by calling the number on ultimatesmiledesign.com.', and then politely end the call.",
        "action": "end_call"
    }
