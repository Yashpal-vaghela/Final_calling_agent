import os
import json
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.app.services.smartflo_service import smartflo_client
from backend.app.services.caller_context import register_caller_context
from backend.app.services.city_service import is_city_covered

router = APIRouter()

class BookingFormSubmission(BaseModel):
    first_name: str = Field(..., min_length=1, description="Caller first name")
    last_name: str = Field(..., min_length=1, description="Caller last name")
    email: str = Field(..., min_length=3, description="Caller email address")
    phone: str = Field(..., min_length=5, description="Caller phone number")
    city: str = Field(..., min_length=1, description="Caller city")
    message: str = Field(default="", description="Enquiry details or notes")

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
    """
    if not is_city_covered(form_data.city):
        raise HTTPException(status_code=400, detail=f"City '{form_data.city}' is not covered. Please select a valid city.")

    lead_id = str(uuid.uuid4())
    full_name = f"{form_data.first_name.strip()} {form_data.last_name.strip()}"
    
    context_data = {
        "id": lead_id,
        "name": full_name,
        "first_name": form_data.first_name.strip(),
        "last_name": form_data.last_name.strip(),
        "phone": form_data.phone.strip(),
        "email": form_data.email.strip(),
        "city": form_data.city.strip(),
        "notes": form_data.message.strip() if form_data.message else "",
        "message": form_data.message.strip() if form_data.message else "",
        "intent": "outbound_booking_form",
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    # Register into active lookup cache & persist lead
    register_caller_context(context_data)
    print(f"[BookingForm] Registered caller context for {context_data['name']} ({context_data['phone']}) - Lead ID: {lead_id}")

    # Initiate Smartflo Click-to-Call
    custom_params = {
        "lead_id": lead_id,
        "opening_intent": "outbound_booking_form",
        "first_name": form_data.first_name.strip(),
        "city": form_data.city.strip()
    }

    result = await smartflo_client.initiate_click_to_call(
        customer_number=form_data.phone.strip(),
        custom_params=custom_params
    )

    print(f"[BookingForm] Smartflo click-to-call response: {result}")
    
    # We could theoretically link response call_id here as well, but wait, 
    # we need to import ACTIVE_CALLER_CONTEXTS if we want to do that.
    # It's better to import it. I'll modify that. Let me include ACTIVE_CALLER_CONTEXTS.
    from backend.app.services.caller_context import ACTIVE_CALLER_CONTEXTS
    
    if isinstance(result, dict):
        resp_call_id = (
            result.get("call_id") or result.get("call_uuid") or
            result.get("id") or result.get("ref_id") or
            (result.get("data") if isinstance(result.get("data"), dict) else {}).get("call_id")
        )
        if resp_call_id:
            ACTIVE_CALLER_CONTEXTS[str(resp_call_id)] = context_data
            print(f"[BookingForm] Associated Smartflo response callId '{resp_call_id}' to lead '{lead_id}'")

    return {
        "success": True,
        "lead_id": lead_id,
        "customer_number": form_data.phone.strip(),
        "smartflo_response": result
    }
