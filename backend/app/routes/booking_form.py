import os
import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.app.services.id_service import generate_18_digit_lead_id
from backend.app.services.smartflo_service import smartflo_client
from backend.app.services.outbound_call_service import initiate_tracked_call
from backend.app.services.caller_context import ACTIVE_CALLER_CONTEXTS, register_caller_context
from backend.app.services.city_service import is_city_covered, get_nearest_covered_city, validate_city_pincode_match_async

router = APIRouter()


class BookingFormSubmission(BaseModel):
    first_name: str = Field(..., min_length=1, description="Caller first name")
    last_name: str = Field(..., min_length=1, description="Caller last name")
    email: str = Field(..., min_length=3, description="Caller email address")
    phone: str = Field(..., min_length=5, description="Caller phone number")
    city: str = Field(..., min_length=1, description="Caller city")
    pincode: str = Field(..., min_length=6, max_length=6, description="Caller 6-digit area pincode")
    message: str = Field(default="", description="Enquiry details or notes")
    doctor_name: Optional[str] = Field(default=None, description="Preferred dentist name selected by caller")

@router.get("/booking-form", response_class=HTMLResponse)
async def get_booking_form():
    """Serves the Booking Appointment Form webpage."""
    template_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates", "booking_form.html"))
    if not os.path.exists(template_path):
        raise HTTPException(status_code=404, detail="Booking form template not found.")
    
    with open(template_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200)

@router.post("/api/booking-form/submit")
async def submit_booking_form(form_data: BookingFormSubmission):
    """
    Receives submitted booking appointment form details, caches context, logs lead,
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
    full_name = f"{form_data.first_name.strip()} {form_data.last_name.strip()}"
    selected_doctor = form_data.doctor_name.strip() if form_data.doctor_name else None

    user_message = form_data.message.strip() if form_data.message else ""
    if selected_doctor:
        doc_note = f"[Preferred Doctor: {selected_doctor}]"
        user_message = f"{user_message} {doc_note}".strip() if user_message else doc_note
    
    context_data = {
        "id": lead_id,
        "name": full_name,
        "first_name": form_data.first_name.strip(),
        "last_name": form_data.last_name.strip(),
        "phone": form_data.phone.strip(),
        "email": form_data.email.strip(),
        "city": clean_city,
        "pincode": clean_pincode,
        "nearest_covered_city": nearest_center,
        "is_covered": is_covered,
        "doctor": selected_doctor,
        "doctor_name": selected_doctor,
        "notes": user_message,
        "message": user_message,
        "intent": "outbound_booking_form",
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    # Register into active lookup cache & persist lead
    register_caller_context(context_data)
    print(f"[BookingForm] Registered caller context for {context_data['name']} ({context_data['phone']}) - Lead ID: {lead_id} - Doctor: {selected_doctor or 'None'}")


    # Initiate Smartflo Click-to-Call
    custom_params = {
        "lead_id": lead_id,
        "opening_intent": "outbound_booking_form",
        "first_name": form_data.first_name.strip(),
        "city": clean_city,
        "pincode": clean_pincode,
        "nearest_center": nearest_center,
        "doctor": selected_doctor or "your selected doctor",
    }

    attempt_id, result = await initiate_tracked_call(
        smartflo_client, form_data.phone.strip(), lead_id=lead_id, source="booking_form",
        custom_params=custom_params, caller_context=context_data,
    )

    print(f"[BookingForm] Smartflo click-to-call response: {result}")
    
    return {
        "success": result.get("success") is True,
        "attempt_id": attempt_id,
        "ref_id": result.get("ref_id"),
        "error": result.get("error") or result.get("message") if result.get("success") is not True else None,
        "lead_id": lead_id,
        "customer_number": form_data.phone.strip(),
        "smartflo_response": result
    }
