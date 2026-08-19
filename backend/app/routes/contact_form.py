import os
import json
import uuid
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.app.services.smartflo_service import smartflo_client

router = APIRouter()

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
    Registers a caller context into both in-memory lookup cache and leads.json file.
    Indexes by lead_id, normalized full digits, and last-10 digits.
    """
    lead_id = context_data.get("id") or str(uuid.uuid4())
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

    # Persist to data/leads.json
    try:
        leads_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "leads.json"))
        leads = []
        if os.path.exists(leads_path):
            with open(leads_path, "r", encoding="utf-8") as f:
                try:
                    leads = json.load(f)
                    if not isinstance(leads, list):
                        leads = []
                except Exception:
                    leads = []
        
        # Avoid duplicate append if ID exists
        existing_idx = next((i for i, l in enumerate(leads) if l.get("id") == lead_id), None)
        if existing_idx is not None:
            leads[existing_idx] = context_data
        else:
            leads.append(context_data)

        with open(leads_path, "w", encoding="utf-8") as f:
            json.dump(leads, f, indent=2)
    except Exception as e:
        print(f"[ContactForm] Warning: Could not write to leads.json: {e}")

    return lead_id


def lookup_caller_context(identifier: Optional[Any]) -> Optional[Dict[str, Any]]:
    """
    Looks up caller context by lead_id or phone number from in-memory cache and leads.json.
    """
    if identifier is None:
        return None

    if isinstance(identifier, str):
        clean_id = identifier.strip()
    else:
        clean_id = str(identifier).strip()

    if not clean_id:
        return None
    
    # 1. Exact match in memory cache
    if clean_id in ACTIVE_CALLER_CONTEXTS:
        return ACTIVE_CALLER_CONTEXTS[clean_id]

    # 2. Normalized phone match in memory cache
    digits = normalize_phone(clean_id)
    if digits and digits in ACTIVE_CALLER_CONTEXTS:
        return ACTIVE_CALLER_CONTEXTS[digits]
    if digits and len(digits) >= 10 and digits[-10:] in ACTIVE_CALLER_CONTEXTS:
        return ACTIVE_CALLER_CONTEXTS[digits[-10:]]

    # 3. Fallback search in data/leads.json
    try:
        leads_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "leads.json"))
        if os.path.exists(leads_path):
            with open(leads_path, "r", encoding="utf-8") as f:
                leads = json.load(f)
                for l in reversed(leads):  # Check newest first
                    if l.get("id") == clean_id:
                        return l
                    l_phone_digits = normalize_phone(l.get("phone", ""))
                    if digits and l_phone_digits and (digits == l_phone_digits or (len(digits) >= 10 and digits[-10:] == l_phone_digits[-10:])):
                        return l
    except Exception as e:
        print(f"[ContactForm] Warning: Error looking up lead in leads.json: {e}")

    return None


class ContactFormSubmission(BaseModel):
    name: str = Field(..., min_length=1, description="Caller full name")
    email: str = Field(..., min_length=3, description="Caller email address")
    phone: str = Field(..., min_length=5, description="Caller phone number (outbound destination)")
    city: str = Field(..., min_length=1, description="Caller city")
    subject: Optional[str] = Field(default="", description="Consultation subject or topic")
    message: Optional[str] = Field(default="", description="Enquiry details or notes")


@router.get("/contact-form", response_class=HTMLResponse)
async def get_contact_form():
    """Serves the luxury Contact Form webpage."""
    template_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates", "contact_form.html"))
    if not os.path.exists(template_path):
        raise HTTPException(status_code=404, detail="Contact form template not found.")
    
    with open(template_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200)


@router.post("/api/contact-form/submit")
async def submit_contact_form(form_data: ContactFormSubmission):
    """
    Receives submitted caller contact form details, caches context, logs lead,
    and initiates outbound Click-to-Call via Tata Smartflo.
    """
    lead_id = str(uuid.uuid4())
    context_data = {
        "id": lead_id,
        "name": form_data.name.strip(),
        "phone": form_data.phone.strip(),
        "email": form_data.email.strip(),
        "city": form_data.city.strip(),
        "subject": form_data.subject.strip() if form_data.subject else "",
        "notes": form_data.message.strip() if form_data.message else "",
        "message": form_data.message.strip() if form_data.message else "",
        "intent": "outbound_contact_form",
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    # Register into active lookup cache & persist lead
    register_caller_context(context_data)
    print(f"[ContactForm] Registered caller context for {context_data['name']} ({context_data['phone']}) - Lead ID: {lead_id}")

    # Initiate Smartflo Click-to-Call
    custom_params = {
        "lead_id": lead_id,
        "opening_intent": "outbound_contact_form"
    }

    result = await smartflo_client.initiate_click_to_call(
        customer_number=form_data.phone.strip(),
        custom_params=custom_params
    )

    print(f"[ContactForm] Smartflo click-to-call response: {result}")

    return {
        "success": True,
        "lead_id": lead_id,
        "customer_number": form_data.phone.strip(),
        "smartflo_response": result
    }
