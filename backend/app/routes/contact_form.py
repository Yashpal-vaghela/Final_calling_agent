import os
import asyncio
import json
import uuid
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.app.services.id_service import generate_18_digit_lead_id
from backend.app.services.smartflo_service import smartflo_client
from backend.app.services.outbound_call_service import initiate_tracked_call
from backend.app.services.city_service import is_city_covered, get_nearest_covered_city, validate_city_pincode_match_async

router = APIRouter()

from backend.app.services.caller_context import ACTIVE_CALLER_CONTEXTS, register_caller_context

class ContactFormSubmission(BaseModel):
    name: str = Field(..., min_length=1, description="Caller full name")
    email: str = Field(..., min_length=3, description="Caller email address")
    phone: str = Field(..., min_length=5, description="Caller phone number (outbound destination)")
    city: str = Field(..., min_length=1, description="Caller city")
    pincode: str = Field(..., min_length=6, max_length=6, description="Caller 6-digit area pincode")
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
    Accepts leads from any city/PIN code across India; dynamically associates nearest covered center.
    """
    clean_city = form_data.city.strip()
    clean_pincode = form_data.pincode.strip()

    is_valid_pin, pin_err = await validate_city_pincode_match_async(clean_city, clean_pincode)
    if not is_valid_pin:
        raise HTTPException(status_code=400, detail=pin_err)

    is_covered = is_city_covered(clean_city)
    nearest_center = clean_city if is_covered else get_nearest_covered_city(clean_city)

    lead_id = generate_18_digit_lead_id()
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
        "city": clean_city,
        "pincode": clean_pincode,
        "nearest_covered_city": nearest_center,
        "is_covered": is_covered,
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
        "opening_intent": "outbound_contact_form",
        "city": clean_city,
        "pincode": clean_pincode,
        "nearest_center": nearest_center,
    }

    attempt_id, result = await initiate_tracked_call(
        smartflo_client, form_data.phone.strip(), lead_id=lead_id, source="contact_form",
        custom_params=custom_params, caller_context=context_data,
    )

    print(f"[ContactForm] Smartflo click-to-call response: {result}")
    return {
        "success": result.get("success") is True,
        "attempt_id": attempt_id,
        "ref_id": result.get("ref_id"),
        "error": result.get("error") or result.get("message") if result.get("success") is not True else None,
        "lead_id": lead_id,
        "customer_number": form_data.phone.strip(),
        "smartflo_response": result
    }
