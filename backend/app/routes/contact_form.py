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

from backend.app.services.caller_context import ACTIVE_CALLER_CONTEXTS, register_caller_context
from backend.app.services.city_service import is_city_covered

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
    if not is_city_covered(form_data.city):
        raise HTTPException(status_code=400, detail=f"City '{form_data.city}' is not covered. Please select a valid city.")

    lead_id = str(uuid.uuid4())
    raw_name = form_data.name.strip()
    name_parts = raw_name.split(maxsplit=1) if raw_name else []
    first_name = name_parts[0] if name_parts else ""
    last_name = name_parts[1] if len(name_parts) > 1 else "."

    context_data = {
        "id": lead_id,
        "name": raw_name,
        "first_name": first_name,
        "last_name": last_name,
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
    if isinstance(result, dict):
        resp_call_id = (
            result.get("call_id") or result.get("call_uuid") or
            result.get("id") or result.get("ref_id") or
            (result.get("data") if isinstance(result.get("data"), dict) else {}).get("call_id")
        )
        if resp_call_id:
            ACTIVE_CALLER_CONTEXTS[str(resp_call_id)] = context_data
            print(f"[ContactForm] Associated Smartflo response callId '{resp_call_id}' to lead '{lead_id}'")

    return {
        "success": True,
        "lead_id": lead_id,
        "customer_number": form_data.phone.strip(),
        "smartflo_response": result
    }
